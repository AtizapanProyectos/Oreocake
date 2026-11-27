"""Reserva atómica de cuatro citas HOPE. PayPal conserva la captura del navegador.

No usa credenciales PayPal ni consulta el estado de cargos externos. Los resultados
inciertos conservan los horarios y requieren retomar la confirmación o revisión humana.
"""
import json
from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.http import JsonResponse
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from .models import Cita, CuestionarioRegistro, EventoAuditoria, PerfilPsicologo, PlanMensual, TratamientoPaciente


class ErrorPlan(Exception):
    def __init__(self, mensaje, status=400):
        self.mensaje, self.status = mensaje, status


def _error(error):
    return JsonResponse({'status': 'error', 'message': error.mensaje}, status=error.status)


def _paciente(request, datos):
    from .views import _convenio_cita

    if not request.user.is_authenticated:
        raise ErrorPlan('Debes iniciar sesión.', 401)
    paciente_id = datos.get('paciente_id')
    if paciente_id and not request.user.is_superuser:
        raise ErrorPlan('No puedes agendar por otro paciente.', 403)
    if not request.user.is_superuser and _convenio_cita(request) != 'hope':
        raise ErrorPlan('El Plan Mensual está disponible en HOPE.', 403)
    try:
        paciente = User.objects.select_related('perfil').get(pk=paciente_id or request.user.pk)
    except (User.DoesNotExist, ValueError, TypeError, OverflowError):
        raise ErrorPlan('Paciente no encontrado.', 404)
    if not hasattr(paciente, 'perfil'):
        raise ErrorPlan('El paciente necesita completar su perfil.')
    if paciente.perfil.es_tectum or paciente.perfil.es_beneficios:
        raise ErrorPlan('El Plan Mensual está disponible para pacientes HOPE.', 403)
    return paciente


def _configuracion(datos):
    from .views import TIPOS_SESION_VALIDOS, MODALIDADES_CITA_VALIDAS, calcular_precio_sesion

    servicio = datos.get('tipo_servicio', 'individual')
    modalidad = datos.get('modalidad', 'En línea')
    if servicio not in TIPOS_SESION_VALIDOS or modalidad not in MODALIDADES_CITA_VALIDAS:
        raise ErrorPlan('Selecciona un servicio y una modalidad válidos.')
    integrantes = datos.get('integrantes_familia')
    if servicio == 'familiar':
        try:
            integrantes = int(integrantes)
            if not 2 <= integrantes <= 32767:
                raise ValueError
        except (TypeError, ValueError):
            raise ErrorPlan('Indica al menos dos integrantes para terapia familiar.')
    precio = calcular_precio_sesion(servicio, integrantes)
    monto = Decimal(str(precio['total'])) * 4
    return servicio, modalidad, precio['integrantes_familia'], monto


def _fechas(datos):
    from .views import parsear_hora_flexible

    try:
        seleccion = json.loads(datos.get('citas', '[]'))
        if not isinstance(seleccion, list) or len(seleccion) != 4:
            raise ValueError
        fechas = sorted((date.fromisoformat(item['fecha']), parsear_hora_flexible(item['hora'])) for item in seleccion)
    except (ValueError, TypeError, KeyError, AttributeError):
        raise ErrorPlan('Selecciona exactamente cuatro fechas y horas válidas.')
    if len({fecha for fecha, _ in fechas}) != 4:
        raise ErrorPlan('Las cuatro sesiones deben estar en fechas distintas.')
    if (fechas[-1][0] - fechas[0][0]).days > 29:
        raise ErrorPlan('Las cuatro citas deben caber en 30 días desde la primera sesión.')
    ahora = timezone.localtime()
    for fecha, hora in fechas:
        inicio = timezone.make_aware(datetime.combine(fecha, hora))
        if inicio < ahora + timedelta(hours=2) or fecha > ahora.date() + timedelta(days=90):
            raise ErrorPlan('Elige horarios con dos horas de anticipación dentro de los próximos 90 días.')
    return fechas


def _reglas_paciente(request, paciente, modalidad):
    from .views import _tiene_consentimiento, MENSAJE_PRESENCIAL_CONTACTA

    if request.user.is_superuser:
        return
    if not _tiene_consentimiento(paciente):
        raise ErrorPlan('Firma el consentimiento informado antes de reservar tu plan.')
    if modalidad == 'Presencial' and _presencial_requiere_contacto(paciente):
        raise ErrorPlan(MENSAJE_PRESENCIAL_CONTACTA)


