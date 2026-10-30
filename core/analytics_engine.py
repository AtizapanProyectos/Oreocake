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
        }

    pacientes = []
    for perfil in perfiles:
        user = perfil.usuario
        agg = citas_agg.get(user.id) if user else None
        pref = prefs.get(user.id) if user else None
        if pref is None:
            pref = {'preferencia': 'Sin registro', 'campos': 0}

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
