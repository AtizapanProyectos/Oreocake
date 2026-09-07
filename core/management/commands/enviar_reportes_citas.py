
import sys
from django.core.management.base import BaseCommand
from core.views import procesar_citas_pendientes_de_reporte, _procesar_reporte_de_una_cita


class Command(BaseCommand):
    help = (
        'Revisa las citas ya finalizadas (sesión + tiempo de espera cumplido) '
        'y genera los PDFs del reporte clínico para paciente y psicólogo, '
        'guardándolos en la base de datos y enviándolos por correo como archivos adjuntos.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--cita-id',
            type=int,
            help='ID de una cita específica para forzar la generación del reporte y envío de PDF inmediatamente.',
        )

    def safe_write(self, msg, style_func=None):
        out = style_func(msg) if style_func else msg
        try:
            self.stdout.write(out)
        except UnicodeEncodeError:
            try:
                self.stdout.write(out.encode('ascii', errors='replace').decode('ascii'))
            except Exception:
                pass

    def handle(self, *args, **options):
        if hasattr(sys.stdout, 'reconfigure'):
            try:
                sys.stdout.reconfigure(encoding='utf-8', errors='replace')
            except Exception:
                pass

        cita_id = options.get('cita_id')
        if cita_id:
            self.safe_write(f"🚀 Forzando generación de reporte para Cita #{cita_id}...")
            exito, mensaje = _procesar_reporte_de_una_cita(cita_id, log_func=self.safe_write)
            if exito:
                self.safe_write(f"✅ Cita #{cita_id}: {mensaje}", self.style.SUCCESS)
            else:
                self.safe_write(f"⚠️ Cita #{cita_id}: {mensaje}", self.style.WARNING)
            return

        total = procesar_citas_pendientes_de_reporte(log_func=self.safe_write)
        self.safe_write(f'✅ Reportes procesados: {total}', self.style.SUCCESS)