"""Diagnóstico de solo lectura del almacenamiento de grabaciones y del texto."""
import os
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from core.models import GrabacionSesion


def _punto_montaje(ruta):
    actual = Path(ruta).absolute()
    while True:
        try:
            if os.path.ismount(actual):
                return actual
        except OSError:
            return None
        if actual.parent == actual:
            return None
        actual = actual.parent


class Command(BaseCommand):
    help = (
        "Muestra (sin escribir nada) dónde se guardan las grabaciones, si la "
        "ruta está en un volumen persistente y si los audios de la BD existen."
    )

    def handle(self, *args, **options):
        configurada = os.environ.get("HOPE_GRABACIONES_ROOT")
        raiz = Path(str(settings.GRABACIONES_ROOT))

        self.stdout.write("== Almacenamiento de grabaciones ==")
        self.stdout.write(f"HOPE_GRABACIONES_ROOT: {configurada or '(no definido)'}")
        self.stdout.write(f"Ruta efectiva: {raiz}")
        self.stdout.write(
            f"Existe: {raiz.exists()} | Escribible: {os.access(raiz, os.W_OK)}"
        )
        montaje = _punto_montaje(raiz)
        self.stdout.write(
            f"Punto de montaje detectado: {montaje or 'ninguno (disco efímero del contenedor)'}"
        )
        if not configurada:
            self.stdout.write(self.style.WARNING(
                "Sin HOPE_GRABACIONES_ROOT se usa una carpeta dentro del contenedor; "
                "en Railway eso se pierde en cada deploy."
            ))

        if raiz.exists():
            archivos = 0
            bytes_totales = 0
            for ruta in raiz.rglob("*"):
                if "_modelos_whisper" in ruta.parts:
                    continue
                try:
                    if ruta.is_file():
                        archivos += 1
                        bytes_totales += ruta.stat().st_size
                except OSError:
                    continue
            self.stdout.write(
                f"Audios/documentos en disco: {archivos} ({bytes_totales / (1024 * 1024):.1f} MB)"
            )
            try:
                hijos = sorted(p.name for p in raiz.iterdir())[:25]
                self.stdout.write("Contenido de primer nivel: " + (", ".join(hijos) or "(vacío)"))
            except OSError as error:
                self.stdout.write(f"No se pudo listar el contenido: {error}")

        self.stdout.write("")
        self.stdout.write("== Base de datos ==")
        total = GrabacionSesion.objects.count()
        self.stdout.write(f"Filas GrabacionSesion: {total}")
        for grabacion in GrabacionSesion.objects.order_by("-fecha_subida")[:10]:
            try:
                existe = bool(
                    grabacion.audio and grabacion.audio.name and
                    grabacion.audio.storage.exists(grabacion.audio.name)
                )
            except Exception:
                existe = False
            self.stdout.write(
                f"  cita {grabacion.cita_id}: {grabacion.get_estado_display()} | "
                f"audio={'OK' if existe else 'FALTA'} | {grabacion.audio.name}"
            )

        self.stdout.write("")
        self.stdout.write("== Transcripción en vivo ==")
        clave = "configurada" if os.environ.get("GROQ_API_KEY") else "no configurada"
        self.stdout.write(f"GROQ_API_KEY: {clave}")