def _sesiones_atendidas(paciente):
    return Cita.objects.filter(paciente=paciente).filter(
        Q(estado='Completada') | Q(registro_llegada='Vino', estado='Confirmada'),
    ).filter(modalidad__in=['Presencial', 'En línea']).order_by('-fecha', '-hora', '-pk')


def modalidad_predeterminada(paciente, servicio='individual'):
    atendidas = _sesiones_atendidas(paciente)
    return (atendidas.filter(tipo_sesion=servicio).values_list('modalidad', flat=True).first()
            or atendidas.values_list('modalidad', flat=True).first() or 'En línea')


def _presencial_requiere_contacto(paciente):
    # Conservar la restricción histórica para quien nunca ha asistido presencial.
    # Un paciente con historial mixto sí debe poder continuar sus sesiones presenciales.
    return (Cita.objects.filter(paciente=paciente, modalidad='En línea').exists()
            and not _sesiones_atendidas(paciente).filter(modalidad='Presencial').exists())


def _asignado(paciente, servicio):
    from .views import _psicologo_asignado_servicio

    return _psicologo_asignado_servicio(paciente, servicio)


def _terapeutas(paciente, servicio, modalidad):
    from .views import CAPACIDAD_POR_TIPO_SESION, COMPATIBILIDAD_MODALIDAD_PSICOLOGO

    capacidad = CAPACIDAD_POR_TIPO_SESION[servicio]
    doctores = PerfilPsicologo.objects.filter(
        modalidad_atencion__in=COMPATIBILIDAD_MODALIDAD_PSICOLOGO[modalidad],
        **{capacidad: True},
    )
    asignado = _asignado(paciente, servicio)
    if asignado:
        # No sustituir silenciosamente al terapeuta asignado cuando no es compatible
        # o no tiene disponibilidad. La misma regla se usa al reservar las cuatro citas.
        return doctores.filter(pk=asignado.pk)
    doctores = doctores.filter(esta_activo=True)
    cuestionario = CuestionarioRegistro.objects.filter(paciente=paciente, flujo_elegido=servicio).order_by('-fecha_completado').first()
    if not cuestionario:
        cuestionario = CuestionarioRegistro.objects.filter(paciente=paciente).order_by('-fecha_completado').first()
    respuestas = cuestionario.respuestas if cuestionario else {}
    if isinstance(respuestas, str):
        try:
            respuestas = json.loads(respuestas)
        except ValueError:
            respuestas = {}
    preferencia = respuestas.get('preferencia_terapeuta', '') if isinstance(respuestas, dict) else ''
    genero = 'Mujer' if 'Mujer' in preferencia else 'Hombre' if 'Hombre' in preferencia else None
    # Ordenar por preferencia, luego carga, conservando alternativas si no hay
    # cuatro fechas compatibles con el género preferido.
    from django.db.models import Case, IntegerField, Value, When
    return doctores.annotate(
        preferido=Case(When(genero=genero, then=Value(0)), default=Value(1), output_field=IntegerField()),
        carga=Count('citas_agendadas', filter=~Q(citas_agendadas__estado='Cancelada')),
    ).order_by('preferido', 'carga', 'pk')


def _serializar(plan):
    return {
        'status': 'success', 'plan_id': plan.pk, 'clave': str(plan.clave),
        'estado': plan.estado, 'monto': str(plan.monto), 'origen': plan.origen,
        'tipo_servicio': plan.tipo_servicio, 'modalidad': plan.modalidad,
        'integrantes_familia': plan.integrantes_familia, 'vence': plan.vence.isoformat(),
        'citas': [dict(
            id=c.pk, fecha=c.fecha.isoformat(), hora=c.hora.strftime('%H:%M'),
            terapeuta=c.psicologo.usuario.get_full_name() or c.psicologo.usuario.username if c.psicologo else '',
            pase_url=reverse('descargar_pase_admision', args=[c.pk]) if c.modalidad == 'Presencial' and plan.estado == PlanMensual.Estado.CONFIRMADO else None,
        ) for c in plan.citas.select_related('psicologo__usuario').order_by('fecha', 'hora')],
    }


