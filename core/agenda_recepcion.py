"""Agenda operativa; no interpreta el cierre de admisiones como bloqueo clínico."""
import hashlib
import json
from datetime import date, datetime, timedelta
from functools import wraps
from uuid import UUID

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import IntegrityError, OperationalError, transaction
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST

from .models import (BloqueoAgendaPsicologo, Cita, CuestionarioRegistro, EventoAuditoria,
                     OperacionAgenda, PagoCitaPayPal, PerfilPsicologo, PlanMensual,
                     SincronizacionAgenda, TratamientoPaciente, UsuarioPerfil,
                     EsquemaHorarioPsicologo, DiaFestivo, DiaLibrePsicologo)

SERVICIOS = ('individual', 'pareja', 'familiar')
MODALIDADES = ('Presencial', 'En línea')


class ErrorAgenda(Exception):
    def __init__(self, mensaje, codigo=400):
        self.mensaje, self.codigo = mensaje, codigo


def autorizado(funcion):
    @wraps(funcion)
    def protegida(request, *args, **kwargs):
        if not request.user.is_authenticated or not request.user.is_active or not request.user.is_superuser:
            return JsonResponse({'status': 'error', 'message': 'Acceso exclusivo de administración.'}, status=403)
        try:
            respuesta = funcion(request, *args, **kwargs)
            respuesta['Cache-Control'] = 'private, no-store'
            return respuesta
        except ErrorAgenda as error:
            return JsonResponse({'status': 'error', 'message': error.mensaje}, status=error.codigo)
        except (ValueError, TypeError, KeyError, ValidationError):
            return JsonResponse({'status': 'error', 'message': 'Revisa los datos y vuelve a intentar.'}, status=400)
        except (User.DoesNotExist, Cita.DoesNotExist, PerfilPsicologo.DoesNotExist, BloqueoAgendaPsicologo.DoesNotExist, SincronizacionAgenda.DoesNotExist):
            return JsonResponse({'status': 'error', 'message': 'El registro ya no existe.'}, status=404)
        except IntegrityError:
            return JsonResponse({'status': 'error', 'message': 'Los datos cambiaron o el horario acaba de ocuparse. Actualiza la agenda.'}, status=409)
        except OperationalError as error:
            if error.args and error.args[0] in (1205, 1213):
                return JsonResponse({'status': 'error', 'message': 'Otra operación está utilizando esta agenda. Actualiza e intenta nuevamente.'}, status=409)
            raise
    return protegida


def nombre(usuario):
    return usuario.get_full_name() or usuario.email or usuario.username


def inicio(cita):
    return timezone.make_aware(datetime.combine(cita.fecha, cita.hora))


def ficha(cita):
    return {'id': cita.pk, 'paciente_id': cita.paciente_id, 'psicologo_id': cita.psicologo_id,
            'fecha': cita.fecha.isoformat(), 'hora': cita.hora.strftime('%H:%M'),
            'servicio': cita.tipo_sesion, 'modalidad': cita.modalidad, 'estado': cita.estado,
            'plan_id': cita.plan_mensual_id}


def version(datos):
    return hashlib.sha256(json.dumps(datos, sort_keys=True, default=str).encode()).hexdigest()


def comprobar_version(enviada, datos):
    if enviada != version(datos):
        raise ErrorAgenda('Los datos cambiaron. Actualiza y revisa el resumen antes de confirmar.', 409)


def rango(datos, maximo=31):
    desde = date.fromisoformat(datos.get('desde') or timezone.localdate().isoformat())
    hasta = date.fromisoformat(datos.get('hasta') or desde.isoformat())
    if not 0 <= (hasta - desde).days < maximo:
        raise ErrorAgenda(f'Selecciona un intervalo de hasta {maximo} días.')
    return desde, hasta


def vigentes(qs):
    """Las reservas sin acreditación de vencimiento permanecen ocupadas."""
    ahora = timezone.now()
    expiradas = PagoCitaPayPal.objects.filter(vence__lte=ahora, captura__isnull=True,
                                             captura_iniciada=False).values('cita_id')
    planes = PlanMensual.objects.filter(Q(estado__in=['cancelado', 'expirado']) |
                                       Q(estado='reservado', vence__lte=ahora)).values('pk')
    return qs.exclude(estado__in=['Cancelada', 'No asistió']).exclude(
        Q(estado='Pendiente') & (Q(pk__in=expiradas) | Q(plan_mensual_id__in=planes)))


