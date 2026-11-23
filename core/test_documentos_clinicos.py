import json
import tempfile
from datetime import time
from types import SimpleNamespace
from unittest.mock import patch

import fitz
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import Cita, HistorialClinico, PerfilPsicologo, UsuarioPerfil


CAMPOS = dict(como_llega='Tranquilo', notas_sesion='Se trabajaron hábitos de sueño.', aprendizaje_paciente='Descansar', como_se_va='En calma', recomendaciones='Registrar horarios')


def respuesta_ia(texto):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=texto), finish_reason='stop')])


def pdf_prueba(escaneado=False):
    with fitz.open() as original:
        original.new_page().insert_text((72, 72), 'Notas de prueba. Se trabajaron habitos de descanso.')
        if not escaneado:
            return original.tobytes()
        imagen = original[0].get_pixmap().tobytes('png')
    with fitz.open() as documento:
        pagina = documento.new_page()
        pagina.insert_image(pagina.rect, stream=imagen)
        return documento.tobytes()


class DocumentosClinicosTests(TestCase):
    def setUp(self):
        temporal = tempfile.TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        ajustes = override_settings(GRABACIONES_ROOT=temporal.name)
        ajustes.enable()
        self.addCleanup(ajustes.disable)
        self.paciente = User.objects.create_user('paciente')
        UsuarioPerfil.objects.create(usuario=self.paciente, nombre='Paciente')
        self.usuario = User.objects.create_user('doctor')
        self.psicologo = PerfilPsicologo.objects.create(usuario=self.usuario, cedula_profesional='pdf-test')
        self.cita = Cita.objects.create(paciente=self.paciente, psicologo=self.psicologo, fecha=timezone.localdate(), hora=time(10))
        self.client.force_login(self.usuario)

    def guardar(self, **datos):
        return self.client.post(reverse('guardar_historial'), {
            'paciente_id': self.paciente.pk, 'cita_id': self.cita.pk, **CAMPOS, **datos,
        })

    @patch('groq.Groq')
    def test_pdf_con_texto_se_envia_completo_a_groq(self, groq):
        groq.return_value.chat.completions.create.return_value = respuesta_ia(json.dumps(CAMPOS))
        r = self.client.post(reverse('procesar_archivo_ia'), {'archivo': SimpleUploadedFile('reporte.PDF', pdf_prueba(), content_type='application/pdf')})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['data'], CAMPOS)
        llamadas = groq.return_value.chat.completions.create.call_args_list
        self.assertEqual(len(llamadas), 1)
        self.assertIn('habitos de descanso', llamadas[0].kwargs['messages'][1]['content'])

    @patch('fitz.Page.get_textpage_ocr', side_effect=RuntimeError('OCR no instalado'))
    @patch('groq.Groq')
    def test_pdf_escaneado_usa_vision_y_despues_estructura_texto(self, groq, ocr):
        crear = groq.return_value.chat.completions.create
        crear.side_effect = [respuesta_ia('Notas de prueba del escaneo.'), respuesta_ia(json.dumps(CAMPOS))]
        r = self.client.post(reverse('procesar_archivo_ia'), {'archivo': SimpleUploadedFile('escaneo.pdf', pdf_prueba(True))})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['data'], CAMPOS)
        self.assertEqual(crear.call_count, 2)
        vision = crear.call_args_list[0].kwargs['messages'][0]['content']
        self.assertTrue(vision[1]['image_url']['url'].startswith('data:image/jpeg;base64,'))
        self.assertIn('Notas de prueba del escaneo.', crear.call_args_list[1].kwargs['messages'][1]['content'])

    @patch('groq.Groq')
    def test_pdf_danado_no_llega_al_modelo(self, groq):
        r = self.client.post(reverse('procesar_archivo_ia'), {'archivo': SimpleUploadedFile('danado.pdf', b'no es un PDF')})
        self.assertEqual(r.status_code, 422, r.content)
        groq.return_value.chat.completions.create.assert_not_called()

    @patch('groq.Groq')
    def test_pdf_protegido_y_largo_se_rechazan_claramente(self, groq):
        with fitz.open() as documento:
            documento.new_page()
            protegido = documento.tobytes(encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw='test', user_pw='test')
            for _ in range(20):
                documento.new_page()
            largo = documento.tobytes()
        for datos, mensaje in ((protegido, 'contraseña'), (largo, '20 páginas')):
            r = self.client.post(reverse('procesar_archivo_ia'), {'archivo': SimpleUploadedFile('documento.pdf', datos)})
            self.assertEqual(r.status_code, 422)
            self.assertIn(mensaje, r.json()['error'])
        groq.return_value.chat.completions.create.assert_not_called()

    def test_guardar_pdf_como_reporte_y_transcripcion_y_editar(self):
        datos = pdf_prueba()
        r = self.guardar(
            archivo_adjunto=SimpleUploadedFile('reporte.pdf', datos),
            transcripcion_meet=SimpleUploadedFile('transcripcion.pdf', datos),
        )
        self.assertEqual(r.status_code, 200, r.content)
        historial = HistorialClinico.objects.get(cita=self.cita)
        for archivo in (historial.archivo_adjunto, historial.transcripcion_meet):
            with archivo.open('rb') as guardado:
                self.assertEqual(guardado.read(), datos)
            descarga = self.client.get(archivo.url)
            self.assertEqual(descarga.status_code, 200)
            descarga.close()
        self.cita.refresh_from_db()
        self.assertEqual(self.cita.estado, 'Completada')
        r = self.guardar(historial_id=historial.pk, notas_sesion='Nota revisada')
        self.assertEqual(r.status_code, 200)
        historial.refresh_from_db()
        self.assertEqual(historial.notas_sesion, 'Nota revisada')
        self.assertTrue(historial.archivo_adjunto.name.endswith('.pdf'))
        from .transcripcion_navegador import texto_del_expediente
        self.assertIn('habitos de descanso', texto_del_expediente(self.cita, None)[0])

    def test_reintento_igual_no_duplica_y_diferente_no_sobrescribe(self):
        self.assertEqual(self.guardar().status_code, 200)
        self.assertEqual(self.guardar().status_code, 200)
        r = self.guardar(notas_sesion='Otro contenido')
        self.assertEqual(r.status_code, 409)
        self.assertEqual(HistorialClinico.objects.count(), 1)
        self.assertEqual(HistorialClinico.objects.get().notas_sesion, CAMPOS['notas_sesion'])

    def test_pdf_danado_no_guarda_nota_ni_completa_cita(self):
        r = self.guardar(archivo_adjunto=SimpleUploadedFile('roto.pdf', b'roto'))
        self.assertEqual(r.status_code, 422, r.content)
        self.assertFalse(HistorialClinico.objects.exists())
        self.cita.refresh_from_db()
        self.assertEqual(self.cita.estado, 'Confirmada')

    def test_guardado_requiere_csrf_y_acepta_token_vigente(self):
        cliente = Client(enforce_csrf_checks=True)
        cliente.force_login(self.usuario)
        datos = {'paciente_id': self.paciente.pk, 'cita_id': self.cita.pk, **CAMPOS}
        self.assertEqual(cliente.post(reverse('guardar_historial'), datos).status_code, 403)
        cliente.get(reverse('detalle_paciente', args=[self.paciente.pk]))
        r = cliente.post(reverse('guardar_historial'), datos, HTTP_X_CSRFTOKEN=cliente.cookies['csrftoken'].value)
        self.assertEqual(r.status_code, 200, r.content)

    def test_sesion_expirada_devuelve_json_en_ambos_endpoints(self):
        self.client.logout()
        for url in ('guardar_historial', 'procesar_archivo_ia'):
            r = self.client.post(reverse(url))
            self.assertEqual(r.status_code, 401)
            self.assertIn('sesión', (r.json().get('message') or r.json().get('error')))

    def test_otro_psicologo_no_puede_guardar(self):
        otro = User.objects.create_user('otro')
        PerfilPsicologo.objects.create(usuario=otro, cedula_profesional='otro')
        self.client.force_login(otro)
        self.assertEqual(self.guardar().status_code, 403)
        self.assertFalse(HistorialClinico.objects.exists())

    def test_fallo_de_almacen_no_pierde_nota_anterior_ni_completa_cita(self):
        historial = HistorialClinico.objects.create(paciente=self.paciente, psicologo=self.psicologo, cita=self.cita, notas_sesion='Nota anterior')
        with patch('core.almacen_grabaciones.AlmacenClinico._save', side_effect=OSError('volumen no disponible')):
            with self.assertLogs('core.views', level='WARNING'):
                r = self.guardar(historial_id=historial.pk, archivo_adjunto=SimpleUploadedFile('reporte.pdf', pdf_prueba()))
        self.assertEqual(r.status_code, 503)
        self.assertIn('no se guardó', r.json()['message'])
        historial.refresh_from_db()
        self.cita.refresh_from_db()
        self.assertEqual(historial.notas_sesion, 'Nota anterior')
        self.assertEqual(self.cita.estado, 'Confirmada')

    @patch('fitz.Page.get_textpage_ocr', side_effect=RuntimeError('OCR no instalado'))
    @patch('groq.Groq')
    def test_transcripcion_pdf_escaneada_vinculada_se_puede_recuperar(self, groq, ocr):
        groq.return_value.chat.completions.create.return_value = respuesta_ia('Transcripción de prueba vinculada a la sesión.')
        self.assertEqual(self.guardar(transcripcion_meet=SimpleUploadedFile('transcripcion.pdf', pdf_prueba(True))).status_code, 200)
        from .transcripcion_navegador import texto_del_expediente
        self.assertIn('Transcripción de prueba', texto_del_expediente(self.cita, None)[0])

    def test_panel_conserva_aprendizaje_y_edicion_devuelve_cita_correcta(self):
        r = self.guardar()
        historial = HistorialClinico.objects.get(pk=r.json()['historial_id'])
        self.assertEqual(historial.aprendizaje_paciente, 'Descansar')
        r = self.client.get(reverse('obtener_bitacora', args=[historial.pk]))
        self.assertEqual(r.json()['data']['cita_id'], self.cita.pk)
