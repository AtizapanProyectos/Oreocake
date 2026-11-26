"""Persistent, bounded clinical orchestration. Never run with production in tests."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from uuid import uuid4
import hashlib
import json
import smtplib
import time
import random

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Q, Case, When, IntegerField, OuterRef, Subquery, Exists
from django.template.loader import render_to_string
from django.utils import timezone

from .models import (Cita, EstadoFlujoSesion, HistorialClinico, ReporteClinicoPDF,
                     TrabajoProceso, EjecucionProceso, ControlOrquestador,
                     EvaluacionSesionPaciente, RespuestaFormularioOrganica)
from . import flujo_clinico as flujo

ETAPAS = ('meet', 'lectura', 'ia', 'escritura', 'ipp', 'sintesis_paciente',
          'sintesis_psicologo', 'pdf_paciente', 'pdf_psicologo', 'correo_paciente', 'correo_psicologo')
ESPERAS = (15, 60, 360, 1440)


def activo():
    return getattr(settings, 'ORQUESTADOR_ACTIVO', False)


def inicio(cita):
    return datetime.combine(cita.fecha, cita.hora, tzinfo=ZoneInfo('America/Mexico_City'))


def elegible(cita):
    return (cita.estado in ('Confirmada', 'Completada') and
            not (cita.modalidad == 'Presencial' and cita.registro_llegada == 'No Vino'))


def nota_valida(cita):
    nota = getattr(cita, 'nota_clinica', None)
    return bool(nota and nota.notas_sesion.strip() and not nota.pendiente_revision_audio)


def registrar(cita, usuario=None):
    with transaction.atomic():
        Cita.objects.select_for_update().get(pk=cita.pk)
        for etapa in ETAPAS:
            TrabajoProceso.objects.get_or_create(cita=cita, etapa=etapa, version=1,
                defaults={'responsable': usuario, 'manual': bool(usuario),
                          'proximo_intento': inicio(cita) + timedelta(hours=2)})
    return TrabajoProceso.objects.filter(cita=cita, version=1)


@transaction.atomic
def solicitar(cita, usuario, etapa='completo'):
    trabajos = registrar(cita, usuario)
    if any(trabajo.estado == 'procesando' for trabajo in trabajos.select_for_update()):
        return False
    # Explicit requests may reopen blocked access, but never ambiguous deliveries.
    indice = len(ETAPAS)-1 if etapa == 'completo' else ETAPAS.index(etapa)
    seleccion = trabajos.filter(etapa__in=ETAPAS[:indice+1])
    seleccion.exclude(estado__in=['completado', 'procesando']).exclude(error='entrega_incierta').update(
        estado='pendiente', proximo_intento=timezone.now(), manual=True, responsable=usuario,
        intentos=0, error='', habilitado=True, mensaje='Solicitud manual registrada.')
    trabajos.filter(etapa__in=ETAPAS[indice+1:]).exclude(estado__in=['completado','procesando']).exclude(error='entrega_incierta').update(
        habilitado=False, estado='espera_dependencia', error='pausa_manual', mensaje='Etapa posterior al alcance solicitado. Solicitar flujo completo para continuar.')
    if etapa in ('completo', 'meet', 'lectura'):
        ControlOrquestador.objects.filter(clave='google', token='bloqueado').update(token='', vence=None)
    return True


def reconciliar(limite=100, despues=0):
    """Only database evidence; never Google, AI, PDF generation or SMTP."""
    ultimo, total = despues, 0
    for cita in flujo._base().filter(pk__gt=despues, estado__in=['Confirmada','Completada']).order_by('pk')[:limite]:
        trabajos = registrar(cita)
        enviados = flujo._enviados(cita) | set(cita.logs_envio_ipp.filter(resultado='Exitoso', tipo_destinatario__in=flujo.DESTINATARIOS).values_list('tipo_destinatario',flat=True))
        if flujo.DESTINATARIOS.issubset(enviados):
            trabajos.exclude(estado='procesando').update(estado='completado',mensaje='Entregas históricas acreditadas.')
        elif cita.reporte_enviado and not enviados:
            trabajos.filter(etapa='meet').exclude(estado='procesando').update(estado='revision_manual',error='historico_ambiguo',mensaje='Marca histórica sin evidencia de entrega. Revisar.')
        ultimo, total = cita.pk, total+1
    return {'reconciliadas':total, 'ultimo_id':ultimo}


class Incidencia(Exception):
    def __init__(self, codigo, mensaje, estado='bloqueado'):
        self.codigo, self.mensaje, self.estado = codigo, mensaje, estado


def _comprobar_token(trabajo, token):
    if not TrabajoProceso.objects.filter(pk=trabajo.pk, token=token, estado='procesando', vence__gt=timezone.now()).exists():
        raise Incidencia('ejecucion_vencida', 'La ejecución perdió su reserva.', 'revision_manual')


def ejecutar(trabajo, token):
    from . import views
    cita = flujo._base().get(pk=trabajo.cita_id)
    if not elegible(cita):
        raise Incidencia('no_elegible', 'La cita no está disponible para procesamiento.', 'espera_dependencia')
    if not cita.psicologo_id:
        raise Incidencia('sin_profesional', 'Falta el profesional responsable.')
    nota = getattr(cita, 'nota_clinica', None)
    if nota and nota.pendiente_revision_audio:
        raise Incidencia('revision_audio', 'El profesional debe revisar la nota de audio.', 'espera_dependencia')
    etapa = trabajo.etapa
    enviados = flujo._enviados(cita) | set(cita.logs_envio_ipp.filter(resultado='Exitoso', tipo_destinatario__in=flujo.DESTINATARIOS).values_list('tipo_destinatario', flat=True))
    if flujo.DESTINATARIOS.issubset(enviados):
        Cita.objects.filter(pk=cita.pk).update(reporte_enviado=True)
        return {'omitido': 'Entregas acreditadas'}
    if cita.reporte_enviado and not enviados:
        raise Incidencia('historico_ambiguo', 'Marca histórica sin evidencia de entrega; revisar antes de enviar.', 'revision_manual')
    if etapa in ETAPAS[:4] and nota_valida(cita):
        return {'omitido': 'Nota existente conservada'}
    if etapa in ETAPAS[:4] and not nota_valida(cita):
        if cita.modalidad == 'Presencial' or not cita.id_evento_google:
            raise Incidencia('nota_manual', 'Se requiere una nota clínica del profesional.', 'revision_manual')
    estado, _ = EstadoFlujoSesion.objects.get_or_create(cita=cita)
    if etapa in ('meet', 'lectura') and ControlOrquestador.objects.filter(clave='google', token='bloqueado').exists():
        raise Incidencia('google_pausado', 'Conexión Google pausada. Corregir acceso y solicitar reactivación.')
    if etapa == 'meet':
        if not estado.adjuntos:
            adjuntos = flujo._leer_meet(cita)
            if not adjuntos:
                raise Incidencia('sin_documentos', 'Meet todavía no tiene documentos.', 'espera_reintento')
            _comprobar_token(trabajo, token)
            estado.adjuntos = adjuntos
            estado.save(update_fields=['adjuntos', 'actualizado'])
        return {'adjuntos': len(estado.adjuntos)}
    if etapa == 'lectura':
        if not estado.texto_notas:
            texto = flujo._leer_documentos(estado.adjuntos)
            _comprobar_token(trabajo, token)
            estado.texto_notas, estado.fuente = texto, 'google_meet'
            estado.save(update_fields=['texto_notas', 'fuente', 'actualizado'])
        return {'hash': hashlib.sha256(estado.texto_notas.encode()).hexdigest()}
    if etapa == 'ia':
        if not estado.datos_clinicos:
            telemetria = {}
            datos = views._generar_bitacora_con_ia_de_texto(estado.texto_notas, cita.paciente.first_name, telemetria)
            if not isinstance(datos, dict) or not all(isinstance(datos.get(c), str) for c in flujo.CAMPOS) or not datos['notas_sesion'].strip():
                raise Incidencia('ia_invalida', 'La IA no produjo una nota válida.', 'espera_reintento')
            _comprobar_token(trabajo, token)
            estado.datos_clinicos = {c: datos[c] for c in flujo.CAMPOS}
            estado.save(update_fields=['datos_clinicos', 'actualizado'])
            return telemetria
        return {'reutilizado': True}
    if etapa == 'escritura':
        with transaction.atomic():
            Cita.objects.select_for_update().get(pk=cita.pk)
            _comprobar_token(trabajo, token)
            nota, _ = HistorialClinico.objects.select_for_update().get_or_create(cita=cita,
                defaults={'paciente': cita.paciente, 'psicologo': cita.psicologo, 'notas_sesion': ''})
            if nota.pendiente_revision_audio:
                raise Incidencia('revision_audio', 'Revise el borrador de audio.', 'espera_dependencia')
            if not estado.datos_clinicos.get('notas_sesion', '').strip():
                raise Incidencia('sin_datos', 'Faltan datos clínicos válidos.')
            for campo in flujo.CAMPOS:
                if not (getattr(nota, campo) or '').strip():
                    setattr(nota, campo, estado.datos_clinicos.get(campo, ''))
            nota.save(update_fields=list(flujo.CAMPOS))
        return {'conservado': True}
    if not nota_valida(cita):
        raise Incidencia('nota_manual', 'Falta nota clínica válida.', 'espera_dependencia')
    if not flujo._ipp(cita):
        raise Incidencia('ipp', 'Falta el IPP de esta sesión.', 'espera_dependencia')
    if etapa == 'ipp':
        return {'validado': True}
    huella = hashlib.sha256(json.dumps({c:getattr(nota,c) for c in flujo.CAMPOS}, sort_keys=True).encode()).hexdigest()
    destinatario = etapa.split('_', 1)[1]
    if destinatario in enviados:
        return {'omitido': 'Destinatario con entrega acreditada'}
    pdf = flujo._pdfs(cita).get(destinatario)
    if etapa.startswith('sintesis_'):
        if flujo._archivo_disponible(pdf):
            return {'datos': pdf.datos_ia_snapshot, 'huella': huella}
        construir = views._construir_datos_checkin_paciente if destinatario == 'paciente' else views._construir_datos_checkin_psicologo
        datos = construir(cita.paciente, cita=cita)
        # Persist only JSON-safe template data, not response objects.
        from django.core.serializers.json import DjangoJSONEncoder
        return {'datos': json.loads(json.dumps(datos, cls=DjangoJSONEncoder)), 'huella': huella}
    sintesis = TrabajoProceso.objects.get(cita=cita, etapa='sintesis_'+destinatario, version=trabajo.version).resultado
    if sintesis.get('huella') and sintesis['huella'] != huella:
        raise Incidencia('nota_modificada', 'La nota cambió después de preparar el reporte. Revisar versión.', 'revision_manual')
    if etapa.startswith('pdf_'):
        if flujo._archivo_disponible(pdf):
            return {'reporte_id': pdf.pk}
        datos = TrabajoProceso.objects.get(cita=cita, etapa='sintesis_'+destinatario, version=trabajo.version).resultado['datos']
        tectum = cita.es_tectum or getattr(getattr(cita.paciente, 'perfil', None), 'es_tectum', False)
        template = 'Pruebas/claude_pascinete.html' if destinatario == 'paciente' else 'Pruebas/claude_psioclog.html'
        html = render_to_string(template, {**datos, 'es_exportacion_pdf': True,
            'logo_base64': views._obtener_logo_base64(tectum=tectum), 'es_tectum': tectum})
        contenido = views.convertir_html_a_pdf(html)
        with transaction.atomic():
            Cita.objects.select_for_update().get(pk=cita.pk)
            _comprobar_token(trabajo, token)
            row = ReporteClinicoPDF(cita=cita, paciente=cita.paciente, psicologo=cita.psicologo,
                tipo_destinatario=destinatario, datos_ia_snapshot=datos, numero_sesion=datos.get('sesion_numero', 1))
            row.archivo_pdf.save('reporte.pdf', ContentFile(contenido))
        return {'reporte_id': row.pk}
    if not flujo._archivo_disponible(pdf):
        raise Incidencia('pdf_ausente', 'Falta el PDF; revisar almacenamiento.', 'revision_manual')
    email = cita.paciente.email if destinatario == 'paciente' else cita.psicologo.usuario.email
    if not email:
        raise Incidencia('correo_ausente', 'Falta correo del destinatario.')
    with pdf.archivo_pdf.open('rb') as archivo:
        contenido = archivo.read()
    _comprobar_token(trabajo, token)
    # Persist intent before crossing the SMTP boundary; expired attempts are never blindly replayed.
    TrabajoProceso.objects.filter(pk=trabajo.pk, token=token).update(error='entrega_incierta')
    tectum = cita.es_tectum or getattr(getattr(cita.paciente, 'perfil', None), 'es_tectum', False)
    try:
        if destinatario == 'paciente':
            views._enviar_email_reporte_paciente_con_pdf(cita.paciente, pdf.datos_ia_snapshot, contenido, 'reporte.pdf', es_tectum=tectum)
        else:
            views._enviar_email_reporte_psicologo_con_pdf(cita.psicologo, cita.paciente, pdf.datos_ia_snapshot, contenido, 'reporte.pdf', es_tectum=tectum)
    except smtplib.SMTPResponseException as exc:
        raise Incidencia('smtp_rechazo', 'El proveedor rechazó el correo.', 'espera_reintento' if 400 <= exc.smtp_code < 500 else 'bloqueado') from exc
    except Exception as exc:
        raise Incidencia('entrega_incierta', 'No se confirmó la entrega. Revisar antes de reenviar.', 'revision_manual') from exc
    with transaction.atomic():
        _comprobar_token(trabajo, token)
        ReporteClinicoPDF.objects.filter(pk=pdf.pk).update(correo_enviado=True)
        views._log_envio_ipp_seguro(cita, destinatario, 'Orquestador', 'Exitoso', 'Correo aceptado por SMTP.')
        if flujo.DESTINATARIOS.issubset(enviados | {destinatario}):
            Cita.objects.filter(pk=cita.pk).update(reporte_enviado=True)
    return {'reporte_id': pdf.pk}


def procesar(trabajo):
    token = str(uuid4())
    ahora = timezone.now()
    with transaction.atomic():
        trabajo = TrabajoProceso.objects.select_for_update().get(pk=trabajo.pk)
        if not trabajo.manual or not trabajo.habilitado or trabajo.estado not in ('pendiente', 'espera_reintento', 'espera_dependencia') or trabajo.proximo_intento > ahora:
            return False
        trabajo.estado, trabajo.token, trabajo.vence = 'procesando', token, ahora + timedelta(minutes=10)
        trabajo.intentos += 1
        trabajo.save()
        EjecucionProceso.objects.create(trabajo=trabajo, token=token, responsable=trabajo.responsable)
    comienzo = time.monotonic()
    resultado, codigo, mensaje, estado = {}, '', '', 'completado'
    retry_after = None
    try:
        resultado = ejecutar(trabajo, token)
    except Incidencia as exc:
        codigo, mensaje, estado = exc.codigo, exc.mensaje, exc.estado
    except flujo.PasoBloqueado as exc:
        codigo = exc.codigo or ('google_acceso' if exc.estado == 'sin_permisos' else 'dependencia')
        mensaje = str(exc)[:400]
        estado = 'bloqueado' if exc.estado == 'sin_permisos' else 'espera_dependencia'
        if codigo == 'google_configuracion':
            ControlOrquestador.objects.update_or_create(clave='google', defaults={'token':'bloqueado', 'vence':None})
    except Exception as exc:
        codigo, mensaje, estado = type(exc).__name__, 'El servicio no completó la etapa. Revisar la integración.', 'espera_reintento'
        response = getattr(exc, 'response', None)
        status = getattr(response, 'status_code', None)
        if status in (400,401,404,422):
            estado = 'bloqueado'
        try:
            retry_after = max(0, int(response.headers.get('Retry-After', '0'))) if response is not None else None
        except (ValueError, TypeError):
            retry_after = None
        if codigo in ('RefreshError', 'DefaultCredentialsError', 'AuthenticationError', 'PermissionDeniedError'):
            estado = 'bloqueado'
            if trabajo.etapa in ('meet', 'lectura'):
                ControlOrquestador.objects.update_or_create(clave='google', defaults={'token':'bloqueado', 'vence':None})
    fin = timezone.now()
    if estado == 'completado':
        mensaje = resultado.get('omitido') or 'Etapa completada.'
    if estado == 'espera_reintento' and trabajo.intentos >= (2 if codigo == 'ia_invalida' else 5):
        estado = 'revision_manual'
    demora = ESPERAS[min(max(trabajo.intentos-1, 0), len(ESPERAS)-1)]
    if estado == 'espera_dependencia':
        demora = 60
    proximo = fin + timedelta(minutes=demora, seconds=random.uniform(0, 30))
    if retry_after:
        proximo = max(proximo, fin+timedelta(seconds=retry_after))
    with transaction.atomic():
        actual = TrabajoProceso.objects.select_for_update().get(pk=trabajo.pk)
        if actual.token != token or actual.estado != 'procesando' or not actual.vence or actual.vence <= fin:
            return False
        resolucion = int((fin-actual.incidencia_desde).total_seconds()*1000) if estado == 'completado' and actual.incidencia_desde else None
        actual.estado, actual.resultado, actual.error, actual.mensaje = estado, resultado, codigo, mensaje
        actual.vence = None
        actual.proximo_intento = proximo
        actual.incidencia_desde = None if estado == 'completado' else (actual.incidencia_desde or fin)
        actual.save()
        EjecucionProceso.objects.filter(token=token).update(fin=fin, duracion_ms=int((time.monotonic()-comienzo)*1000),
            resolucion_ms=resolucion, estado=estado, error=codigo)
    from .views import _reg_log_auto
    _reg_log_auto(cita=trabajo.cita, etapa='correo' if trabajo.etapa.startswith('correo') else 'reporte' if '_' in trabajo.etapa else trabajo.etapa,
        estado='exitoso' if estado == 'completado' else 'error', origen='manual' if trabajo.manual else 'automatico',
        detalle=mensaje or 'Etapa completada.', error_tecnico=codigo, usuario=trabajo.responsable,
        telemetria=resultado if trabajo.etapa == 'ia' else None)
    return estado == 'completado'


def correr(limite=25, segundos=240, cita_id=None):
    if not activo():
        return {'activo': False, 'etapas': 0}
    token, ahora = str(uuid4()), timezone.now()
    ControlOrquestador.objects.get_or_create(clave='maestro')
    with transaction.atomic():
        control = ControlOrquestador.objects.select_for_update().get(clave='maestro')
        if control.vence and control.vence > ahora:
            return {'ocupado': True, 'etapas': 0}
        control.token, control.vence = token, ahora + timedelta(minutes=10)
        control.save()
    total, reloj = 0, time.monotonic()
    try:
        for viejo in TrabajoProceso.objects.filter(estado='procesando', vence__lte=ahora):
            incierto = viejo.error == 'entrega_incierta'
            recuperado = 'revision_manual' if incierto or viejo.intentos >= 5 else 'espera_reintento'
            TrabajoProceso.objects.filter(pk=viejo.pk, token=viejo.token).update(
                estado=recuperado, proximo_intento=ahora, vence=None)
            EjecucionProceso.objects.filter(token=viejo.token, fin__isnull=True).update(fin=ahora, estado=recuperado, error='ejecucion_interrumpida')
        orden = Case(*[When(etapa=e, then=i) for i,e in enumerate(ETAPAS)], output_field=IntegerField())
        primera = TrabajoProceso.objects.filter(cita_id=OuterRef('cita_id'), version=1).exclude(estado='completado').annotate(orden=orden).order_by('orden').values('pk')[:1]
        pendientes = TrabajoProceso.objects.filter(pk=Subquery(primera), habilitado=True, manual=True)
        pendientes = pendientes.filter(cita__estado__in=['Confirmada','Completada']).exclude(
            cita__modalidad='Presencial', cita__registro_llegada='No Vino')
        ipp = EvaluacionSesionPaciente.objects.filter(cita_id=OuterRef('cita_id'), paciente_id=OuterRef('cita__paciente_id')).exclude(respuestas_ipp={})
        legacy_ipp = RespuestaFormularioOrganica.objects.filter(cita_id=OuterRef('cita_id'), paciente_id=OuterRef('cita__paciente_id')).exclude(respuestas={})
        pendientes = pendientes.annotate(tiene_ipp=Exists(ipp), tiene_ipp_legacy=Exists(legacy_ipp))
        pendientes = pendientes.exclude(error='ipp', tiene_ipp=False, tiene_ipp_legacy=False).exclude(
            error='revision_audio', cita__nota_clinica__pendiente_revision_audio=True)
        if cita_id:
            pendientes = pendientes.filter(cita_id=cita_id)
        pendientes = pendientes.filter(Q(estado__in=['pendiente','espera_reintento','espera_dependencia']) |
            Q(estado__in=['revision_manual','bloqueado'], etapa__in=ETAPAS[:4],
              cita__nota_clinica__notas_sesion__gt='', cita__nota_clinica__pendiente_revision_audio=False)).exclude(error='historico_ambiguo')
        # Ignore blocked earlier stages so they cannot starve later appointments.
        candidatos = pendientes.filter(proximo_intento__lte=ahora).order_by('cita__fecha', 'cita__hora', 'cita_id')
        ids = list(candidatos.values_list('cita_id', flat=True)[:limite])
        for pk in ids:
            cita = flujo._base().get(pk=pk)
            if not elegible(cita):
                continue
            if nota_valida(cita):
                TrabajoProceso.objects.filter(cita=cita, etapa__in=ETAPAS[:4]).exclude(estado__in=['procesando', 'completado']).update(
                    estado='completado', error='', mensaje='Nota existente conservada.')
            for etapa in ETAPAS:
                if time.monotonic()-reloj >= segundos:
                    return {'activo': True, 'etapas': total}
                if not ControlOrquestador.objects.filter(clave='maestro', token=token, vence__gt=timezone.now()).update(vence=timezone.now()+timedelta(minutes=10)):
                    return {'activo':True, 'etapas':total, 'reserva_perdida':True}
                trabajo = TrabajoProceso.objects.get(cita_id=pk, etapa=etapa, version=1)
                if trabajo.estado == 'completado':
                    continue
                if not procesar(trabajo):
                    break
                total += 1
        return {'activo': True, 'etapas': total}
    finally:
        ControlOrquestador.objects.filter(clave='maestro', token=token).update(vence=None, token='')
