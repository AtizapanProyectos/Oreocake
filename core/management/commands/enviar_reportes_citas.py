
from django.core.management.base import BaseCommand
from core.views import procesar_citas_pendientes_de_reporte


class Command(BaseCommand):
    help = (
        'Revisa las citas ya finalizadas (sesión + tiempo de espera cumplido) '
        'y envía por correo el Reporte de Check-In automático al paciente y al psicólogo.'
    )

    def handle(self, *args, **options):
        total = procesar_citas_pendientes_de_reporte()
        self.stdout.write(self.style.SUCCESS(f'✅ Reportes procesados: {total}'))