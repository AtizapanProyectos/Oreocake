import time

from django.core.management.base import BaseCommand, CommandError
from django.db import connections
from core.orquestador import correr, reconciliar


class Command(BaseCommand):
    help = 'Atiende solicitudes manuales del centro de orquestación.'

    def add_arguments(self, parser):
        parser.add_argument('--continuo', action='store_true', help='Escucha solicitudes como servicio permanente.')
        parser.add_argument('--limite', type=int, default=25)
        parser.add_argument('--segundos', type=int, default=240)
        parser.add_argument('--cita-id', type=int)
        parser.add_argument('--reconciliar', action='store_true', help='Clasificar evidencia histórica sin llamadas externas ni envíos.')
        parser.add_argument('--despues-id', type=int, default=0)

    def handle(self, *args, **options):
        if not 1 <= options['limite'] <= 100 or not 1 <= options['segundos'] <= 300:
            raise CommandError('Límite permitido: 1–100 citas; duración: 1–300 segundos.')
        if options['continuo']:
            if options['reconciliar'] or options['cita_id']:
                raise CommandError('--continuo requiere el maestro general.')
            while True:
                try:
                    resultado = correr(options['limite'], options['segundos'])
                finally:
                    connections.close_all()
                if not resultado.get('etapas'):
                    time.sleep(3)
        try:
            resultado = reconciliar(options['limite'], options['despues_id']) if options['reconciliar'] else correr(options['limite'], options['segundos'], options.get('cita_id'))
            self.stdout.write(str(resultado))
        finally:
            connections.close_all()
