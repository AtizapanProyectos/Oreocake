"""Consultas del reporte ejecutivo de crecimiento y sesiones.

Todas las cifras se calculan con la fecha de la Cita (no con su fecha de
registro). Las canceladas se excluyen de los indicadores operativos y se
muestran por separado; las confirmadas incluyen sesiones futuras.
"""

import calendar
import json
from datetime import timedelta

from django.contrib.auth.models import User
from django.db.models import Avg, Count, Min, Q
from django.shortcuts import render
from django.utils import timezone

from .models import Cita, EvaluacionSesionPaciente, PerfilPsicologo


MESES_NOMBRES = {
    1: 'Enero', 2: 'Febrero', 3: 'Marzo', 4: 'Abril',
    5: 'Mayo', 6: 'Junio', 7: 'Julio', 8: 'Agosto',
    9: 'Septiembre', 10: 'Octubre', 11: 'Noviembre', 12: 'Diciembre',
}


def _json_script(valor):
    """JSON seguro para incrustar en el script del template."""
    return json.dumps(valor).replace('</', '<\\/')


def _inicio_mes(fecha):
    return fecha.replace(day=1)


def _fin_mes(anio, mes):
    from datetime import date
    return date(anio, mes, calendar.monthrange(anio, mes)[1])


def _periodos_mensuales(inicio, fin, recortar=False):
    periodos = []
    cursor = _inicio_mes(inicio)
    while cursor <= fin:
        fin_del_mes = _fin_mes(cursor.year, cursor.month)
        p_ini = max(cursor, inicio) if recortar else cursor
        p_fin = min(fin_del_mes, fin) if recortar else fin_del_mes
        periodos.append({
            'label': f"{MESES_NOMBRES[cursor.month][:3]} {cursor.year}",
            'inicio': p_ini,
            'fin': p_fin,
        })
        cursor = fin_del_mes + timedelta(days=1)
    return periodos


def _periodos_semanales(inicio, fin):
    periodos = []
    cursor = inicio
    numero = 1
    while cursor <= fin:
        p_fin = min(cursor + timedelta(days=6), fin)
        periodos.append({
            'label': f"Sem {numero} · {cursor.strftime('%d/%m')}–{p_fin.strftime('%d/%m')}",
            'inicio': cursor,
            'fin': p_fin,
        })
        cursor = p_fin + timedelta(days=1)
        numero += 1
    return periodos


def _porcentaje(parte, total):
    return round((parte / total * 100), 1) if total else 0


