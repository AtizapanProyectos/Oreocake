from datetime import date
from django.core.paginator import Paginator
from django.db.models import Avg, Count, Q, Exists, OuterRef, Case, When, IntegerField, Subquery, Prefetch, Value, CharField
from django.db.models.functions import Coalesce
from django.http import JsonResponse
from django.shortcuts import render, get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_POST
from .flujo_clinico import solo_admin
from .models import Cita, TrabajoProceso, EjecucionProceso, ControlOrquestador, LogEnvioReporteIPP, ReporteClinicoPDF
from . import orquestador as motor

LABELS = {'sin_solicitud': 'Sin solicitud', 'pendiente': 'Solicitud registrada',
          'espera_dependencia': 'En preparación', 'espera_reintento': 'Reintento programado',
          'procesando': 'Ejecuciones en curso', 'completado': 'Completadas',
          'bloqueado': 'Con incidencia', 'revision_manual': 'Requieren intervención'}
ETIQUETAS = {'meet':'Documentos de Meet', 'lectura':'Lectura de notas', 'ia':'Preparación de bitácora',
             'escritura':'Registro en expediente', 'ipp':'Validación del IPP',
             'sintesis_paciente':'Síntesis para paciente', 'sintesis_psicologo':'Síntesis clínica',
             'pdf_paciente':'PDF para paciente', 'pdf_psicologo':'PDF para profesional',
             'correo_paciente':'Entrega al paciente', 'correo_psicologo':'Entrega al profesional'}

ACCIONES = {
    'google_acceso': 'Verificar la cuenta HOPE, permisos de Drive y autorización OAuth. Después solicitar lectura.',
    'google_pausado': 'Corregir la conexión central y solicitar reactivación de Meet.',
    'nota_manual': 'Solicitar al profesional la nota clínica en el expediente.',
    'revision_audio': 'Revisar y aprobar la nota de audio desde el expediente.',
    'ipp': 'Completar el IPP vinculado a esta sesión.',
    'correo_ausente': 'Corregir el correo del destinatario y solicitar la etapa.',
    'entrega_incierta': 'Contrastar con el proveedor SMTP antes de autorizar cualquier reenvío.',
    'historico_ambiguo': 'Verificar entregas históricas; no reenviar sin evidencia.',
    'nota_modificada': 'Revisar cambios clínicos antes de preparar una nueva versión.',
}


def filtrar(params):
    qs = Cita.objects.filter(estado__in=['Confirmada', 'Completada'])
    orden = Case(*[When(etapa=e, then=i) for i,e in enumerate(motor.ETAPAS)], output_field=IntegerField())
    abiertos = TrabajoProceso.objects.filter(cita_id=OuterRef('pk')).exclude(estado='completado').annotate(orden=orden).order_by('orden')
    qs = qs.annotate(estado_proceso=Subquery(abiertos.values('estado')[:1]),
        tiene_trabajos=Exists(TrabajoProceso.objects.filter(cita_id=OuterRef('pk'))),
        manual_proceso=Subquery(abiertos.values('manual')[:1]),
        error_proceso=Subquery(abiertos.values('error')[:1]))
    qs = qs.annotate(estado_visual=Case(
        When(modalidad='Presencial', registro_llegada='No Vino', then=Value('revision_manual')),
        When(tiene_trabajos=True, estado_proceso__isnull=True, then=Value('completado')),
        When(estado_proceso='pendiente', manual_proceso=False, then=Value('sin_solicitud')),
        default=Coalesce('estado_proceso',Value('sin_solicitud')),output_field=CharField()))
    for key, lookup in [('fecha','fecha'), ('desde','fecha__gte'), ('hasta','fecha__lte'), ('creada','fecha_creacion__date')]:
        if params.get(key):
            qs = qs.filter(**{lookup: date.fromisoformat(params[key])})
    if params.get('desde') and params.get('hasta') and params['desde'] > params['hasta']:
        raise ValueError('El rango de fechas es inválido.')
    for key, lookup in [('cita','pk'), ('servicio','tipo_sesion'), ('modalidad','modalidad')]:
        if params.get(key):
            qs = qs.filter(**{lookup: params[key]})
    for key, rel in [('paciente','paciente'), ('psicologo','psicologo__usuario')]:
        if params.get(key):
            value = params[key]
            qs = qs.filter(Q(**{rel+'__first_name__icontains':value}) | Q(**{rel+'__last_name__icontains':value}) | Q(**{rel+'__email__icontains':value}))
    org = params.get('org')
    if org == 'hope':
        qs = qs.filter(es_tectum=False, es_beneficios=False)
    elif org == 'tectum_inhouse':
        qs = qs.filter(es_tectum=True, es_beneficios=False)
    elif org == 'tectum_beneficios':
        qs = qs.filter(es_beneficios=True)
    if params.get('estado') in LABELS:
        qs = qs.filter(estado_visual=params['estado'])
    return qs.order_by('-manual_proceso', 'fecha', 'hora', 'pk')


