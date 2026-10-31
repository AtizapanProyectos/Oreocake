# =========================================================================
# INTELLIGENCE CENTER (30/10/2026)
# Motor de cálculo operativa para el panel de análisis de Dirección.
#
# REGLAS DE ESTE MÓDULO (respetan AGENTS.md):
# - SOLO LECTURA: ninguna función escribe en la base de datos.
# - Sin datos simulados: toda métrica proviene de tablas reales (User,
#   UsuarioPerfil, Cita, CuestionarioRegistro, PerfilPsicologo).
# - El estado de la Cita ('Confirmada'/'Completada'/'Pendiente') NO se usa
#   para contar actividad (instrucción de operación 30/10/2026: el estatus
#   "no se comporta como debería"). Una fila de Cita = una sesión tomada.
#   'Cancelada' se lee únicamente como señal adicional en alertas.
# - Ingresos = ESTIMACIÓN con la constante oficial del sistema
#   (calcular_precio_sesion); no existe registro de pagos en la BD.
#   Citas TECTUM In-House (es_tectum=True) = 0 (cobertura 100%).
# - Agregación mayormente en base de datos (annotate/values) o en una
#   pasada en memoria; sin consultas N+1.
# =========================================================================

from collections import defaultdict
from datetime import timedelta, date, datetime, time as _time

from django.db.models import Count, Max, Min, Q
from django.utils import timezone

from .models import Cita, PerfilPsicologo, CuestionarioRegistro, UsuarioPerfil, User
from django.db import transaction  # noqa: F401 — solo para Reasignar_Real (escritura atómica)
# --------------------------------------------------------------------------
# Helpers internos
# --------------------------------------------------------------------------

def _hoy():
    return timezone.localdate()

def _ahora():
    return timezone.now()

def _aware_medianoche(fecha):
    """Fecha -> datetime aware (inicio del dia); evita warnings de tz de Django."""
    return timezone.make_aware(datetime.combine(fecha, _time.min))

_SENTINEL = None

def _fecha_sentinela():
    """Fecha minima segura para ordenar registros sin date_joined."""
    global _SENTINEL
    if _SENTINEL is None:
        _SENTINEL = timezone.make_aware(datetime.combine(date(2000, 1, 1), _time.min))
    return _SENTINEL

def _lunes_de_semana(fecha):
    return fecha - timedelta(days=fecha.weekday())

def _hace_dias(n):
    return _hoy() - timedelta(days=n)

def a_fecha(valor):
    """Convierte ISO string o date a date."""
    if isinstance(valor, str):
        try:
            return date.fromisoformat(str(valor)[:10])
        except (ValueError, TypeError):
            return None
    return valor if isinstance(valor, date) else None

TIPO_SESION_LABEL = {
    'individual': 'Individual',
    'pareja': 'En Pareja',
    'familiar': 'Terapia Familiar',
}

# Caben con las opciones del cuestionario: Mañana 9-12, Tarde 12-17, Noche 17-20
FRANJA_HORARIA = (('Mañana', 9, 12), ('Tarde', 12, 17), ('Noche', 17, 20))

DIA_LABEL = ['Lunes', 'Martes', 'Miércoles', 'Jueves', 'Viernes', 'Sábado', 'Domingo']

ESTADO_LABEL = {
    'nunca': 'Nunca agendó',
    'activo': 'Activo',
    'en_riesgo': 'En riesgo',
    'inactivo': 'Inactivo',
}


def _calcular_precio_total(tipo_sesion, integrantes_familia=None):
    """
    Precio oficial por cita (estimación de ingreso). Import lazy para evitar
    ciclo de imports con views.py (única fuente de verdad de precios).
    """
    from . import views as _views
    try:
        return _views.calcular_precio_sesion(tipo_sesion, integrantes_familia)['total']
    except Exception:
        return 0


def _pref_clasificador(valor):
    """Normaliza preferencia_terapeuta a Mujer / Hombre / Indistinto / Sin registro."""
    v = (str(valor or '')).strip().lower()
    if 'mujer' in v:
        return 'Mujer'
    if 'hombre' in v:
        return 'Hombre'
    if 'indistinto' in v or 'indiferente' in v:
        return 'Indistinto'
    return 'Sin registro'


def _franja_de_texto(valores):
    """
    Normaliza la respuesta 'horario' del cuestionario (lista o cadena con
    textos como 'Mañana (9:00 a 12:00)') a nombres de franja estándar.
    """
    if valores is None:
        return []
    if not isinstance(valores, (list, tuple)):
        valores = [valores]
    franjas = []
    for v in valores:
        l = (str(v or '')).strip().lower()
        if not l:
            continue
        if 'mañana' in l or 'manana' in l:
            f = 'Mañana'
        elif 'tarde' in l:
            f = 'Tarde'
        elif 'noche' in l:
            f = 'Noche'
        elif 'fin' in l or 'sábado' in l or 'sabado' in l or 'domingo' in l:
            f = 'Fin de semana'
        else:
            f = None
        if f and f not in franjas:
            franjas.append(f)
    return franjas


def _formato_hora_12(h_int):
    hh12 = h_int % 12 or 12
    return f'{hh12}:00 {"PM" if h_int >= 12 else "AM"}'


# --------------------------------------------------------------------------
# Colección base: pacientes, preferencias y agregados de citas
# --------------------------------------------------------------------------

def _coleccion_pacientes():
    """
    Una pasada por cada tabla:

    1) pacientes: UsuarioPerfil de pacientes (es_psicologo=False) + usuario.
    2) citas_agg: MIN/MAX/COUNT por paciente en UNA consulta:
       - total: TODAS las filas de Cita (el estado no descarta nada).
       - ultima_pasada: última fecha ya transcurrida.
       - proxima: próxima cita futura (hoy en adelante).
       - canceladas: conteo del estado 'Cancelada' (solo señal de alerta).
    3) prefs: preferencia_terapeuta del cuestionario más reciente por
       paciente + número de respuestas con contenido (embudo "perfil
       completo" = >= 6 campos contestados).

    Devuelve (pacientes:list, citas_agg:dict, prefs:dict).
    """
    hoy = _hoy()

    perfiles = (
        UsuarioPerfil.objects
        .filter(es_psicologo=False)
        .select_related('usuario', 'psicologo_asignado', 'psicologo_asignado__usuario')
        .order_by('usuario__date_joined', 'id')
    )

    citas_agg = {
        row['paciente_id']: row
        for row in (
            Cita.objects
            .values('paciente_id')
            .annotate(
                total=Count('id'),
                primera=Min('fecha'),
                ultima_pasada=Max('fecha', filter=Q(fecha__lt=hoy)),
                proxima=Min('fecha', filter=Q(fecha__gte=hoy)),
                canceladas=Count('id', filter=Q(estado='Cancelada')),
            )
        )
    }

    prefs = {}
    vistos = set()
    registros_cuestionario = (
        CuestionarioRegistro.objects
        .order_by('paciente_id', '-fecha_completado', '-id')
        .values('paciente_id', 'respuestas')
    )
    for row in registros_cuestionario:
        pid = row['paciente_id']
        if pid in vistos:
            continue
        vistos.add(pid)
        resp = row['respuestas'] or {}
        prefs[pid] = {
            'preferencia': _pref_clasificador(resp.get('preferencia_terapeuta')),
            'campos': sum(1 for v in resp.values() if v not in (None, '', [], {})),
            'horarios': _franja_de_texto(resp.get('horario')),
        }
        # Si horario es una sola cadena (no lista), normalizar a lista
        if prefs[pid]['horarios'] and isinstance(prefs[pid]['horarios'], str):
            prefs[pid]['horarios'] = [prefs[pid]['horarios']]

    pacientes = []
    for perfil in perfiles:
        user = perfil.usuario
        agg = citas_agg.get(user.id) if user else None
        pref = prefs.get(user.id) if user else None
        if pref is None:
            pref = {'preferencia': 'Sin registro', 'campos': 0, 'horarios': []}

        psicologo = None
        if perfil.psicologo_asignado:
            p = perfil.psicologo_asignado
            nombre = None
            if p.usuario:
                nombre = p.usuario.first_name or p.usuario.username
            psicologo = {
                'id': p.id,
                'nombre': f'{"Dra." if p.genero == "Mujer" else "Dr."} {nombre or "Sin nombre"}'.strip(),
                'genero': p.genero,
            }

        pacientes.append({
            'id': perfil.id,
            'user_id': user.id if user else None,
            'nombre': (perfil.nombre or (user.first_name if user else '') or
                       (user.username if user else 'Paciente')),
            'email': user.email if user else '',
            'fecha_registro': user.date_joined.isoformat() if user else None,
            'date_joined': user.date_joined if user else None,
            'es_tectum': perfil.es_tectum,
            'es_beneficios': perfil.es_beneficios,
            'psicologo': psicologo,
            'preferencia': pref['preferencia'],
            'perfil_campos': pref['campos'],
            'citas_total': agg['total'] if agg else 0,
            'primera': agg['primera'].isoformat() if agg and agg['primera'] else None,
            'ultima_pasada': agg['ultima_pasada'].isoformat() if agg and agg['ultima_pasada'] else None,
            'proxima': agg['proxima'].isoformat() if agg and agg['proxima'] else None,
            'canceladas': agg['canceladas'] if agg else 0,
        })
    return pacientes, citas_agg, prefs


def _estado_paciente(fila, hoy=None):
    """
    Regla oficial (30/10/2026):
      - Nunca agendó: 0 filas de Cita.
      - Activo: cita futura programada O última sesión hace <= 15 días
        (ciclo semanal o quincenal).
      - En riesgo: 16-30 días sin sesión y sin próxima cita.
      - Inactivo: más de 30 días sin sesión y sin próxima cita.
    """
    hoy = hoy or _hoy()
    citas_total = fila.get('citas_total') or 0
    if not citas_total:
        return 'nunca'
    if fila.get('proxima'):
        return 'activo'
    ultima = a_fecha(fila.get('ultima_pasada'))
    if ultima is None:
        return 'en_riesgo'
    dias = (hoy - ultima).days
    if dias <= 15:
        return 'activo'
    if dias <= 30:
        return 'en_riesgo'
    return 'inactivo'


# --------------------------------------------------------------------------
# Conteo de estados sobre cualquier lista de pacientes
# --------------------------------------------------------------------------

