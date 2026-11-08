import time

from django.core.management.base import BaseCommand
from django.db import close_old_connections

from core.grabaciones import procesar_siguiente


class Command(BaseCommand):
    help = "Procesa grabaciones presenciales con faster-whisper local (sin Groq)."

    def add_arguments(self, parser):
        parser.add_argument("--una-vez", action="store_true", help="Consume hasta una grabación y sale.")

    def handle(self, *args, **options):
        while True:
            close_old_connections()
            procesada = procesar_siguiente()
            if options["una_vez"]:
                return
            if not procesada:
                time.sleep(3)
