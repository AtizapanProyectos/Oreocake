from django.core.management.base import BaseCommand
from django.db import transaction

from core.covisiones import calendario_acordado, citas_en_conflicto, reglas_globales_anteriores
from core.models import BloqueoAgendaPsicologo


class Command(BaseCommand):
    help = 'Vista previa de los 33 bloqueos de Co-Visión; --aplicar los carga y desactiva las dos reglas globales originales.'

    def add_arguments(self, parser):
        parser.add_argument('--aplicar', action='store_true', help='Aplicar el calendario en una transacción. Sin esta opción solo consulta.')

    def handle(self, *args, **options):
        with transaction.atomic():
            calendario = calendario_acordado()
            conflictos = set()
            for b in calendario:
                self.stdout.write(f'{b.fecha} {b.hora_inicio:%H:%M}–{b.hora_fin:%H:%M} {b.psicologo.usuario.username}')
                for cita in citas_en_conflicto(b):
                    conflictos.add(cita.pk)
                    self.stdout.write(self.style.WARNING(f'  CONFLICTO: cita #{cita.pk} ({cita.estado}); se conserva sin cambios.'))
            reglas = reglas_globales_anteriores()
            ids = list(reglas.filter(activo=True).values_list('id', flat=True))
            self.stdout.write(f'{len(calendario)} bloqueos; {len(conflictos)} citas en conflicto. Reglas globales a desactivar: {ids}.')
            self.stdout.write('Karla: sin bloqueo, pendiente ajuste de entrada una hora más tarde.')
            if not options['aplicar']:
                self.stdout.write('SOLO VISTA PREVIA. Usar --aplicar después de desplegar el código y aplicar la migración.')
                return
            creados = 0
            for b in calendario:
                _, creado = BloqueoAgendaPsicologo.objects.get_or_create(
                    psicologo_id=b.psicologo_id, fecha=b.fecha, hora_inicio=b.hora_inicio,
                    hora_fin=b.hora_fin, motivo=b.motivo,
                )
                creados += int(creado)
            reglas.update(activo=False)
            self.stdout.write(self.style.SUCCESS(f'Calendario aplicado: {creados} creados; {len(calendario) - creados} ya existían. Citas intactas.'))