def _liberar(plan, estado):
    plan.citas.filter(estado='Pendiente').update(estado='Cancelada', psicologo=None)
    plan.estado = estado
    plan.save(update_fields=['estado'])


def liberar_planes_vencidos():
    """Nunca libera una captura incierta, aunque hayan pasado los 15 minutos."""
    with transaction.atomic():
        for plan in PlanMensual.objects.select_for_update().filter(
            estado=PlanMensual.Estado.RESERVADO, vence__lte=timezone.now(),
        ).order_by('pk'):
            _liberar(plan, PlanMensual.Estado.EXPIRADO)


@require_GET
def disponibilidad(request):
    from .views import obtener_slots_psicologo

    try:
        paciente = _paciente(request, request.GET)
        servicio, modalidad, integrantes, monto = _configuracion(request.GET)
        if not request.user.is_superuser and modalidad == 'Presencial' and _presencial_requiere_contacto(paciente):
            from .views import MENSAJE_PRESENCIAL_CONTACTA
            raise ErrorPlan(MENSAJE_PRESENCIAL_CONTACTA)
        liberar_planes_vencidos()
        hoy = timezone.localdate()
        limite = hoy + timedelta(days=90)
        ahora = timezone.localtime() + timedelta(hours=2)
        ocupadas = set(Cita.objects.filter(
            paciente=paciente, estado__in=['Pendiente', 'Confirmada'], fecha__gte=hoy,
        ).values_list('fecha', 'hora'))
        doctores = []
        for doctor in _terapeutas(paciente, servicio, modalidad).select_related('usuario'):
            dias = obtener_slots_psicologo(doctor, hoy, limite, tipo_sesion=servicio, modalidad=modalidad)
            # La unicidad histórica de Cita incluye filas canceladas que aún
            # conservan psicólogo. No ofrecer un slot que reservar() rechazaría.
            no_reutilizables = set(Cita.objects.filter(
                psicologo=doctor, fecha__gte=hoy, fecha__lte=limite,
            ).values_list('fecha', 'hora'))
            from .views import parsear_hora_flexible
            dias = {fecha: [hora for hora in horas if (
                timezone.make_aware(datetime.combine(date.fromisoformat(fecha), parsear_hora_flexible(hora))) >= ahora
                and (date.fromisoformat(fecha), parsear_hora_flexible(hora)) not in ocupadas
                and (date.fromisoformat(fecha), parsear_hora_flexible(hora)) not in no_reutilizables
            )] for fecha, horas in dias.items()}
            dias = {fecha: horas for fecha, horas in dias.items() if horas}
            if dias:
                doctores.append({'id': doctor.pk, 'dias': dias})
        asignado = _asignado(paciente, servicio)
        return JsonResponse({
            'status': 'success', 'terapeutas': doctores, 'monto': str(monto), 'por_sesion': str(monto / 4),
            'hoy': hoy.isoformat(), 'fecha_limite': limite.isoformat(),
            'modalidad_predeterminada': modalidad_predeterminada(paciente, servicio),
            'psicologo_asignado': {'id': asignado.pk, 'nombre': asignado.usuario.get_full_name() or asignado.usuario.username} if asignado else None,
        })
    except ErrorPlan as error:
        return _error(error)


def _confirmar(plan, request):
    from .views import _registrar_tratamiento_cita, _notificar_cita_paciente

    citas = list(plan.citas.select_related('paciente', 'psicologo__usuario').order_by('fecha'))
    if len(citas) != 4 or any(c.estado != 'Pendiente' or not c.psicologo_id for c in citas):
        raise ErrorPlan('El plan requiere revisión del personal antes de confirmarse.', 409)
    plan.citas.update(estado='Confirmada')
    plan.estado = PlanMensual.Estado.CONFIRMADO
    plan.save(update_fields=['estado', 'referencia'])
    _registrar_tratamiento_cita(citas[0])
    for cita in citas:
        cita.estado = 'Confirmada'
        transaction.on_commit(lambda cita=cita: _notificar_cita_paciente(cita, request), robust=True)