def fila(cita):
    trabajos = {t.etapa: t for t in cita.trabajos_proceso.all()}
    pendiente = next((trabajos[e] for e in motor.ETAPAS if e in trabajos and trabajos[e].estado != 'completado'), None)
    estado = pendiente.estado if pendiente else 'completado' if trabajos else 'sin_solicitud'
    if pendiente and estado == 'pendiente' and not pendiente.manual:
        estado = 'sin_solicitud'
    edad = max(0, int((timezone.now()-motor.inicio(cita)).total_seconds()/3600))
    no_vino = cita.modalidad == 'Presencial' and cita.registro_llegada == 'No Vino'
    if no_vino:
        estado = 'revision_manual'
    return {'id': cita.pk, 'paciente': cita.paciente.get_full_name() or cita.paciente.username,
        'profesional': cita.psicologo.usuario.get_full_name() or cita.psicologo.usuario.username if cita.psicologo else 'Sin asignar',
        'organizacion': 'TECTUM Beneficios' if cita.es_beneficios else 'TECTUM In-House' if cita.es_tectum else 'HOPE',
        'servicio': cita.get_tipo_sesion_display(), 'modalidad': cita.modalidad, 'asistencia': cita.registro_llegada,
        'confirmacion': 'Enviada' if cita.correo_confirmacion_enviado else 'Sin evidencia de envío',
        'fecha': f'{cita.fecha} {cita.hora:%H:%M}', 'creada': timezone.localtime(cita.fecha_creacion).isoformat(),
        'estado': estado, 'estado_label': LABELS[estado], 'edad': edad, 'prioridad': 'Alta' if edad >=48 or estado in ('bloqueado','revision_manual') else 'Normal',
        'actualizado': max((t.actualizado.isoformat() for t in trabajos.values()), default=None),
        'siguiente': 'Revisar inasistencia' if no_vino else 'Selecciona una acción' if estado == 'sin_solicitud' else ETIQUETAS[pendiente.etapa] if pendiente else 'Entregas completas',
        'mensaje': pendiente.mensaje if pendiente else '', 'elegible': motor.elegible(cita),
        'pasos': [{'etapa':e, 'etiqueta':ETIQUETAS[e], 'estado':trabajos[e].estado if e in trabajos else 'pendiente',
                   'estado_label':LABELS[trabajos[e].estado] if e in trabajos and (trabajos[e].manual or trabajos[e].estado != 'pendiente') else 'Pendiente',
                   'intentos': trabajos[e].intentos if e in trabajos else 0,
                   'error': trabajos[e].error if e in trabajos else '',
                   'accion': ACCIONES.get(trabajos[e].error, 'Revisar la integración y el historial de esta etapa.') if e in trabajos and trabajos[e].error else '',
                   'impacto': 'Esta sesión espera; las demás continúan.' if e in trabajos and trabajos[e].error else '',
                   'mensaje': trabajos[e].mensaje if e in trabajos else '',
                   'proximo': trabajos[e].proximo_intento.isoformat() if e in trabajos and trabajos[e].manual and trabajos[e].habilitado and trabajos[e].estado in ('pendiente','espera_reintento') else None} for e in motor.ETAPAS]}


@solo_admin
def panel(request):
    return render(request, 'centro_operaciones.html', {'estados': LABELS, 'etapas': ETIQUETAS, 'activo': motor.activo()})


