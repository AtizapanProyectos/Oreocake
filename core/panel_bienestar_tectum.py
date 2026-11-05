"""Panel TECTUM de bienestar emocional.

Vista nueva e independiente. No escribe en la base: solo lee citas del convenio
y, para el sentimiento, manda a Groq extractos anonimizados de HistorialClinico.
El acceso es exclusivamente de superusuario.
"""
import hashlib
import json
import logging
import os
import re
import time
from calendar import monthrange
from collections import Counter, defaultdict

from django.contrib.auth.decorators import user_passes_test
from django.core.cache import cache
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET

from core.models import Cita, EvaluacionSesionPaciente, HistorialClinico

logger = logging.getLogger(__name__)

MESES = [
    "", "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]
MESES_CORTO = ["", "Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"]
DIAS = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]

EMOCIONES = [
    ("Estrés", "#0C2F7C"),
    ("Ansiedad", "#2F5BB7"),
    ("Agotamiento", "#6F8FD6"),
    ("Desmotivación", "#A9BCE8"),
    ("Problemas familiares", "#858688"),
    ("Conflictos laborales", "#B5B6B8"),
    ("Bienestar positivo", "#1F8A70"),
]

ACCIONES = {
    "Estrés": [
        "Taller breve de manejo de estrés",
        "Revisar con líderes la carga y los plazos de la franja más demandada",
        "Promover pausas activas durante la jornada",
    ],
    "Ansiedad": [
        "Abrir un espacio de contención en la semana de mayor carga",
        "Dar información clara cuando haya cambios organizacionales",
        "Dejar técnicas cortas de regulación para usar entre sesiones",
    ],
    "Agotamiento": [
        "Revisar jornadas y horas extra en las áreas con más citas",
        "Proteger al menos un bloque de descanso en la semana",
        "Dar seguimiento a quien acumula varias sesiones por cansancio",
    ],
    "Desmotivación": [
        "Conversar reconocimiento y ruta de crecimiento con líderes",
        "Identificar equipos con varias bitácoras de desmotivación",
        "Cerrar cada sesión con un paso concreto y visible",
    ],
    "Problemas familiares": [
        "Ofrecer horarios que faciliten la conciliación",
        "Mantener cupo de pareja o familia si el motivo se repite",
        "No tratar el tema familiar como un asunto solo laboral",
    ],
    "Conflictos laborales": [
        "Capacitar a líderes en conversaciones difíciles",
        "Revisar relaciones de equipo señaladas en más de una bitácora",
        "Separar el conflicto laboral del estado clínico de la persona",
    ],
    "Bienestar positivo": [
        "Sostener lo que ya está funcionando",
        "Documentar las prácticas que las bitácoras marcan como avance",
        "No reducir la oferta solo porque el ánimo mejora",
    ],
}

