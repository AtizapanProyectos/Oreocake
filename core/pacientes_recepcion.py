"""Directorio de recepción y reasignación administrativa de expedientes vigentes."""
from copy import copy
from datetime import datetime

from django.contrib.auth.models import User
from django.core.paginator import Paginator
from django.db.models import CharField, Exists, OuterRef, Prefetch, Q, Value
from django.db.models.functions import Coalesce, Concat, Lower, NullIf, Trim
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET

from . import agenda_recepcion as agenda
from .models import (Cita, EventoAuditoria, PerfilPsicologo, SincronizacionAgenda,
                     TratamientoPaciente, UsuarioPerfil)


def pacientes():
    return User.objects.filter(
        Q(perfil__es_psicologo=False) | Q(tratamientos__isnull=False) |
        Q(citas_como_paciente__isnull=False)
    ).filter(perfil_psicologo__isnull=True, is_superuser=False).distinct()


def obtener(pk, bloquear=False):
    qs = User.objects.select_for_update() if bloquear else pacientes()
    usuario = qs.get(pk=pk)
    if bloquear and not pacientes().filter(pk=pk).exists():
        raise agenda.ErrorAgenda('Selecciona un paciente del directorio.', 404)
    return usuario


def futuras(paciente):
    ahora = timezone.localtime()
    return agenda.vigentes(Cita.objects.filter(paciente=paciente,
        estado__in=['Confirmada', 'Pendiente']).filter(
        Q(fecha__gt=ahora.date()) | Q(fecha=ahora.date(), hora__gt=ahora.time().replace(tzinfo=None)))).order_by('pk')


def asignaciones(paciente):
    perfil = UsuarioPerfil.objects.filter(usuario=paciente).first()
    return {'perfil': perfil.psicologo_asignado_id if perfil else None,
            'tratamientos': list(TratamientoPaciente.objects.filter(paciente=paciente, activo=True)
                .order_by('pk').values('id', 'tipo_servicio', 'psicologo_asignado_id')),
            'citas': [agenda.ficha(c) for c in futuras(paciente)]}


def doctor_ficha(doctor):
    return {'id': doctor.pk, 'nombre': agenda.nombre(doctor.usuario)} if doctor else None


def identidad(paciente):
    perfil = getattr(paciente, 'perfil', None)
    return {'id': paciente.pk, 'nombre': (getattr(perfil, 'nombre', '') or agenda.nombre(paciente)).strip(),
            'email': paciente.email, 'telefono': getattr(perfil, 'telefono', '') or '',
            'emergencia': getattr(perfil, 'telefono_emergencia', '') or '',
            'organizacion': 'TECTUM Beneficios' if getattr(perfil, 'es_beneficios', False)
                else 'TECTUM In-House' if getattr(perfil, 'es_tectum', False) else 'HOPE'}


def tratamientos_ficha(paciente):
    return [{'id': t.pk, 'servicio': t.tipo_servicio, 'activo': t.activo,
             'psicologo': doctor_ficha(t.psicologo_asignado)} for t in paciente.tratamientos.all()]


@agenda.autorizado
@require_GET
@ensure_csrf_cookie
def panel(request):
    return render(request, 'recepcion_pacientes.html', {
        'usuario_nombre': request.user.get_short_name() or request.user.username})


