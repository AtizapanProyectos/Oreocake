"""Procesamiento local de grabaciones presenciales con faster-whisper.

El audio se transcribe dentro del proceso web (hilo daemon) usando el modelo
local de faster-whisper. No se llama a Groq ni se necesitan binarios externos
(ffmpeg / whisper-cli): ffmpeg viene empaquetado en PyAV, dependencia de
faster-whisper.
"""
import datetime
import logging
import os
from pathlib import Path
import re
import shutil
import tempfile
import threading

from django.conf import settings
from django.db import close_old_connections, connections, transaction
from django.db.models import Q
from django.utils import timezone

from .models import Cita, GrabacionSesion, HistorialClinico

logger = logging.getLogger(__name__)

Estado = GrabacionSesion.Estado
EN_PROCESO = (Estado.AUDIO, Estado.TRANSCRIBIENDO, Estado.NOTAS, Estado.GUARDANDO)
LIMITE_AUDIO = 12 * 3600
# Un intento abandonado (worker reiniciado, deploy, OOM) se puede reclamar
# después de este tiempo. El reintento manual no espera: pone la fila en
# 'cargada' y relanza el procesamiento.
LEASE_PROCESO = datetime.timedelta(hours=6)
# Un intento en curso puede reintentarse manualmente después de este tiempo
# (deploy/reinicio del contenedor a mitad de la transcripción).
ESPERA_REINTENTO = datetime.timedelta(minutes=30)
CONTENEDORES = {"webm": "matroska", "ogg": "ogg", "m4a": "mov", "wav": "wav", "mp3": "mp3"}
SECCIONES = {
    "como_llega": r"(?:cómo llega|al inicio|estado inicial)",
    "aprendizaje_paciente": r"(?:qué se lleva|qué te llevas|aprendizaje)",
    "como_se_va": r"(?:cómo se va|al cierre|al finalizar|cierre)",
    "recomendaciones": r"(?:recomendaciones|acuerdos|tareas para la próxima sesión)",
}

_modelo_whisper = None
_cerrojo_modelo = threading.Lock()
_hilo_procesamiento = None
_cerrojo_hilo = threading.Lock()