PALABRAS = [
    ("Estrés", ["estrés", "estres", "presión", "presion", "sobrecarga", "plazo", "tensión", "tension"]),
    ("Ansiedad", ["ansiedad", "ansioso", "angustia", "preocup", "miedo", "incertidumbre", "nervios"]),
    ("Agotamiento", ["agotamiento", "burnout", "cansancio", "insomnio", "no duerm", "sueño", "sueno", "fatiga"]),
    ("Desmotivación", ["desmotiv", "triste", "ánimo bajo", "animo bajo", "desesperanza", "vacío", "vacio", "depre"]),
    ("Problemas familiares", ["familia", "pareja", "hijo", "hija", "casa", "divorcio", "crianza"]),
    ("Conflictos laborales", ["jefe", "líder", "lider", "compañer", "companer", "conflicto laboral", "acoso"]),
    ("Bienestar positivo", ["avance", "mejor", "logro", "estable", "herramienta", "alivio", "tranquil"]),
]
MANIFESTACIONES = [
    ("Sobrecarga laboral", ["sobrecarga", "carga de trabajo", "exceso de trabajo", "trabajo"]),
    ("Plazos y presión", ["plazo", "presión", "presion", "entrega"]),
    ("Incertidumbre", ["incertidumbre", "no saber", "futuro"]),
    ("Preocupación económica", ["dinero", "econom", "deuda", "pago"]),
    ("Falta de descanso", ["sueño", "sueno", "insomnio", "no duerm", "descanso"]),
    ("Exceso de horas", ["horas extra", "jornada", "turno"]),
    ("Falta de reconocimiento", ["reconoc", "no valor", "invisib"]),
    ("Conciliación vida-trabajo", ["familia", "hijo", "casa", "pareja"]),
    ("Relación con líderes", ["jefe", "líder", "lider", "supervisor"]),
    ("Avance en sesión", ["avance", "mejor", "logro", "herramienta"]),
]
ALIASES = {
    "estres": "Estrés", "stress": "Estrés", "tension": "Estrés", "tensión": "Estrés",
    "ansiedad": "Ansiedad", "angustia": "Ansiedad",
    "agotamiento": "Agotamiento", "burnout": "Agotamiento", "cansancio": "Agotamiento",
    "desmotivacion": "Desmotivación", "desmotivación": "Desmotivación", "tristeza": "Desmotivación",
    "problemas familiares": "Problemas familiares", "familia": "Problemas familiares",
    "conflictos laborales": "Conflictos laborales", "conflicto laboral": "Conflictos laborales",
    "bienestar positivo": "Bienestar positivo", "bienestar": "Bienestar positivo",
}

_solo_superuser = user_passes_test(lambda u: u.is_superuser, login_url="/")


def _qs_tectum():
    return Cita.objects.exclude(estado="Cancelada").filter(Q(es_tectum=True) | Q(es_beneficios=True))


def _periodo(request):
    hoy = timezone.localdate()
    # Por defecto se abre el mes más reciente con bitácoras clínicas: un mes con
    # citas pero sin notas mostraría el panel vacío. Si aún no hay notas, se usa
    # el mes de la última cita del convenio.
    ultima_nota = (
        HistorialClinico.objects
        .filter(Q(cita__es_tectum=True) | Q(cita__es_beneficios=True))
        .exclude(cita__estado="Cancelada")
        .exclude(notas_sesion="")
        .exclude(notas_sesion__isnull=True)
        .order_by("-cita__fecha")
        .values_list("cita__fecha", flat=True)
        .first()
    )
    ultima = _qs_tectum().order_by("-fecha").values_list("fecha", flat=True).first()
    referencia = ultima_nota or ultima
    anio = referencia.year if referencia else hoy.year
    mes = referencia.month if referencia else hoy.month
    try:
        if request.GET.get("anio"):
            anio = int(request.GET.get("anio"))
        if request.GET.get("mes"):
            mes = int(request.GET.get("mes"))
    except (TypeError, ValueError):
        pass
    if not (1 <= mes <= 12 and 2000 <= anio <= 2100):
        anio = referencia.year if referencia else hoy.year
        mes = referencia.month if referencia else hoy.month
    return anio, mes


def _mes_anterior(anio, mes):
    return (anio - 1, 12) if mes == 1 else (anio, mes - 1)


def _nombre_psico(cita):
    perfil = cita.psicologo
    usuario = perfil.usuario if perfil and getattr(perfil, "usuario_id", None) else None
    if not usuario:
        return "Sin asignar"
    first = (usuario.first_name or "").strip()
    last = (usuario.last_name or "").strip()
    nombre = " ".join(parte for parte in (first, last) if parte).strip() or usuario.username or "Sin asignar"
    return nombre.title() if nombre.isupper() else nombre


def _citas_mes(anio, mes):
    return list(
        _qs_tectum()
        .filter(fecha__year=anio, fecha__month=mes)
        .select_related("psicologo__usuario", "paciente")
        .order_by("fecha", "hora")
    )