def futuras(paciente, servicio):
    ahora = timezone.localtime()
    return vigentes(Cita.objects.filter(paciente=paciente, tipo_sesion=servicio,
                    estado__in=['Confirmada', 'Pendiente']).filter(
                        Q(fecha__gt=ahora.date()) | Q(fecha=ahora.date(), hora__gt=ahora.time().replace(tzinfo=None)))).order_by('pk')


def slots(doctor, desde, hasta, servicio, modalidad, excluir=(), paciente=None):
    from .views import obtener_slots_psicologo, parsear_hora_flexible
    contexto = getattr(doctor, '_agenda_contexto', None)
    if contexto is not None:
        resultado = obtener_slots_psicologo(doctor, desde, hasta, servicio, modalidad, excluir_citas=excluir, paciente=paciente)
        retenidas = {(str(c.fecha), c.hora.strftime('%H:%M')) for c in contexto['citas'] if c.pk not in excluir}
        return {f: [parsear_hora_flexible(h).strftime('%H:%M') for h in horas
                    if (f, parsear_hora_flexible(h).strftime('%H:%M')) not in retenidas] for f, horas in resultado.items()}
    # Los vencimientos se interpretan en lectura; nunca se expira una reserva desde GET.
    todas = Cita.objects.filter(psicologo=doctor, fecha__range=(desde, hasta))
    expiradas = set(todas.values_list('pk', flat=True)) - set(vigentes(todas).values_list('pk', flat=True))
    resultado = obtener_slots_psicologo(doctor, desde, hasta, servicio, modalidad,
                                      excluir_citas=set(excluir) | expiradas, paciente=paciente)
    # La restricción histórica de unicidad incluye canceladas que conservan terapeuta.
    retenidas = {(str(f), h.strftime('%H:%M')) for f, h in todas.exclude(pk__in=excluir).values_list('fecha', 'hora')}
    return {f: [parsear_hora_flexible(h).strftime('%H:%M') for h in horas
                if (f, parsear_hora_flexible(h).strftime('%H:%M')) not in retenidas] for f, horas in resultado.items()}


def carga(doctor, dia, propuestas=(), excluir=()):
    lunes = dia - timedelta(days=dia.weekday())
    domingo = lunes + timedelta(days=6)
    contexto = getattr(doctor, '_agenda_contexto', None)
    citas = [c for c in contexto['citas'] if lunes <= c.fecha <= domingo and c.estado not in ('Cancelada', 'No asistió')] if contexto is not None else list(vigentes(Cita.objects.filter(psicologo=doctor, fecha__range=(lunes, domingo))))
    capacidad = set()
    for servicio in SERVICIOS:
        for modalidad in MODALIDADES:
            for f, horas in slots(doctor, lunes, domingo, servicio, modalidad, [c.pk for c in citas]).items():
                capacidad.update((f, h) for h in horas)
    ocupadas = {(c.fecha.isoformat(), c.hora.strftime('%H:%M')) for c in citas if c.pk not in excluir}
    ocupadas.update((f, h) for f, h in propuestas if lunes <= date.fromisoformat(f) <= domingo)
    porcentaje = round(100 * len(ocupadas) / len(capacidad), 1) if capacidad else None
    return {'semana': lunes.isoformat(), 'ocupados': len(ocupadas), 'capacidad': len(capacidad),
            'porcentaje': porcentaje, 'alerta': porcentaje is not None and porcentaje >= 85}


def config(doctor):
    return {'id': doctor.pk, 'esta_activo': doctor.esta_activo,
            'modalidad_atencion': doctor.modalidad_atencion}


def bloque_ficha(bloque):
    return {'id': bloque.pk, 'psicologo_id': bloque.psicologo_id, 'fecha': str(bloque.fecha),
            'hora_inicio': bloque.hora_inicio.strftime('%H:%M'), 'hora_fin': bloque.hora_fin.strftime('%H:%M'),
            'motivo': bloque.motivo, 'activo': bloque.activo}