def conteo_estados(fila_list):
    conteo = defaultdict(int)
    for p in fila_list:
        conteo[p['estado']] += 1
    return dict(conteo)


# --------------------------------------------------------------------------
# 2. RESUMEN GENERAL DEL NEGOCIO (KPIs)
# --------------------------------------------------------------------------

def _ingresos_estimados_rango(desde, hasta):
    """Ingresos estimados por rango de fechas de la Cita (1 consulta)."""
    rows = (Cita.objects
            .filter(fecha__gte=desde, fecha__lte=hasta)
            .values('tipo_sesion', 'integrantes_familia', 'es_tectum'))
    total = 0
    exentos = 0
    cobrables = 0
    for r in rows:
        if r['es_tectum']:
            exentos += 1
            continue
        total += _calcular_precio_total(r['tipo_sesion'], r['integrantes_familia'])
        cobrables += 1
    return {'total': total, 'exentos': exentos, 'cobrables': cobrables, 'citas': len(rows)}


def _conteo_citas_rango(desde, hasta):
    return Cita.objects.filter(fecha__gte=desde, fecha__lte=hasta).count()


def _conteo_registros(desde, hasta=None):
    qs = User.objects.filter(date_joined__gte=_aware_medianoche(desde), perfil__isnull=False)
    if hasta:
        qs = qs.filter(date_joined__lt=_aware_medianoche(hasta))
    return qs.count()


def delta_pct(actual, previo):
    if not previo:
        return None
    return round(100.0 * (actual - previo) / previo, 1)


def KPIs():
    """
    Tarjetas principales. Delta = últimos 30 días vs 30 días previos
    (comparación justa con periodos parciales).
    """
    hoy = _hoy()
    inicio_mes = hoy.replace(day=1)
    lunes = _lunes_de_semana(hoy)

    pacientes, _, _ = _coleccion_pacientes()
    estados = defaultdict(int)
    for p in pacientes:
        estados[_estado_paciente(p, hoy)] += 1

    psicologos_activos = PerfilPsicologo.objects.filter(esta_activo=True).count()
    psicologos_totales = PerfilPsicologo.objects.count()

    con_cita = sum(1 for p in pacientes if p['citas_total'] >= 1)
    con_segunda = sum(1 for p in pacientes if p['citas_total'] >= 2)
    retencion = round(100.0 * con_segunda / con_cita, 1) if con_cita else 0

    ingresos_hoy = _ingresos_estimados_rango(hoy, hoy)
    ingresos_mes = _ingresos_estimados_rango(inicio_mes, hoy)

    d30_ini = _hace_dias(30)
    d60_ini = _hace_dias(60)
    registros_30 = _conteo_registros(d30_ini)
    registros_previos = _conteo_registros(d60_ini, d30_ini)
    citas_30 = _conteo_citas_rango(d30_ini, hoy)
    citas_30_prev = _conteo_citas_rango(d60_ini, _hace_dias(1))

    return {
        'generado_en': _ahora().isoformat(),
        'pacientes': {
            'total': len(pacientes),
            'activos': estados['activo'],
            'en_riesgo': estados['en_riesgo'],
            'inactivos': estados['inactivo'],
            'nunca_agendaron': estados['nunca'],
            'nuevos_semana': _conteo_registros(lunes),
            'nuevos_mes': _conteo_registros(inicio_mes),
            'retencion_pct': retencion,
        },
        'psicologos': {'activos': psicologos_activos, 'total': psicologos_totales},
        'citas': {
            'hoy': _conteo_citas_rango(hoy, hoy),
            'mes': _conteo_citas_rango(inicio_mes, hoy),
            'semana': _conteo_citas_rango(lunes, hoy),
            'total': Cita.objects.count(),
        },
        'ingresos': {
            'hoy': ingresos_hoy,
            'mes': ingresos_mes,
            'nota': ('Estimado con la tarifa oficial del sistema; convenio '
                     'In-House no paga y no existe registro de pagos en la base de datos.'),
        },
        'delta': {
            'registros_30d': delta_pct(registros_30, registros_previos),
            'registros_30d_actual': registros_30,
            'registros_30d_previo': registros_previos,
            'citas_30d': delta_pct(citas_30, citas_30_prev),
            'citas_30d_actual': citas_30,
            'citas_30d_previo': citas_30_prev,
        },
    }


# --------------------------------------------------------------------------
# 1. FEED EN TIEMPO REAL
# --------------------------------------------------------------------------

def Feed_Eventos(desde=None, limite=50):
    """
    Feed por cursor: eventos con timestamp mayor a `desde` (ISO). Fuentes
    reales existentes:
      - Registros: User.date_joined (cuentas de pacientes con perfil).
      - Citas: Cita.fecha_creacion (alta de la sesión; incluye primeras).
    NOTA 30/10/2026: el sistema no registra reagendaciones ni pagos (la vista
    de reagendar actualiza fecha/hora sin dejar rastro y nadie guarda
    montos), por lo que esos eventos no existen como datos; NO se simulan.
    """
    desde_dt = desde or (_ahora() - timedelta(hours=6))
    if isinstance(desde_dt, str):
        from django.utils.dateparse import parse_datetime
        parsed = parse_datetime(desde_dt)
        if parsed is None:
            desde_dt = _ahora() - timedelta(hours=6)
        else:
            desde_dt = timezone.make_aware(parsed) if timezone.is_naive(parsed) else parsed

    def _iso(dt):
        try:
            return timezone.localtime(dt).isoformat()
        except Exception:
            return str(dt)

    eventos = []
    for u in (User.objects
              .filter(date_joined__gt=desde_dt, perfil__isnull=False)
              .select_related('perfil')
              .order_by('date_joined')[:limite]):
        perfil = u.perfil
        marca = []
        if perfil.es_tectum:
            marca.append('TECTUM In-House')
        elif perfil.es_beneficios:
            marca.append('TECTUM Beneficios')
        nombre = perfil.nombre or u.first_name or u.username
        eventos.append({
            'tipo': 'registro',
            'ts': _iso(u.date_joined),
            'texto': f'{nombre} se registró en la plataforma',
            'detalle': ', '.join(marca) if marca else (u.email or ''),
        })

    for c in (Cita.objects
              .filter(fecha_creacion__gt=desde_dt)
              .select_related('paciente', 'paciente__perfil', 'psicologo__usuario')
              .order_by('fecha_creacion')[:limite]):
        pac = c.paciente
        perfil_pac = getattr(pac, 'perfil', None)
        nombre_p = ((perfil_pac and perfil_pac.nombre) or pac.first_name or pac.username)
        if c.psicologo and c.psicologo.usuario:
            nombre_psi = c.psicologo.usuario.first_name or c.psicologo.usuario.username
            titulo = 'Dra.' if c.psicologo.genero == 'Mujer' else 'Dr.'
            nombre_psi = f'{titulo} {nombre_psi}'
        else:
            nombre_psi = 'psicólogo por asignar'
        mot = (c.motivo or '').strip().lower()
        primera = 'primera' in mot
        texto = (f'{nombre_p} agendó{" su primera" if primera else " una"}'
                 f' sesión con {nombre_psi}')
        if c.es_tectum:
            detalle = 'TECTUM In-House'
        elif c.es_beneficios:
            detalle = 'TECTUM Beneficios'
        else:
            detalle = TIPO_SESION_LABEL.get(c.tipo_sesion, 'Individual')
        eventos.append({'tipo': 'cita', 'ts': _iso(c.fecha_creacion),
                        'texto': texto, 'detalle': detalle})

    eventos.sort(key=lambda e: e['ts'], reverse=True)
    return {'eventos': eventos[:limite], 'servidor': _ahora().isoformat()}


# --------------------------------------------------------------------------
# 8. CITAS FUTURAS (nuevos pacientes programados)
# --------------------------------------------------------------------------

def _proximas_citas_base(psicologo_id=None):
    hoy = _hoy()
    qs = (Cita.objects
          .filter(fecha__gte=hoy)
          .select_related('paciente', 'paciente__perfil', 'psicologo__usuario')
          .order_by('fecha', 'hora'))
    if psicologo_id:
        qs = qs.filter(psicologo_id=int(psicologo_id))
    return list(qs)


