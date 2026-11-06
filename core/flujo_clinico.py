"""Monitor y ejecución MANUAL por etapas. Los comandos programados no se modifican."""
import json
import os
import re
from datetime import datetime, timedelta
from functools import wraps

from django.contrib.auth.models import User
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q, Prefetch
from django.http import JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import (Cita, EstadoFlujoSesion, HistorialClinico,
                     LogAutomatizacionNotas)

ETAPAS = [('meet', 'Google Meet', 'video'), ('lectura', 'Notas', 'file-lines'),
          ('ia', 'Groq', 'arrows-rotate'), ('escritura', 'Expediente', 'folder-open'),
          ('ipp', 'IPP de la sesión', 'chart-line'), ('reporte', 'Reportes PDF', 'file-pdf'),
          ('correo', 'Correo', 'envelope')]
CAMPOS = ('como_llega', 'notas_sesion', 'aprendizaje_paciente', 'como_se_va', 'recomendaciones')
DESTINATARIOS = {'paciente', 'psicologo'}
ERRORES = {'error', 'sin_permisos', 'conflicto'}
LABELS = {'exitoso': 'Completado', 'pendiente': 'Pendiente', 'error': 'Error',
          'sin_permisos': 'Sin acceso', 'ignorado': 'No requerido', 'conflicto': 'Bloqueado'}