def precargar(doctores, desde, hasta):
    """Datos de la petición, nunca una caché global de disponibilidad."""
    from .views import _calcular_bloqueos_covision
    desde -= timedelta(days=desde.weekday())
    hasta += timedelta(days=6-hasta.weekday())
    ids = [d.pk for d in doctores]
    esquemas = list(EsquemaHorarioPsicologo.objects.filter(psicologo_id__in=ids, activo=True,
        fecha_inicio__lte=hasta, fecha_fin__gte=desde).prefetch_related('dias_personalizados').order_by('fecha_inicio'))
    festivos = set(DiaFestivo.objects.filter(fecha__range=(desde,hasta)).values_list('fecha', flat=True))
    dias = list(DiaLibrePsicologo.objects.filter(psicologo_id__in=ids, fecha__range=(desde,hasta)))
    bloqueos = list(BloqueoAgendaPsicologo.objects.filter(psicologo_id__in=ids, fecha__range=(desde,hasta)))
    citas = list(Cita.objects.filter(psicologo_id__in=ids, fecha__range=(desde,hasta)).select_related('paciente__perfil'))
    syncs = {t.cita_id:t for t in SincronizacionAgenda.objects.filter(cita_id__in=[c.pk for c in citas]).order_by('pk')}
    covision = _calcular_bloqueos_covision(desde,hasta)
    for doctor in doctores:
        doctor._esquemas_rango = [e for e in esquemas if e.psicologo_id == doctor.pk]
        doctor._agenda_contexto = {'festivos':festivos, 'dias_libres':{d.fecha for d in dias if d.psicologo_id==doctor.pk},
            'bloqueos':[(b.fecha,b.hora_inicio,b.hora_fin) for b in bloqueos if b.psicologo_id==doctor.pk and b.activo],
            'covision':covision, 'citas':[c for c in citas if c.psicologo_id==doctor.pk]}
        doctor._agenda_bloqueos = [b for b in bloqueos if b.psicologo_id==doctor.pk]
    return syncs


@autorizado
@require_GET
@ensure_csrf_cookie
def panel(request):
    return render(request, 'recepcion_agenda.html', {'usuario_nombre': request.user.get_short_name() or request.user.username})


@autorizado
@require_GET
def datos(request):
    desde, hasta = rango(request.GET)
    servicio = request.GET.get('servicio', 'individual')
    if servicio not in SERVICIOS:
        raise ErrorAgenda('Servicio inválido.')
    doctores = PerfilPsicologo.objects.select_related('usuario').order_by('usuario__first_name', 'pk')
    if request.GET.get('psicologo'):
        doctores = doctores.filter(pk=int(request.GET['psicologo']))
    doctores = list(doctores)
    syncs = precargar(doctores, desde, hasta)
    tratamientos = list(TratamientoPaciente.objects.values('paciente_id','tipo_servicio','psicologo_asignado_id','activo'))
    individuales = {t['paciente_id'] for t in tratamientos if t['tipo_servicio']=='individual'}
    heredados = list(UsuarioPerfil.objects.filter(psicologo_asignado_id__in=[d.pk for d in doctores]).exclude(usuario_id__in=individuales).values_list('usuario_id','psicologo_asignado_id'))
    salida = []
    for doctor in doctores:
        citas = [c for c in doctor._agenda_contexto['citas'] if desde<=c.fecha<=hasta
                 and (request.GET.get('canceladas')=='1' or c.estado not in ('Cancelada','No asistió'))
                 and (request.GET.get('modalidad') not in MODALIDADES or c.modalidad==request.GET['modalidad'])]
        detalle = []
        for cita in sorted(citas, key=lambda c:(c.fecha,c.hora,c.pk)):
            f = ficha(cita)
            perfil = getattr(cita.paciente, 'perfil', None)
            f.update(paciente=nombre(cita.paciente), email=cita.paciente.email,
                     telefono=getattr(perfil, 'telefono', '') or '',
                     emergencia=getattr(perfil, 'telefono_emergencia', '') or '', version=version(ficha(cita)))
            sync = syncs.get(cita.pk)
            f['sincronizacion'] = {'id': sync.pk, 'estado': sync.estado, 'mensaje': sync.mensaje} if sync else None
            detalle.append(f)
        c = config(doctor)
        libres = {m: slots(doctor, desde, hasta, servicio, m) for m in MODALIDADES
                  if request.GET.get('modalidad') not in MODALIDADES or m == request.GET['modalidad']}
        salida.append({**c, 'nombre': nombre(doctor.usuario), 'genero': doctor.genero,
                       'version': version(c), 'citas': detalle, 'libres': libres,
                       'carga': carga(doctor, desde),
                       'pacientes_activos': len({t['paciente_id'] for t in tratamientos if t['activo'] and t['psicologo_asignado_id']==doctor.pk} | {u for u,d in heredados if d==doctor.pk and u}),
                       'bloqueos': [{**bloque_ficha(b), 'version': version(bloque_ficha(b))}
                                    for b in doctor._agenda_bloqueos if desde<=b.fecha<=hasta]})
    return JsonResponse({'status': 'success', 'psicologos': salida, 'desde': str(desde), 'hasta': str(hasta)})


