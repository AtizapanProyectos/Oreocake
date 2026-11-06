"""Procesamiento local de grabaciones presenciales, fuera del proceso web."""
import datetime
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import wave

from django.db import close_old_connections, transaction
from django.db.models import Q
from django.utils import timezone

from .models import Cita, GrabacionSesion, HistorialClinico

Estado = GrabacionSesion.Estado
EN_PROCESO = (Estado.AUDIO, Estado.TRANSCRIBIENDO, Estado.NOTAS, Estado.GUARDANDO)
LIMITE_AUDIO = 12 * 3600
TIMEOUT_TRANSCRIPCION = 8 * 3600
CONTENEDORES = {"webm": "matroska", "ogg": "ogg", "m4a": "mov", "wav": "wav", "mp3": "mp3"}
SECCIONES = {
    "como_llega": r"(?:cómo llega|al inicio|estado inicial)",
    "aprendizaje_paciente": r"(?:qué se lleva|qué te llevas|aprendizaje)",
    "como_se_va": r"(?:cómo se va|al cierre|al finalizar|cierre)",
    "recomendaciones": r"(?:recomendaciones|acuerdos|tareas para la próxima sesión)",
}


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


def transcribir(grabacion, avanzar, ffmpeg, whisper, modelo):
    contenedor = CONTENEDORES.get(grabacion.formato)
    if not contenedor:
        raise ValueError("Formato de audio no admitido")
    with tempfile.TemporaryDirectory(prefix="hope_presencial_") as carpeta:
        base = Path(carpeta)
        origen = base / f"origen.{grabacion.formato}"
        wav = base / "audio.wav"
        salida = base / "transcripcion"
        with grabacion.audio.open("rb") as entrada, origen.open("wb") as archivo:
            shutil.copyfileobj(entrada, archivo)
        subprocess.run([
            ffmpeg, "-nostdin", "-protocol_whitelist", "file", "-f", contenedor, "-i", str(origen),
            "-map", "0:a:0", "-vn", "-t", str(LIMITE_AUDIO + 1),
            "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav),
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=1800)
        with wave.open(str(wav), "rb") as audio:
            if not 0 < audio.getnframes() <= LIMITE_AUDIO * audio.getframerate():
                raise ValueError("Duración de audio fuera de rango")
        if not avanzar(Estado.TRANSCRIBIENDO):
            return ""
        subprocess.run([
            whisper, "-m", modelo, "-f", str(wav), "-l", "es", "-otxt", "-of", str(salida),
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=TIMEOUT_TRANSCRIPCION)
        return salida.with_suffix(".txt").read_text(encoding="utf-8").strip()


def procesar_siguiente(ffmpeg, whisper, modelo):
    ahora = timezone.now()
    vencido = ahora - datetime.timedelta(hours=10)
    disponibles = GrabacionSesion.objects.filter(
        Q(estado=Estado.CARGADA) | Q(estado__in=EN_PROCESO, intento_iniciado__lt=vencido)
    )
    grabacion = disponibles.order_by("fecha_subida", "pk").first()
    if grabacion is None:
        return False
    if not disponibles.filter(pk=grabacion.pk, audio=grabacion.audio.name).update(
        estado=Estado.AUDIO, intento_iniciado=ahora,
    ):
        return True

    intento = GrabacionSesion.objects.filter(pk=grabacion.pk, intento_iniciado=ahora)

    def avanzar(estado):
        close_old_connections()
        return intento.update(estado=estado)

    try:
        texto = transcribir(grabacion, avanzar, ffmpeg, whisper, modelo)
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
    except (OSError, subprocess.SubprocessError, ValueError, wave.Error, EOFError):
        avanzar(Estado.ERROR)
    return True


def configuracion_motor():
    ffmpeg = shutil.which(os.environ.get("HOPE_FFMPEG", "ffmpeg"))
    whisper = shutil.which(os.environ.get("HOPE_WHISPER_CLI", "whisper-cli"))
    modelo = os.environ.get("HOPE_WHISPER_MODEL", "")
    if ffmpeg and whisper and modelo and Path(modelo).is_file():
        return ffmpeg, whisper, str(Path(modelo).resolve())
    return None