def reporte_crecimiento_sesiones_view(request):
    """Dashboard ejecutivo con cifras dinámicas derivadas de la BD."""
    hoy = timezone.localdate()
    fecha_inicio_str = request.GET.get('fecha_inicio', '').strip()
    fecha_fin_str = request.GET.get('fecha_fin', '').strip()
    filtro_req = request.GET.get('filtro', '').strip().lower()
    periodo_req = request.GET.get('periodo', '').strip().lower()
    es_historico = (
        filtro_req in ['historico', 'totales', 'all']
        or periodo_req in ['historico', 'totales', 'all']
    )

    try:
        mes_sel = int(request.GET.get('mes', hoy.month))
    except (TypeError, ValueError):
        mes_sel = hoy.month
    try:
        anio = int(request.GET.get('anio', hoy.year))
    except (TypeError, ValueError):
        anio = hoy.year
    if mes_sel not in MESES_NOMBRES:
        mes_sel = hoy.month
    if not 2000 <= anio <= 2100:
        anio = hoy.year

    # Censo acumulado: misma definición que el censo/CSV ya existente.
    psicologos_uids = set(PerfilPsicologo.objects.values_list('usuario_id', flat=True))
    pacientes_qs = User.objects.exclude(id__in=psicologos_uids).exclude(is_superuser=True)
    total_registros = pacientes_qs.count()
    citas_validas = Cita.objects.exclude(estado='Cancelada')

    usuarios_activos = citas_validas.filter(
        paciente__in=pacientes_qs
    ).values('paciente').distinct().count()
    usuarios_sin_sesion = max(0, total_registros - usuarios_activos)
    pct_activos = _porcentaje(usuarios_activos, total_registros)
    pct_sin_sesion = _porcentaje(usuarios_sin_sesion, total_registros)

    # Rango dinámico de meses: incluye altas aunque todavía no tengan citas y
    # meses futuros si ya existen sesiones agendadas en ellos.
    fechas_semilla = []
    primera_cita = citas_validas.order_by('fecha').values_list('fecha', flat=True).first()
    ultima_cita = citas_validas.order_by('-fecha').values_list('fecha', flat=True).first()
    primer_registro = pacientes_qs.order_by('date_joined').values_list('date_joined', flat=True).first()
    ultimo_registro = pacientes_qs.order_by('-date_joined').values_list('date_joined', flat=True).first()
    for fecha in (primera_cita, ultima_cita):
        if fecha:
            fechas_semilla.append(fecha)
    for fecha_dt in (primer_registro, ultimo_registro):
        if fecha_dt:
            fechas_semilla.append(timezone.localtime(fecha_dt).date())
    fechas_semilla.append(hoy)
    mes_inicial_bd = _inicio_mes(min(fechas_semilla))
    mes_final_bd = _inicio_mes(max(fechas_semilla))
    meses_timeline = _periodos_mensuales(mes_inicial_bd, mes_final_bd)

    # Periodo solicitado: rango explícito > histórico > mes (actual por default).
    d_ini = None
    d_fin = None
    es_rango_personalizado = False
    if fecha_inicio_str and fecha_fin_str and not es_historico:
        from datetime import datetime
        try:
            d_ini = datetime.strptime(fecha_inicio_str, '%Y-%m-%d').date()
            d_fin = datetime.strptime(fecha_fin_str, '%Y-%m-%d').date()
            if d_ini > d_fin:
                d_ini, d_fin = d_fin, d_ini
            es_rango_personalizado = True
        except (ValueError, TypeError):
            d_ini, d_fin = None, None

    if es_rango_personalizado:
        filtro_activo = 'rango'
        periodo_inicio, periodo_fin = d_ini, d_fin
        nombre_periodo = f"{d_ini.strftime('%d/%m/%Y')} al {d_fin.strftime('%d/%m/%Y')}"
        subtitulo_periodo = 'Sesiones por su fecha de atención/agenda dentro del rango seleccionado'
        citas_filtradas = citas_validas.filter(fecha__gte=d_ini, fecha__lte=d_fin)
        citas_canceladas_filtradas = Cita.objects.filter(
            estado='Cancelada', fecha__gte=d_ini, fecha__lte=d_fin
        )
        evaluaciones_filtradas = EvaluacionSesionPaciente.objects.filter(
            cita__fecha__gte=d_ini, cita__fecha__lte=d_fin
        )
        registros_filtrados = pacientes_qs.filter(
            date_joined__date__gte=d_ini, date_joined__date__lte=d_fin
        )
    elif es_historico:
        filtro_activo = 'historico'
        periodo_inicio = periodo_fin = None
        nombre_periodo = 'Histórico Global'
        subtitulo_periodo = 'Todos los registros disponibles, agrupados por mes de la sesión'
        citas_filtradas = citas_validas
        citas_canceladas_filtradas = Cita.objects.filter(estado='Cancelada')
        evaluaciones_filtradas = EvaluacionSesionPaciente.objects.all()
        registros_filtrados = pacientes_qs
    else:
        filtro_activo = 'mes'
        periodo_inicio = _inicio_mes(hoy.replace(year=anio, month=mes_sel, day=1))
        periodo_fin = _fin_mes(anio, mes_sel)
        nombre_mes = MESES_NOMBRES[mes_sel]
        nombre_periodo = f"{nombre_mes} {anio}"
        subtitulo_periodo = 'Sesiones por su fecha de atención/agenda; las confirmadas pueden ser futuras'
        citas_filtradas = citas_validas.filter(fecha__year=anio, fecha__month=mes_sel)
        citas_canceladas_filtradas = Cita.objects.filter(
            estado='Cancelada', fecha__year=anio, fecha__month=mes_sel
        )
        # La satisfacción pertenece a las sesiones del periodo, no a la fecha
        # en que el paciente contestó el formulario.
        evaluaciones_filtradas = EvaluacionSesionPaciente.objects.filter(
            cita__fecha__year=anio, cita__fecha__month=mes_sel
        )
        registros_filtrados = pacientes_qs.filter(
            date_joined__year=anio, date_joined__month=mes_sel
        )

    nombre_mes = MESES_NOMBRES[mes_sel]
    total_sesiones = citas_filtradas.count()
    estados = {
        (row['estado'] or 'Sin estado'): row['total']
        for row in citas_filtradas.values('estado').annotate(total=Count('id'))
    }
    sesiones_completadas = estados.get('Completada', 0)
    sesiones_confirmadas = estados.get('Confirmada', 0)
    sesiones_pendientes = max(0, total_sesiones - sesiones_completadas - sesiones_confirmadas)
    sesiones_canceladas = citas_canceladas_filtradas.count()
    tasa_efectividad = _porcentaje(sesiones_completadas, total_sesiones)

    modalidades = {
        (row['modalidad'] or 'Sin especificar'): row['total']
        for row in citas_filtradas.values('modalidad').annotate(total=Count('id'))
    }
    sesiones_en_linea = modalidades.get('En línea', 0)
    sesiones_presenciales = modalidades.get('Presencial', 0)
    sesiones_sin_modalidad = max(0, total_sesiones - sesiones_en_linea - sesiones_presenciales)
    pct_en_linea = _porcentaje(sesiones_en_linea, total_sesiones)
    pct_presencial = _porcentaje(sesiones_presenciales, total_sesiones)

    tipos_conteo = {
        (row['tipo_sesion'] or ''): row['total']
        for row in citas_filtradas.values('tipo_sesion').annotate(total=Count('id'))
    }
    etiquetas_servicio = {
        'individual': 'Individual', 'pareja': 'Pareja', 'familiar': 'Familiar', '': 'Sin especificar',
    }
    servicios_stats = []
    for clave in ['individual', 'pareja', 'familiar', '']:
        cantidad = tipos_conteo.get(clave, 0)
        if cantidad:
            servicios_stats.append({
                'nombre': etiquetas_servicio[clave],
                'total': cantidad,
                'pct': _porcentaje(cantidad, total_sesiones),
            })
    for clave, cantidad in tipos_conteo.items():
        if clave not in etiquetas_servicio:
            servicios_stats.append({
                'nombre': str(clave).title(), 'total': cantidad,
                'pct': _porcentaje(cantidad, total_sesiones),
            })

    # Recurrencia real: historial completo de las personas que aparecen en el
    # periodo. Antes una sola cita en el mes se etiquetaba erróneamente como
    # "cita única" aunque el paciente tuviera muchas sesiones anteriores.
    pacientes_periodo_ids = set(citas_filtradas.values_list('paciente_id', flat=True).distinct())
    pacientes_unicos = len(pacientes_periodo_ids)
    historial_pacientes = list(
        citas_validas.filter(paciente_id__in=pacientes_periodo_ids)
        .values('paciente').annotate(total=Count('id'), primera=Min('fecha'))
    )
    pacs_1_cita = sum(1 for row in historial_pacientes if row['total'] == 1)
    pacs_2_3_citas = sum(1 for row in historial_pacientes if 2 <= row['total'] <= 3)
    pacs_4_mas_citas = sum(1 for row in historial_pacientes if row['total'] >= 4)
    pacientes_recurrentes = sum(1 for row in historial_pacientes if row['total'] > 1)
    pct_1_cita = _porcentaje(pacs_1_cita, pacientes_unicos)
    pct_2_3_citas = _porcentaje(pacs_2_3_citas, pacientes_unicos)
    pct_4_mas_citas = _porcentaje(pacs_4_mas_citas, pacientes_unicos)
    pct_fidelizacion = _porcentaje(pacientes_recurrentes, pacientes_unicos)

    if es_historico:
        pacientes_nuevos_periodo = pacientes_unicos
    else:
        pacientes_nuevos_periodo = sum(
            1 for row in historial_pacientes
            if row['primera'] and periodo_inicio <= row['primera'] <= periodo_fin
        )
    pacientes_con_historial = max(0, pacientes_unicos - pacientes_nuevos_periodo)

    registros_periodo = registros_filtrados.count()
    registros_periodo_ids = set(registros_filtrados.values_list('id', flat=True))
    registros_convertidos = citas_validas.filter(
        paciente_id__in=registros_periodo_ids
    ).values('paciente_id').distinct().count()
    tasa_conversion_registros = _porcentaje(registros_convertidos, registros_periodo)

    # Agrupación de las gráficas: mensual en histórico o rangos largos; semanal
    # en mes/rangos cortos. Nunca se trunca a seis semanas como antes.
    if es_historico:
        periodos_grafica = meses_timeline
        modo_agrupacion = 'mensual'
    elif es_rango_personalizado and (d_fin - d_ini).days > 62:
        periodos_grafica = _periodos_mensuales(d_ini, d_fin, recortar=True)
        modo_agrupacion = 'mensual'
    elif es_rango_personalizado:
        periodos_grafica = _periodos_semanales(d_ini, d_fin)
        modo_agrupacion = 'semanal'
    else:
        periodos_grafica = _periodos_semanales(periodo_inicio, periodo_fin)
        modo_agrupacion = 'semanal'

    filas_tabla = []
    semanas_labels = []
    semanas_datos = []
    semanas_completadas_datos = []
    semanas_confirmadas_datos = []
    semanas_pendientes_datos = []
    crecimiento_labels = []
    crecimiento_datos = []

    for periodo in periodos_grafica:
        p_ini, p_fin = periodo['inicio'], periodo['fin']
        c_grupo = citas_filtradas.filter(fecha__gte=p_ini, fecha__lte=p_fin)
        total = c_grupo.count()
        completadas = c_grupo.filter(estado='Completada').count()
        confirmadas = c_grupo.filter(estado='Confirmada').count()
        pendientes = max(0, total - completadas - confirmadas)
        en_linea = c_grupo.filter(modalidad='En línea').count()
        presenciales = c_grupo.filter(modalidad='Presencial').count()
        nuevos = registros_filtrados.filter(
            date_joined__date__gte=p_ini, date_joined__date__lte=p_fin
        ).count()

        semanas_labels.append(periodo['label'])
        semanas_datos.append(total)
        semanas_completadas_datos.append(completadas)
        semanas_confirmadas_datos.append(confirmadas)
        semanas_pendientes_datos.append(pendientes)
        crecimiento_labels.append(periodo['label'])
        crecimiento_datos.append(nuevos)
        filas_tabla.append({
            'nombre': periodo['label'], 'total': total,
            'completadas': completadas, 'confirmadas': confirmadas,
            'pendientes': pendientes, 'en_linea': en_linea,
            'presenciales': presenciales,
        })

    promedio_sesiones_texto = f"{round(total_sesiones / max(1, len(periodos_grafica)), 1)}"
    promedio_crecimiento = round(registros_periodo / max(1, len(periodos_grafica)), 1)
    max_crec = max(crecimiento_datos) if crecimiento_datos else 0
    crecimiento_tarjetas = [
        {
            'label': label, 'val': valor,
            'sub': 'Pico del periodo' if valor == max_crec and valor > 0 else 'nuevos registros',
            'destacado': valor == max_crec and valor > 0,
        }
        for label, valor in zip(crecimiento_labels, crecimiento_datos)
    ]

    # Satisfacción exclusivamente del periodo. Sin respuestas => N/D, nunca un
    # falso 5.0 ni el histórico como fallback.
    evals_qs = evaluaciones_filtradas
    total_evaluaciones = evals_qs.count()
    if total_evaluaciones:
        agg_sat = evals_qs.aggregate(
            prom=Avg('promedio_satisfaccion'),
            atencion=Avg('satisfaccion_atencion'),
            escucha=Avg('sentirse_escuchado_respetado'),
            avance=Avg('avance_hacia_objetivos'),
        )
        promedio_satisfaccion_general = round(agg_sat['prom'], 2) if agg_sat['prom'] is not None else None
        pct_satisfaccion_general = round(promedio_satisfaccion_general * 20, 1) if promedio_satisfaccion_general is not None else None
        prom_atencion = round(agg_sat['atencion'], 2) if agg_sat['atencion'] is not None else None
        prom_escucha = round(agg_sat['escucha'], 2) if agg_sat['escucha'] is not None else None
        prom_avance = round(agg_sat['avance'], 2) if agg_sat['avance'] is not None else None
        distribucion_estrellas = [
            evals_qs.filter(promedio_satisfaccion__gte=4.8).count(),
            evals_qs.filter(promedio_satisfaccion__gte=4.0, promedio_satisfaccion__lt=4.8).count(),
            evals_qs.filter(promedio_satisfaccion__gte=3.0, promedio_satisfaccion__lt=4.0).count(),
            evals_qs.filter(promedio_satisfaccion__lt=3.0).count(),
        ]
    else:
        promedio_satisfaccion_general = None
        pct_satisfaccion_general = None
        prom_atencion = prom_escucha = prom_avance = None
        distribucion_estrellas = [0, 0, 0, 0]

    def _promedio_servicio(tipo):
        valor = evals_qs.filter(tipo_proceso=tipo).aggregate(
            promedio=Avg('promedio_satisfaccion')
        )['promedio']
        return round(valor, 2) if valor is not None else None

    mod_ind = _promedio_servicio('individual')
    mod_par = _promedio_servicio('pareja')
    mod_fam = _promedio_servicio('familiar')

    # Producción por terapeuta + satisfacción, incluida la modalidad presencial.
    evals_por_psicologo = {
        row['psicologo']: row
        for row in evals_qs.values('psicologo').annotate(
            cant=Count('id'),
            prom=Avg('promedio_satisfaccion'),
            atencion=Avg('satisfaccion_atencion'),
            escucha=Avg('sentirse_escuchado_respetado'),
            avance=Avg('avance_hacia_objetivos'),
        )
    }
    produccion = citas_filtradas.values('psicologo').annotate(
        total=Count('id'),
        completadas=Count('id', filter=Q(estado='Completada')),
        confirmadas=Count('id', filter=Q(estado='Confirmada')),
        en_linea=Count('id', filter=Q(modalidad='En línea')),
        presenciales=Count('id', filter=Q(modalidad='Presencial')),
        pacientes=Count('paciente', distinct=True),
    ).order_by('-total')
    perfil_ids = [row['psicologo'] for row in produccion if row['psicologo']]
    perfiles = {
        p.id: p for p in PerfilPsicologo.objects.filter(id__in=perfil_ids).select_related('usuario')
    }

    terapeutas_stats = []
    for row in produccion:
        psicologo_id = row['psicologo']
        perfil = perfiles.get(psicologo_id)
        if perfil:
            nombre = f"{perfil.usuario.first_name} {perfil.usuario.last_name}".strip() or perfil.usuario.username
            especialidad = perfil.especialidad or 'Psicólogo Clínico HOPE'
        else:
            nombre = 'Sin asignar'
            especialidad = 'Citas pendientes de asignación'
        evaluacion = evals_por_psicologo.get(psicologo_id)
        cant_evals = evaluacion['cant'] if evaluacion else 0
        promedio = round(evaluacion['prom'], 2) if evaluacion and evaluacion['prom'] is not None else None
        comentarios = []
        if cant_evals:
            comentarios = list(
                evals_qs.filter(psicologo_id=psicologo_id)
                .exclude(comentarios__isnull=True).exclude(comentarios__exact='')
                .order_by('-fecha_respuesta').values_list('comentarios', flat=True)[:3]
            )
        terapeutas_stats.append({
            'id': psicologo_id,
            'nombre': nombre,
            'especialidad': especialidad,
            'total': row['total'],
            'completadas': row['completadas'],
            'confirmadas': row['confirmadas'],
            'pendientes': max(0, row['total'] - row['completadas'] - row['confirmadas']),
            'en_linea': row['en_linea'],
            'presenciales': row['presenciales'],
            'pacientes': row['pacientes'],
            'cant': cant_evals,
            'promedio': promedio,
            'porcentaje': round(promedio * 20, 1) if promedio is not None else 0,
            'atencion': round(evaluacion['atencion'], 1) if evaluacion and evaluacion['atencion'] is not None else None,
            'escucha': round(evaluacion['escucha'], 1) if evaluacion and evaluacion['escucha'] is not None else None,
            'avance': round(evaluacion['avance'], 1) if evaluacion and evaluacion['avance'] is not None else None,
            'comentarios': comentarios,
        })

    psicologos_stats = sorted(
        [row for row in terapeutas_stats if row['cant'] and row['promedio'] is not None],
        key=lambda row: (row['promedio'], row['cant']), reverse=True,
    )
    psicologos_chart_labels = [row['nombre'] for row in psicologos_stats]
    psicologos_chart_datos = [row['promedio'] for row in psicologos_stats]
    psicologos_chart_cants = [row['cant'] for row in psicologos_stats]

    meses_disponibles = [
        {
            'numero': periodo['inicio'].month,
            'anio': periodo['inicio'].year,
            'nombre': f"{MESES_NOMBRES[periodo['inicio'].month][:3]} {periodo['inicio'].year}",
            'activo': (
                not es_historico and not es_rango_personalizado
                and mes_sel == periodo['inicio'].month and anio == periodo['inicio'].year
            ),
        }
        for periodo in meses_timeline
    ]

    context = {
        'anio': anio,
        'nombre_mes': nombre_mes,
        'mes_seleccionado': mes_sel,
        'fecha_inicio': fecha_inicio_str,
        'fecha_fin': fecha_fin_str,
        'nombre_periodo': nombre_periodo,
        'subtitulo_periodo': subtitulo_periodo,
        'filtro_activo': filtro_activo,
        'es_historico': es_historico,
        'es_rango_personalizado': es_rango_personalizado,
        'meses_disponibles': meses_disponibles,
        'fecha_corte': timezone.now(),
        'modo_agrupacion': modo_agrupacion,

        'usuarios_activos': usuarios_activos,
        'pct_activos': pct_activos,
        'usuarios_sin_sesion': usuarios_sin_sesion,
        'pct_sin_sesion': pct_sin_sesion,
        'total_registros': total_registros,
        'registros_periodo': registros_periodo,
        'registros_convertidos': registros_convertidos,
        'tasa_conversion_registros': tasa_conversion_registros,

        'total_sesiones': total_sesiones,
        'sesiones_completadas': sesiones_completadas,
        'sesiones_confirmadas': sesiones_confirmadas,
        'sesiones_pendientes': sesiones_pendientes,
        'sesiones_canceladas': sesiones_canceladas,
        'sesiones_en_linea': sesiones_en_linea,
        'sesiones_presenciales': sesiones_presenciales,
        'sesiones_sin_modalidad': sesiones_sin_modalidad,
        'pct_en_linea': pct_en_linea,
        'pct_presencial': pct_presencial,
        'servicios_stats': servicios_stats,
        'pacientes_unicos': pacientes_unicos,
        'pacientes_recurrentes': pacientes_recurrentes,
        'pacientes_nuevos_periodo': pacientes_nuevos_periodo,
        'pacientes_con_historial': pacientes_con_historial,
        'tasa_efectividad': tasa_efectividad,
        'promedio_sesiones_texto': promedio_sesiones_texto,

        'pacs_1_cita': pacs_1_cita,
        'pacs_2_3_citas': pacs_2_3_citas,
        'pacs_4_mas_citas': pacs_4_mas_citas,
        'pct_1_cita': pct_1_cita,
        'pct_2_3_citas': pct_2_3_citas,
        'pct_4_mas_citas': pct_4_mas_citas,
        'pct_fidelizacion': pct_fidelizacion,

        'filas_tabla': filas_tabla,
        'crecimiento_tarjetas': crecimiento_tarjetas,
        'promedio_crecimiento': promedio_crecimiento,
        'semanas_labels_json': _json_script(semanas_labels),
        'semanas_datos_json': _json_script(semanas_datos),
        'semanas_completadas_json': _json_script(semanas_completadas_datos),
        'semanas_confirmadas_json': _json_script(semanas_confirmadas_datos),
        'semanas_pendientes_json': _json_script(semanas_pendientes_datos),
        'crecimiento_labels_json': _json_script(crecimiento_labels),
        'crecimiento_datos_json': _json_script(crecimiento_datos),
        'retencion_valores_json': _json_script([pacs_2_3_citas, pacs_1_cita, pacs_4_mas_citas]),
        'modalidad_valores_json': _json_script([
            sesiones_en_linea, sesiones_presenciales, sesiones_sin_modalidad,
        ]),

        'total_evaluaciones': total_evaluaciones,
        'promedio_satisfaccion_general': promedio_satisfaccion_general,
        'pct_satisfaccion_general': pct_satisfaccion_general,
        'prom_atencion': prom_atencion,
        'prom_escucha': prom_escucha,
        'prom_avance': prom_avance,
        'distribucion_estrellas_json': _json_script(distribucion_estrellas),
        'mod_ind': mod_ind,
        'mod_par': mod_par,
        'mod_fam': mod_fam,

        'terapeutas_stats': terapeutas_stats,
        'psicologos_stats': psicologos_stats,
        'psicologos_chart_labels_json': _json_script(psicologos_chart_labels),
        'psicologos_chart_datos_json': _json_script(psicologos_chart_datos),
        'psicologos_chart_cants_json': _json_script(psicologos_chart_cants),
        'url_panel_sentimientos': '/sentimientos/',
    }
    return render(request, 'reporte_crecimiento_sesiones.html', context)