def preferencias(paciente, servicio):
    cuestionario = CuestionarioRegistro.objects.filter(paciente=paciente, flujo_elegido=servicio).order_by('-fecha_completado').first()
    if cuestionario is None:
        cuestionario = CuestionarioRegistro.objects.filter(paciente=paciente).order_by('-fecha_completado').first()
    respuestas = cuestionario.respuestas if cuestionario and isinstance(cuestionario.respuestas, dict) else {}
    texto = str(respuestas.get('preferencia_terapeuta', ''))
    return 'Mujer' if 'Mujer' in texto else ('Hombre' if 'Hombre' in texto else '')


def compatible(doctor, servicio, modalidades, genero, paciente=None):
    from .views import CAPACIDAD_POR_TIPO_SESION, psicologo_atiende_modalidad
    razones = []
    if not doctor.esta_activo:
        razones.append('No acepta nuevos pacientes ni nuevas transferencias.')
    if not getattr(doctor, CAPACIDAD_POR_TIPO_SESION[servicio]):
        razones.append('No atiende este servicio.')
    if any(not psicologo_atiende_modalidad(doctor, m, paciente=paciente) for m in modalidades):
        razones.append('No atiende todas las modalidades de las citas seleccionadas.')
    diferencia = bool(genero and doctor.genero != genero)
    return razones, diferencia


@autorizado
@require_GET
def candidatos(request):
    from .views import _psicologo_asignado_servicio
    paciente = User.objects.get(pk=int(request.GET['paciente']))
    servicio = request.GET.get('servicio', 'individual')
    if servicio not in SERVICIOS:
        raise ErrorAgenda('Servicio inválido.')
    desde, hasta = rango(request.GET, 91)
    citas = list(futuras(paciente, servicio))
    modalidades = {c.modalidad for c in citas} or {request.GET.get('modalidad', 'En línea')}
    if not modalidades <= set(MODALIDADES):
        raise ErrorAgenda('Modalidad inválida.')
    genero = preferencias(paciente, servicio)
    asignado = _psicologo_asignado_servicio(paciente, servicio)
    foto = {'asignado': asignado.pk if asignado else None, 'citas': [ficha(c) for c in citas]}
    salida = []
    doctores = list(PerfilPsicologo.objects.select_related('usuario').order_by('pk'))
    precargar(doctores, desde, hasta)
    for doctor in doctores:
        if request.GET.get('genero') in ('Hombre', 'Mujer') and doctor.genero != request.GET['genero']:
            continue
        razones, excepcion = compatible(doctor, servicio, modalidades, genero, paciente=paciente)
        libres = {m: slots(doctor, desde, hasta, servicio, m, [c.pk for c in citas], paciente=paciente) for m in modalidades}
        if not any(any(horas for horas in dias.values()) for dias in libres.values()):
            razones.append('Sin horarios libres en el intervalo consultado.')
        salida.append({'id': doctor.pk, 'nombre': nombre(doctor.usuario), 'genero': doctor.genero,
                       'elegible': not razones, 'razones': razones or ['Compatible con servicio, modalidad y admisión.'],
                       'requiere_excepcion_genero': excepcion, 'libres': libres,
                       'version': version(config(doctor)), 'carga': carga(doctor, desde)})
    return JsonResponse({'status': 'success', 'version': version(foto), 'paciente': nombre(paciente),
                         'asignado': foto['asignado'], 'citas': foto['citas'], 'psicologos': salida})