def _redactar(texto, nombres=()):
    texto = texto or ""
    for nombre in nombres:
        texto = re.sub(r"\b" + re.escape(nombre) + r"\b", "[paciente]", texto, flags=re.I)
    texto = re.sub(r"[\w.+-]+@[\w.-]+\.\w+", "[correo]", texto)
    texto = re.sub(r"\+?\d[\d\s().-]{8,}\d", "[telefono]", texto)
    texto = re.sub(r"\s+", " ", texto).strip()
    return texto[:400]


def _texto_nota(nota, nombres=()):
    partes = []
    for etiqueta, valor in (
        ("llegada", nota.como_llega),
        ("sesion", nota.notas_sesion),
        ("aprendizaje", nota.aprendizaje_paciente),
        ("cierre", nota.como_se_va),
        ("recomendaciones", nota.recomendaciones),
        ("diagnostico", nota.diagnostico_temporal),
    ):
        limpio = _redactar(valor, nombres)
        if limpio:
            partes.append(f"{etiqueta}: {limpio}")
    return " ".join(partes)[:600]


def _normalizar_emocion(valor):
    if not valor:
        return None
    clave = str(valor).strip().casefold()
    if clave in ALIASES:
        return ALIASES[clave]
    for nombre, _color in EMOCIONES:
        if nombre.casefold() == clave:
            return nombre
    return None


def _clasificar_palabras(texto):
    bajo = (texto or "").casefold()
    mejor, puntos = "Estrés", 0
    for nombre, palabras in PALABRAS:
        n = sum(bajo.count(p) for p in palabras)
        if n > puntos:
            mejor, puntos = nombre, n
    if puntos == 0:
        mejor = "Estrés" if bajo else "Bienestar positivo"
    manif = "No especificado en la nota"
    for etiqueta, palabras in MANIFESTACIONES:
        if any(p in bajo for p in palabras):
            manif = etiqueta
            break
    return mejor, manif


def _extraer_json(texto):
    if not texto:
        return None
    limpio = re.sub(r"^```(?:json)?", "", texto.strip(), flags=re.I).strip()
    limpio = re.sub(r"```$", "", limpio).strip()
    candidatos = [limpio]
    # Objetos equilibrados, respetando cadenas y escapes (más seguro que un regex).
    indice = 0
    while indice < len(limpio):
        if limpio[indice] != "{":
            indice += 1
            continue
        nivel, dentro_cadena, escape = 0, False, False
        for fin in range(indice, len(limpio)):
            letra = limpio[fin]
            if dentro_cadena:
                if escape:
                    escape = False
                elif letra == "\\":
                    escape = True
                elif letra == '"':
                    dentro_cadena = False
                continue
            if letra == '"':
                dentro_cadena = True
            elif letra == "{":
                nivel += 1
            elif letra == "}":
                nivel -= 1
                if nivel == 0:
                    candidatos.append(limpio[indice:fin + 1])
                    indice = fin
                    break
        indice += 1
    for blob in candidatos:
        for intento in (blob, re.sub(r",\s*([}\]])", r"\1", blob)):
            try:
                data = json.loads(intento)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict):
                return data
    return None


def _llamar_groq(messages):
    from groq import Groq

    key = os.environ.get("GROQ_API_KEY")
    if not key:
        raise RuntimeError("GROQ_API_KEY no configurada")
    client = Groq(api_key=key, timeout=10.0, max_retries=0)
    modelos = ("openai/gpt-oss-120b", "llama-3.3-70b-versatile")
    ultimo = None
    for modelo in modelos:
        try:
            resp = client.chat.completions.create(
                model=modelo,
                temperature=0.1,
                max_tokens=3500,
                messages=messages,
            )
            texto = resp.choices[0].message.content or ""
            uso = getattr(resp, "usage", None)
            return texto, modelo, getattr(uso, "prompt_tokens", None), getattr(uso, "completion_tokens", None)
        except Exception as exc:
            ultimo = exc
            if "429" in str(exc) or "rate" in str(exc).lower():
                time.sleep(2)
                continue
            break
    raise RuntimeError(ultimo or "Groq no respondió")