@agenda.autorizado
@require_GET
def listado(request):
    qs = pacientes().select_related('perfil__psicologo_asignado__usuario').prefetch_related(
        Prefetch('tratamientos', queryset=TratamientoPaciente.objects.select_related('psicologo_asignado__usuario').order_by('tipo_servicio')))
    texto = request.GET.get('q', '').strip()[:150]
    if texto:
        for palabra in texto.split():
            qs = qs.filter(Q(first_name__icontains=palabra) | Q(last_name__icontains=palabra) |
                Q(perfil__nombre__icontains=palabra) | Q(email__icontains=palabra) |
                Q(perfil__telefono__icontains=palabra) | Q(username__icontains=palabra))
    if request.GET.get('servicio') in agenda.SERVICIOS:
        qs = qs.filter(tratamientos__tipo_servicio=request.GET['servicio'], tratamientos__activo=True)
    if request.GET.get('psicologo'):
        # Tratamiento individual explícito prevalece sobre la asignación histórica.
        individual = TratamientoPaciente.objects.filter(paciente_id=OuterRef('pk'), tipo_servicio='individual')
        ids = TratamientoPaciente.objects.filter(activo=True,
            psicologo_asignado_id=int(request.GET['psicologo'])).values('paciente_id')
        qs = qs.annotate(tiene_individual=Exists(individual)).filter(Q(pk__in=ids) |
            Q(tiene_individual=False, perfil__psicologo_asignado_id=int(request.GET['psicologo'])))
    if request.GET.get('organizacion') == 'beneficios':
        qs = qs.filter(perfil__es_beneficios=True)
    elif request.GET.get('organizacion') == 'tectum':
        qs = qs.filter(perfil__es_tectum=True, perfil__es_beneficios=False)
    elif request.GET.get('organizacion') == 'hope':
        qs = qs.exclude(Q(perfil__es_tectum=True) | Q(perfil__es_beneficios=True))
    if request.GET.get('citas') in ('con', 'sin'):
        ahora = timezone.localtime()
        proximas = agenda.vigentes(Cita.objects.filter(paciente_id=OuterRef('pk'),
            estado__in=['Confirmada', 'Pendiente']).filter(Q(fecha__gt=ahora.date()) |
                Q(fecha=ahora.date(), hora__gt=ahora.time().replace(tzinfo=None))))
        qs = qs.annotate(con_citas=Exists(proximas)).filter(con_citas=request.GET['citas'] == 'con')
    qs = qs.annotate(orden=Lower(Coalesce(NullIf(Trim('perfil__nombre'), Value('')),
        NullIf(Trim(Concat('first_name', Value(' '), 'last_name')), Value('')),
        NullIf('email', Value('')), 'username', output_field=CharField()))).order_by('orden', 'pk')
    pagina = Paginator(qs, 24).get_page(request.GET.get('pagina', 1))
    salida = []
    for p in pagina:
        # Usar el prefetch evita consultas por tarjeta y conserva el fallback explícito.
        ts = list(p.tratamientos.all())
        individual = next((t for t in ts if t.tipo_servicio == 'individual'), None)
        d = individual.psicologo_asignado if individual and individual.activo else None
        if individual is None:
            d = getattr(getattr(p, 'perfil', None), 'psicologo_asignado', None)
        salida.append({**identidad(p), 'psicologo': doctor_ficha(d), 'tratamientos': tratamientos_ficha(p)})
    doctores = PerfilPsicologo.objects.select_related('usuario').order_by('usuario__first_name', 'pk')
    return JsonResponse({'status': 'success', 'pacientes': salida, 'total': pagina.paginator.count,
        'pagina': pagina.number, 'paginas': pagina.paginator.num_pages,
        'psicologos': [doctor_ficha(d) for d in doctores]})


@agenda.autorizado
@require_GET
def detalle(request, pk):
    from .views import _psicologo_asignado_servicio
    p = obtener(pk)
    estado = asignaciones(p)
    citas = Cita.objects.filter(paciente=p).select_related('psicologo__usuario').order_by('-fecha', '-hora', '-pk')
    if request.GET.get('historial') != '1':
        citas = citas.filter(pk__in=[c['id'] for c in estado['citas']]).order_by('fecha', 'hora', 'pk')
    pagina = Paginator(citas, 20).get_page(request.GET.get('pagina', 1))
    eventos = EventoAuditoria.objects.filter(accion__startswith='agenda.', detalles__paciente_id=p.pk).select_related('usuario').order_by('-pk')[:50]
    return JsonResponse({'status': 'success', 'paciente': identidad(p),
        'psicologo': doctor_ficha(_psicologo_asignado_servicio(p, 'individual')),
        'tratamientos': tratamientos_ficha(p), 'version': agenda.version(estado),
        'citas': [{**agenda.ficha(c), 'version': agenda.version(agenda.ficha(c)),
            'psicologo': doctor_ficha(c.psicologo), 'editable': c.pk in {x['id'] for x in estado['citas']}} for c in pagina],
        'pagina': pagina.number, 'paginas': pagina.paginator.num_pages, 'total_citas': pagina.paginator.count,
        'historial': [{'id': e.pk, 'fecha': e.timestamp.isoformat(), 'usuario': agenda.nombre(e.usuario) if e.usuario else 'Usuario eliminado',
            'accion': e.accion, 'detalles': e.detalles} for e in eventos]})