@autorizado
@require_GET
def disponibilidad(request):
    cita = Cita.objects.get(pk=int(request.GET['cita']))
    if not cita.psicologo_id:
        raise ErrorAgenda('La cita no tiene terapeuta.')
    desde, hasta = rango(request.GET, 91)
    doctor = PerfilPsicologo.objects.get(pk=int(request.GET.get('psicologo') or cita.psicologo_id))
    excluir = list(futuras(cita.paciente, cita.tipo_sesion).values_list('pk', flat=True)) if request.GET.get('traslado') == '1' else [cita.pk]
    dias = slots(doctor, desde, hasta, cita.tipo_sesion, cita.modalidad, excluir, paciente=cita.paciente)
    return JsonResponse({'status': 'success', 'version': version(ficha(cita)),
                         'dias': filtrar_intervalos(doctor, cita.paciente, cita.tipo_sesion, dias, excluir)})


def filtrar_intervalos(doctor, paciente, servicio, dias, excluir=(), ocupadas=None):
    """Exige la hora completa dentro de jornada y fuera de comida/otras sesiones."""
    if not dias:
        return dias
    fechas = [date.fromisoformat(f) for f in dias]
    if ocupadas is None:
        ocupadas = list(vigentes(Cita.objects.filter(fecha__range=(min(fechas), max(fechas))).filter(
            Q(psicologo=doctor) | Q(paciente=paciente))).exclude(pk__in=excluir))
    ahora = timezone.localtime().replace(tzinfo=None)
    esquemas = getattr(doctor, '_esquemas_rango', None)
    if esquemas is None:
        esquemas = list(EsquemaHorarioPsicologo.objects.filter(psicologo=doctor, activo=True,
            fecha_inicio__lte=max(fechas), fecha_fin__gte=min(fechas)).prefetch_related('dias_personalizados').order_by('fecha_inicio'))
    resultado = {}
    for f, horas in dias.items():
        dia = date.fromisoformat(f)
        esquema = next((e for e in esquemas if e.fecha_inicio <= dia <= e.fecha_fin), None)
        bloques = esquema.horario_para_dia(dia, servicio) if esquema else []
        permitidas = []
        for hora in horas:
            comienzo = datetime.combine(dia, datetime.strptime(hora, '%H:%M').time())
            fin = comienzo + timedelta(hours=1)
            if comienzo <= ahora:
                continue
            if any(c.fecha == dia and abs((datetime.combine(dia, c.hora)-comienzo).total_seconds()) < 3600 for c in ocupadas):
                continue
            for b in bloques:
                if not datetime.combine(dia, b['hora_inicio']) <= comienzo < fin <= datetime.combine(dia, b['hora_fin']):
                    continue
                comida_ini, comida_fin = b.get('hora_comida_inicio'), b.get('hora_comida_fin')
                if comida_ini and comida_fin and comienzo < datetime.combine(dia, comida_fin) and fin > datetime.combine(dia, comida_ini):
                    continue
                permitidas.append(hora)
                break
        resultado[f] = permitidas
    return resultado


def validar_destino(cita, doctor, fecha, hora, excluir, excepcion):
    from .views import parsear_hora_flexible
    f, h = date.fromisoformat(fecha), parsear_hora_flexible(hora)
    destino = timezone.make_aware(datetime.combine(f, h))
    ahora = timezone.localtime()
    if destino <= ahora:
        raise ErrorAgenda('No se permiten horarios pasados.')
    fuera = destino < ahora + timedelta(hours=2) or f > ahora.date() + timedelta(days=90)
    if fuera and not excepcion:
        raise ErrorAgenda('Este horario requiere confirmar la excepción de plazos.')
    libres = slots(doctor, f, f, cita.tipo_sesion, cita.modalidad, excluir, paciente=cita.paciente)
    libres = filtrar_intervalos(doctor, cita.paciente, cita.tipo_sesion, libres, excluir)
    if h.strftime('%H:%M') not in libres.get(str(f), []):
        raise ErrorAgenda('El horario no está libre o no es compatible con la agenda del terapeuta.', 409)
    # La restricción histórica de unicidad incluye canceladas que aún retengan terapeuta.
    if Cita.objects.filter(psicologo=doctor, fecha=f, hora=h).exclude(pk__in=excluir).exists():
        raise ErrorAgenda('El horario tiene un registro previo y no se puede reutilizar.', 409)
    if vigentes(Cita.objects.filter(paciente_id=cita.paciente_id, fecha=f, hora=h)).exclude(pk__in=excluir).exists():
        raise ErrorAgenda('El paciente ya tiene otra cita o reserva en ese horario.', 409)
    return f, h


