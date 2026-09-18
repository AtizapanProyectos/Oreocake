import sys
from django.core.management.base import BaseCommand
from core.views import procesar_bitacoras_historicas_meet


class Command(BaseCommand):
    help = (
        'Recorre todas las citas pasadas que NO tengan bitácora escrita por psicólogo '
        'y genera automáticamente la bitácora extrayendo las notas de Google Meet con Groq.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--limite',
            type=int,
            default=None,
            help='Número máximo de citas a evaluar en esta corrida.',
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

        limite = options.get('limite')
        self.safe_write("🚀 Iniciando escaneo de sesiones pasadas sin bitácora...")
        resumen = procesar_bitacoras_historicas_meet(log_func=self.safe_write, limite=limite)
        self.safe_write(
            f"\n🎯 Proceso concluido: {resumen['creadas_con_exito']} bitácoras creadas con IA de {resumen['total_analizadas']} citas evaluadas.",
            self.style.SUCCESS if resumen['creadas_con_exito'] > 0 else self.style.WARNING
        )