@solo_admin
def datos(request):
    try:
        qs = filtrar(request.GET)
        pagina = Paginator(qs.select_related('paciente','psicologo__usuario').prefetch_related(
            Prefetch('trabajos_proceso', queryset=TrabajoProceso.objects.defer('resultado'))), 24).get_page(request.GET.get('page'))
        ejecuciones = EjecucionProceso.objects.filter(trabajo__cita__in=qs, fin__date=timezone.localdate())
        terminadas = ejecuciones.count()
        exitos = ejecuciones.filter(estado='completado').count()
        medidas = ejecuciones.aggregate(duracion=Avg('duracion_ms'), resolucion=Avg('resolucion_ms'))
        control = ControlOrquestador.objects.filter(clave='maestro', vence__gt=timezone.now()).exists()
        ultimo_intento = EjecucionProceso.objects.order_by('-pk').values_list('inicio', flat=True).first()
        entregado = {}
        for tipo in ('paciente', 'psicologo'):
            reportes = ReporteClinicoPDF.objects.filter(cita_id=OuterRef('pk'), tipo_destinatario=tipo, correo_enviado=True)
            logs = LogEnvioReporteIPP.objects.filter(cita_id=OuterRef('pk'), tipo_destinatario=tipo, resultado='Exitoso')
            entregado[tipo] = Exists(reportes) | Exists(logs)
        conteo = qs.annotate(envio_paciente=entregado['paciente'], envio_psicologo=entregado['psicologo'])
        enviada = Q(envio_paciente=True, envio_psicologo=True)
        problema = (Q(estado_visual__in=['bloqueado', 'revision_manual', 'espera_reintento']) |
                    (Q(estado_visual='espera_dependencia') & ~Q(error_proceso__in=['', 'pausa_manual'])))
        estados = conteo.aggregate(enviadas=Count('pk', filter=enviada), problemas=Count('pk', filter=~enviada & problema), total=Count('pk'))
        estados['faltan'] = estados['total'] - estados['enviadas'] - estados['problemas']
        return JsonResponse({'filas':[fila(c) for c in pagina], 'total':pagina.paginator.count, 'pagina':pagina.number,
            'paginas':pagina.paginator.num_pages, 'actualizado':timezone.localtime().isoformat(), 'activo':motor.activo(),
            'capacidad':1, 'ejecucion_activa':control,
            'ultimo_intento':ultimo_intento,
            'modo':'automatico' if motor.modo_automatico() else 'manual', 'resumen_citas':estados,
            'kpi': {'ejecuciones':terminadas, 'exitos':exitos, 'incidencias':terminadas-exitos,
                'tasa':round(100*exitos/terminadas,1) if terminadas else None, **medidas},
            'servicios':list(qs.values('tipo_sesion').annotate(total=Count('pk')).order_by('-total')),
            'organizaciones':list(qs.values('es_tectum','es_beneficios').annotate(total=Count('pk')).order_by('-total')),
            'profesionales':list(qs.filter(modalidad='Presencial').values('psicologo__usuario__first_name','registro_llegada').annotate(total=Count('pk')).order_by('-total')[:20])})
    except (ValueError, TypeError):
        return JsonResponse({'mensaje':'Revise los filtros de fecha e identificador.'},status=400)


@solo_admin
def historial(request, cita_id):
    cita = get_object_or_404(Cita.objects.select_related('paciente', 'psicologo__usuario').prefetch_related(
        Prefetch('trabajos_proceso', queryset=TrabajoProceso.objects.defer('resultado'))), pk=cita_id)
    eventos = EjecucionProceso.objects.filter(trabajo__cita_id=cita_id).select_related('responsable','trabajo').order_by('-inicio')[:100]
    return JsonResponse({'fila':fila(cita), 'eventos':[{'etapa': ETIQUETAS[e.trabajo.etapa], 'estado':LABELS[e.estado], 'error':e.error,
        'inicio':e.inicio.isoformat(), 'duracion_ms':e.duracion_ms,
        'responsable':e.responsable.get_username() if e.responsable else 'Programación automática'} for e in eventos]})


@solo_admin
@require_POST
def cambiar_modo(request):
    modo = request.POST.get('modo')
    if modo not in ('manual', 'automatico'):
        return JsonResponse({'mensaje': 'Modo inválido.'}, status=400)
    if not motor.activo():
        return JsonResponse({'mensaje': 'El motor está desactivado.'}, status=409)
    ControlOrquestador.objects.update_or_create(clave='modo', defaults={'token': modo})
    return JsonResponse({'modo': modo})


@solo_admin
@require_POST
def solicitar(request, cita_id=None):
    if not motor.activo():
        return JsonResponse({'mensaje':'El motor está desactivado.'}, status=409)
    if cita_id is None:
        return JsonResponse({'mensaje':'Selecciona una cita individual en el panel.'}, status=400)
    etapa = request.POST.get('tipo') or 'completo'
    etapa = {'reporte':'pdf_psicologo', 'correo':'correo_psicologo',
             'paciente':'correo_paciente', 'psicologo':'correo_psicologo'}.get(etapa, etapa)
    if request.POST.get('solo_generar') in ('true','True','1'):
        etapa = 'pdf_psicologo'
    if etapa not in ('completo', *motor.ETAPAS):
        return JsonResponse({'mensaje':'Etapa inválida.'},status=400)
    cita = get_object_or_404(Cita, pk=cita_id)
    if not motor.elegible(cita):
        return JsonResponse({'mensaje':'La cita debe estar confirmada o completada y sin inasistencia registrada.'}, status=409)
    if not motor.solicitar(cita, request.user, etapa):
        return JsonResponse({'mensaje':'La cita ya tiene una etapa en ejecución.'}, status=409)
    return JsonResponse({'status':'success', 'mensaje':'Solicitud registrada para el próximo ciclo del maestro.'}, status=202)