def Proximas_Citas(psicologo_id=None, desde_pagina=1, per_page=25, lista=None):
    """
    Citas futuras ordenadas por fecha/hora. Filas: paciente, psicólogo,
    fecha, hora, tipo, modalidad y bandera de "primera sesión" (comparación
    contra la primera cita global del paciente; el estado no se usa).
    """
    hoy = _hoy()
    citas = lista if lista is not None else _proximas_citas_base(psicologo_id)

    # primera cita global por paciente (1 consulta liviana)
    primeras = {
        row['paciente_id']: {'primera': row['primera'], 'total': row['total']}
        for row in Cita.objects.values('paciente_id')
        .annotate(primera=Min('fecha'), total=Count('id'))
    }

    total = len(citas)
    ini = (max(1, desde_pagina) - 1) * per_page
    fin = ini + per_page
    filas = []
    for c in citas[ini:fin]:
        pac = c.paciente
        perfil_pac = getattr(pac, 'perfil', None)
        nombre_p = ((perfil_pac and perfil_pac.nombre) or pac.first_name or pac.username)
        psi = ''
        if c.psicologo and c.psicologo.usuario:
            n = c.psicologo.usuario.first_name or c.psicologo.usuario.username
            titulo = 'Dra.' if c.psicologo.genero == 'Mujer' else 'Dr.'
            psi = f'{titulo} {n}'
        info = primeras.get(pac.id)
        es_primera = bool(info and info['primera'] and
                          info['primera'] >= hoy and info['total'] == 1)
        filas.append({
            'paciente': nombre_p,
            'psicologo': psi or 'Por asignar',
            'fecha': c.fecha.isoformat(),
            'hora': c.hora.strftime('%I:%M %p') if c.hora else '',
            'tipo': TIPO_SESION_LABEL.get(c.tipo_sesion, 'Individual'),
            'modalidad': c.modalidad or 'En línea',
            'es_primera': es_primera,
            'es_tectum': c.es_tectum,
            'es_beneficios': c.es_beneficios,
        })
    return {
        'total': total,
        'filas': filas[:per_page],
        'pages': max(1, (total + per_page - 1) // per_page),
        'page': max(1, desde_pagina),
    }


# --------------------------------------------------------------------------
# 3-6. LISTADOS DE PACIENTES CON FILTROS Y PAGINADO
# --------------------------------------------------------------------------

def Listado_Pacientes(vista='nuevos', page=1, per_page=25,
                      psicologo_id=None, preferencia=None, estado=None,
                      primera_cita=None, q=None):
    """
    Vistas: nuevos / activos / atencion.
    Filtros: psicologo_id (asignado), preferencia (Mujer/Hombre/Indistinto),
    estado del paciente, primera_cita ('si' ya tomada / 'no' futura / 'none'
    nunca agendó), q (texto por nombre o email).
    En 'atencion' cada fila incluye el motivo real de la alerta.
    """
    hoy = _hoy()
    pacientes, _, _ = _coleccion_pacientes()

    for p in pacientes:
        p['estado'] = _estado_paciente(p, hoy)
        p['estado_label'] = ESTADO_LABEL[p['estado']]

    if q:
        ql = q.strip().lower()
        pacientes = [p for p in pacientes
                     if ql in (p['nombre'] or '').lower()
                     or ql in (p['email'] or '').lower()]
    if psicologo_id:
        pacientes = [p for p in pacientes
                     if p['psicologo'] and p['psicologo']['id'] == int(psicologo_id)]
    if preferencia:
        pacientes = [p for p in pacientes
                     if p['preferencia'].upper() == str(preferencia).upper()]
    if estado:
        pacientes = [p for p in pacientes if p['estado'] == str(estado).strip().lower()]
    if primera_cita == 'si':
        pacientes = [p for p in pacientes
                     if p['primera'] and a_fecha(p['primera']) <= hoy]
    elif primera_cita == 'no':
        pacientes = [p for p in pacientes
                     if p['proxima'] and not p['ultima_pasada']]
    elif primera_cita == 'none':
        pacientes = [p for p in pacientes if not p['citas_total']]

    if vista == 'nuevos':
        pacientes.sort(key=lambda p: p['date_joined'] or _fecha_sentinela(), reverse=True)
    elif vista == 'atencion':
        pacientes = [p for p in pacientes if p['estado'] in ('nunca', 'en_riesgo', 'inactivo')]

        def _peso(p):
            if p['estado'] == 'nunca':
                return (0, 99999)
            u = a_fecha(p['ultima_pasada'])
            dias = (hoy - u).days if u else 10000
            return (dias, -(p['citas_total'] or 0))
        pacientes.sort(key=_peso, reverse=True)
    else:  # activos
        pacientes = [p for p in pacientes if p['estado'] == 'activo']
        pacientes.sort(key=lambda p: p['proxima'] or '9999-12-31')

    total = len(pacientes)
    pages = max(1, (total + per_page - 1) // per_page)
    page = max(1, min(page, pages))
    ini = (page - 1) * per_page
    slice_ = pacientes[ini:ini + per_page]

    filas = []
    for p in slice_:
        fila = {
            'id': p['id'],
            'nombre': p['nombre'],
            'email': p['email'],
            'fecha_registro': p['fecha_registro'],
            'preferencia': p['preferencia'],
            'psicologo': p['psicologo']['nombre'] if p['psicologo'] else None,
            'psicologo_id': p['psicologo']['id'] if p['psicologo'] else None,
            'primera': p['primera'],
            'proxima': p['proxima'],
            'ultima': p['ultima_pasada'],
            'citas_total': p['citas_total'],
            'estado': p['estado'],
            'estado_label': p['estado_label'],
            'es_tectum': p['es_tectum'],
            'es_beneficios': p['es_beneficios'],
        }
        if vista == 'atencion':
            motivos = []
            if p['estado'] == 'nunca':
                motivos.append('Nunca agendó después del registro')
            else:
                u = a_fecha(p['ultima_pasada'])
                if u:
                    dias = (hoy - u).days
                    if dias > 30:
                        motivos.append(f'{dias} días sin sesión')
                    elif dias > 21:
                        motivos.append(f'{dias} días sin sesión (zona de alerta)')
                if not p['proxima']:
                    motivos.append('Sin próxima cita programada')
                if (p['canceladas'] or 0) >= 2:
                    motivos.append(f'{p["canceladas"]} cancelaciones históricas')
            fila['motivo'] = ' · '.join(motivos) if motivos else 'Sin próximo paso'
        filas.append(fila)

    resultado = {
        'vista': vista,
        'page': page,
        'per_page': per_page,
        'total': total,
        'pages': pages,
        'filas': filas,
        'conteo_estados': conteo_estados([{k: p[k] for k in ('estado',)} for p in pacientes]),
    }
    return resultado


# --------------------------------------------------------------------------
# 7. RANKING DE PSICOLOGOS
# --------------------------------------------------------------------------

def Ranking_Psicologos():
    """
    Ranking real: una consulta agregada por (psicologo, paciente) + datos de
    horario con una segunda consulta liviana (fecha, hora, psicologo).
    """
    hoy = _hoy()
    binomios = {
        (row['psicologo_id'], row['paciente_id']): row
        for row in (
            Cita.objects
            .filter(psicologo__isnull=False)
            .values('psicologo_id', 'paciente_id')
            .annotate(
                total=Count('id'),
                ultima=Max('fecha', filter=Q(fecha__lt=hoy)),
                proxima=Min('fecha', filter=Q(fecha__gte=hoy)),
            )
            .order_by()
        )
    }
    vacio = lambda: {'pacientes': 0, 'con_2': 0, 'proximas_bino': 0, 'citas': 0,
                     'en_riesgo': 0, 'inactivos': 0, 'activos': 0}
    por_psicologo = defaultdict(vacio)

    for (pid, pac_id), row in binomios.items():
        d = por_psicologo[pid]
        d['pacientes'] += 1
        d['citas'] += row['total']
        if row['total'] >= 2:
            d['con_2'] += 1
        if row['proxima']:
            d['proximas_bino'] += 1
        estado = _estado_paciente({
            'citas_total': row['total'],
            'proxima': row['proxima'],
            'ultima_pasada': row['ultima'],
        }, hoy)
        if estado == 'activo':
            d['activos'] += 1
        elif estado == 'en_riesgo':
            d['en_riesgo'] += 1
        elif estado == 'inactivo':
            d['inactivos'] += 1

    horas_por_psi = defaultdict(lambda: defaultdict(int))
    dias_por_psi = defaultdict(lambda: defaultdict(int))
    for f, h, pid in Cita.objects.exclude(psicologo=None).values_list('fecha', 'hora', 'psicologo_id'):
        if h is not None:
            horas_por_psi[pid][h.hour] += 1
        dias_por_psi[pid][f.weekday()] += 1

    ranking = []
    perfiles = PerfilPsicologo.objects.select_related('usuario').order_by('id')
    for p in perfiles:
        d = por_psicologo.get(p.id)
        nombre = (p.usuario.first_name if p.usuario and p.usuario.first_name
                  else (p.usuario.username if p.usuario else 'Sin nombre'))
        titulo = 'Dra. ' if p.genero == 'Mujer' else 'Dr. '
        pacientes_totales = d['pacientes'] if d else 0
        ret = round(100.0 * d['con_2'] / d['pacientes'], 1) if d and d['pacientes'] else 0
        prom = round(1.0 * d['citas'] / d['pacientes'], 2) if d and d['pacientes'] else 0
        horas = horas_por_psi.get(p.id) or {}
        dias = dias_por_psi.get(p.id) or {}
        if horas:
            hora_top = max(horas, key=lambda k: horas[k])
            horario_txt = _formato_hora_12(hora_top)
        else:
            horario_txt = None
        ranking.append({
            'id': p.id,
            'nombre': f'{titulo}{nombre}'.strip(),
            'esta_activo': p.esta_activo,
            'pacientes_totales': pacientes_totales,
            'pacientes_activos': d['activos'] if d else 0,
            'pacientes_en_riesgo': d['en_riesgo'] if d else 0,
            'pacientes_inactivos': d['inactivos'] if d else 0,
            'citas_realizadas': d['citas'] if d else 0,
            'proximas_citas': d['proximas_bino'] if d else 0,
            'retencion_pct': ret,
            'promedio_sesiones': prom,
            'horario_mas_solicitado': horario_txt,
            'dia_mas_solicitado': DIA_LABEL[max(dias, key=lambda k: dias[k])] if dias else None,
        })
    ranking.sort(key=lambda r: (-r['pacientes_totales'], -r['retencion_pct']))
    for i, r in enumerate(ranking, 1):
        r['posicion'] = i
    return ranking


# --------------------------------------------------------------------------
# 9. CURVA DE HORARIOS
# --------------------------------------------------------------------------

def Heatmap_Horarios(psicologo_id=None, desde=None, hasta=None):
    """
    Curva de demanda: una consulta liviana (fecha, hora, tipo) y una pasada
    en memoria. Devuelve heatmap 7 x horas, demanda por franja, por día, por
    hora y por tipo de sesión.
    """
    qs = Cita.objects.all()
    if psicologo_id:
        qs = qs.filter(psicologo_id=int(psicologo_id))
    if desde:
        qs = qs.filter(fecha__gte=desde)
    if hasta:
        qs = qs.filter(fecha__lte=hasta)

    grid = defaultdict(int)
    franja = defaultdict(int)
    por_dia = defaultdict(int)
    por_hora = defaultdict(int)
    por_tipo = defaultdict(int)
    horas_limpias = set()
    total = 0

    for f, h, tipo in qs.values_list('fecha', 'hora', 'tipo_sesion'):
        h_val = h.hour if h is not None else None
        if h_val is None:
            continue
        weekday = f.weekday()
        grid[(weekday, h_val)] += 1
        por_hora[h_val] += 1
        por_dia[weekday] += 1
        por_tipo[tipo] += 1
        horas_limpias.add(h_val)
        total += 1
        for nombre, ini, fin in FRANJA_HORARIA:
            if ini <= h_val < fin:
                franja[nombre] += 1
                break
        else:
            franja['Otras'] += 1

    horas_orden = sorted(horas_limpias)
    heatmap_filas = [
        {
            'dia': DIA_LABEL[w],
            'valores': [{'hora': h, 'citas': grid.get((w, h), 0)} for h in horas_orden],
        }
        for w in range(7)
    ]
    return {
        'total': total,
        'horas': horas_orden,
        'heatmap': heatmap_filas,
        'por_hora': [{'hora': _formato_hora_12(h), 'citas': por_hora[h]} for h in horas_orden],
        'por_dia': [{'dia': DIA_LABEL[w], 'citas': por_dia[w]} for w in range(7)],
        'franja': {k: franja[k] for k in ['Mañana', 'Tarde', 'Noche', 'Otras']},
        'por_tipo': {k: v for k, v in sorted(por_tipo.items(), key=lambda x: -x[1])},
        'pico_hora': max(por_hora, key=lambda k: por_hora[k]) if por_hora else None,
        'pico_dia': max(por_dia, key=lambda k: por_dia[k]) if por_dia else None,
    }


# --------------------------------------------------------------------------
# 10-11-14. EMBUDO, COMPORTAMIENTO E INSIGHTS
# --------------------------------------------------------------------------

def _pasada_citas_por_paciente():
    """
    Una pasada por (paciente, fecha, hora): primera cita del paciente y gaps
    reales entre sesiones. Fuente del 'tiempo entre sesiones' y del
    'registro -> primera cita'.
    """
    filas = list(Cita.objects
                 .order_by('paciente_id', 'fecha', 'hora')
                 .values_list('paciente_id', 'fecha', 'hora'))
    primera = {}
    por_paciente = defaultdict(set)
    for pid, f, h in filas:
        primera.setdefault(pid, {'fecha': f, 'hora': h})
        por_paciente[pid].add(f)

    gaps = []
    for fechas in por_paciente.values():
        orden = sorted(fechas)
        for a, b in zip(orden, orden[1:]):
            dias = (b - a).days
            if dias >= 0:
                gaps.append(dias)
    return primera, gaps


def Embudo_y_Comportamiento():
    hoy = _hoy()
    pacientes, _, _ = _coleccion_pacientes()
    total = len(pacientes)

    perfil_completo = sum(1 for p in pacientes if p['perfil_campos'] >= 6)
    agendaron = sum(1 for p in pacientes if p['citas_total'] >= 1)
    primera_tomada = sum(1 for p in pacientes
                         if p['primera'] and a_fecha(p['primera']) <= hoy)
    recurrentes = sum(1 for p in pacientes if p['citas_total'] >= 2)
    activos_hoy = sum(1 for p in pacientes if _estado_paciente(p, hoy) == 'activo')

    embudo = [
        {'etapa': 'Registrados', 'valor': total},
        {'etapa': 'Cuestionario completo', 'valor': perfil_completo},
        {'etapa': 'Agendaron cita', 'valor': agendaron},
        {'etapa': 'Primera sesión tomada', 'valor': primera_tomada},
        {'etapa': 'Continuaron (2+ sesiones)', 'valor': recurrentes},
        {'etapa': 'Activos hoy', 'valor': activos_hoy},
    ]
    caidas = []
    for a, b in zip(embudo, embudo[1:]):
        caidas.append({
            'de': a['etapa'], 'a': b['etapa'],
            'perdidos': a['valor'] - b['valor'],
            'pct': round(100.0 * (a['valor'] - b['valor']) / a['valor'], 1) if a['valor'] else 0,
        })
    mayor_caida = max(caidas, key=lambda c: c['perdidos']) if caidas else None

    primera_map, gaps = _pasada_citas_por_paciente()
    usuarios_fechas = {row['id']: row['date_joined'] for row in
                       User.objects.filter(id__in=list(primera_map.keys()))
                       .values('id', 'date_joined')}
    dias_al_primero = []
    for pid, fila in primera_map.items():
        dj = usuarios_fechas.get(pid)
        if dj:
            dias = (fila['fecha'] - timezone.localdate(dj)).days
            if dias >= 0:
                dias_al_primero.append(dias)

    def _promedio(lista):
        return round(1.0 * sum(lista) / len(lista), 1) if lista else None

    def _pct_menores(lista, dias):
        return round(100.0 * sum(1 for d in lista if d < dias) / len(lista), 1) if lista else 0

    gaps_ord = sorted(gaps)

    primeras_por_hora = defaultdict(int)
    for fila in primera_map.values():
        if fila['hora']:
            primeras_por_hora[fila['hora'].hour] += 1
    hora_conv = max(primeras_por_hora, key=lambda k: primeras_por_hora[k]) if primeras_por_hora else None

    quedan90 = _hace_dias(90)
    prefs_recientes = {'Mujer': 0, 'Hombre': 0, 'Indistinto': 0, 'Sin registro': 0}
    for p in pacientes:
        js = p['fecha_registro'] or ''
        if str(js)[:10] >= str(quedan90) and p['preferencia'] in prefs_recientes:
            prefs_recientes[p['preferencia']] += 1

    comportamiento = {
        'registro_a_primera': {
            'promedio_dias': _promedio(dias_al_primero),
            'bucket_1d_pct': _pct_menores(dias_al_primero, 1),
            'bucket_3d_pct': _pct_menores(dias_al_primero, 3),
            'bucket_7d_pct': _pct_menores(dias_al_primero, 7),
            'muestras': len(dias_al_primero),
        },
        'gap_entre_sesiones': {
            'promedio_dias': _promedio(gaps_ord),
            'mediana_dias': gaps_ord[len(gaps_ord) // 2] if gaps_ord else None,
            'muestras': len(gaps_ord),
        },
        'pacientes_5mas_sesiones': sum(1 for p in pacientes if (p['citas_total'] or 0) >= 5),
        'hora_primera_cita_top': _formato_hora_12(hora_conv) if hora_conv is not None else None,
        'prefs_recientes_90d': prefs_recientes,
    }
    return {'embudo': embudo, 'caidas': caidas, 'mayor_caida': mayor_caida,
            'comportamiento': comportamiento}


def Insights():
    """
    Hallazgos automáticos, deterministas (sin IA). Cada hallazgo lleva el
    dato real que lo respalda. Se basa en las demás funciones del motor.
    """
    kpis = KPIs()
    ranking = Ranking_Psicologos()
    heatmap = Heatmap_Horarios()
    embudo = Embudo_y_Comportamiento()

    hallazgos = []
    delta_reg = kpis['delta'].get('registros_30d')
    if delta_reg is not None and delta_reg >= 0:
        hallazgos.append({
            'tono': 'positivo',
            'texto': (f'Los registros crecieron {delta_reg}% en los últimos 30 días '
                      f"({kpis['delta']['registros_30d_actual']} vs "
                      f"{kpis['delta']['registros_30d_previo']}).")})
    elif delta_reg is not None:
        hallazgos.append({
            'tono': 'alerta',
            'texto': (f'Los registros cayeron {abs(delta_reg)}% en los últimos 30 días '
                      f"({kpis['delta']['registros_30d_actual']} vs "
                      f"{kpis['delta']['registros_30d_previo']}).")})

    sin_proxima = kpis['pacientes']['total'] - kpis['pacientes']['activos']
    if sin_proxima > 0:
        hallazgos.append({'tono': 'alerta',
                          'texto': f'{sin_proxima} pacientes no tienen cita próxima programada.'})
    else:
        hallazgos.append({'tono': 'positivo',
                          'texto': 'Todos los pacientes registrados tienen cita próxima o sesión reciente.'})

    if heatmap['pico_hora'] is not None:
        slot = heatmap['por_hora'][0]
        for r in heatmap['por_hora']:
            if heatmap['pico_hora'] == _hora_de_etiqueta(r['hora']):
                slot = r
                break
        hallazgos.append({'tono': 'neutro',
                          'texto': (f'La hora con más demanda es {slot["hora"]} '
                                    f'({slot["citas"]} citas históricas).')})
    if heatmap['pico_dia'] is not None:
        dia_mas = next(d for d in heatmap['por_dia'] if DIA_LABEL[heatmap['pico_dia']] == d['dia'])
        hallazgos.append({'tono': 'neutro',
                          'texto': f'El día más demandado es {dia_mas["dia"]} ({dia_mas["citas"]} citas).'})

    candidatos = [r for r in ranking if r['pacientes_totales'] >= 3]
    if candidatos:
        mejor = max(candidatos, key=lambda r: r['retencion_pct'])
        hallazgos.append({'tono': 'positivo',
                          'texto': (f'{mejor["nombre"]} tiene la mejor tasa de continuidad '
                                    f'({mejor["retencion_pct"]}% de sus pacientes regresa).')})

    prefs = embudo['comportamiento']
    prefe = prefs['prefs_recientes_90d']
    n_prefs = prefe['Mujer'] + prefe['Hombre'] + prefe['Indistinto']
    if n_prefs:
        pc = round(100.0 * prefe['Mujer'] / n_prefs, 1)
        hallazgos.append({'tono': 'neutro',
                          'texto': (f'El {pc}% de los pacientes que eligieron preferencia '
                                    f'se inclinó por terapeuta mujer (últimos 90 días).')})

    b3 = embudo['comportamiento']['registro_a_primera']['bucket_3d_pct']
    if embudo['comportamiento']['registro_a_primera']['promedio_dias'] is not None:
        hallazgos.append({'tono': 'positivo',
                          'texto': f'El {b3}% de los pacientes agendó su primera cita en menos de 3 días desde su registro.'})

    mc = embudo.get('mayor_caida')
    if mc and mc['pct'] is not None:
        hallazgos.append({'tono': 'alerta',
                          'texto': (f'La mayor fuga del embudo está entre "{mc["de"]}" y "{mc["a"]}" '
                                    f'({mc["perdidos"]} pacientes, {mc["pct"]}%).')})

    franja_top = max(heatmap['franja'].items(), key=lambda kv: kv[1]) if heatmap['total'] else None
    if franja_top and franja_top[1]:
        hallazgos.append({'tono': 'neutro',
                          'texto': f'La franja con más demanda es {franja_top[0].lower()} '
                                   f'({franja_top[1]} citas, {round(100.0*franja_top[1]/heatmap["total"])}% del total).'})

    return {'hallazgos': hallazgos}


def _hora_de_etiqueta(etiqueta):
    """'6:00 PM' -> 18 ; usado para comparar pico con por_hora."""
    try:
        cuerpo, ampm = etiqueta.rsplit(' ', 1)
        hh = int(cuerpo.split(':')[0])
        if ampm.upper() == 'PM' and hh != 12:
            hh += 12
        if ampm.upper() == 'AM' and hh == 12:
            hh = 0
        return hh
    except Exception:
        return None


# --------------------------------------------------------------------------
# Evolución mensual (registros y citas)
# --------------------------------------------------------------------------

def Evolucion_Mensual(meses=6):
    """Serie mensual de registros (date_joined) y citas (fecha). 2 consultas."""
    from django.db.models.functions import TruncMonth
    hoy = _hoy()
    ini = _fecha_menos_meses(hoy.replace(day=1), max(0, meses - 1))

    registros = (UsuarioPerfil.objects
                 .filter(es_psicologo=False, usuario__date_joined__gte=_aware_medianoche(ini))
                 .annotate(m=TruncMonth('usuario__date_joined'))
                 .values('m').annotate(n=Count('id')).order_by('m'))
    citas = (Cita.objects.filter(fecha__gte=ini)
             .annotate(m=TruncMonth('fecha'))
             .values('m').annotate(n=Count('id')).order_by('m'))

    meses_idx = {}
    cur = ini
    i = 0
    while cur <= hoy.replace(day=1):
        meses_idx[(cur.year, cur.month)] = i
        cur = _fecha_mas_un_mes(cur)
        i += 1
    labels = [f'{_MES_AB[m-1]} {y}' for (y, m) in sorted(meses_idx)]

    def _serie(qs):
        out = [0] * len(labels)
        for row in qs:
            m = row['m']
            idx = meses_idx.get((m.year, m.month))
            if idx is not None:
                out[idx] = row['n']
        return out

    return {'labels': labels, 'registros': _serie(registros), 'citas': _serie(citas)}

_MES_AB = ['Ene', 'Feb', 'Mar', 'Abr', 'May', 'Jun', 'Jul', 'Ago', 'Sep', 'Oct', 'Nov', 'Dic']


def _fecha_menos_meses(fecha, n):
    y, m = fecha.year, fecha.month - n
    while m <= 0:
        m += 12
        y -= 1
    return date(y, m, 1)


def _fecha_mas_un_mes(fecha):
    y, m = (fecha.year + 1) if fecha.month == 12 else fecha.year, (1 if fecha.month == 12 else fecha.month + 1)
    return date(y, m, 1)


# --------------------------------------------------------------------------
# EXPORTACION (CSV / Excel utf-8-sig)
# --------------------------------------------------------------------------

def Filas_Exportacion(vista, psicologo_id=None, desde=None, hasta=None):
    """
    Devuelve (encabezados, filas) para CSV/Excel. Vistas reales y sin datos
    simulados. Límite prudente de 50,000 filas.
    """
    LIMITE = 50000

    if vista == 'psicologos':
        headers = ['Posicion', 'Psicologo', 'Pacientes totales', 'Activos', 'En riesgo',
                   'Inactivos', 'Citas realizadas', 'Proximas citas', 'Retencion %',
                   'Promedio sesiones', 'Horario mas solicitado', 'Dia mas solicitado']
        rows = [
            [r['posicion'], r['nombre'], r['pacientes_totales'], r['pacientes_activos'],
             r['pacientes_en_riesgo'], r['pacientes_inactivos'], r['citas_realizadas'],
             r['proximas_citas'], r['retencion_pct'], r['promedio_sesiones'],
             r['horario_mas_solicitado'] or '', r['dia_mas_solicitado'] or '']
            for r in Ranking_Psicologos()[:LIMITE]
        ]
        return headers, rows

    if vista == 'programados':
        citas = _proximas_citas_base(psicologo_id)[:LIMITE]
        data = Proximas_Citas(lista=citas, desde_pagina=1, per_page=LIMITE)
        headers = ['Paciente', 'Psicologo', 'Fecha', 'Hora', 'Tipo sesion', 'Modalidad', 'Primera sesion']
        rows = [[f['paciente'], f['psicologo'], f['fecha'], f['hora'], f['tipo'],
                 f['modalidad'], 'Si' if f['es_primera'] else 'No'] for f in data['filas']]
        return headers, rows

    if vista == 'horarios':
        data = Heatmap_Horarios(psicologo_id=psicologo_id, desde=desde, hasta=hasta)
        franja_por_hora = {}
        for h in data['horas']:
            for nombre, ini, fin in FRANJA_HORARIA:
                if ini <= h < fin:
                    franja_por_hora[h] = nombre
                    break
            else:
                franja_por_hora[h] = 'Otras'
        headers = ['Hora', 'Franja'] + [d['dia'] for d in data['heatmap']]
        rows = []
        for h in data['horas']:
            row = [f'{h}:00', franja_por_hora[h]]
            for dia_data in data['heatmap']:
                val = next((v['citas'] for v in dia_data['valores'] if v['hora'] == h), 0)
                row.append(val)
            rows.append(row)
        return headers, rows

    # Listados de pacientes: nuevos / activos / atencion
    data = Listado_Pacientes(vista=vista, page=1, per_page=LIMITE,
                             psicologo_id=psicologo_id)
    headers = ['ID', 'Nombre', 'Email', 'Fecha registro', 'Preferencia', 'Psicologo asignado',
               'Primera cita', 'Ultima sesion', 'Proxima cita', 'Citas totales', 'Estado']
    if vista == 'atencion':
        headers.append('Motivo')
    rows = []
    for f in data['filas']:
        row = [f['id'], f['nombre'], f['email'], f['fecha_registro'], f['preferencia'],
               f['psicologo'] or 'Sin asignar', f['primera'] or '', f['ultima'] or '',
               f['proxima'] or '', f['citas_total'], f['estado_label']]
        if vista == 'atencion':
            row.append(f.get('motivo', ''))
        rows.append(row)
    return headers, rows


def Filas_Exportacion_KPIs():
    kpis = KPIs()
    headers = ['Concepto', 'Valor']
    rows = [
        ['Pacientes registrados', kpis['pacientes']['total']],
        ['Pacientes activos', kpis['pacientes']['activos']],
        ['Pacientes en riesgo', kpis['pacientes']['en_riesgo']],
        ['Pacientes inactivos', kpis['pacientes']['inactivos']],
        ['Pacientes que nunca agendaron', kpis['pacientes']['nunca_agendaron']],
        ['Nuevos pacientes esta semana', kpis['pacientes']['nuevos_semana']],
        ['Nuevos pacientes este mes', kpis['pacientes']['nuevos_mes']],
        ['Tasa de retencion %', kpis['pacientes']['retencion_pct']],
        ['Psicologos activos', kpis['psicologos']['activos']],
        ['Psicologos totales', kpis['psicologos']['total']],
        ['Citas hoy', kpis['citas']['hoy']],
        ['Citas esta semana', kpis['citas']['semana']],
        ['Citas este mes', kpis['citas']['mes']],
        ['Citas totales', kpis['citas']['total']],
        ['Ingresos estimados hoy', kpis['ingresos']['hoy']['total']],
        ['Ingresos estimados este mes', kpis['ingresos']['mes']['total']],
    ]
    return headers, rows


# =========================================================================
# DISTRIBUCION DE PACIENTES Y SIMULADOR DE REASIGNACION (30/10/2026)
# Herramienta de ANALISIS/SIMULACION para Dirección:
# - NINGUNA funcion de esta seccion escribe en la base de datos ni
#   modifica asignaciones reales (TratamientoPaciente/UsuarioPerfil quedan
#   intactos; el efecto de un movimiento se calcula en memoria).
# - Compatibilidad paciente-psicologo evaluada con datos reales:
#   preferencia de genero del cuestionario, capacidad por tipo de sesion,
#   esquema de horario vigente y solapamiento de franjas de disponibilidad.
# =========================================================================

from .models import Cita, EventoAuditoria, EsquemaHorarioPsicologo, TratamientoPaciente

FRANJAS_TODAS = ['Mañana', 'Tarde', 'Noche', 'Fin de semana']

# Burbujas visuales del tablero (color = estado real del paciente):
#   verde  = Activo (con sesiones)
#   amarilla = En riesgo
#   roja   = Inactivo
#   azul   = Nuevo paciente (se registro, cero citas)
#   morada = Primera cita pendiente (sin sesion, cita futura agendada)
BURBUJA_COLORS = ['verde', 'amarilla', 'roja', 'azul', 'morada']

BURBUJA_LABEL = {
    'verde': 'Activo',
    'amarilla': 'En riesgo',
    'roja': 'Inactivo',
    'azul': 'Nuevo paciente',
    'morada': 'Primera cita pendiente',
}


def _burbuja_de(pac):
    """Clasificador de burbuja por estado real (morada gana sobre azul)."""
    if pac.get('nuevo') and pac.get('proxima'):
        return 'morada'
    if pac['estado'] == 'activo':
        return 'verde'
    if pac['estado'] == 'en_riesgo':
        return 'amarilla'
    if pac['estado'] == 'inactivo':
        return 'roja'
    return 'azul'


def _minutos(t):
    return t.hour * 60 + t.minute


def _psi_info():
    """
    Información horaria real por psicólogo (2 consultas livianas):
      - esquema_ok: tiene al menos un EsquemaHorarioPsicologo activo que
        cubre hoy.
      - ventanas: [(dia_semana 0-6, inicio_min, fin_min, tipo_sesion)]
        construidas con la MISMA regla del algoritmo de slots oficial:
        dia con bloques personalizados ignora la jornada base; comida
        dividida en dos ventanas cuando cabe dentro de la jornada.
      - horas: capacidad semanal en horas de las ventanas vigentes.
    """
    hoy = _hoy()
    por_psi = {}
    esquemas = (EsquemaHorarioPsicologo.objects.filter(activo=True)
                .prefetch_related('dias_personalizados'))
    for es in esquemas:
        pid = es.psicologo_id
        if not (es.fecha_inicio <= hoy <= es.fecha_fin):
            continue
        d = por_psi.setdefault(pid, {'esquema_ok': False, 'ventanas': []})
        d['esquema_ok'] = True
        pers = {}
        for drow in es.dias_personalizados.all():
            pers.setdefault(drow.dia_semana, []).append(
                (drow.hora_inicio, drow.hora_fin, drow.tipo_sesion))
        descansos = es.dias_descanso or []
        for dia in range(7):
            if dia in descansos:
                continue
            if dia in pers:
                for hi, hf, tipo in pers[dia]:
                    d['ventanas'].append((dia, _minutos(hi), _minutos(hf), tipo))
            else:
                ini, fin = _minutos(es.hora_inicio), _minutos(es.hora_fin)
                if es.hora_comida_inicio and es.hora_comida_fin:
                    ci = _minutos(es.hora_comida_inicio)
                    cf = _minutos(es.hora_comida_fin)
                    if ini < ci < cf < fin:
                        d['ventanas'].append((dia, ini, ci, None))
                        d['ventanas'].append((dia, cf, fin, None))
                        continue
                d['ventanas'].append((dia, ini, fin, None))
    for d in por_psi.values():
        d['horas'] = round(sum(max(0, v[2] - v[1]) / 60.0 for v in d['ventanas']), 1)
        franjas = set()
        for dia, ini, fin, _tipo in d['ventanas']:
            for nombre, fi, ff in FRANJA_HORARIA:
                # Solapamiento de la ventana con la franja (franjas en horas)
                if ini < ff * 60 and fin > fi * 60:
                    franjas.add(nombre)
            if dia >= 5:
                franjas.add('Fin de semana')
        d['franjas'] = sorted(franjas, key=FRANJAS_TODAS.index)
    return por_psi


def _dataset_distribucion():
    """
    Dataset compartido del módulo: pacientes enriquecidos con estado
    ("Activo"/"En riesgo"/...), indicador 'nuevo' (aún no toma su primera
    sesión), tipos de servicio de sus tratamientos, franjas declaradas y su
    psicólogo principal (fuente real: TratamientoPaciente activo; fallback
    UsuarioPerfil.psicologo_asignado). Solo lectura.
    """
    hoy = _hoy()
    pacientes, citas_agg, prefs = _coleccion_pacientes()
    trat_por_pac = {}
    for row in (TratamientoPaciente.objects
                .values('paciente_id', 'psicologo_asignado_id', 'tipo_servicio', 'activo')):
        if row['psicologo_asignado_id']:
            trat_por_pac.setdefault(row['paciente_id'], []).append(row)

    # Primera cita futura de cada paciente (para la burbuja morada y para
    # poder mostrar/confirmar la cita que se actualizaría en una reasignación).
    primera_cita = {}
    futuras_n = defaultdict(int)
    for row in (Cita.objects.filter(fecha__gte=hoy)
                .order_by('paciente_id', 'fecha', 'hora')
                .values('paciente_id', 'id', 'fecha', 'hora', 'estado', 'psicologo_id')):
        uid = row['paciente_id']
        futuras_n[uid] += 1
        if uid not in primera_cita:
            primera_cita[uid] = {
                'cita_id': row['id'],
                'fecha': row['fecha'].isoformat(),
                'hora': row['hora'].isoformat() if row['hora'] else '',
                'estado': row['estado'],
                'psicologo_id': row['psicologo_id'],
            }

    for pac in pacientes:
        uid = pac['user_id']
        pac['estado'] = _estado_paciente(pac, hoy)
        pac['estado_label'] = ESTADO_LABEL[pac['estado']]
        pac['nuevo'] = not pac['ultima_pasada']
        rows = trat_por_pac.get(uid) or []
        activos = [r for r in rows if r['activo']]
        pac['tipos'] = sorted({r['tipo_servicio'] for r in (activos or rows)}) or ['individual']
        if activos:
            pac['psicologo_principal'] = activos[0]['psicologo_asignado_id']
        elif pac['psicologo']:
            pac['psicologo_principal'] = pac['psicologo']['id']
        else:
            pac['psicologo_principal'] = None
        pac['franjas'] = (prefs.get(uid) or {}).get('horarios') or []
        pac['prefs'] = prefs.get(uid) or {}
        pac['burbuja'] = _burbuja_de(pac)
        pac['cita_proxima'] = primera_cita.get(uid)
        pac['citas_futuras'] = futuras_n.get(uid, 0)
    return pacientes


def _chip_paciente(pac):
    """DTO del paciente para las burbujas del simulador."""
    return {
        'pid': pac['id'],
        'user_id': pac['user_id'],
        'nombre': pac['nombre'],
        'preferencia': pac['preferencia'],
        'franjas': pac['franjas'],
        'estado': pac['estado'],
        'estado_label': pac['estado_label'],
        'burbuja': pac['burbuja'],
        'burbuja_label': BURBUJA_LABEL[pac['burbuja']],
        'cita_proxima': pac['cita_proxima'],
        'citas_futuras': pac['citas_futuras'],
        'nuevo': pac['nuevo'],
        'citas_total': pac['citas_total'],
        'tipos': pac['tipos'],
        'es_tectum': pac['es_tectum'],
        'es_beneficios': pac['es_beneficios'],
        'psicologo_perfil': pac['psicologo']['id'] if pac['psicologo'] else None,
    }


def Distribucion():
    """
    Foto real de la distribución de pacientes entre psicólogos:
      - pacientes asignados por psicólogo (fuente TratamientoPaciente
        activo, fallback UsuarioPerfil.psicologo_asignado).
      - desglose activos / nuevos (sin primera sesión) / en riesgo /
        inactivos; próximas citas reales de los próximos 7 días.
      - franjas y capacidad semanal de su esquema vigente.
      - bucket 'sin_asignar' para pacientes sin psicólogo real.
      - media de activos entre psicólogos activos (referencia de carga).
    """
    pacientes = _dataset_distribucion()
    psi_infos = _psi_info()
    prox = {r['psicologo_id']: r['n'] for r in (
        Cita.objects.filter(psicologo__isnull=False, fecha__gte=_hoy())
        .values('psicologo_id').annotate(n=Count('id')))}

    vacio = lambda: {'total': 0, 'activos': 0, 'nuevos': 0, 'en_riesgo': 0,
                     'inactivos': 0, 'sin_proxima': 0, 'chips': []}
    por_psi = defaultdict(vacio)
    sin_asignar = []

    for pac in pacientes:
        chip = _chip_paciente(pac)
        destino = pac['psicologo_principal']
        if not destino:
            sin_asignar.append(chip)
            continue
        d = por_psi[destino]
        d['total'] += 1
        d['chips'].append(chip)
        if pac['estado'] == 'activo':
            d['activos'] += 1
        elif pac['estado'] == 'en_riesgo':
            d['en_riesgo'] += 1
        elif pac['estado'] == 'inactivo':
            d['inactivos'] += 1
        if pac['nuevo']:
            d['nuevos'] += 1
        if pac['citas_total'] >= 1 and not pac['proxima']:
            d['sin_proxima'] += 1

    lista = []
    for p in PerfilPsicologo.objects.select_related('usuario').order_by('id'):
        info = psi_infos.get(p.id, {})
        d = por_psi.get(p.id, {'total': 0, 'activos': 0, 'nuevos': 0,
                               'en_riesgo': 0, 'inactivos': 0,
                               'sin_proxima': 0, 'chips': []})
        nombre = (p.usuario.first_name if p.usuario and p.usuario.first_name
                  else (p.usuario.username if p.usuario else 'Sin nombre'))
        horas = info.get('horas', 0.0)
        lista.append({
            'id': p.id,
            'nombre': f'{"Dra." if p.genero == "Mujer" else "Dr."} {nombre}'.strip(),
            'genero': p.genero,
            'esta_activo': p.esta_activo,
            'especialidad': p.especialidad or '',
            'atiende': [t for t, flag in (
                ('individual', p.atiende_individual),
                ('pareja', p.atiende_pareja),
                ('familiar', p.atiende_familiar)) if flag],
            'esquema_ok': bool(info.get('esquema_ok')),
            'franjas': info.get('franjas', []),
            'horas_semana': horas,
            'horas_libres': round(max(0.0, horas - prox.get(p.id, 0)), 1),
            'proximas_7d': prox.get(p.id, 0),
            'total': d['total'],
            'activos': d['activos'],
            'nuevos': d['nuevos'],
            'en_riesgo': d['en_riesgo'],
            'inactivos': d['inactivos'],
            'sin_proxima': d['sin_proxima'],
            'chips': d['chips'],
        })
    lista.sort(key=lambda r: (-r['activos'], -r['total']))
    con_activos = [r['activos'] for r in lista if r['esta_activo']]
    media = round(sum(con_activos) / len(con_activos), 1) if con_activos else 0
    return {
        'psicologos': lista,
        'sin_asignar': sin_asignar,
        'media_activos': media,
        'total_pacientes': len(pacientes),
        'generado_en': _ahora().isoformat(),
    }


def Paciente_Detalle(perfil_id):
    """
    Ficha del paciente para el modal del simulador (solo lectura):
    respuestas completas del cuestionario más reciente, asignaciones reales
    (perfil global + tratamientos por modalidad) y resumen de citas.
    """
    hoy = _hoy()
    perfil = (UsuarioPerfil.objects
              .select_related('usuario', 'psicologo_asignado',
                              'psicologo_asignado__usuario')
              .get(id=perfil_id))
    user = perfil.usuario

    reg = (CuestionarioRegistro.objects
           .filter(paciente=user)
           .order_by('-fecha_completado', '-id')
           .values('respuestas', 'fecha_completado', 'flujo_elegido')
           .first())

    def _nombre_psi(p):
        if not p:
            return None
        n = None
        if p.usuario:
            n = p.usuario.first_name or p.usuario.username
        return {'id': p.id, 'nombre': f'{"Dra." if p.genero == "Mujer" else "Dr."} {n or "Sin nombre"}'.strip(),
                'genero': p.genero}

    perfiles_trat = {tp.psicologo_asignado_id: tp for tp in
                     TratamientoPaciente.objects.filter(paciente=user)
                     .exclude(psicologo_asignado=None)
                     .select_related('psicologo_asignado',
                                     'psicologo_asignado__usuario')}
    tratamientos = []
    for row in (TratamientoPaciente.objects.filter(paciente=user)
                .values('tipo_servicio', 'activo', 'psicologo_asignado_id')):
        psi = None
        tr = perfiles_trat.get(row['psicologo_asignado_id'])
        if tr is not None:
            psi = _nombre_psi(tr.psicologo_asignado)
        tratamientos.append({'tipo': TIPO_SESION_LABEL.get(row['tipo_servicio'], row['tipo_servicio']),
                             'activo': row['activo'], 'psicologo': psi})

    agg = (Cita.objects.filter(paciente=user)
           .aggregate(total=Count('id'),
                      primera=Min('fecha'),
                      ultima_pasada=Max('fecha', filter=Q(fecha__lt=hoy)),
                      proxima=Min('fecha', filter=Q(fecha__gte=hoy)),
                      canceladas=Count('id', filter=Q(estado='Cancelada'))))
    fila = {
        'citas_total': agg['total'],
        'proxima': agg['proxima'].isoformat() if agg['proxima'] else None,
        'ultima_pasada': agg['ultima_pasada'].isoformat() if agg['ultima_pasada'] else None,
    }
    fila_estado = _estado_paciente(fila, hoy)
    fila_burbuja = _burbuja_de({'estado': fila_estado,
                                'nuevo': not agg['ultima_pasada'],
                                'proxima': agg['proxima']})

    resp = (reg or {}).get('respuestas') or {}
    respuestas_limpias = {}
    for k, v in resp.items():
        if v in (None, '', [], {}):
            continue
        if isinstance(v, (list, tuple)):
            respuestas_limpias[k] = [str(x) for x in v]
        else:
            respuestas_limpias[k] = str(v)

    return {
        'pid': perfil.id,
        'user_id': user.id if user else None,
        'nombre': perfil.nombre or (user.first_name if user else '') or 'Paciente',
        'email': user.email if user else '',
        'fecha_registro': user.date_joined.isoformat() if user else None,
        'es_tectum': perfil.es_tectum,
        'es_beneficios': perfil.es_beneficios,
        'preferencia': _pref_clasificador(resp.get('preferencia_terapeuta')),
        'franjas': _franja_de_texto(resp.get('horario')),
        'cuestionario': {
            'flujo': (reg or {}).get('flujo_elegido') or '',
            'fecha': (reg or {}).get('fecha_completado').isoformat() if reg and reg.get('fecha_completado') else None,
            'campos_contestados': len(respuestas_limpias),
            'respuestas': respuestas_limpias,
        },
        'psicologo_perfil': _nombre_psi(perfil.psicologo_asignado),
        'tratamientos': tratamientos,
        'citas': {'total': agg['total'], 'primera': agg['primera'].isoformat() if agg['primera'] else None,
                  'ultima': agg['ultima_pasada'].isoformat() if agg['ultima_pasada'] else None,
                  'proxima': agg['proxima'].isoformat() if agg['proxima'] else None,
                  'canceladas': agg['canceladas']},
        'estado_label': ESTADO_LABEL[fila_estado],
        'burbuja': fila_burbuja,
        'burbuja_label': BURBUJA_LABEL.get(fila_burbuja, fila_burbuja),
    }


def _evaluacion_compat(pac, perfil_psi, info_psi, conteos, media=None):
    """
    Compatibilidad real paciente-psicólogo. Devuelve lista de revisiones:
      nivel: 'ok' | 'aviso' | 'incompatible' (+ texto con el dato real).
    Referencia de carga incluida (activos del destino vs media).
    """
    if media is None:
        media = next(iter(conteos.values()), {}).get('media', 0) if conteos else 0
    checks = []

    if perfil_psi.esta_activo:
        checks.append({'nivel': 'ok',
                       'texto': 'El psicólogo está aceptando pacientes nuevos.'})
    else:
        checks.append({'nivel': 'incompatible',
                       'texto': 'El psicólogo NO está aceptando pacientes en la agenda.'})

    pref = pac['preferencia']
    if pref in ('Mujer', 'Hombre'):
        if perfil_psi.genero == pref:
            checks.append({'nivel': 'ok',
                           'texto': f'El paciente pidió terapeuta {pref.lower()} y coincide.'})
        else:
            checks.append({'nivel': 'incompatible',
                           'texto': (f'El paciente pidió terapeuta {pref.lower()} y '
                                     f'{"Dra." if perfil_psi.genero == "Mujer" else "Dr."} '
                                     f'{perfil_psi.usuario.first_name if perfil_psi.usuario else ""} es {perfil_psi.genero.lower()}.')})
    elif pref == 'Indistinto':
        checks.append({'nivel': 'ok',
                       'texto': 'El paciente declaró que le es indistinto el género del terapeuta.'})
    else:
        checks.append({'nivel': 'aviso',
                       'texto': 'El paciente no registró preferencia de terapeuta (compatible con cualquiera).'})

    tipos_no_atiende = [t for t in pac['tipos'] if t not in
                        [s for s, flag in (('individual', perfil_psi.atiende_individual),
                                           ('pareja', perfil_psi.atiende_pareja),
                                           ('familiar', perfil_psi.atiende_familiar)) if flag]]
    if tipos_no_atiende:
        checks.append({'nivel': 'incompatible',
                       'texto': (f'No atiende el tipo de sesión requerido: '
                                 f'{", ".join(TIPO_SESION_LABEL.get(t, t) for t in tipos_no_atiende)}.')})
    else:
        checks.append({'nivel': 'ok',
                       'texto': 'Atiende el tipo de sesión que el paciente maneja.'})

    if info_psi and info_psi.get('esquema_ok'):
        checks.append({'nivel': 'ok', 'texto': 'Tiene esquema de horario vigente a la fecha.'})
    else:
        checks.append({'nivel': 'incompatible',
                       'texto': 'Sin esquema de horario vigente para hoy (no genera citas en agenda).'})

    franjas_psi = set((info_psi or {}).get('franjas') or [])
    franjas_pac = set(pac.get('franjas') or [])
    if not franjas_pac:
        checks.append({'nivel': 'aviso',
                       'texto': 'El paciente no declaró franja de disponibilidad en su cuestionario.'})
    elif franjas_pac & franjas_psi:
        comunes = sorted(franjas_pac & franjas_psi, key=FRANJAS_TODAS.index)
        comunes_txt = ' · '.join(comunes)
        checks.append({'nivel': 'ok',
                       'texto': f'Su horario atiende la franja del paciente ({comunes_txt}).'})
    elif franjas_psi:
        checks.append({'nivel': 'incompatible',
                       'texto': (f'Conflicto de horario: el paciente solo puede '
                                 f'({", ".join(sorted(franjas_pac, key=FRANJAS_TODAS.index))}) y el '
                                 f'psicólogo atiende ({", ".join(franjas_psi)}).')})
    else:
        checks.append({'nivel': 'aviso',
                       'texto': 'El psicólogo no tiene franjas derivables de su esquema vigente.'})

    if pac['estado'] == 'nunca':
        checks.append({'nivel': 'aviso',
                       'texto': 'El paciente registró cero citas: se movería como paciente nuevo.'})

    c = conteos.get(perfil_psi.id, {})
    activos = c.get('activos', 0)
    nuevos = c.get('nuevos', 0)
    if activos > media * 1.25 and media:
        checks.append({'nivel': 'aviso',
                       'texto': f'Ya carga {activos} pacientes activos y {nuevos} nuevos '
                                f'(media de la operación: {media} activos).'})
    elif media and activos < media * 0.75:
        checks.append({'nivel': 'ok',
                       'texto': f'Carga por debajo de la media ({activos} activos y {nuevos} '
                                f'nuevos vs media {media}).'})
    else:
        checks.append({'nivel': 'ok',
                       'texto': f'Carga en línea con la media ({activos} activos y {nuevos} '
                                f'nuevos vs media {media}).'})
    return checks


def _conteos_carga():
    """Conteos reales por psicólogo + media (sin chips, para compat y sugerencias)."""
    pacientes = _dataset_distribucion()
    conteos = defaultdict(lambda: {'total': 0, 'activos': 0, 'nuevos': 0,
                                   'en_riesgo': 0, 'inactivos': 0})
    pac_map = {}
    for pac in pacientes:
        destino = pac['psicologo_principal']
        if destino:
            d = conteos[destino]
            d['total'] += 1
            if pac['estado'] == 'activo':
                d['activos'] += 1
            elif pac['estado'] == 'en_riesgo':
                d['en_riesgo'] += 1
            elif pac['estado'] == 'inactivo':
                d['inactivos'] += 1
            if pac['nuevo']:
                d['nuevos'] += 1
        pac_map[pac['id']] = pac
    activos_lista = [d['activos'] for d in conteos.values()]
    media = round(sum(activos_lista) / len(activos_lista), 1) if activos_lista else 0
    for d in conteos.values():
        d['media'] = media
    return pac_map, conteos, media, _psi_info()


def _perfil_psi_por_id(psicologo_id):
    return PerfilPsicologo.objects.select_related('usuario').filter(id=psicologo_id).first()


def Compatibilidad(perfil_id, psicologo_id, pac_map=None, conteos=None, psi_extra=None):
    """
    Evalúa compatibilidad de UNA reasignación simulada. Reutilizable desde
    sugerencias masivas (pasando caches) para no recalcular por movimiento.
    """
    if pac_map is None:
        pac_map, conteos, media, _ = _conteos_carga()
    psi_extra = psi_extra if psi_extra is not None else _psi_info()
    pac = pac_map.get(perfil_id)
    perfil_psi = _perfil_psi_por_id(psicologo_id)
    if pac is None or perfil_psi is None:
        return None
    media = (conteos.get(psicologo_id, {}).get('media')
             if psicologo_id in conteos else None)
    if media is None:
        media = next(iter(conteos.values()), {}).get('media', 0) if conteos else 0
    checks = _evaluacion_compat(pac, perfil_psi, psi_extra.get(psicologo_id),
                                conteos, media=media)
    incompatible = any(c['nivel'] == 'incompatible' for c in checks)
    avisos = sum(1 for c in checks if c['nivel'] == 'aviso')
    resumen = ('Incompatible: revísalos antes de confirmar en producción'
               if incompatible else
               ('Compatible con avisos' if avisos else 'Compatible'))
    n = conteos.get(psicologo_id, {})
    franjas_psi = set((psi_extra.get(psicologo_id) or {}).get('franjas') or [])
    comunes = sorted(set(pac.get('franjas') or []) & franjas_psi, key=FRANJAS_TODAS.index)
    if comunes:
        horario_comp = ' · '.join(comunes)
    elif not (pac.get('franjas') or []):
        horario_comp = 'Sin franja declarada por el paciente'
    else:
        horario_comp = 'Sin cruce con su horario disponible'
    return {
        'psicologo': {'id': perfil_psi.id,
                      'nombre': f'{"Dra." if perfil_psi.genero == "Mujer" else "Dr."} {(perfil_psi.usuario.first_name if perfil_psi.usuario and perfil_psi.usuario.first_name else (perfil_psi.usuario.username if perfil_psi.usuario else "Sin nombre"))}'.strip(),
                      'genero': perfil_psi.genero},
        'checks': checks,
        'resumen': resumen,
        'compatible': not incompatible,
        'horario_compatible': horario_comp,
        'destino_hoy': {'total': n.get('total', 0), 'activos': n.get('activos', 0),
                        'nuevos': n.get('nuevos', 0)},
    }


def Sugerencias_Reasignacion(perfil_id, limite=4):
    """
    Mejores destinos reales para un paciente: psicólogos compatibles
    (preferencia + capacidad + esquema + franja) ordenados por menor carga
    de pacientes activos. Solo análisis; no modifica nada.
    """
    pac_map, conteos, media, psi_extra = _conteos_carga()
    pac = pac_map.get(perfil_id)
    if pac is None:
        return {'sugerencias': [], 'media_activos': media}
    candidatos = PerfilPsicologo.objects.filter(esta_activo=True).select_related('usuario')
    sugerencias = []
    for p in candidatos:
        if pac['psicologo_principal'] == p.id:
            continue
        eval_ = Compatibilidad(perfil_id, p.id, pac_map=pac_map, conteos=conteos, psi_extra=psi_extra)
        if eval_ is None:
            continue
        avisos = sum(1 for c in eval_['checks'] if c['nivel'] == 'aviso')
        carga = eval_['destino_hoy']['activos']
        origen_id = pac.get('psicologo_principal')
        carga_origen = conteos.get(origen_id, {}).get('activos', 0) if origen_id else None
        diferencia = round(carga_origen - carga, 1) if carga_origen is not None else None
        sugerencias.append({
            'psicologo': eval_['psicologo'],
            'resumen': eval_['resumen'],
            'compatible': eval_['compatible'],
            'horario_compatible': eval_['horario_compatible'],
            'checks': eval_['checks'],
            'carga_activos': carga,
            'diferencia_vs_origen': diferencia,
            'avisos': avisos,
        })
    sugerencias.sort(key=lambda s: (not s['compatible'], s['carga_activos'], s['avisos']))
    return {'sugerencias': sugerencias[:limite], 'media_activos': media}


def Sugerencias_Automaticas(limite=25):
    """
    Balanceo sugerido de punta a punta: revisa pacientes activos/nuevos cuya
    preferencia sea 'Indistinto' o no registrada, que estén asignados a un
    psicólogo por encima de la media, y propone el destino compatible con
    menor carga. Devuelve hasta `limite` movimientos simulados (no escribe).
    """
    pac_map, conteos, media, psi_extra = _conteos_carga()
    pacientes = list(pac_map.values())
    hacheteados = (p for p in pacientes
                   if p['psicologo_principal']
                   and (p['nuevo'] or p['estado'] == 'activo')
                   and p['preferencia'] in ('Indistinto', 'Sin registro')
                   and conteos.get(p['psicologo_principal'], {}).get('activos', 0) > media)
    propuestas = []
    for pac in hacheteados:
        origen = pac['psicologo_principal']
        carga_origen = conteos.get(origen, {}).get('activos', 0)
        candidatos = PerfilPsicologo.objects.filter(esta_activo=True).select_related('usuario')
        mejor = None
        alternativos = []
        for p in candidatos:
            if p.id == origen:
                continue
            eval_ = Compatibilidad(pac['id'], p.id, pac_map=pac_map, conteos=conteos, psi_extra=psi_extra)
            if eval_ is None or not eval_['compatible']:
                continue
            carga_dest = eval_['destino_hoy']['activos']
            item = {'psicologo': eval_['psicologo'], 'carga': carga_dest,
                    'resumen': eval_['resumen'], 'checks': eval_['checks'],
                    'horario_compatible': eval_['horario_compatible'],
                    'destino_hoy': eval_['destino_hoy']}
            alternativos.append(item)
            if mejor is None or carga_dest < mejor['carga']:
                mejor = item
        if not mejor:
            continue
        propuestas.append({
            'paciente': _chip_paciente(pac),
            'origen_id': origen,
            'mejor': mejor,
            'alternativos': [a for a in sorted(alternativos, key=lambda x: x['carga'])[:3] if a is not mejor],
            'alivio_origen': round(carga_origen - mejor['carga'], 1),
            'delta_activos': conteos.get(origen, {}).get('activos', 0) - mejor['carga'],
        })
    propuestas.sort(key=lambda m: -m['delta_activos'])
    return {'movimientos': propuestas[:limite], 'media_activos': media}


def Reasignar_Real(perfil_id, psicologo_id, usuario=None):
    """
    Reasignacion REAL de un paciente a otro psicologo (accion de Direccion,
    endpoint con CSRF activo y permiso es_admin). Refleja el cambio en las
    TRES tablas de negocio en una transaccion atomica:
      1. Cita: TODAS las citas futuras del paciente con el psicologo de
         origen (fecha >= hoy) pasan al nuevo psicologo.
      2. UsuarioPerfil.psicologo_asignado: asignacion global del perfil.
      3. TratamientoPaciente: fila(s) ACTIVAS ligadas al psicologo de
         origen; retrocompatibilidad: si no hay fila ligada al origen y
         existe exactamente una fila activa, se actualiza esa.
    Registra EventoAuditoria. NO envia correos y NO modifica eventos de
    Google Meet ya creados (el enlace sigue siendo valido).

    Conflicto duro (NO toca nada): preferencia de genero incumplida,
    tipo de sesion no atendido, sin esquema vigente o cruce de horario
    imposible. Devuelve dict {ok, motivo|resumen}.
    """
    from django.db import transaction as _trx

    perfil = (UsuarioPerfil.objects.select_related('usuario').filter(id=perfil_id).first())
    destino = _perfil_psi_por_id(psicologo_id)
    if perfil is None or destino is None:
        return {'ok': False, 'tipo_error': 'parametro',
                'motivo': 'Paciente o psicólogo destino inexistente.'}
    if perfil.usuario is None:
        return {'ok': False, 'tipo_error': 'parametro',
                'motivo': 'El perfil del paciente no tiene usuario asociado.'}

    pac_map, conteos, media, psi_extra = _conteos_carga()
    compat = Compatibilidad(perfil_id, psicologo_id, pac_map=pac_map,
                            conteos=conteos, psi_extra=psi_extra)
    if compat is None:
        return {'ok': False, 'tipo_error': 'parametro',
                'motivo': 'No se pudo evaluar la compatibilidad.'}
    if not compat['compatible']:
        return {'ok': False, 'tipo_error': 'incompatible',
                'motivo': ('No fue posible realizar la reasignación debido a conflictos '
                           'de horario, preferencias o reglas de negocio.'),
                'checks': compat['checks']}

    origen = (pac_map.get(perfil_id) or {}).get('psicologo_principal')
    hoy = _hoy()

    with _trx.atomic():
        # 1) Citas futuras del paciente con el psicologo de origen
        citas_qs = (Cita.objects.select_for_update()
                    .filter(paciente=perfil.usuario, fecha__gte=hoy))
        if origen:
            citas_qs = citas_qs.filter(psicologo_id=origen)
        else:
            citas_qs = citas_qs.filter(psicologo__isnull=True)
        citas_detalle = list(citas_qs.order_by('fecha', 'hora')
                             .values('id', 'fecha', 'hora', 'estado'))
        n_citas = citas_qs.update(psicologo=destino)

        # 2) Perfil global del paciente
        destino_id = destino.id
        perfil.psicologo_asignado = destino
        perfil.save(update_fields=['psicologo_asignado'])

        # 3) Tratamiento/servicio activo ligado al origen
        trat_base = TratamientoPaciente.objects.filter(paciente=perfil.usuario, activo=True)
        if origen:
            trat_qs = trat_base.filter(psicologo_asignado_id=origen)
            if not trat_qs.exists():
                activas = list(trat_base.exclude(psicologo_asignado_id=destino_id))
                if len(activas) == 1:
                    trat_qs = trat_base.filter(id=activas[0].id)
                else:
                    trat_qs = trat_base.none()
        else:
            trat_qs = trat_base.filter(psicologo_asignado__isnull=True)
        trat_ids = list(trat_qs.values_list('id', flat=True))
        n_trat = trat_qs.update(psicologo_asignado=destino)

        EventoAuditoria.objects.create(
            usuario=usuario,
            accion='Reasignacion admin',
            detalles={
                'paciente_perfil_id': perfil.id,
                'paciente_user_id': perfil.usuario_id,
                'psicologo_origen_id': origen,
                'psicologo_destino_id': destino_id,
                'citas_actualizadas': n_citas,
                'tratamientos_actualizados': n_trat,
            })

    def _nombre(psicologo):
        if not psicologo:
            return 'Sin asignar'
        n = None
        if psicologo.usuario:
            n = psicologo.usuario.first_name or psicologo.usuario.username
        return f'{"Dra." if psicologo.genero == "Mujer" else "Dr."} {n or "Sin nombre"}'.strip()

    origen_psi = _perfil_psi_por_id(origen) if origen else None
    activos_destino = conteos.get(psicologo_id, {}).get('activos', 0)
    return {
        'ok': True,
        'resumen': {
            'paciente': {'id': perfil.id, 'nombre': perfil.nombre or '',
                         'user_id': perfil.usuario_id},
            'origen': {'id': origen, 'nombre': _nombre(origen_psi)},
            'destino': compat['psicologo'],
            'motivo': compat['resumen'],
            'checks': compat['checks'],
            'horario_compatible': compat['horario_compatible'],
            'impacto': {
                'activos_origen_antes': conteos.get(origen, {}).get('activos', 0),
                'activos_destino_antes': activos_destino,
                'activos_destino_despues': activos_destino + 1,
                'media_operacion': media,
            },
            'actualizado': {
                'citas': n_citas,
                'citas_detalle': [{'id': c['id'],
                                   'fecha': c['fecha'].isoformat(),
                                   'hora': c['hora'].isoformat() if c['hora'] else '',
                                   'estado': c['estado']} for c in citas_detalle],
                'perfil': True,
                'tratamientos': n_trat,
                'tratamientos_ids': trat_ids,
            },
        },
    }
