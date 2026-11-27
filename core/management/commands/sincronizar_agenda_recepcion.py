import time
from datetime import datetime, timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.core.management.base import BaseCommand, CommandError
from django.db import connections, transaction
from django.utils import timezone

from core.models import Cita, SincronizacionAgenda


def actualizar_calendar(cita):
    from googleapiclient.errors import HttpError
    from core.views import _servicio_calendar
    inicio = timezone.make_aware(datetime.combine(cita.fecha, cita.hora))
    correos = {cita.paciente.email, cita.psicologo.usuario.email} - {''}
    body = {
        'start': {'dateTime': inicio.isoformat(), 'timeZone': 'America/Mexico_City'},
        'end': {'dateTime': (inicio + timedelta(minutes=50 if cita.tipo_sesion == 'individual' else 60)).isoformat(), 'timeZone': 'America/Mexico_City'},
        'attendees': [{'email': correo} for correo in sorted(correos)],
    }
    eventos = _servicio_calendar().events()
    if not cita.id_evento_google:
        # ID estable: una respuesta perdida no crea dos reuniones al reintentar.
        cita.id_evento_google = 'hopeagenda' + format(cita.pk, 'x')
        cita.save(update_fields=['id_evento_google'])
    try:
        evento = eventos.patch(calendarId='primary', eventId=cita.id_evento_google,
                               sendUpdates='all', body=body).execute()
    except HttpError as error:
        if error.resp.status != 404 or not cita.id_evento_google.startswith('hopeagenda'):
            raise
        try:
            evento = eventos.insert(calendarId='primary', sendUpdates='all', conferenceDataVersion=1,
                body={**body, 'id':cita.id_evento_google, 'summary':'Sesión Espacio HOPE',
                      'conferenceData':{'createRequest':{'requestId':cita.id_evento_google,
                                        'conferenceSolutionKey':{'type':'hangoutsMeet'}}}}).execute()
        except HttpError as insert_error:
            if insert_error.resp.status != 409:
                raise
            evento = eventos.get(calendarId='primary', eventId=cita.id_evento_google).execute()
    enlace = evento.get('hangoutLink') or cita.enlace_meet
    if not enlace:
        raise ValueError('Meet aún está preparando el enlace; se volverá a consultar.')
    if enlace != cita.enlace_meet:
        cita.enlace_meet = enlace
        cita.save(update_fields=['enlace_meet'])


def sincronizar(pk):
    # Fase 1: Calendar es idempotente y la intención de correo se confirma en BD
    # ANTES de hablar con SMTP. Un proceso interrumpido no provoca doble envío.
    with transaction.atomic():
        referencia = SincronizacionAgenda.objects.get(pk=pk)
        cita = Cita.objects.select_for_update().select_related('paciente','psicologo__usuario').get(pk=referencia.cita_id)
        tarea = SincronizacionAgenda.objects.select_for_update().get(pk=pk)
        if tarea.estado in ('completado','sustituido','revision_manual'):
            return tarea.estado
        if tarea.estado == 'enviando' and tarea.proximo_intento > timezone.now():
            return tarea.estado
        if SincronizacionAgenda.objects.filter(cita=cita,pk__gt=pk).exists():
            tarea.estado = 'sustituido'
        elif cita.estado == 'Pendiente':
            tarea.estado,tarea.mensaje = 'espera_pago','La reserva conserva sus referencias y su vencimiento.'
            tarea.proximo_intento = timezone.now()+timedelta(minutes=1)
        elif cita.estado != 'Confirmada' or not cita.psicologo_id:
            tarea.estado,tarea.mensaje = 'sustituido','La cita ya no requiere esta sincronización.'
        elif tarea.correo_iniciado:
            tarea.estado,tarea.mensaje = 'revision_manual','Entrega incierta; no se reenvía automáticamente.'
        else:
            tarea.intentos += 1
            try:
                if not tarea.calendario_listo and cita.modalidad == 'En línea':
                    actualizar_calendar(cita)
                tarea.calendario_listo = True
                tarea.correo_iniciado = True
                tarea.estado = 'enviando'
                tarea.proximo_intento = timezone.now()+timedelta(minutes=10)
            except Exception as error:
                tarea.estado = 'revision_manual' if tarea.intentos >= 5 else 'pendiente'
                tarea.mensaje = 'Revisar sincronización: '+type(error).__name__
                tarea.proximo_intento = timezone.now()+timedelta(minutes=min(60,2**tarea.intentos))
        tarea.save()
        estado = tarea.estado
    if estado != 'enviando':
        return estado
    # Fase 2: refrescar y bloquear la cita para no enviar datos obsoletos mientras
    # se guarda un cambio posterior. La marca de intención ya es persistente.
    with transaction.atomic():
        cita = Cita.objects.select_for_update().select_related('paciente','psicologo__usuario').get(pk=referencia.cita_id)
        tarea = SincronizacionAgenda.objects.select_for_update().get(pk=pk)
        if SincronizacionAgenda.objects.filter(cita=cita,pk__gt=pk).exists() or cita.estado != 'Confirmada':
            tarea.estado = 'sustituido'
        else:
            try:
                enviados = send_mail('Actualización de tu cita — Espacio HOPE',
                    f'Tu cita de {cita.get_tipo_sesion_display()} quedó para el {cita.fecha:%d/%m/%Y} '
                    f'a las {cita.hora:%H:%M} (hora de Ciudad de México), con '
                    f'{cita.psicologo.usuario.get_full_name() or cita.psicologo.usuario.username}. '
                    f'Modalidad: {cita.modalidad}. Consulta tu panel para ver los detalles actualizados.',
                    settings.DEFAULT_FROM_EMAIL,[cita.paciente.email],fail_silently=False)
                if not enviados:
                    tarea.correo_iniciado = False
                    tarea.estado = 'pendiente'
                    tarea.mensaje = 'El servidor de correo no aceptó el aviso.'
                    tarea.proximo_intento = timezone.now()+timedelta(minutes=5)
                else:
                    tarea.estado,tarea.mensaje = 'completado','Calendario y aviso actualizados.'
            except Exception as error:
                tarea.estado = 'revision_manual'
                tarea.mensaje = 'Entrega de correo incierta: '+type(error).__name__
        tarea.save()
        return tarea.estado


class Command(BaseCommand):
    help = 'Sincroniza cambios de agenda de recepción, sin generar cobros ni reportes clínicos.'

    def add_arguments(self, parser):
        parser.add_argument('--limite', type=int, default=25)
        parser.add_argument('--continuo', action='store_true')

    def handle(self, *args, **options):
        if not 1 <= options['limite'] <= 100:
            raise CommandError('Límite permitido: 1–100.')
        while True:
            ids = list(SincronizacionAgenda.objects.filter(estado__in=['pendiente', 'espera_pago', 'enviando'],
                proximo_intento__lte=timezone.now()).order_by('pk').values_list('pk', flat=True)[:options['limite']])
            for pk in ids:
                try:
                    self.stdout.write(f'{pk}: {sincronizar(pk)}')
                except Exception as error:
                    self.stderr.write(f'{pk}: {type(error).__name__}; pendiente de reintento.')
                finally:
                    connections.close_all()
            if not options['continuo']:
                return
            time.sleep(30)
