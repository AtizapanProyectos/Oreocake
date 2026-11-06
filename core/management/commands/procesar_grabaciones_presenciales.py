import time

from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections

from core.grabaciones import configuracion_motor, procesar_siguiente


class Command(BaseCommand):
    help = "Procesa grabaciones presenciales con FFmpeg y whisper.cpp locales."

    def add_arguments(self, parser):
        parser.add_argument("--una-vez", action="store_true", help="Consume hasta una grabación y sale.")

    def handle(self, *args, **options):
        motor = configuracion_motor()
        if motor is None:
            raise CommandError("Configura FFmpeg, whisper-cli y HOPE_WHISPER_MODEL con un modelo multilingüe local.")
        while True:
            close_old_connections()
            procesada = procesar_siguiente(*motor)
            if options["una_vez"]:
                return
            if not procesada:
                time.sleep(3)
