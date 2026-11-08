"""Análisis de texto por peticiones acotadas, sin consumidor de audio."""
import json
import logging
import os

from django.db import transaction
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.views.decorators.cache import never_cache

from .analisis_grabacion import CAMPOS, _solicitar, _texto
from .models import AnalisisGrabacionClinica, GrabacionSesion


@require_POST
@never_cache
def notas_desde_texto(request, cita_id):
    from .views import _cita_grabacion_autorizada
    cita = _cita_grabacion_autorizada(request, cita_id)
    if cita.estado not in ('Confirmada', 'Completada'):
        return JsonResponse({'mensaje': 'La cita no admite procesamiento.'}, status=409)
    try:
        datos = json.loads(request.body)
        accion = datos.get('accion')
        texto = datos.get('texto', '')
        if accion not in ('fragmento', 'finalizar') or not isinstance(texto, str) or not 1 <= len(texto.strip()) <= 10000:
            raise ValueError()
        transcripcion = datos.get('transcripcion', '')
        if accion == 'finalizar' and (not isinstance(transcripcion, str) or not 1 <= len(transcripcion.strip()) <= 500000):
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        return JsonResponse({'mensaje': 'Texto inválido o demasiado largo.'}, status=400)
    grabacion = GrabacionSesion.objects.filter(cita=cita).first()
    if not grabacion or datos.get('version_audio') != grabacion.audio.name:
        return JsonResponse({'mensaje': 'Guarda el audio actual antes de generar las notas.'}, status=409)
    if not os.environ.get('GROQ_API_KEY'):
        return JsonResponse({'mensaje': 'Falta configurar GROQ_API_KEY en el servidor.'}, status=503)
    try:
        from groq import Groq
        cliente = Groq(api_key=os.environ['GROQ_API_KEY'], timeout=20, max_retries=0)
        if accion == 'fragmento':
            evidencia = _solicitar(cliente, 'Resume en máximo 250 palabras la evidencia de los cinco apartados '
                                   'clínicos. Conserva dudas y negaciones. No inventes datos ni hablantes.', texto, 500)
            return JsonResponse({'evidencia': evidencia})
        respuesta = json.loads(_solicitar(
            cliente, 'Devuelve solo JSON con cinco campos de texto: como_llega, notas_sesion, '
            'aprendizaje_paciente, como_se_va, recomendaciones. Sintetiza la evidencia sin inventar '
            'datos; deja vacío lo desconocido. La fuente es una transcripción automática revisable.',
            texto, 2000, response_format={'type': 'json_object'},
        ))
        notas = {campo: _texto(respuesta.get(campo), 4000) for campo in CAMPOS}
        if not any(notas.values()):
            raise ValueError('Sin notas')
    except Exception as exc:
        logging.getLogger(__name__).warning('Análisis de texto falló (%s)', type(exc).__name__)
        return JsonResponse({'mensaje': 'Groq no completó este paso. Tu texto sigue en pantalla; puedes reintentar.'}, status=502)
    with transaction.atomic():
        # Revalidar permisos y versión después de la llamada externa.
        cita = _cita_grabacion_autorizada(request, cita_id)
        actual = GrabacionSesion.objects.select_for_update().get(pk=grabacion.pk)
        if actual.audio.name != datos['version_audio'] or cita.estado not in ('Confirmada', 'Completada'):
            return JsonResponse({'mensaje': 'La grabación cambió. Recarga antes de continuar.'}, status=409)
        actual.transcripcion = transcripcion.strip()
        actual.estado = GrabacionSesion.Estado.COMPLETADA
        actual.intento_iniciado = None
        actual.save(update_fields=['transcripcion', 'estado', 'intento_iniciado'])
        AnalisisGrabacionClinica.objects.update_or_create(grabacion=actual, defaults={
            'archivo_origen': actual.audio.name, 'estado': AnalisisGrabacionClinica.Estado.LISTO,
            'notas': notas, 'progreso': {'porcentaje': None, 'nivel': '', 'compromiso': '', 'evolucion': ''},
        })
    respuesta = JsonResponse({'notas': notas, 'mensaje': 'Borrador guardado. Revisa las notas en el expediente.'})
    respuesta['Cache-Control'] = 'private, no-store'
    return respuesta