def solo_admin(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated or not request.user.is_superuser:
            return JsonResponse({'status': 'error', 'mensaje': 'Acceso exclusivo de administración.'}, status=403)
        return view(request, *args, **kwargs)
    return wrapped


class PasoBloqueado(Exception):
    def __init__(self, mensaje, estado='pendiente'):
        super().__init__(mensaje)
        self.estado = estado


def _inicio(cita):
    return timezone.make_aware(datetime.combine(cita.fecha, cita.hora))


def _ipp(cita):
    evaluacion = getattr(cita, 'evaluacion_servicio', None)
    if evaluacion and evaluacion.paciente_id == cita.paciente_id and evaluacion.respuestas_ipp:
        return evaluacion
    return next((r for r in cita.respuestas_formulario_organico.all()
                 if r.paciente_id == cita.paciente_id and r.respuestas), None)


def _enviados(cita):
    return {r.tipo_destinatario for r in cita.reportes_pdf.all() if r.correo_enviado}


def _pdfs(cita):
    resultado = {}
    for reporte in sorted(cita.reportes_pdf.all(), key=lambda r: r.pk, reverse=True):
        resultado.setdefault(reporte.tipo_destinatario, reporte)
    return resultado


def _archivo_disponible(row):
    try:
        return bool(row and row.archivo_pdf and row.archivo_pdf.storage.exists(row.archivo_pdf.name))
    except (OSError, ValueError):
        return False


def _base():
    return Cita.objects.select_related(
        'paciente__perfil', 'psicologo__usuario', 'nota_clinica',
        'evaluacion_servicio', 'flujo_manual',
    ).prefetch_related(
        Prefetch('logs_automatizacion', queryset=LogAutomatizacionNotas.objects.select_related('usuario').order_by('-fecha', '-id')),
        'logs_envio_ipp__usuario', 'reportes_pdf', 'respuestas_formulario_organico',
    )


def evaluar(cita):
    from .views import _amn_organizacion_de_cita, _amn_evaluar_cita
    nota = getattr(cita, 'nota_clinica', None)
    escrita = bool(nota and nota.notas_sesion.strip())
    revision = bool(nota and getattr(nota, 'pendiente_revision_audio', False))
    flujo = getattr(cita, 'flujo_manual', None)
    logs = list(cita.logs_automatizacion.all())
    ultimos = {}
    for log in sorted(logs, key=lambda l: (l.fecha, l.pk), reverse=True):
        if (log.etapa in ('reporte', 'correo') and log.estado == 'conflicto'
                and (log.detalle or '').startswith('Primero complete la cita #')):
            continue  # El bloqueo histórico se conserva en la bitácora.
        ultimos.setdefault(log.etapa, log)
    automatica = any(l.etapa == 'escritura' and l.estado == 'exitoso' for l in logs)
    fuente = ('Nota clínica existente (autoría histórica no verificable)' if escrita and not automatica
              else 'Bitácora automática registrada' if escrita else 'Google Meet')
    if flujo and flujo.fuente == 'nota_existente':
        fuente = 'Notas del expediente: contenido conservado'
    pasos = []
    for key, label, icon in ETAPAS:
        log = ultimos.get(key)
        estado = log.estado if log else 'pendiente'
        detalle = (log.detalle or '') if log else 'Sin ejecución registrada.'
        fecha = timezone.localtime(log.fecha).isoformat() if log else None
        if flujo and key in flujo.fechas:
            fecha = flujo.fechas[key]
        if escrita and key in ('meet', 'lectura', 'ia'):
            estado, detalle = 'ignorado', 'Se utiliza la nota clínica guardada; no requiere acceso a Meet ni nueva IA.'
        elif key == 'meet' and not cita.id_evento_google:
            estado, detalle = 'pendiente', 'Sin evento de Google Meet. Puede continuarse con una nota capturada en el expediente.'
        elif key == 'lectura' and flujo and flujo.texto_notas and estado not in ERRORES:
            estado, detalle = 'exitoso', 'Notas extraídas y guardadas para procesar.'
        elif key == 'ia' and flujo and flujo.datos_clinicos and estado not in ERRORES:
            estado, detalle = 'exitoso', 'Cinco campos estructurados; listos para guardar en el expediente.'
        elif key == 'ia' and flujo and not flujo.datos_clinicos and estado == 'exitoso':
            estado, detalle, fecha = 'pendiente', 'Las notas se extrajeron nuevamente; ejecute Groq con la nueva versión.', None
        elif key == 'escritura' and escrita:
            estado, detalle = ('conflicto', 'La nota de audio requiere revisión del psicólogo.') if revision else ('exitoso', fuente)
            fecha = timezone.localtime(nota.fecha_registro).isoformat()
        elif key == 'ipp':
            ipp = _ipp(cita)
            estado, detalle = ('exitoso', 'Formulario vinculado a esta sesión y paciente.') if ipp else ('pendiente', 'Falta el IPP de esta sesión; el panel detiene reportes y correo hasta recibirlo.')
            if ipp:
                fecha = timezone.localtime(ipp.fecha_respuesta).isoformat()
        elif key == 'reporte':
            pdfs = _pdfs(cita)
            if DESTINATARIOS.issubset(pdfs):
                disponibles = all(_archivo_disponible(pdfs[t]) for t in DESTINATARIOS)
                if disponibles and estado not in ERRORES:
                    estado, detalle = 'exitoso', 'Ambos PDF disponibles.'
                elif not disponibles:
                    estado, detalle = 'error', 'Hay registros de PDF, pero faltan archivos en almacenamiento. Regenerar reportes.'
                fecha = timezone.localtime(max(r.fecha_generacion for r in pdfs.values())).isoformat()
        elif key == 'correo':
            enviados = _enviados(cita)
            if DESTINATARIOS.issubset(enviados):
                estado, detalle = 'exitoso', 'Envío registrado para paciente y psicólogo.'
                exitos = [l.fecha for l in cita.logs_envio_ipp.all() if l.resultado == 'Exitoso']
                if exitos:
                    fecha = timezone.localtime(max(exitos)).isoformat()
            else:
                detalle = 'Pendiente: ' + ', '.join(sorted(DESTINATARIOS - enviados)) + '.'
                ultimo_correo = sorted(cita.logs_envio_ipp.all(), key=lambda l: (l.fecha, l.pk), reverse=True)
                por_tipo = {}
                for item in ultimo_correo:
                    por_tipo.setdefault(item.tipo_destinatario, item)
                if any(l.resultado == 'Error' for t, l in por_tipo.items() if t not in enviados):
                    estado = 'error'
        pasos.append({'key': key, 'label': label, 'icon': icon, 'estado': estado,
                      'estado_label': LABELS.get(estado, estado), 'detalle': detalle, 'fecha': fecha})
    terminado = DESTINATARIOS.issubset(_enviados(cita))
    error = any(p['estado'] in ERRORES for p in pasos)
    estado = 'exitoso' if terminado else 'error' if error else 'pendiente'
    pendiente = next((p for p in pasos if p['estado'] not in ('exitoso', 'ignorado')), pasos[-1])
    minutos = int((timezone.now() - _inicio(cita)).total_seconds() // 60)
    return {
        'id': cita.pk, 'paciente': cita.paciente.get_full_name() or cita.paciente.username,
        'psicologo': (cita.psicologo.usuario.get_full_name() or cita.psicologo.usuario.username) if cita.psicologo else 'Sin asignar',
        'org': _amn_organizacion_de_cita(cita), 'fecha': cita.fecha.isoformat(),
        'hora': cita.hora.strftime('%H:%M'), 'estado': estado, 'etapa': pendiente['label'],
        'pasos': pasos, 'fuente': fuente, 'edad': f'{max(0, minutos) // 60} h',
        'prioridad': 'Alta' if error or minutos > 24 * 60 else 'Normal',
        'elegible': minutos >= 120 and cita.estado in ('Confirmada', 'Completada'),
        'detalle_url': reverse('detalle_paciente', args=[cita.paciente_id]),
        'api_url': reverse('api_reintento_automatizacion', args=[cita.pk]),
        'eventos': _amn_evaluar_cita(cita)['eventos'],
    }


def _filtrar(params):
    qs = _base().filter(fecha__lte=timezone.localdate(), estado__in=['Confirmada', 'Completada'])
    for parametro, lookup in [('fecha', 'fecha'), ('desde', 'fecha__gte'), ('hasta', 'fecha__lte')]:
        if params.get(parametro):
            try:
                valor = datetime.strptime(params[parametro], '%Y-%m-%d').date()
            except ValueError:
                raise ValueError('Fecha inválida: ' + parametro)
            qs = qs.filter(**{lookup: valor})
    if params.get('desde') and params.get('hasta') and params['desde'] > params['hasta']:
        raise ValueError('La fecha inicial debe ser anterior o igual a la final.')
    for parametro, relacion in [('paciente', 'paciente'), ('psicologo', 'psicologo__usuario')]:
        valor = params.get(parametro, '').strip()
        if valor:
            qs = qs.filter(Q(**{relacion + '__first_name__icontains': valor}) |
                           Q(**{relacion + '__last_name__icontains': valor}) |
                           Q(**{relacion + '__username__icontains': valor}) |
                           Q(**{relacion + '__email__icontains': valor}))
    return qs.order_by('fecha', 'hora', 'pk')


def _filas(params):
    from .views import _amn_organizacion_de_cita
    for cita in _filtrar(params):
        org = params.get('org', 'todas')
        if org != 'todas' and org != _amn_organizacion_de_cita(cita):
            continue
        fila = evaluar(cita)
        estado = params.get('estado', 'todos')
        if estado != 'todos' and estado != fila['estado']:
            continue
        yield fila


@solo_admin
def panel(request):
    error = ''
    try:
        filas = list(_filas(request.GET))
    except ValueError as exc:
        filas, error = [], str(exc)
    resumen = []
    for index, (key, label, icon) in enumerate(ETAPAS):
        pasos = [f['pasos'][index] for f in filas]
        resumen.append({'key': key, 'label': label, 'icon': icon,
                        'procesados': sum(p['estado'] in ('exitoso', 'ignorado') for p in pasos),
                        'errores': sum(p['estado'] in ERRORES for p in pasos),
                        'pendientes': sum(p['estado'] not in ERRORES | {'exitoso', 'ignorado'} for p in pasos),
                        'fecha': max((p['fecha'] for p in pasos if p['fecha']), default=None)})
    page = Paginator(filas, 24).get_page(request.GET.get('page'))
    filtros = {key: request.GET.get(key, '') for key in ('fecha', 'desde', 'hasta', 'paciente', 'psicologo')}
    filtros.update(org=request.GET.get('org', 'todas'), estado=request.GET.get('estado', 'todos'))
    qs = request.GET.copy()
    qs.pop('page', None)
    return render(request, 'panel_admin_automatizacion_notas.html', {
        'filas': list(page), 'page_obj': page, 'filtros': filtros, 'resumen': resumen,
        'total': len(filas), 'errores': sum(f['estado'] == 'error' for f in filas),
        'pendientes': sum(f['estado'] != 'exitoso' for f in filas),
        'querystring': qs.urlencode(), 'error_filtro': error,
    })


@solo_admin
@require_POST
def preparar_lote(request):
    """Congela IDs ordenados; el navegador pide UN paso por request, sin cola nueva."""
    limite = request.POST.get('limite', '1')
    if limite not in ('1', '2', '5', '10', 'todos'):
        return JsonResponse({'mensaje': 'Límite inválido.'}, status=400)
    try:
        filas = [f for f in _filas(request.POST) if f['elegible'] and f['estado'] != 'exitoso'
                 and (request.POST.get('fallidos') != '1' or f['estado'] == 'error')]
    except ValueError as exc:
        return JsonResponse({'mensaje': str(exc)}, status=400)
    if limite != 'todos':
        filas = filas[:int(limite)]
    return JsonResponse({'items': [{'id': f['id'], 'url': f['api_url']} for f in filas]})


def _credenciales():
    from google.oauth2.credentials import Credentials
    token = os.environ.get('GOOGLE_TOKEN_JSON')
    if not token:
        raise PasoBloqueado('Falta configurar la conexión con Google.', 'sin_permisos')
    return Credentials.from_authorized_user_info(json.loads(token))


def _leer_meet(cita):
    from googleapiclient.discovery import build
    if not cita.id_evento_google:
        raise PasoBloqueado('La sesión no tiene evento de Meet. Capture las notas en el expediente.')
    try:
        event = build('calendar', 'v3', credentials=_credenciales(), cache_discovery=False).events().get(
            calendarId='primary', eventId=cita.id_evento_google).execute()
    except Exception as exc:
        status = getattr(getattr(exc, 'resp', None), 'status', None)
        if status in (401, 403, 404):
            raise PasoBloqueado('Google no permite consultar el evento, o no existe para la cuenta conectada.', 'sin_permisos') from exc
        raise
    return [{'title': a.get('title', 'Notas de Meet'), 'fileUrl': a.get('fileUrl', '')}
            for a in event.get('attachments', [])]


def _leer_documentos(adjuntos):
    # Solo exportación autenticada. Nunca se interpreta una pantalla HTML de login como nota clínica.
    from google.auth.transport.requests import AuthorizedSession
    partes = []
    sin_acceso = False
    with AuthorizedSession(_credenciales()) as session:
        for adjunto in adjuntos:
            match = re.fullmatch(r'https://(?:docs|drive)\.google\.com/(?:document|file)/d/([\w-]+)(?:[/?#].*)?', adjunto['fileUrl'])
            if not match:
                continue
            response = session.get(f'https://www.googleapis.com/drive/v3/files/{match.group(1)}/export',
                                   params={'mimeType': 'text/plain'}, timeout=25)
            if response.status_code in (401, 403, 404):
                sin_acceso = True
                continue
            response.raise_for_status()
            if 'text/html' in response.headers.get('Content-Type', '').lower() or '<html' in response.text[:200].lower():
                raise PasoBloqueado('Google devolvió una página web en lugar de notas.', 'sin_permisos')
            if response.text.strip():
                partes.append(response.text.strip())
    if sin_acceso:
        raise PasoBloqueado('Google no permite exportar uno o más documentos (privados, inexistentes o sin permiso OAuth de lectura de Drive). Revise el acceso de la cuenta conectada o use la nota del expediente.', 'sin_permisos')
    if not partes:
        raise PasoBloqueado('No hay texto extraíble en los documentos adjuntos.')
    return '\n\n'.join(partes)


def _prerrequisitos(cita, tipo):
    if cita.estado not in ('Confirmada', 'Completada') or timezone.now() < _inicio(cita) + timedelta(hours=2):
        raise PasoBloqueado('La sesión debe ser confirmada/completada y cumplir 2 horas desde su inicio.')
    nota = getattr(cita, 'nota_clinica', None)
    if nota and getattr(nota, 'pendiente_revision_audio', False):
        raise PasoBloqueado('El psicólogo debe revisar la nota de audio antes de continuar.', 'conflicto')
    if tipo in ('reporte', 'correo'):
        if not nota or not nota.notas_sesion.strip():
            raise PasoBloqueado('Falta la nota clínica de esta sesión.')
        if not _ipp(cita):
            raise PasoBloqueado('Falta el IPP correspondiente a esta sesión.')


def ejecutar_paso(cita, tipo, usuario):
    from . import views
    _prerrequisitos(cita, tipo)
    flujo, _ = EstadoFlujoSesion.objects.get_or_create(cita=cita)
    nota = getattr(cita, 'nota_clinica', None)
    escrita = bool(nota and nota.notas_sesion.strip())
    if escrita and tipo in ('meet', 'lectura', 'ia', 'escritura'):
        flujo.fuente = 'nota_existente'
        flujo.datos_clinicos = {campo: getattr(nota, campo) or '' for campo in CAMPOS}
        mensaje = 'Se extrajeron los campos de la nota clínica existente; se conserva el contenido escrito por el psicólogo o registrado previamente.'
    elif tipo == 'meet':
        flujo.adjuntos = _leer_meet(cita)
        mensaje = f'Evento accesible. {len(flujo.adjuntos)} documento(s) adjunto(s).'
    elif tipo == 'lectura':
        if not flujo.adjuntos:
            raise PasoBloqueado('Verifique Meet primero; aún no hay documentos adjuntos disponibles.')
        flujo.texto_notas = _leer_documentos(flujo.adjuntos)
        flujo.datos_clinicos = {}
        flujo.fechas.pop('ia', None)
        flujo.fuente = 'google_meet'
        mensaje = 'Notas extraídas. Puede ejecutar Groq.'
    elif tipo == 'ia':
        if not flujo.texto_notas:
            raise PasoBloqueado('Extraiga las notas antes de ejecutar Groq.')
        datos = views._generar_bitacora_con_ia_de_texto(flujo.texto_notas, paciente_nombre=cita.paciente.first_name)
        if not isinstance(datos, dict) or not all(isinstance(datos.get(c), str) for c in CAMPOS) or not datos['notas_sesion'].strip():
            raise PasoBloqueado('Groq no devolvió los cinco campos clínicos válidos; no se escribió el expediente.', 'error')
        flujo.datos_clinicos = {c: datos[c] for c in CAMPOS}
        mensaje = 'Contenido estructurado y guardado; pendiente de escribir en el expediente.'
    elif tipo == 'escritura':
        datos = flujo.datos_clinicos
        if not datos or not datos.get('notas_sesion', '').strip():
            raise PasoBloqueado('Ejecute Groq antes de guardar el expediente.')
        historial, _ = HistorialClinico.objects.select_for_update().get_or_create(
            cita=cita, defaults={'paciente': cita.paciente, 'psicologo': cita.psicologo, 'notas_sesion': ''})
        # Releer bajo bloqueo: nunca sobreescribir texto capturado durante el procesamiento.
        for campo in CAMPOS:
            if not (getattr(historial, campo) or '').strip():
                setattr(historial, campo, datos.get(campo, ''))
        historial.save(update_fields=list(CAMPOS))
        Cita.objects.filter(pk=cita.pk).update(estado='Completada')
        mensaje = 'Expediente completado; se conservaron todos los campos que ya tenían contenido.'
    elif tipo == 'ipp':
        if not _ipp(cita):
            raise PasoBloqueado('Falta el IPP de esta sesión. No se sustituye con el de otra cita.')
        mensaje = 'IPP verificado para esta sesión y paciente.'
    elif tipo == 'reporte':
        pdfs = _pdfs(cita)
        if DESTINATARIOS.issubset(pdfs) and all(_archivo_disponible(pdfs[t]) for t in DESTINATARIOS):
            mensaje = 'Ambos PDF ya están disponibles; no se regeneraron ni enviaron.'
        else:
            ok, mensaje, _ = views._procesar_reporte_de_una_cita_detallado(cita.pk, forzar=True, solo_generar=True)
            if not ok:
                raise PasoBloqueado(mensaje, 'error')
    elif tipo == 'correo':
        pdfs, enviados = _pdfs(cita), _enviados(cita)
        for destinatario in sorted(DESTINATARIOS - enviados):
            row = pdfs.get(destinatario)
            if not _archivo_disponible(row):
                raise PasoBloqueado(f'Falta el PDF de {destinatario}. Ejecute Reportes PDF antes de enviar.')
            email = cita.paciente.email if destinatario == 'paciente' else (cita.psicologo.usuario.email if cita.psicologo else '')
            if not email:
                raise PasoBloqueado(f'Falta el correo de {destinatario}.', 'error')
            with row.archivo_pdf.open('rb') as archivo:
                contenido = archivo.read()
            tectum = cita.es_tectum or getattr(getattr(cita.paciente, 'perfil', None), 'es_tectum', False)
            nombre = os.path.basename(row.archivo_pdf.name)
            try:
                if destinatario == 'paciente':
                    views._enviar_email_reporte_paciente_con_pdf(cita.paciente, row.datos_ia_snapshot, contenido, nombre, es_tectum=tectum)
                else:
                    views._enviar_email_reporte_psicologo_con_pdf(cita.psicologo, cita.paciente, row.datos_ia_snapshot, contenido, nombre, es_tectum=tectum)
            except Exception:
                views._log_envio_ipp_seguro(cita, destinatario, 'Panel de flujo manual', 'Error', 'El proveedor de correo rechazó o no confirmó el envío.')
                raise PasoBloqueado(f'No se confirmó el envío a {destinatario}. Revise el servicio de correo.', 'error')
            row.correo_enviado = True
            row.save(update_fields=['correo_enviado'])
            views._log_envio_ipp_seguro(cita, destinatario, 'Panel de flujo manual', 'Exitoso', 'Correo enviado con PDF adjunto.')
        Cita.objects.filter(pk=cita.pk).update(reporte_enviado=True)
        mensaje = 'Ambos destinatarios tienen envío registrado; solo se enviaron los pendientes.'
    flujo.fechas[tipo] = timezone.localtime().isoformat()
    flujo.save()
    return mensaje


@solo_admin
@require_POST
def reintento(request, cita_id):
    """Un paso por request. El cliente encadena los pasos de un flujo completo."""
    from . import views
    tipo = request.POST.get('tipo', '')
    if tipo not in dict((key, label) for key, label, _ in ETAPAS):
        return JsonResponse({'status': 'error', 'mensaje': 'Seleccione una etapa válida.'}, status=400)
    paciente_id = Cita.objects.filter(pk=cita_id).values_list('paciente_id', flat=True).first()
    if not paciente_id:
        return JsonResponse({'status': 'error', 'mensaje': 'Cita no encontrada.'}, status=404)
    # Bloqueo compartido por paciente en todos los workers del panel, también entre citas.
    with transaction.atomic():
        User.objects.select_for_update().get(pk=paciente_id)
        Cita.objects.select_for_update().get(pk=cita_id)
        cita = _base().get(pk=cita_id)
        error_tecnico = None
        try:
            mensaje = ejecutar_paso(cita, tipo, request.user)
            estado = 'exitoso'
        except PasoBloqueado as exc:
            mensaje, estado = str(exc), exc.estado
        except Exception as exc:
            # No registrar texto clínico ni tokens en las bitácoras del panel.
            mensaje, estado = 'No se pudo completar esta etapa. Revise la conexión del servicio y reintente.', 'error'
            status = getattr(getattr(exc, 'resp', None), 'status', None)
            error_tecnico = type(exc).__name__ + (f' (HTTP {status})' if status else '')
        views._reg_log_auto(cita, etapa=tipo, estado=estado, origen='manual',
                            detalle=mensaje, error_tecnico=error_tecnico,
                            usuario=request.user, ip=views._amn_ip_del_request(request))
    return JsonResponse({'status': 'success' if estado == 'exitoso' else 'error',
                         'mensaje': mensaje, 'estado': estado, 'fila': evaluar(_base().get(pk=cita_id))})