def auditar(request, operacion, antes, despues, **entidades):
    EventoAuditoria.objects.create(usuario=request.user, accion='agenda.' + operacion.accion,
        detalles={'operacion': str(operacion.clave), 'motivo': operacion.solicitud['motivo'],
                  'antes': antes, 'despues': despues, **entidades})


def bloquear_pagos(paciente):
    # Mismo orden que captura/conciliación: paciente -> plan/pago -> citas -> terapeutas.
    list(PlanMensual.objects.select_for_update().filter(paciente=paciente).order_by('pk'))
    list(PagoCitaPayPal.objects.select_for_update().filter(cita__paciente=paciente).order_by('pk'))


def bloquear_doctores(ids):
    return {d.pk: d for d in PerfilPsicologo.objects.select_for_update().filter(pk__in=ids).order_by('pk')}


def cambios_citas(request, op, data):
    from .views import _psicologo_asignado_servicio
    es_cambio = op.accion == 'transferir'
    if es_cambio:
        paciente = User.objects.select_for_update().get(pk=int(data['paciente']))
        servicio = data['servicio']
        if servicio not in SERVICIOS:
            raise ErrorAgenda('Servicio inválido.')
        bloquear_pagos(paciente)
        citas = list(futuras(paciente, servicio).select_for_update())
        asignado = _psicologo_asignado_servicio(paciente, servicio)
        comprobar_version(data.get('version'), {'asignado': asignado.pk if asignado else None, 'citas': [ficha(c) for c in citas]})
        docs = bloquear_doctores({c.psicologo_id for c in citas} | {int(data['psicologo'])} | ({asignado.pk} if asignado else set()))
        destino = docs[int(data['psicologo'])]
        comprobar_version(data.get('version_psicologo'), config(destino))
        if asignado and destino.pk == asignado.pk:
            raise ErrorAgenda('Selecciona un psicólogo diferente al actual.')
        razones, genero = compatible(destino, servicio, {c.modalidad for c in citas} or {data.get('modalidad', 'En línea')}, preferencias(paciente, servicio), paciente=paciente)
        if razones:
            raise ErrorAgenda(' '.join(razones), 409)
        if genero and data.get('excepcion_genero') is not True:
            raise ErrorAgenda('Debes confirmar expresamente la excepción de género.')
    else:
        referencia = Cita.objects.get(pk=int(data['cita']))
        paciente = User.objects.select_for_update().get(pk=referencia.paciente_id)
        bloquear_pagos(paciente)
        cita = vigentes(Cita.objects.select_for_update()).get(pk=referencia.pk, estado__in=['Confirmada', 'Pendiente'])
        if inicio(cita) <= timezone.now():
            raise ErrorAgenda('No se modifica una sesión que ya comenzó.')
        comprobar_version(data.get('version'), ficha(cita))
        destino = bloquear_doctores([cita.psicologo_id]).get(cita.psicologo_id)
        if destino is None:
            raise ErrorAgenda('La cita no tiene terapeuta.')
        citas, servicio = [cita], cita.tipo_sesion
        if inicio(cita) - timezone.now() < timedelta(hours=1) and data.get('excepcion_plazo') is not True:
            raise ErrorAgenda('Debes confirmar la excepción: falta menos de una hora para la cita.')
    propuestas = data.get('citas', [])
    if not isinstance(propuestas, list) or len(propuestas) != len(citas):
        raise ErrorAgenda('Debes revisar el destino de todas las citas y reservas futuras.')
    por_id = {int(p['id']): p for p in propuestas}
    if set(por_id) != {c.pk for c in citas}:
        raise ErrorAgenda('La lista de citas cambió. Actualiza y revisa todas las reservas.', 409)
    if propuestas:
        fechas = [date.fromisoformat(p['fecha']) for p in propuestas]
        if max(fechas) - min(fechas) > timedelta(days=730):
            raise ErrorAgenda('Revisa el intervalo de las citas: el traslado no puede abarcar más de dos años.')
        precargar([destino], min(fechas), max(fechas))
    antes = [ficha(c) for c in citas]
    validadas, elegidas = [], set()
    excluir = [c.pk for c in citas]
    for cita in citas:
        p = por_id[cita.pk]
        f, h = validar_destino(cita, destino, p['fecha'], p['hora'], excluir, data.get('excepcion_plazo') is True)
        if (f, h) in elegidas:
            raise ErrorAgenda('Dos citas del traslado usan el mismo horario.', 409)
        elegidas.add((f, h))
        validadas.append((cita, f, h))
    cargas = [carga(destino, f, [(str(a), b.strftime('%H:%M')) for a, b in elegidas], excluir) for f, h in elegidas]
    if any(c['alerta'] for c in cargas) and data.get('acepta_carga') is not True:
        raise ErrorAgenda('La ocupación proyectada alcanza el 85 %. Revisa y confirma la alerta de carga.')
    if es_cambio:
        TratamientoPaciente.objects.update_or_create(paciente=paciente, tipo_servicio=servicio,
            defaults={'psicologo_asignado': destino, 'activo': True})
        if servicio == 'individual':
            UsuarioPerfil.objects.filter(usuario=paciente).update(psicologo_asignado=destino)
    # Evita conflictos temporales de unicidad al permutar horarios del mismo lote.
    Cita.objects.filter(pk__in=excluir).update(psicologo=None)
    for cita, f, h in validadas:
        cita.psicologo, cita.fecha, cita.hora = destino, f, h
        cita.correo_confirmacion_enviado = False
        cita.save(update_fields=['psicologo', 'fecha', 'hora', 'correo_confirmacion_enviado'])
        SincronizacionAgenda.objects.create(operacion=op, cita=cita)
    despues = [ficha(c) for c in citas]
    auditar(request, op, {'citas': antes, 'asignado': asignado.pk if es_cambio and asignado else None},
            {'citas': despues, 'asignado': destino.pk if es_cambio else None},
            paciente_id=paciente.pk, psicologo_id=destino.pk, servicio=servicio)
    return {'citas': despues, 'carga': cargas, 'sincronizacion': 'pendiente' if citas else 'no requerida'}