def estructurar_notas(transcripcion):
    """Conserva pasajes literales y solo clasifica etiquetas reconocibles."""
    notas = {campo: [] for campo in (*SECCIONES, "notas_sesion")}
    for fragmento in re.split(r"(?<=[.!?])\s+|\n+", transcripcion.strip()):
        if not fragmento:
            continue
        for campo, patron in SECCIONES.items():
            if re.match(rf"^{patron}(?:\s*[:,]|\s)", fragmento, re.IGNORECASE):
                notas[campo].append(fragmento)
                break
        else:
            notas["notas_sesion"].append(fragmento)
    # La fuente completa queda en GrabacionSesion. Acota el extracto de desarrollo.
    desarrollo = notas["notas_sesion"]
    if not desarrollo:
        desarrollo = [fragmento for campo in SECCIONES for fragmento in notas[campo]]
    if len(desarrollo) > 12:
        desarrollo = [desarrollo[indice * (len(desarrollo) - 1) // 11] for indice in range(12)]
    notas["notas_sesion"] = desarrollo
    return {campo: "\n".join(fragmentos) for campo, fragmentos in notas.items()}


def configuracion_modelo():
    """Nombre del modelo y carpeta de caché (persistente si hay volumen)."""
    nombre = (os.environ.get("HOPE_WHISPER_MODELO") or "tiny").strip() or "tiny"
    carpeta = (os.environ.get("HOPE_WHISPER_DIR") or "").strip()
    if not carpeta:
        base = getattr(settings, "GRABACIONES_ROOT", None)
        if not base:
            base = Path(settings.BASE_DIR) / "grabaciones_privadas"
        # Dentro del volumen de grabaciones para no re-descargar el modelo en
        # cada deploy/arranque del contenedor.
        carpeta = str(Path(str(base)) / "_modelos_whisper")
    return nombre, carpeta


def _entero_env(nombre, defecto):
    try:
        return max(1, int(os.environ.get(nombre, defecto)))
    except (TypeError, ValueError):
        return defecto


def _cargar_modelo():
    """Carga (y cachea por proceso) el modelo local de faster-whisper."""
    global _modelo_whisper
    if _modelo_whisper is not None:
        return _modelo_whisper

    with _cerrojo_modelo:
        if _modelo_whisper is not None:
            return _modelo_whisper
        try:
            from faster_whisper import WhisperModel
        except ImportError as error:
            raise RuntimeError(
                "Falta la dependencia faster-whisper. Instala requirements.txt."
            ) from error

        nombre, carpeta = configuracion_modelo()
        Path(carpeta).mkdir(parents=True, exist_ok=True)
        logger.info("Cargando modelo Whisper '%s' desde %s", nombre, carpeta)
        try:
            modelo = WhisperModel(
                nombre,
                device=os.environ.get("HOPE_WHISPER_DISPOSITIVO", "cpu"),
                compute_type=os.environ.get("HOPE_WHISPER_PRECISION", "int8"),
                cpu_threads=_entero_env("HOPE_WHISPER_HILOS", 1),
                num_workers=1,
                download_root=carpeta,
            )
        except Exception as error:
            raise RuntimeError(
                f"No se pudo cargar el modelo Whisper '{nombre}': {error}"
            ) from error
        _modelo_whisper = modelo
        logger.info("Modelo Whisper '%s' listo", nombre)
        return modelo


def transcribir(grabacion, avanzar):
    """Transcribe el audio de la grabación y devuelve el texto plano."""
    formato = (grabacion.formato or "").lower()
    if formato not in CONTENEDORES:
        raise ValueError("Formato de audio no admitido")

    with tempfile.TemporaryDirectory(prefix="hope_presencial_") as carpeta:
        origen = Path(carpeta) / f"audio.{formato}"
        with grabacion.audio.open("rb") as entrada, origen.open("wb") as archivo:
            shutil.copyfileobj(entrada, archivo, length=1024 * 1024)
        if origen.stat().st_size == 0:
            raise ValueError("El audio está vacío")

        if not avanzar(Estado.TRANSCRIBIENDO):
            return ""

        modelo = _cargar_modelo()
        segmentos, info = modelo.transcribe(
            str(origen),
            language="es",
            beam_size=1,
            best_of=1,
            temperature=0.0,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500},
            condition_on_previous_text=False,
            without_timestamps=True,
            initial_prompt="Transcripción de una sesión de psicología en español.",
        )
        duracion = float(getattr(info, "duration", 0) or 0)
        if duracion > LIMITE_AUDIO:
            raise ValueError("Duración de audio fuera de rango")

        partes = []
        for segmento in segmentos:
            texto = (segmento.text or "").strip()
            if texto:
                partes.append(texto)
        return " ".join(partes).strip()


def procesar_siguiente():
    """Toma la siguiente grabación pendiente y la procesa. True si había trabajo."""
    ahora = timezone.now()
    vencido = ahora - LEASE_PROCESO
    disponibles = GrabacionSesion.objects.filter(
        Q(estado=Estado.CARGADA) | Q(estado__in=EN_PROCESO, intento_iniciado__lt=vencido)
    )
    grabacion = disponibles.order_by("fecha_subida", "pk").first()
    if grabacion is None:
        return False
    # Reclamo atómico: dos workers/procesos no procesan la misma fila.
    if not disponibles.filter(pk=grabacion.pk, audio=grabacion.audio.name).update(
        estado=Estado.AUDIO, intento_iniciado=ahora,
    ):
        return True

    intento = GrabacionSesion.objects.filter(pk=grabacion.pk, intento_iniciado=ahora)

    def avanzar(estado):
        close_old_connections()
        return intento.update(estado=estado)

    try:
        texto = transcribir(grabacion, avanzar)
        if not texto:
            raise ValueError("Sin texto transcrito")
        if not avanzar(Estado.NOTAS):
            return True
        notas = estructurar_notas(texto)
        if not avanzar(Estado.GUARDANDO):
            return True
        with transaction.atomic():
            cita = Cita.objects.select_for_update().filter(pk=grabacion.cita_id).first()
            actual = intento.select_for_update().first()
            if actual is None:
                return True
            if (cita is None or cita.modalidad != "Presencial" or
                    cita.estado not in ("Confirmada", "Completada") or
                    cita.psicologo_id != grabacion.psicologo_id):
                intento.update(estado=Estado.ERROR)
                return True
            # Solo reemplaza la bitácora si todavía es un borrador generado desde audio.
            historial, creado = HistorialClinico.objects.select_for_update().get_or_create(
                cita=cita,
                defaults={
                    "paciente_id": cita.paciente_id, "psicologo_id": cita.psicologo_id,
                    "pendiente_revision_audio": True, **notas,
                },
            )
            if not creado and historial.pendiente_revision_audio:
                for campo, valor in notas.items():
                    setattr(historial, campo, valor)
                historial.save(update_fields=list(notas))
            if cita.estado == "Confirmada":
                cita.estado = "Completada"
                cita.save(update_fields=["estado"])
            actual.transcripcion = texto
            actual.notas_generadas = notas
            actual.estado = Estado.COMPLETADA
            actual.save(update_fields=["transcripcion", "notas_generadas", "estado"])
    except Exception:
        logger.exception("Falló el procesamiento de la grabación %s", grabacion.pk)
        try:
            avanzar(Estado.ERROR)
        except Exception:
            logger.exception("No se pudo marcar como error la grabación %s", grabacion.pk)
    return True


def _consumir_cola():
    global _hilo_procesamiento
    try:
        while True:
            close_old_connections()
            try:
                if not procesar_siguiente():
                    break
            except Exception:
                logger.exception("Falló el consumidor de grabaciones")
                break
    finally:
        try:
            connections.close_all()
        except Exception:
            pass
        with _cerrojo_hilo:
            _hilo_procesamiento = None


def disparar_procesamiento():
    """Arranca (si hace falta) el hilo que vacía la cola de grabaciones.

    Es idempotente por proceso: si ya hay un consumidor vivo no crea otro. En
    pruebas se desactiva con HOPE_PROCESAR_GRABACIONES=False.
    """
    if not getattr(settings, "HOPE_PROCESAR_GRABACIONES", True):
        return False
    global _hilo_procesamiento
    with _cerrojo_hilo:
        if _hilo_procesamiento is not None and _hilo_procesamiento.is_alive():
            return False
        hilo = threading.Thread(
            target=_consumir_cola, name="hope-grabaciones", daemon=True,
        )
        _hilo_procesamiento = hilo
        hilo.start()
        return True