def doctor_administrativo(doctor):
    """Las advertencias de perfil son informativas en un traslado manual autorizado.

    No modifica el perfil persistido ni omite descansos, esquemas, comida o bloqueos.
    """
    d = copy(doctor)
    d.atiende_individual = d.atiende_pareja = d.atiende_familiar = True
    d.modalidad_atencion = 'Mixto'
    return d


@agenda.autorizado
@require_GET
def disponibilidad(request):
    p = obtener(int(request.GET['paciente']))
    c = Cita.objects.get(pk=int(request.GET['cita']), paciente=p)
    doctor = PerfilPsicologo.objects.get(pk=int(request.GET['psicologo']))
    desde, hasta = agenda.rango(request.GET, 91)
    excluir = list(futuras(p).values_list('pk', flat=True))
    administrativo = doctor_administrativo(doctor)
    dias = agenda.slots(administrativo, desde, hasta, c.tipo_sesion, c.modalidad, excluir, paciente=p)
    dias = agenda.filtrar_intervalos(administrativo, p, c.tipo_sesion, dias, excluir)
    return JsonResponse({'status': 'success', 'dias': dias})


@agenda.autorizado
@require_GET
def candidatos(request):
    p = obtener(int(request.GET['paciente']))
    desde, hasta = agenda.rango(request.GET, 32)
    estado = asignaciones(p)
    asignados = {estado['perfil']} | {t['psicologo_asignado_id'] for t in estado['tratamientos']} | {c['psicologo_id'] for c in estado['citas']}
    asignados.discard(None)
    servicios = {t['tipo_servicio'] for t in estado['tratamientos']} | {c['servicio'] for c in estado['citas']} or {'individual'}
    doctores = list(PerfilPsicologo.objects.select_related('usuario').order_by('usuario__first_name', 'pk'))
    agenda.precargar(doctores, desde, hasta)
    preferencias = {s: agenda.preferencias(p, s) for s in servicios}
    excluir = [c['id'] for c in estado['citas']]
    ocupadas = list(agenda.vigentes(Cita.objects.filter(fecha__range=(desde, hasta))).exclude(pk__in=excluir))
    salida = []
    for d in doctores:
        if request.GET.get('genero') in ('Hombre', 'Mujer') and d.genero != request.GET['genero']:
            continue
        avisos = []
        if not d.usuario.is_active:
            avisos.append('La cuenta de este psicólogo está desactivada.')
        if not d.esta_activo:
            avisos.append('Tiene cerrada la admisión de pacientes nuevos.')
        for servicio in sorted(servicios):
            modalidades = {c['modalidad'] for c in estado['citas'] if c['servicio'] == servicio}
            razones, genero = agenda.compatible(d, servicio, modalidades, preferencias[servicio], p)
            avisos.extend(servicio.title() + ': ' + r for r in razones if 'nuevos pacientes' not in r)
            if genero:
                avisos.append('La preferencia de género registrada para ' + servicio + ' no coincide.')
        administrativo = doctor_administrativo(d)
        cruces = [c for c in ocupadas if c.psicologo_id == d.pk or c.paciente_id == p.pk]
        libres = {s: {m: agenda.filtrar_intervalos(administrativo, p, s,
            agenda.slots(administrativo, desde, hasta, s, m, excluir, paciente=p),
            excluir, ocupadas=cruces) for m in agenda.MODALIDADES} for s in sorted(servicios)}
        cantidad = len({(f, h) for ms in libres.values() for dias in ms.values() for f, hs in dias.items() for h in hs})
        if not cantidad:
            avisos.append('Sin horarios disponibles en las fechas consultadas.')
        salida.append({**doctor_ficha(d), 'genero': d.genero, 'modalidad': d.modalidad_atencion,
            'avisos': list(dict.fromkeys(avisos)), 'ya_asignado': asignados == {d.pk}, 'version': agenda.version(agenda.config(d)),
            'carga': agenda.carga(administrativo, desde), 'horarios_disponibles': cantidad, 'libres': libres})
    return JsonResponse({'status': 'success', 'version': agenda.version(estado), 'citas': estado['citas'], 'psicologos': salida})