def editar_config(request, op, data):
    doctor = bloquear_doctores([int(data['psicologo'])])[int(data['psicologo'])]
    antes = config(doctor)
    comprobar_version(data.get('version'), antes)
    if type(data.get('esta_activo')) is not bool:
        raise ErrorAgenda('Selecciona si acepta nuevos pacientes.')
    # ÚNICO campo de admisión: no modificar modalidad ni bloquear pacientes actuales.
    doctor.esta_activo = data['esta_activo']
    doctor.save(update_fields=['esta_activo'])
    auditar(request, op, antes, config(doctor), psicologo_id=doctor.pk)
    return config(doctor)


def editar_bloqueo(request, op, data):
    from .views import parsear_hora_flexible
    bloque = None
    if data.get('id'):
        bloque = BloqueoAgendaPsicologo.objects.get(pk=int(data['id']))
    ids = {int(data['psicologo'])} | ({bloque.psicologo_id} if bloque else set())
    docs = bloquear_doctores(ids)
    if bloque:
        bloque = BloqueoAgendaPsicologo.objects.select_for_update().get(pk=bloque.pk)
        comprobar_version(data.get('version'), bloque_ficha(bloque))
    antes = bloque_ficha(bloque) if bloque else None
    if op.accion == 'eliminar_bloqueo':
        if bloque is None:
            raise ErrorAgenda('Selecciona el bloqueo a eliminar.')
        bloque.activo = False
    else:
        if bloque is None:
            bloque = BloqueoAgendaPsicologo()
        bloque.psicologo = docs[int(data['psicologo'])]
        bloque.fecha = date.fromisoformat(data['fecha'])
        bloque.hora_inicio = parsear_hora_flexible(data['hora_inicio'])
        bloque.hora_fin = parsear_hora_flexible(data['hora_fin'])
        bloque.motivo = data['motivo'][:150]
        bloque.activo = True
        bloque.full_clean()
        ocupadas = vigentes(Cita.objects.filter(psicologo=bloque.psicologo, fecha=bloque.fecha))
        conflictos = [str(c.pk) for c in ocupadas if c.hora < bloque.hora_fin and
                      datetime.combine(c.fecha, c.hora) + timedelta(hours=1) > datetime.combine(bloque.fecha, bloque.hora_inicio)]
        if conflictos:
            raise ErrorAgenda('El bloqueo afecta citas/reservas: ' + ', '.join(conflictos) + '. Reagéndalas primero.', 409)
    bloque.save()
    auditar(request, op, antes, bloque_ficha(bloque), psicologo_id=bloque.psicologo_id, bloqueo_id=bloque.pk)
    return bloque_ficha(bloque)


