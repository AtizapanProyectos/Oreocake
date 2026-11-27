import base64
from hashlib import sha256
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from django.contrib.auth.models import User
from django.template.loader import render_to_string
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import ConsentimientoInformado


class TextoHTML(HTMLParser):
    def __init__(self):
        super().__init__()
        self.textos = []

    def handle_data(self, texto):
        self.textos.append(texto)


@override_settings(STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.InMemoryStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
})
class ConsentimientoActualizadoTests(TestCase):
    def setUp(self):
        self.usuario = User.objects.create_user('consultante-prueba', first_name='Consultante', email='prueba@example.test')
        self.client.force_login(self.usuario)
        archivo = BytesIO()
        Image.new('RGB', (20, 20), 'black').save(archivo, 'PNG')
        self.datos = {
            'firma_base64': 'data:image/png;base64,' + base64.b64encode(archivo.getvalue()).decode(),
            'nombre_firmante': 'Consultante de prueba', 'acepta_telepsicologia': 'true',
            'declaracion_consentimiento': 'true', 'version_documento': '3.0',
        }

    def test_contenido_completo_del_word_sin_resumen(self):
        html = render_to_string('documentos/consentimiento_contenido_v2.html')
        parser = TextoHTML()
        parser.feed(html)
        texto = ' '.join(' '.join(parser.textos).split())
        # Huella del texto del Word entregado: sin título, viñetas ni línea de firma.
        self.assertEqual(sha256(texto.encode()).hexdigest(), '4802bd96578ed07d7b65787635154ea4263d3ce2ce31ef6e861bae576fa24855')
        self.assertEqual(html.count('<h2'), 13)

    def test_contenido_word_actualizado_completo(self):
        html = render_to_string('documentos/consentimiento_contenido_v3.html')
        parser = TextoHTML()
        parser.feed(html)
        texto = ' '.join(' '.join(parser.textos).split())
        # Word actualizado: sin título ni línea de firma, con numeración
        # consecutiva (el original repite 11) y espacios tipográficos corregidos.
        self.assertEqual(sha256(texto.encode()).hexdigest(),
                         '680258ceb48e18ebe4aac9fd13f9cdf0a6f1a0d5074c496e89fe969897c41803')
        self.assertEqual(html.count('<h2'), 14)
        self.assertIn('anonimizada y agregada', texto)
        self.assertIn('reprogramar una segunda vez', texto)

    @patch('core.views.convertir_html_a_pdf', return_value=b'%PDF-1.4 prueba')
    def test_firma_guarda_nueva_version_y_pdf_con_mismo_texto(self, convertir):
        anterior = ConsentimientoInformado.objects.create(paciente=self.usuario, nombre_firmante='Anterior',
                                                         firma_base64='original', version='1.0')
        respuesta = self.client.post(reverse('guardar_consentimiento_ajax'), self.datos)
        self.assertEqual(respuesta.json()['status'], 'success', respuesta.content)
        actual = ConsentimientoInformado.objects.exclude(pk=anterior.pk).get()
        self.assertEqual(actual.version, '3.0')
        self.assertEqual(actual.nombre_firmante, self.datos['nombre_firmante'])
        self.assertEqual(actual.firma_base64, self.datos['firma_base64'])
        self.assertEqual(actual.archivo_pdf.read(), b'%PDF-1.4 prueba')
        self.assertIn(render_to_string('documentos/consentimiento_contenido_v3.html'), convertir.call_args.args[0])
        self.assertTrue(actual.acepta_telepsicologia)
        self.assertTrue(actual.declaracion_consentimiento)
        self.assertEqual(actual.ip_registro, '127.0.0.1')
        self.assertEqual(respuesta.json()['pdf_url'], actual.archivo_pdf.url)
        anterior.refresh_from_db()
        self.assertEqual((anterior.version, anterior.firma_base64), ('1.0', 'original'))

    @patch('core.views.convertir_html_a_pdf', return_value=b'%PDF-1.4 prueba')
    def test_panel_anterior_conserva_version_y_pdf_anteriores(self, convertir):
        datos = {k: v for k, v in self.datos.items() if k != 'version_documento'}
        self.assertEqual(self.client.post(reverse('guardar_consentimiento_ajax'), datos).json()['status'], 'success')
        self.assertEqual(ConsentimientoInformado.objects.get().version, '1.0')
        self.assertNotIn('8. USO DE HERRAMIENTAS', convertir.call_args.args[0])
        self.assertIn('10. Declaración de Consentimiento', convertir.call_args.args[0])

    @patch('core.views.convertir_html_a_pdf', return_value=b'%PDF-1.4 prueba')
    def test_version_dos_conserva_texto_anterior(self, convertir):
        datos = dict(self.datos, version_documento='2.0')
        self.assertEqual(self.client.post(reverse('guardar_consentimiento_ajax'), datos).json()['status'], 'success')
        self.assertEqual(ConsentimientoInformado.objects.get().version, '2.0')
        self.assertIn(render_to_string('documentos/consentimiento_contenido_v2.html'), convertir.call_args.args[0])
        self.assertNotIn('8. USO DE INFORMACIÓN CON FINES', convertir.call_args.args[0])

    @patch('core.views.convertir_html_a_pdf')
    def test_rechaza_version_invalida_y_sin_aceptacion(self, convertir):
        for datos in [dict(self.datos, version_documento='4.0'), dict(self.datos, declaracion_consentimiento='false'),
                      dict(self.datos, acepta_telepsicologia='false')]:
            self.assertEqual(self.client.post(reverse('guardar_consentimiento_ajax'), datos).status_code, 400)
        self.assertFalse(ConsentimientoInformado.objects.exists())
        convertir.assert_not_called()

    @patch('core.views.convertir_html_a_pdf', side_effect=RuntimeError('Motor PDF no disponible'))
    def test_error_pdf_no_deja_consentimiento_firmado_incompleto(self, convertir):
        self.assertEqual(self.client.post(reverse('guardar_consentimiento_ajax'), self.datos).json()['status'], 'error')
        self.assertFalse(ConsentimientoInformado.objects.exists())

    def test_panel_hope_muestra_y_envia_version_tres(self):
        panel = (Path(__file__).resolve().parent.parent / 'templates/panel_generico.html').read_text(encoding='utf-8')
        self.assertIn("{% include 'documentos/consentimiento_contenido_v3.html' %}", panel)
        self.assertIn("formData.append('version_documento', '3.0')", panel)
        self.assertNotIn('id="check-acepta-telepsicologia" checked', panel)
