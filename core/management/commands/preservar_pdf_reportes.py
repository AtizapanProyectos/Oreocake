"""Run on the OLD web container while its legacy files still exist."""
from django.core.management.base import BaseCommand, CommandError
from django.core.files.base import ContentFile
from core.models import ReporteClinicoPDF


class Command(BaseCommand):
    help = 'Copia PDFs locales existentes al almacén privado compartido. No genera ni envía.'

    def add_arguments(self, parser):
        parser.add_argument('--limite', type=int, default=100)
        parser.add_argument('--despues-id', type=int, default=0)

    def handle(self, *args, **options):
        if not 1 <= options['limite'] <= 100:
            raise CommandError('Límite permitido: 1–100.')
        copiados, ausentes, ultimo = 0, 0, options['despues_id']
        rows = ReporteClinicoPDF.objects.filter(pk__gt=ultimo).exclude(archivo_pdf__startswith='privado-db/').order_by('pk')[:options['limite']]
        for row in rows:
            ultimo = row.pk
            if not row.archivo_pdf or not row.archivo_pdf.storage.exists(row.archivo_pdf.name):
                ausentes += 1
                continue
            with row.archivo_pdf.open('rb') as stream:
                contenido = stream.read()
            row.archivo_pdf.save('historico.pdf', ContentFile(contenido))
            copiados += 1
        self.stdout.write(str({'copiados':copiados, 'ausentes':ausentes, 'ultimo_id':ultimo}))