@require_POST
def reservar(request):
    from .views import obtener_slots_psicologo_para_dia, parsear_hora_flexible, _generar_codigo_pase

    try:
        paciente = _paciente(request, request.POST)
        servicio, modalidad, integrantes, monto = _configuracion(request.POST)
        _reglas_paciente(request, paciente, modalidad)
        try:
            clave = UUID(request.POST.get('clave', ''))
        except (ValueError, TypeError, AttributeError):
            raise ErrorPlan('La solicitud necesita una clave válida.')
        # Liberar antes de tomar bloqueos de paciente/terapeuta (orden consistente).
        liberar_planes_vencidos()
        with transaction.atomic():
            User.objects.select_for_update().get(pk=paciente.pk)
            existente = PlanMensual.objects.select_for_update().filter(clave=clave).first()
            if existente:
                if existente.paciente_id != paciente.pk or existente.creado_por_id != request.user.pk:
                    raise ErrorPlan('No tienes acceso a este plan.', 403)
                try:
                    pedido = sorted((item['fecha'], parsear_hora_flexible(item['hora']).strftime('%H:%M')) for item in json.loads(request.POST.get('citas', '[]')))
                except (TypeError, ValueError, KeyError, AttributeError):
                    raise ErrorPlan('La solicitud de reserva no es válida.')
                guardado = [(c.fecha.isoformat(), c.hora.strftime('%H:%M')) for c in existente.citas.order_by('fecha', 'hora')]
                if (existente.tipo_servicio, existente.modalidad, existente.integrantes_familia, guardado) != (servicio, modalidad, integrantes, pedido):
                    raise ErrorPlan('Esta solicitud ya corresponde a otra selección. Consulta tu plan antes de continuar.', 409)
                return JsonResponse(_serializar(existente))
            # Un reintento con otra clave tampoco debe iniciar un segundo cargo.
            activo = PlanMensual.objects.filter(paciente=paciente, estado__in=['reservado', 'procesando']).first()
            if activo:
                raise ErrorPlan('Ya tienes un plan pendiente. Retómalo antes de iniciar otro.', 409)
            fechas = _fechas(request.POST)
            if any(Cita.objects.filter(paciente=paciente, fecha=f, hora=h, estado__in=['Confirmada', 'Pendiente']).exists() for f, h in fechas):
                raise ErrorPlan('Ya tienes una cita o reserva en uno de esos horarios.', 409)
            doctor = None
            asignado = _asignado(paciente, servicio)
            candidatos = list(_terapeutas(paciente, servicio, modalidad).values_list('pk', flat=True))
            # El orden de los bloqueos no depende de la carga ni de preferencias:
            # dos pacientes que compiten por varios doctores no se bloquean al revés.
            bloqueados = {doc.pk: doc for doc in PerfilPsicologo.objects.select_for_update().filter(pk__in=candidatos).order_by('pk')}
            for candidato_id in candidatos:
                bloqueado = bloqueados[candidato_id]
                if not bloqueado.esta_activo and (asignado is None or asignado.pk != bloqueado.pk):
                    continue
                if all(h.strftime('%I:%M %p') in obtener_slots_psicologo_para_dia(bloqueado, f, servicio, modalidad) for f, h in fechas):
                    # La restricción única histórica también cuenta canceladas que
                    # todavía conservan terapeuta; no reutilizar esas filas.
                    if not any(Cita.objects.filter(psicologo=bloqueado, fecha=f, hora=h).exists() for f, h in fechas):
                        doctor = bloqueado
                        break
            if not doctor:
                raise ErrorPlan('No hay un mismo terapeuta disponible para las cuatro fechas. Ajusta tus horarios.', 409)
            plan = PlanMensual.objects.create(
                clave=clave, paciente=paciente, creado_por=request.user, tipo_servicio=servicio,
                modalidad=modalidad, integrantes_familia=integrantes, monto=monto,
                origen='admin' if request.user.is_superuser else 'paypal', vence=timezone.now() + timedelta(minutes=15),
            )
            for fecha, hora in fechas:
                Cita.objects.create(
                    plan_mensual=plan, paciente=paciente, psicologo=doctor, fecha=fecha, hora=hora,
                    tipo_sesion=servicio, modalidad=modalidad, integrantes_familia=integrantes,
                    estado='Pendiente', motivo='Plan Mensual · Agendada por administración' if plan.origen == 'admin' else 'Plan Mensual',
                    estado_animo=request.POST.get('animo', 'No especificó'), correo_confirmacion_enviado=False,
                    codigo_pase=_generar_codigo_pase() if modalidad == 'Presencial' else None,
                )
            if plan.origen == 'admin':
                _confirmar(plan, request)
                EventoAuditoria.objects.create(usuario=request.user, accion='plan_mensual_admin', detalles={'plan_id': plan.pk, 'paciente_id': paciente.pk, 'sin_cobro': True})
            return JsonResponse(_serializar(plan))
    except ErrorPlan as error:
        return _error(error)
    except IntegrityError:
        return _error(ErrorPlan('Un horario acaba de ocuparse o la solicitud ya existe. Consulta tu plan antes de reintentar.', 409))