@autorizado
@require_POST
def ejecutar(request):
    from .pacientes_recepcion import transferir
    data = json.loads(request.body)
    if not isinstance(data, dict):
        raise ErrorAgenda('La solicitud debe contener un objeto JSON.')
    motivo = str(data.get('motivo') or '').strip()
    if not 5 <= len(motivo) <= 1000:
        raise ErrorAgenda('Escribe un motivo de 5 a 1000 caracteres.')
    data['motivo'] = motivo
    clave = UUID(data['clave'])
    acciones = {'reagendar': cambios_citas, 'transferir': cambios_citas, 'reasignar_paciente': transferir, 'configurar': editar_config,
                'bloqueo': editar_bloqueo, 'eliminar_bloqueo': editar_bloqueo}
    if data.get('accion') not in acciones:
        raise ErrorAgenda('Acción no disponible.')
    with transaction.atomic():
        # Serializa reenvíos del mismo operador antes del get_or_create de la clave.
        User.objects.select_for_update().get(pk=request.user.pk)
        op, nueva = OperacionAgenda.objects.get_or_create(clave=clave,
            defaults={'usuario': request.user, 'accion': data['accion'], 'solicitud': data})
        if not nueva:
            if op.usuario_id != request.user.pk or op.solicitud != data:
                raise ErrorAgenda('La clave de esta operación ya fue utilizada con otros datos.', 409)
            return JsonResponse({'status': 'success', 'operacion': str(clave), 'resultado': op.resultado})
        op.resultado = acciones[op.accion](request, op, data)
        op.save(update_fields=['resultado'])
    return JsonResponse({'status': 'success', 'operacion': str(clave), 'resultado': op.resultado})


@autorizado
@require_GET
def historial(request):
    qs = EventoAuditoria.objects.filter(accion__startswith='agenda.').select_related('usuario')
    if request.GET.get('psicologo'):
        qs = qs.filter(detalles__psicologo_id=int(request.GET['psicologo']))
    if request.GET.get('paciente'):
        qs = qs.filter(detalles__paciente_id=int(request.GET['paciente']))
    antes = int(request.GET.get('antes') or 0)
    if antes:
        qs = qs.filter(pk__lt=antes)
    return JsonResponse({'status': 'success', 'eventos': [{'id': e.pk, 'usuario': nombre(e.usuario) if e.usuario else 'Usuario eliminado',
        'fecha': e.timestamp.isoformat(), 'accion': e.accion, 'detalles': e.detalles} for e in qs.order_by('-pk')[:50]]})


@autorizado
@require_POST
def reintentar(request, pk):
    datos = json.loads(request.body or b'{}')
    motivo = str(datos.get('motivo') or '').strip()
    if not 5 <= len(motivo) <= 1000:
        raise ErrorAgenda('Escribe el motivo del reintento.')
    with transaction.atomic():
        tarea = SincronizacionAgenda.objects.select_for_update().get(pk=pk)
        antes = {'estado': tarea.estado, 'correo_iniciado': tarea.correo_iniciado}
        if tarea.correo_iniciado and tarea.estado != 'completado':
            if tarea.estado == 'enviando' and tarea.proximo_intento > timezone.now():
                raise ErrorAgenda('Hay un envío en curso; espera a que termine.', 409)
            if datos.get('no_entregado_verificado') is not True:
                raise ErrorAgenda('Entrega de correo incierta. Revisa la bandeja de enviados antes de un reenvío manual.', 409)
            tarea.correo_iniciado = False
        if tarea.estado not in ('completado', 'sustituido'):
            tarea.estado, tarea.proximo_intento = 'pendiente', timezone.now()
            tarea.save()
        EventoAuditoria.objects.create(usuario=request.user, accion='agenda.reintentar',
            detalles={'cita_id': tarea.cita_id, 'paciente_id': tarea.cita.paciente_id,
                      'psicologo_id': tarea.cita.psicologo_id, 'sincronizacion': tarea.pk, 'motivo': motivo,
                      'antes': antes, 'despues': {'estado': tarea.estado, 'correo_iniciado': tarea.correo_iniciado}})
    return JsonResponse({'status': 'success'})