def transferir(request, op, data):
    """Se ejecuta dentro de la transacción e idempotencia de agenda.ejecutar."""
    p = obtener(int(data['paciente']), bloquear=True)
    agenda.bloquear_pagos(p)
    list(TratamientoPaciente.objects.select_for_update().filter(paciente=p).order_by('pk'))
    citas = list(futuras(p).select_for_update())
    antes = asignaciones(p)
    agenda.comprobar_version(data.get('version'), antes)
    docs = agenda.bloquear_doctores({c.psicologo_id for c in citas} |
        {t['psicologo_asignado_id'] for t in antes['tratamientos']} | {antes['perfil'], int(data['psicologo'])})
    destino = docs.get(int(data['psicologo']))
    if destino is None:
        raise agenda.ErrorAgenda('El psicólogo ya no existe.', 404)
    agenda.comprobar_version(data.get('version_psicologo'), agenda.config(destino))
    asignados = {antes['perfil']} | {t['psicologo_asignado_id'] for t in antes['tratamientos']} | {c['psicologo_id'] for c in antes['citas']}
    asignados.discard(None)
    if asignados == {destino.pk}:
        raise agenda.ErrorAgenda('Este paciente ya está asignado a ese psicólogo. Para cambiar un horario utiliza Reagendar.')
    propuestas = data.get('citas')
    if not isinstance(propuestas, list) or len(propuestas) != len(citas):
        raise agenda.ErrorAgenda('Revisa todas las citas y reservas futuras antes de confirmar.', 409)
    por_id = {int(x['id']): x for x in propuestas}
    if set(por_id) != {c.pk for c in citas}:
        raise agenda.ErrorAgenda('Las citas cambiaron. Actualiza y vuelve a revisar.', 409)
    elegidas, validadas = set(), []
    excluir = [c.pk for c in citas]
    administrativo = doctor_administrativo(destino)
    for c in citas:
        x = por_id[c.pk]
        f, h = agenda.validar_destino(c, administrativo, x['fecha'], x['hora'], excluir,
            data.get('excepcion_plazo') is True)
        instante = datetime.combine(f, h)
        if any(abs((instante - i).total_seconds()) < 3600 for i in elegidas):
            raise agenda.ErrorAgenda('Dos citas del traslado se superponen.', 409)
        elegidas.add(instante)
        validadas.append((c, f, h))
    anteriores = {str(k): agenda.nombre(d.usuario) for k, d in docs.items()}
    TratamientoPaciente.objects.filter(paciente=p, activo=True).update(psicologo_asignado=destino)
    # No activar tratamientos cerrados ni crear servicios que el paciente no tenía.
    perfil, _ = UsuarioPerfil.objects.get_or_create(usuario=p, defaults={'nombre': agenda.nombre(p)})
    perfil.psicologo_asignado = destino
    perfil.save(update_fields=['psicologo_asignado'])
    Cita.objects.filter(pk__in=excluir).update(psicologo=None)
    for c, f, h in validadas:
        c.psicologo, c.fecha, c.hora = destino, f, h
        c.correo_confirmacion_enviado = False
        c.save(update_fields=['psicologo', 'fecha', 'hora', 'correo_confirmacion_enviado'])
        SincronizacionAgenda.objects.create(operacion=op, cita=c)
    despues = asignaciones(p)
    agenda.auditar(request, op, antes, despues, paciente_id=p.pk, psicologo_id=destino.pk,
        psicologos=anteriores, nuevo_psicologo=agenda.nombre(destino.usuario),
        autores_clinicos_conservados=True)
    return {'paciente': p.pk, 'asignaciones': despues, 'sincronizacion': 'pendiente' if citas else 'no requerida'}