def _plan_accesible(request, datos, bloquear=False):
    paciente = _paciente(request, datos)
    if bloquear:
        User.objects.select_for_update().get(pk=paciente.pk)
    qs = PlanMensual.objects.select_for_update() if bloquear else PlanMensual.objects
    try:
        plan = qs.get(pk=datos.get('plan_id'), paciente=paciente)
    except (PlanMensual.DoesNotExist, ValueError, TypeError, OverflowError):
        raise ErrorPlan('Plan no encontrado.', 404)
    return plan


@require_GET
def estado(request):
    try:
        paciente = _paciente(request, request.GET)
        liberar_planes_vencidos()
        if request.GET.get('plan_id'):
            return JsonResponse(_serializar(_plan_accesible(request, request.GET)))
        planes = PlanMensual.objects.filter(paciente=paciente, estado__in=['reservado', 'procesando']).order_by('-creado')
        return JsonResponse({
            'status': 'success', 'planes': [_serializar(plan) for plan in planes],
            'modalidad_predeterminada': modalidad_predeterminada(paciente, request.GET.get('tipo_servicio', 'individual')),
        })
    except ErrorPlan as error:
        return _error(error)


@require_POST
def actualizar(request):
    try:
        with transaction.atomic():
            plan = _plan_accesible(request, request.POST, bloquear=True)
            accion = request.POST.get('accion')
            if plan.estado == PlanMensual.Estado.CONFIRMADO:
                return JsonResponse(_serializar(plan))
            if plan.estado in [PlanMensual.Estado.CANCELADO, PlanMensual.Estado.EXPIRADO]:
                if accion == 'cancelar':
                    return JsonResponse(_serializar(plan))
                raise ErrorPlan('Esta reserva ya no está activa. Elige nuevamente tus fechas.', 409)
            if plan.estado == PlanMensual.Estado.RESERVADO and plan.vence <= timezone.now():
                _liberar(plan, PlanMensual.Estado.EXPIRADO)
                return JsonResponse(_serializar(plan))
            revision = request.user.is_superuser and request.POST.get('revisado') == '1'
            if accion == 'cancelar':
                if plan.estado == PlanMensual.Estado.PROCESANDO and not revision:
                    raise ErrorPlan('Hay un pago pendiente de resolver. Contacta al personal antes de cancelar.', 409)
                _liberar(plan, PlanMensual.Estado.CANCELADO)
            elif accion == 'capturar':
                plan.estado = PlanMensual.Estado.PROCESANDO
                plan.save(update_fields=['estado'])
            elif accion == 'confirmar':
                referencia = request.POST.get('referencia', '').strip()
                if not revision and (plan.estado != PlanMensual.Estado.PROCESANDO or request.POST.get('resultado') != 'COMPLETED'):
                    raise ErrorPlan('El pago necesita confirmación o revisión del personal.', 409)
                if not 1 <= len(referencia) <= 64 or not referencia.isascii() or not referencia.isalnum():
                    raise ErrorPlan('Conserva la referencia del pago para confirmar el plan.')
                plan.referencia = referencia
                _confirmar(plan, request)
            else:
                raise ErrorPlan('Acción no válida.')
            if revision and accion in ['confirmar', 'cancelar']:
                EventoAuditoria.objects.create(
                    usuario=request.user, accion='plan_mensual_revision_' + accion,
                    detalles={'plan_id': plan.pk, 'paciente_id': plan.paciente_id, 'referencia': plan.referencia, 'monto': str(plan.monto)},
                )
            return JsonResponse(_serializar(plan))
    except ErrorPlan as error:
        return _error(error)
    except IntegrityError:
        return _error(ErrorPlan('Esta referencia ya está asociada a otro plan. Contacta al personal.', 409))
