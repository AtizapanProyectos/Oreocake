"""Análisis de texto por peticiones acotadas, sin consumidor de audio."""
import json
import logging
import os
import re
from textwrap import wrap
from uuid import uuid4

from django.core.files.base import ContentFile
from django.db import transaction
from django.http import Http404, JsonResponse
from django.views.decorators.http import require_http_methods, require_POST
from django.views.decorators.cache import never_cache

from .analisis_grabacion import CAMPOS, _solicitar, _texto
from .models import AnalisisGrabacionClinica, Cita, GrabacionSesion, HistorialClinico


@require_POST
@never_cache
def transcribir_fragmento(request, cita_id):
    from .views import _cita_grabacion_autorizada
    cita = _cita_grabacion_autorizada(request, cita_id)
    if cita.estado not in ('Confirmada', 'Completada'):
        return JsonResponse({'mensaje': 'Cita no disponible.'}, status=409)
    audio = request.FILES.get('audio')
    if not audio or not 0 < audio.size <= 8 * 1024 * 1024:
        return JsonResponse({'mensaje': 'Envía un fragmento de hasta 8 MB.'}, status=400)
    formatos = {'audio/webm': 'webm', 'audio/ogg': 'ogg', 'audio/mp4': 'm4a', 'audio/wav': 'wav'}
    extension = formatos.get((audio.content_type or '').split(';')[0])
    if not extension:
        return JsonResponse({'mensaje': 'Formato de audio no compatible.'}, status=400)
    if not os.environ.get('GROQ_API_KEY'):
        return JsonResponse({'mensaje': 'Falta configurar GROQ_API_KEY.'}, status=503)
    try:
        from groq import Groq
        resultado = Groq(api_key=os.environ['GROQ_API_KEY'], timeout=20, max_retries=0).audio.transcriptions.create(
            file=('fragmento.' + extension, audio.read()), model='whisper-large-v3-turbo',
            language='es', response_format='json', temperature=0,
        )
        return JsonResponse({'texto': resultado.text})
    except Exception as exc:
        logging.getLogger(__name__).warning('Transcripción remota falló (%s)', type(exc).__name__)
        return JsonResponse({'mensaje': 'No se pudo transcribir el fragmento. Reintenta.'}, status=502)


# Indicadores para seleccionar pasajes literales, sin asignar hablantes ni inferir respuestas.
PISTAS = (
    r'cómo (?:lleg|te sient)|al inicio|estado inicial|me siento',
    r'mencion|refier|coment|ejercicio|trabaj|interven|habl|dificultad',
    r'qué te llev|aprend|me llevo|me di cuenta|me quedo',
    r'cómo te vas|se va|al cierre|al final|termin|desped',
    r'recomiend|recomend|acord|tarea|próxima|practic|siguiente sesión',
)
PATRONES = tuple(re.compile(pista, re.IGNORECASE) for pista in PISTAS)


