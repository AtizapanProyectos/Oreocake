import os
import sys
import subprocess
from django.core.management.base import BaseCommand
from django.template.loader import render_to_string
from core.models import Cita
from core.views import (
    _construir_datos_checkin_paciente,
    _construir_datos_checkin_psicologo,
    _obtener_logo_base64
)
from core.utils_pdf import convertir_html_a_pdf, obtener_ejecutable_chromium


class Command(BaseCommand):
    help = (
        'Comando de prueba local para generar e inspeccionar los 2 PDFs (Paciente y Psicólogo) '
        'de una cita específica sin enviar correos ni modificar el estado de la cita.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--cita-id',
            type=int,
            help='ID de la cita a probar. Si no se especifica, toma la cita más reciente con nota clínica.',
        )
        parser.add_argument(
            '--abrir',
            action='store_true',
            help='Abre automáticamente los PDFs generados en el visor predeterminado del sistema.',
        )
        parser.add_argument(
            '--salida',
            type=str,
            default='.',
            help='Directorio donde guardar los PDFs de prueba (por defecto la carpeta raíz del proyecto).',
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
        abrir = options.get('abrir', False)
        dir_salida = options.get('salida', '.')

        if cita_id:
            try:
                cita = Cita.objects.select_related('paciente__perfil', 'psicologo__usuario', 'nota_clinica').get(id=cita_id)
            except Cita.DoesNotExist:
                self.safe_write(f"❌ Error: No existe ninguna Cita con ID #{cita_id}.", self.style.ERROR)
                return
        else:
            cita = Cita.objects.filter(nota_clinica__isnull=False).select_related(
                'paciente__perfil', 'psicologo__usuario', 'nota_clinica'
            ).order_by('-id').first()
            if not cita:
                self.safe_write("❌ Error: No se encontraron citas con notas clínicas en la base de datos.", self.style.ERROR)
                return
            self.safe_write(f"ℹ️ No se especificó --cita-id. Seleccionando la cita más reciente con nota clínica (Cita #{cita.id}).")

        self.safe_write("=" * 80)
        self.safe_write(f"📋 PROBANDO GENERACIÓN LOCAL DE REPORTES PDF — CITA #{cita.id}", self.style.SUCCESS)
        self.safe_write(f"   👤 Paciente:  {cita.paciente.get_full_name() or cita.paciente.username} ({cita.paciente.email})")
        psic_name = cita.psicologo.usuario.get_full_name() if cita.psicologo else 'N/A'
        self.safe_write(f"   🩺 Psicólogo: {psic_name}")
        self.safe_write(f"   📅 Fecha:     {cita.fecha}")
        self.safe_write("=" * 80)

        # 1. Verificar motor de renderizado disponible
        browser_path = obtener_ejecutable_chromium()
        if browser_path:
            self.safe_write(f"🌐 Motor PDF detectado: Chromium ({browser_path})", self.style.SUCCESS)
        else:
            self.safe_write("⚠️ AVISO: No se detectó Chromium/Chrome/Edge en el sistema. Los estilos podrían verse afectados.", self.style.WARNING)

        # 2. Extraer datos y renderizar
        self.safe_write("🤖 Extrayendo métricas clínicas y calculando dimensiones...")
        datos_paciente = _construir_datos_checkin_paciente(cita.paciente, cita=cita)
        datos_psicologo = _construir_datos_checkin_psicologo(cita.paciente, cita=cita)
        logo_base64 = _obtener_logo_base64()

        os.makedirs(dir_salida, exist_ok=True)
        archivos_generados = []

        # 3. PDF Paciente
        self.safe_write("📄 Renderizando plantilla claude_pascinete.html...")
        ctx_pac = {
            **datos_paciente,
            'es_exportacion_pdf': True,
            'logo_base64': logo_base64,
        }
        html_pac = render_to_string('Pruebas/claude_pascinete.html', ctx_pac)
        self.safe_write("🖨️ Convirtiendo a PDF (Paciente)...")
        pdf_bytes_pac = convertir_html_a_pdf(html_pac)

        path_pac = os.path.abspath(os.path.join(dir_salida, f"reporte_cita_{cita.id}_paciente.pdf"))
        with open(path_pac, 'wb') as f:
            f.write(pdf_bytes_pac)
        kb_pac = round(len(pdf_bytes_pac) / 1024, 1)
        self.safe_write(f"✅ PDF Paciente generado ({kb_pac} KB): {path_pac}", self.style.SUCCESS)
        archivos_generados.append(path_pac)

        # 4. PDF Psicólogo
        self.safe_write("📋 Renderizando plantilla claude_psioclog.html...")
        ctx_psi = {
            **datos_psicologo,
            'es_exportacion_pdf': True,
            'logo_base64': logo_base64,
        }
        html_psi = render_to_string('Pruebas/claude_psioclog.html', ctx_psi)
        self.safe_write("🖨️ Convirtiendo a PDF (Psicólogo)...")
        pdf_bytes_psi = convertir_html_a_pdf(html_psi)

        path_psi = os.path.abspath(os.path.join(dir_salida, f"reporte_cita_{cita.id}_psicologo.pdf"))
        with open(path_psi, 'wb') as f:
            f.write(pdf_bytes_psi)
        kb_psi = round(len(pdf_bytes_psi) / 1024, 1)
        self.safe_write(f"✅ PDF Psicólogo generado ({kb_psi} KB): {path_psi}", self.style.SUCCESS)
        archivos_generados.append(path_psi)

        self.safe_write("=" * 80)
        self.safe_write("🎉 ¡Prueba completada con éxito! Ambos archivos PDF están listos para revisión.", self.style.SUCCESS)

        if abrir:
            for p in archivos_generados:
                try:
                    if sys.platform.startswith('win'):
                        os.startfile(p)
                    elif sys.platform.startswith('darwin'):
                        subprocess.run(['open', p])
                    else:
                        subprocess.run(['xdg-open', p])
                except Exception as e:
                    self.safe_write(f"⚠️ No se pudo abrir automáticamente {p}: {e}")