def _bitacoras(anio, mes, citas):
    ids = [c.id for c in citas]
    notas = {
        n.cita_id: n
        for n in HistorialClinico.objects.filter(cita_id__in=ids)
        if (n.notas_sesion or "").strip()
    }
    # Nombres del periodo para sustituirlos antes de mandar texto a Groq.
    nombres = set()
    for cita in citas:
        usuario = cita.paciente
        if not usuario:
            continue
        first = (usuario.first_name or "").strip()
        last = (usuario.last_name or "").strip()
        for nombre in (first, last, f"{first} {last}".strip()):
            if len(nombre) >= 3 and nombre.casefold() not in {"paciente", "usuario", "sin nombre"}:
                nombres.add(nombre)
    nombres = sorted(nombres, key=len, reverse=True)
    paquetes = []
    for cita in citas:
        nota = notas.get(cita.id)
        if not nota:
            continue
        texto = _texto_nota(nota, nombres)
        if not texto:
            continue
        paquetes.append({
            "id": f"b{cita.id}",
            "cita_id": cita.id,
            "semana": min(5, (cita.fecha.day - 1) // 7 + 1),
            "texto": texto,
        })
    return paquetes


def _cache_key(anio, mes, paquetes):
    firma = hashlib.sha256(
        "|".join(f"{p['id']}:{p['texto']}" for p in paquetes).encode("utf-8")
    ).hexdigest()[:16]
    return f"tectum-bienestar-sentimiento:{anio}:{mes}:{firma}"


def _contar_sentimiento(anio, mes, paquetes, clasificacion, fuente, modelo=None, notas_ia=0, sintesis=""):
    semanas = min(5, ((monthrange(anio, mes)[1] - 1) // 7) + 1)
    por_semana = {nombre: [0] * semanas for nombre, _color in EMOCIONES}
    manifs = defaultdict(Counter)
    for paquete in paquetes:
        emocion, manif = clasificacion.get(paquete["id"]) or _clasificar_palabras(paquete["texto"])
        emocion = _normalizar_emocion(emocion) or "Estrés"
        semana = max(1, min(semanas, int(paquete["semana"])))
        por_semana[emocion][semana - 1] += 1
        manifs[emocion][manif or "No especificado en la nota"] += 1

    emo = []
    tm = []
    for nombre, color in EMOCIONES:
        serie = por_semana[nombre]
        total = sum(serie)
        if not total:
            continue
        emo.append([nombre, serie, color])
        tops = manifs[nombre].most_common(2)
        primera = [tops[0][0], tops[0][1]] if tops else [nombre, total]
        segunda = [tops[1][0], tops[1][1]] if len(tops) > 1 else None
        tm.append([nombre, primera, segunda, color])

    total_notas = len(paquetes)
    if emo:
        principal, serie, _color = max(emo, key=lambda e: sum(e[1]))
        n = sum(serie)
        pct = round(100 * n / total_notas) if total_notas else 0
        manif = tm[[e[0] for e in emo].index(principal)][1]
        texto = (
            f"{principal} es el estado predominante en las bitácoras del mes "
            f"({n} de {total_notas}, {pct}%). La manifestación más frecuente es "
            f"«{manif[0]}» ({manif[1]})."
        )
        hallazgo = [texto, "Principal estado en bitácoras", ACCIONES.get(principal, ACCIONES["Estrés"])]
    else:
        principal, pct = None, 0
        hallazgo = [
            "No hay bitácoras clínicas en este periodo para interpretar el sentimiento.",
            "Sin notas de sesión",
            ["Pedir el cierre de bitácora al terminar la sesión", "No leer el vacío como ausencia de malestar"],
        ]

    if fuente == "groq":
        alcance = (
            f"{notas_ia} de {total_notas}"
            if notas_ia and notas_ia < total_notas
            else f"{total_notas}"
        )
        nota = (
            f"Lectura Groq ({modelo}) sobre {alcance} bitácoras anonimizadas. "
            "Las cifras las cuenta el sistema, no el modelo. No es un diagnóstico."
        )
    elif fuente == "palabras":
        nota = (
            f"Groq no estuvo disponible. Se clasificaron {total_notas} bitácoras por palabras clave. "
            "No es un diagnóstico."
        )
    else:
        nota = "No hay bitácoras clínicas en este periodo."

    return {
        "emo": emo,
        "tm": tm,
        "hallazgo": hallazgo,
        "ia_nota": nota,
        "notas": total_notas,
        "emocion_principal": principal,
        "emocion_pct": pct,
        "fuente": fuente,
        "sintesis": sintesis,
    }


def construir_sentimiento(anio, mes, citas, forzar=False):
    paquetes = _bitacoras(anio, mes, citas)
    if not paquetes:
        return _contar_sentimiento(anio, mes, [], {}, "vacio")

    key = _cache_key(anio, mes, paquetes)
    if not forzar:
        guardado = cache.get(key)
        if guardado:
            return guardado

    clasificacion = {}
    fuente = "palabras"
    modelo = None
    sintesis = ""
    # Techo de notas enviadas a Groq: mantiene la respuesta bajo el timeout de Gunicorn.
    con_ia = paquetes[:80]
    try:
        payload = [{"id": p["id"], "semana": p["semana"], "texto": p["texto"]} for p in con_ia]
        messages = [
            {
                "role": "system",
                "content": (
                    "Eres analista de bienestar organizacional. Clasificas bitácoras clínicas "
                    "ya anonimizadas. No inventes sesiones, nombres ni cifras. No emitas diagnósticos. "
                    "Devuelve únicamente JSON válido."
                ),
            },
            {
                "role": "user",
                "content": (
                    "Catálogo cerrado de emociones: Estrés, Ansiedad, Agotamiento, Desmotivación, "
                    "Problemas familiares, Conflictos laborales, Bienestar positivo.\n"
                    "Asigna a cada id exactamente una emoción de ese catálogo y una manifestación "
                    "de 2 a 5 palabras, basada solo en el texto. Si el texto describe avance o "
                    "estabilidad, usa Bienestar positivo. No repitas nombres, correos ni teléfonos.\n\n"
                    "Bitácoras:\n"
                    + json.dumps(payload, ensure_ascii=False)
                    + "\n\nResponde con este esquema y nada más:\n"
                    '{"clasificaciones":[{"id":"b1","emocion":"Estrés","manifestacion":"Sobrecarga laboral"}],'
                    '"sintesis":"Dos o tres frases sobre el patrón emocional del mes, sin cifras, sin nombres y sin diagnósticos."}'
                ),
            },
        ]
        texto, modelo, tok_in, tok_out = _llamar_groq(messages)
        data = _extraer_json(texto) or {}
        for item in data.get("clasificaciones") or []:
            if not isinstance(item, dict):
                continue
            emocion = _normalizar_emocion(item.get("emocion"))
            manif = re.sub(r"\s+", " ", str(item.get("manifestacion") or "")).strip()[:42]
            if re.search(r"@|\d{7,}", manif):
                manif = ""
            if item.get("id") and emocion:
                clasificacion[str(item["id"])] = (emocion, manif or "No especificado en la nota")
        if clasificacion:
            fuente = "groq"
        texto_sintesis = data.get("sintesis")
        if isinstance(texto_sintesis, str):
            texto_sintesis = re.sub(r"\s+", " ", texto_sintesis).strip()[:600]
            if not re.search(r"[@\d]", texto_sintesis):
                sintesis = texto_sintesis
        logger.info(
            "tectum_bienestar_ia modelo=%s notas=%s clasificadas=%s tokens=%s/%s",
            modelo, len(paquetes), len(clasificacion), tok_in, tok_out,
        )
    except Exception:
        logger.exception("tectum_bienestar_ia fallo; se usa clasificación por palabras")

    for paquete in paquetes:
        if paquete["id"] not in clasificacion:
            clasificacion[paquete["id"]] = _clasificar_palabras(paquete["texto"])

    resultado = _contar_sentimiento(
        anio, mes, paquetes, clasificacion, fuente, modelo,
        notas_ia=sum(1 for p in con_ia if p["id"] in clasificacion),
        sintesis=sintesis,
    )
    cache.set(key, resultado, 6 * 3600)
    return resultado


def _meses_nav(anio, mes):
    filas = (
        _qs_tectum()
        .values("fecha__year", "fecha__month")
        .annotate(n=Count("id"))
        .order_by("-fecha__year", "-fecha__month")
    )
    return [
        {
            "mes": fila["fecha__month"],
            "anio": fila["fecha__year"],
            "n": fila["n"],
            "label": f"{MESES_CORTO[fila['fecha__month']]} {fila['fecha__year']}",
            "activo": fila["fecha__year"] == anio and fila["fecha__month"] == mes,
        }
        for fila in filas
    ]


def _serie_diaria(anio, mes):
    dias = monthrange(anio, mes)[1]
    serie = [0] * dias
    filas = (
        _qs_tectum()
        .filter(fecha__year=anio, fecha__month=mes)
        .values("fecha")
        .annotate(n=Count("id"))
    )
    for fila in filas:
        serie[fila["fecha"].day - 1] = fila["n"]
    return serie


def construir_operacion(anio, mes):
    citas = _citas_mes(anio, mes)
    dias = monthrange(anio, mes)[1]
    day = [0] * dias
    horas = []
    por_psico = Counter()
    for cita in citas:
        day[cita.fecha.day - 1] += 1
        if cita.hora:
            horas.append(cita.hora.hour)
        por_psico[_nombre_psico(cita)] += 1

    h0 = min([8] + horas) if horas else 8
    h1 = max([20] + horas) if horas else 20
    hrs = list(range(h0, h1 + 1))
    w = [[0] * len(hrs) for _ in DIAS]
    for cita in citas:
        if not cita.hora:
            continue
        w[cita.fecha.weekday()][cita.hora.hour - h0] += 1

    ids = [c.id for c in citas]
    con_nota = set(
        HistorialClinico.objects.filter(cita_id__in=ids)
        .exclude(notas_sesion="")
        .exclude(notas_sesion__isnull=True)
        .values_list("cita_id", flat=True)
    )
    con_eval = set(EvaluacionSesionPaciente.objects.filter(cita_id__in=ids).values_list("cita_id", flat=True))
    total = len(citas)
    completadas = sum(1 for c in citas if c.estado == "Completada")
    colaboradores = len({c.paciente_id for c in citas})
    prev_anio, prev_mes = _mes_anterior(anio, mes)
    prev_full = _serie_diaria(prev_anio, prev_mes)
    prev_total = sum(prev_full)
    hoy = timezone.localdate()
    en_curso = anio == hoy.year and mes == hoy.month and hoy.day < dias

    otros = (
        _qs_tectum()
        .exclude(fecha__year=anio, fecha__month=mes)
        .values("fecha__year", "fecha__month")
        .annotate(n=Count("id"))
        .order_by("fecha__year", "fecha__month")
    )
    mostrar_hist = len(otros) >= 2
    largo = max(dias, len(prev_full))
    hist_full = [0] * largo
    if mostrar_hist:
        acumulado = [0] * largo
        for fila in otros:
            serie = _serie_diaria(fila["fecha__year"], fila["fecha__month"])
            for i in range(largo):
                acumulado[i] += serie[i] if i < len(serie) else 0
        hist_full = [round(v / len(otros)) for v in acumulado]

    def _pad(serie, n):
        return list(serie[:n]) + [0] * max(0, n - len(serie))

    columnas = [sum(fila[j] for fila in w) for j in range(len(hrs))]
    pk = columnas.index(max(columnas)) if any(columnas) else 0
    pico = hrs[pk] if hrs else 0
    pros = [[f"Psic. {nombre}", n] for nombre, n in por_psico.most_common()]

    if en_curso:
        growth_txt, growth_cls = "en curso", ""
        growth_sub = f"al día {hoy.day}; {MESES[prev_mes]} cerró con {prev_total}"
    elif prev_total == 0 and total == 0:
        growth_txt, growth_sub, growth_cls = "sin citas", f"ni en {MESES[prev_mes]}", ""
    elif prev_total == 0:
        growth_txt, growth_sub, growth_cls = "nuevo", f"sin citas en {MESES[prev_mes]}", "up"
    else:
        growth = (total / prev_total - 1) * 100
        growth_txt = f"{growth:+.0f}%"
        growth_sub = f"de {prev_total} a {total} citas"
        growth_cls = "up" if growth >= 0 else "down"

    if en_curso:
        evaluables = [c for c in citas if c.fecha <= hoy]
    else:
        evaluables = citas
    documentadas = sum(1 for c in evaluables if c.id in con_nota)
    cierre_txt = f"{round(100 * documentadas / len(evaluables))}%" if evaluables else "—"
    if not total:
        cierre_sub = "sin citas en el periodo"
    elif not evaluables:
        cierre_sub = "aún no hay citas realizadas"
    elif en_curso:
        cierre_sub = f"{documentadas} bitácoras de {len(evaluables)} citas al día {hoy.day}"
    else:
        cierre_sub = f"{documentadas} bitácoras de {len(evaluables)} citas"
    horario_txt = f"{pico:02d}:00 h" if total else "—"
    horario_sub = f"entre {pico:02d}:00 y {pico + 1:02d}:00" if total else "sin citas"
    psico_txt = pros[0][0].replace("Psic. ", "") if pros else "Sin citas"
    psico_sub = f"{pros[0][1]} citas agendadas" if pros else "nadie en el periodo"
    psico_cls = "s" if pros else ""

    kpis = [
        ["Citas agendadas", total, f"{MESES[mes]} {anio}", ""],
        ["Colaboradores atendidos", colaboradores, "personas únicas", ""],
        ["Crecimiento vs. " + MESES[prev_mes], growth_txt, growth_sub, growth_cls],
        ["Horario de mayor demanda", horario_txt, horario_sub, ""],
        ["Psicólogo con más citas", psico_txt, psico_sub, psico_cls],
        ["Cierre documental", cierre_txt, cierre_sub, ""],
    ]

    ins = []
    if total:
        orden = sorted(((columnas[i], hrs[i]) for i in range(len(hrs))), reverse=True)
        top3 = [h for n, h in orden if n][:3]
        share = round(100 * sum(n for n, h in orden if h in top3) / total)
        horas_txt = ", ".join(f"{h:02d}:00" for h in sorted(top3))
        ins.append([
            f"{share}% de las citas se concentran en {horas_txt}.",
            "Concentración horaria",
            [
                "Ampliar la capacidad de atención en esa franja",
                "Coordinar con líderes para facilitar esos horarios",
                "Ofrecer alternativa cuando esa franja se llene",
            ],
        ])
        por_dia = [sum(fila) for fila in w]
        dia_pico = por_dia.index(max(por_dia))
        ins.append([
            f"{DIAS[dia_pico]} concentra {por_dia[dia_pico]} citas ({round(100 * por_dia[dia_pico] / total)}% del mes).",
            "Día de mayor demanda",
            [
                "Revisar si ese día tiene suficiente cobertura",
                "No asumir que el fin de semana se reparte parejo",
                "Cuidar la carga de quien cubre ese día",
            ],
        ])
    else:
        ins.append([
            f"No hay citas TECTUM agendadas en {MESES[mes]} {anio}.",
            "Sin operación en el periodo",
            ["Confirmar que el convenio esté marcado en la cita", "Revisar el mes anterior en el selector"],
        ])

    if en_curso:
        ins.append([
            f"{MESES[mes].capitalize()} va en {total} citas al día {hoy.day}. {MESES[prev_mes].capitalize()} cerró con {prev_total}. No es un cierre de mes.",
            "Adopción del programa",
            [
                "No comparar un mes abierto contra un mes cerrado",
                "Volver a medir cuando cierre el periodo",
                f"Usar {MESES[prev_mes]} como línea base del convenio",
            ],
        ])
    elif prev_total:
        ins.append([
            f"El programa pasó de {prev_total} citas en {MESES[prev_mes]} a {total} en {MESES[mes]}.",
            "Adopción del programa",
            [
                "Comparar este mes con el anterior antes de concluir una tendencia",
                "Medir de nuevo con este mismo panel",
                "Separar agenda de cierre documental",
            ],
        ])
    elif total:
        ins.append([
            f"{MESES[prev_mes].capitalize()} no tiene citas del convenio; {MESES[mes]} abre con {total}.",
            "Adopción del programa",
            [
                "Tomar este mes como línea base",
                "No calcular un crecimiento porcentual sin mes previo",
                "Volver a medir el mes siguiente",
            ],
        ])

    return {
        "anio": anio,
        "mes": mes,
        "mes_nombre": MESES[mes],
        "mes_corto": MESES_CORTO[mes],
        "prev_nombre": MESES[prev_mes],
        "mostrar_hist": mostrar_hist,
        "en_curso": en_curso,
        "corte_dia": hoy.day if en_curso else None,
        "meses": _meses_nav(anio, mes),
        "citas": citas,
        "panel": {
            "day": day,
            "cmp_day": _pad(day, largo),
            "cmp_prev": _pad(prev_full, largo),
            "cmp_hist": _pad(hist_full, largo),
            "mostrar_hist": mostrar_hist,
            "pros": pros,
            "days": DIAS,
            "hrs": hrs,
            "w": w,
            "kpis": kpis,
            "ins": ins,
            "mes_corto": MESES_CORTO[mes],
            "dots": max(2, (max(day) if day else 0)),
            "notas": len(con_nota),
            "checkins": len(con_eval),
            "completadas": completadas,
        },
    }


def _respuesta(request, plantilla, context):
    response = render(request, plantilla, context)
    response["Cache-Control"] = "private, no-store"
    return response


def _json(datos):
    response = JsonResponse(datos)
    response["Cache-Control"] = "private, no-store"
    return response


@require_GET
@_solo_superuser
def panel_bienestar_tectum_view(request):
    anio, mes = _periodo(request)
    data = construir_operacion(anio, mes)
    data["panel"]["sentimiento_url"] = (
        reverse("panel_bienestar_tectum_sentimiento")
        + f"?mes={mes}&anio={anio}"
        + ("&refresh=1" if request.GET.get("refresh") == "1" else "")
    )
    context = {
        "anio": anio,
        "mes": mes,
        "mes_nombre": data["mes_nombre"],
        "prev_nombre": data["prev_nombre"],
        "mostrar_hist": data["mostrar_hist"],
        "meses": data["meses"],
        "panel": data["panel"],
        "en_curso": data["en_curso"],
        "corte_dia": data["corte_dia"],
    }
    return _respuesta(request, "tectum/Tectum · Panel de bienestar emocional.html", context)


@require_GET
@_solo_superuser
def panel_bienestar_tectum_sentimiento_view(request):
    anio, mes = _periodo(request)
    citas = _citas_mes(anio, mes)
    usuario = request.user.id or 0
    forzar = request.GET.get("refresh") == "1"
    if forzar:
        golpes = cache.get(f"tectum-bienestar-ia-rl:{usuario}", 0)
        if golpes >= 8:
            paquetes = _bitacoras(anio, mes, citas)
            resultado = _contar_sentimiento(
                anio, mes, paquetes,
                {p["id"]: _clasificar_palabras(p["texto"]) for p in paquetes},
                "palabras",
            )
            resultado["ia_nota"] = "Límite de lecturas IA alcanzado esta hora. Se usó clasificación por palabras."
            return _json(resultado)
        cache.set(f"tectum-bienestar-ia-rl:{usuario}", golpes + 1, 3600)
    resultado = construir_sentimiento(anio, mes, citas, forzar=forzar)
    return _json(resultado)