def seleccionar_evidencia(texto, limite=9000):
    """Combina indicios de cada campo con muestras distribuidas cronológicamente."""
    partes = list(dict.fromkeys(
        pasaje for parte in re.split(r'\n+|(?<=[.!?])\s+', texto)
        for pasaje in wrap(parte.strip(), width=420, break_on_hyphens=False)
    ))
    evidencia = '\n'.join(partes)
    if len(evidencia) <= limite:
        return evidencia

    indices = set()
    for patron in PATRONES:
        coincidencias = [i for i, parte in enumerate(partes) if patron.search(parte)]
        if coincidencias:
            for i in coincidencias[:2] + coincidencias[-2:]:
                indices.update((i, min(i + 1, len(partes) - 1)))
    indices.update(i * (len(partes) - 1) // 7 for i in range(8))
    por_parte = (limite - len(indices)) // len(indices)
    return '\n'.join(partes[i][:por_parte] for i in sorted(indices))


def _generar_notas(cliente, evidencia):
    instruccion = (
        'Devuelve JSON con cinco campos de texto: como_llega (estado inicial), notas_sesion '
        '(temas y trabajo clínico), aprendizaje_paciente (qué se lleva), como_se_va (cierre), '
        'recomendaciones (acuerdos y tareas). Los pasajes pueden ser discontinuos. No atribuyas '
        'respuestas a partir de preguntas solas. Deja vacío lo desconocido.'
    )
    for limite in (2400, 3600):
        try:
            datos = json.loads(_solicitar(
                cliente, instruccion, evidencia, limite,
                reasoning_effort='low', response_format={'type': 'json_object'},
            ))
            if isinstance(datos, dict):
                notas = {campo: _texto(datos.get(campo), 4000) for campo in CAMPOS}
                if any(notas.values()):
                    return notas
        except ValueError:
            continue
    raise ValueError('Sin notas clínicas utilizables')


@require_http_methods(['GET', 'POST'])
@never_cache
def notas_desde_texto(request, cita_id):
    from .views import _cita_grabacion_autorizada
    cita = _cita_grabacion_autorizada(request, cita_id)
    if request.method == 'GET':
        grabacion = GrabacionSesion.objects.filter(cita=cita).first()
        if not grabacion or not grabacion.transcripcion.strip() or grabacion.transcripcion_anterior:
            return JsonResponse({'mensaje': 'No hay texto guardado para el audio actual.'}, status=404)
        return JsonResponse({
            'transcripcion': grabacion.transcripcion,
            'version_audio': grabacion.audio.name,
        })
    if cita.estado not in ('Confirmada', 'Completada'):
        return JsonResponse({'mensaje': 'La cita no admite procesamiento.'}, status=409)
    try:
        datos = json.loads(request.body)
        accion = datos.get('accion')
        texto = datos.get('texto', '')
        if accion not in ('guardar', 'generar', 'fragmento', 'finalizar'):
            raise ValueError()
        if accion in ('fragmento', 'finalizar') and (
            not isinstance(texto, str) or not 1 <= len(texto.strip()) <= 10000
        ):
            raise ValueError()
        transcripcion = datos.get('transcripcion', '')
        if accion in ('guardar', 'finalizar') and (
            not isinstance(transcripcion, str) or not transcripcion.strip() or len(transcripcion) > 500000
        ):
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        return JsonResponse({'mensaje': 'Texto inválido o demasiado largo.'}, status=400)

    version = datos.get('version_audio')
    if accion == 'guardar':
        with transaction.atomic():
            cita, actual = _bloquear_grabacion(request, cita_id)
            if not actual or not version or version != actual.audio.name or not _admite_notas(cita):
                return _grabacion_cambio()
            actual.asociar_transcripcion(transcripcion.strip())
            actual.save(update_fields=['transcripcion', 'transcripcion_audio', 'transcripcion_fecha'])
        return JsonResponse({'mensaje': 'Transcripción guardada en el expediente.'})

    grabacion = GrabacionSesion.objects.filter(cita=cita).first()
    if not grabacion or not version or version != grabacion.audio.name:
        return _grabacion_cambio()
    texto_guardado = grabacion.transcripcion
    if accion == 'generar':
        if not texto_guardado.strip() or grabacion.transcripcion_anterior:
            return JsonResponse({'mensaje': 'Guarda la transcripción del audio actual antes de generar las notas.'}, status=409)
        transcripcion = texto_guardado
        texto = seleccionar_evidencia(transcripcion)
    if not os.environ.get('GROQ_API_KEY'):
        return JsonResponse({'mensaje': 'Falta configurar GROQ_API_KEY en el servidor.'}, status=503)
    try:
        from groq import Groq
        cliente = Groq(api_key=os.environ['GROQ_API_KEY'], timeout=12, max_retries=0)
        # Compatibilidad con páginas de la grabadora abiertas antes de esta versión.
        if accion == 'fragmento':
            evidencia = _solicitar(cliente, 'Resume la evidencia explícita de los cinco apartados en '
                                   'hasta 200 palabras, sin inventar datos.', texto, 1400, reasoning_effort='low')
            return JsonResponse({'evidencia': evidencia})
        notas = _generar_notas(cliente, texto)
    except Exception as exc:
        logging.getLogger(__name__).warning('Análisis de notas falló (%s, etapa %s)', type(exc).__name__, accion)
        return JsonResponse({'mensaje': 'No se obtuvo un borrador válido. El análisis no cambió la transcripción. Puedes reintentar.'}, status=502)

    archivo_nuevo = None
    try:
        with transaction.atomic():
            cita, actual = _bloquear_grabacion(request, cita_id)
            if (not actual or actual.audio.name != version or actual.transcripcion != texto_guardado
                    or not _admite_notas(cita)):
                return _grabacion_cambio()

            historial, _ = HistorialClinico.objects.select_for_update().get_or_create(
                cita=cita,
                defaults={'paciente_id': cita.paciente_id, 'psicologo_id': cita.psicologo_id,
                          'pendiente_revision_audio': True, **notas},
            )
            # Una bitácora existente conserva su texto y cualquier adjunto previo.
            if (historial.psicologo_id == cita.psicologo_id and historial.paciente_id == cita.paciente_id
                    and not historial.transcripcion_meet):
                campo = historial.transcripcion_meet
                campo.save(f'presencial-{uuid4().hex}.txt', ContentFile(transcripcion.strip().encode('utf-8-sig')), save=False)
                archivo_nuevo = campo
                historial.save(update_fields=['transcripcion_meet'])

            if accion == 'finalizar':
                actual.asociar_transcripcion(transcripcion.strip())
            actual.estado = GrabacionSesion.Estado.COMPLETADA
            actual.intento_iniciado = None
            actual.save(update_fields=[
                'transcripcion', 'transcripcion_audio', 'transcripcion_fecha', 'estado', 'intento_iniciado',
            ])
            AnalisisGrabacionClinica.objects.update_or_create(grabacion=actual, defaults={
                'archivo_origen': version, 'estado': AnalisisGrabacionClinica.Estado.LISTO,
                'notas': notas, 'progreso': {'porcentaje': None, 'nivel': '', 'compromiso': '', 'evolucion': ''},
            })
    except Http404:
        raise
    except Exception as exc:
        if archivo_nuevo is not None:
            try:
                archivo_nuevo.delete(save=False)
            except OSError:
                logging.getLogger(__name__).warning('No se pudo limpiar el adjunto de transcripción')
        logging.getLogger(__name__).warning('Falló el guardado del borrador clínico (%s)', type(exc).__name__)
        return JsonResponse({'mensaje': 'No se pudo guardar el borrador. Puedes reintentar con el texto guardado.'}, status=500)
    return JsonResponse({'notas': notas, 'mensaje': 'Borrador guardado. Revisa las notas en el expediente.'})


def _admite_notas(cita):
    return cita.modalidad == 'Presencial' and cita.estado in ('Confirmada', 'Completada')


def _grabacion_cambio():
    return JsonResponse({'mensaje': 'La grabación o su texto cambiaron. Recarga el texto guardado antes de continuar.'}, status=409)


def _bloquear_grabacion(request, cita_id):
    # Mismo orden de bloqueo que el reemplazo de audio.
    cita = Cita.objects.select_for_update().filter(pk=cita_id).first()
    if not cita or not (request.user.is_superuser or
                        (cita.psicologo_id and cita.psicologo.usuario_id == request.user.id)):
        raise Http404
    return cita, GrabacionSesion.objects.select_for_update().filter(cita=cita).first()
