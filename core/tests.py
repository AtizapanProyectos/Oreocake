import json
import tempfile
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

import fitz
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .grabaciones import procesar_siguiente
from .models import (
    Cita, EvaluacionSesionPaciente, GrabacionSesion, HistorialClinico, PerfilPsicologo,
    RespuestaFormularioOrganica, UsuarioPerfil,
)
from .views import FORMULARIO_ORGANICO_PREGUNTAS, _procesar_reporte_de_una_cita_detallado


@override_settings(ALLOWED_HOSTS=['testserver'])
class SesionPresencialTests(TestCase):
    def setUp(self):
        carpeta = tempfile.TemporaryDirectory()
        self.addCleanup(carpeta.cleanup)
        ajustes = override_settings(GRABACIONES_ROOT=carpeta.name)
        ajustes.enable()
        self.addCleanup(ajustes.disable)
        self.paciente = User.objects.create_user('paciente')
        UsuarioPerfil.objects.create(usuario=self.paciente, nombre='Paciente')
        self.doctor = User.objects.create_user('doctor')
        self.psicologo = PerfilPsicologo.objects.create(usuario=self.doctor, cedula_profesional='test')
        self.otro = User.objects.create_user('otro')
        ayer = timezone.localtime() - timedelta(days=1)
        self.cita = Cita.objects.create(
            paciente=self.paciente, psicologo=self.psicologo, fecha=ayer.date(),
            hora=ayer.time(), modalidad='Presencial',
        )

    def url(self, nombre):
        return reverse(nombre, args=[self.cita.pk])

    def respuestas(self):
        return {**{p['id']: 'siempre' for p in FORMULARIO_ORGANICO_PREGUNTAS},
                'sat_1': '2', 'sat_2': '3', 'sat_3': '4', 'comentarios_satisfaccion': 'Comentario de prueba'}

    def subir(self):
        return self.client.post(self.url('api_guardar_grabacion_sesion'), {
            'audio': SimpleUploadedFile('prueba.wav', b'archivo sintetico', content_type='audio/wav'),
        })

    def test_paciente_presencial_guarda_ipp_en_ambos_modelos_y_reenvio_no_lo_cambia(self):
        url = self.url('formulario_previo_meet')
        self.assertRedirects(self.client.get(url), reverse('modulo_informativo'), fetch_redirect_response=False)
        self.client.force_login(self.otro)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.client.force_login(self.paciente)
        self.assertContains(self.client.get(url), 'Guardar IPP de la sesión')
        self.assertEqual(self.client.post(url, {}).status_code, 400)
        self.assertFalse(EvaluacionSesionPaciente.objects.exists())
        data = self.respuestas()
        self.assertEqual(self.client.post(url, data).json()['redirect_url'], reverse('panel_generico'))
        self.assertEqual(self.client.post(url, {**data, 'ipp_1': 'nunca'}).json()['status'], 'success')
        evaluacion = EvaluacionSesionPaciente.objects.get(cita=self.cita)
        self.assertEqual((evaluacion.ipt, evaluacion.promedio_satisfaccion, evaluacion.comentarios),
                         (100, 3, 'Comentario de prueba'))
        anterior = RespuestaFormularioOrganica.objects.get(cita=self.cita)
        self.assertEqual(anterior.respuestas, evaluacion.respuestas_ipp)
        self.assertContains(self.client.get(url), 'ya está registrado')

    def test_doctor_registra_ipp_y_online_conserva_destino_meet(self):
        url = self.url('formulario_previo_meet')
        self.client.force_login(self.doctor)
        self.assertEqual(self.client.post(url, self.respuestas()).json()['redirect_url'], reverse('panel_doctor'))
        self.cita.modalidad = 'En línea'
        self.cita.enlace_meet = 'https://meet.google.com/abc'
        self.cita.save(update_fields=['modalidad', 'enlace_meet'])
        self.client.force_login(self.paciente)
        self.assertRedirects(self.client.get(url), self.cita.enlace_meet, fetch_redirect_response=False)

    def test_audio_grande_requiere_csrf_y_guarda_el_archivo_temporal(self):
        cliente = Client(enforce_csrf_checks=True)
        cliente.force_login(self.doctor)
        url = self.url('api_guardar_grabacion_sesion')
        self.assertEqual(cliente.post(url).status_code, 403)
        cliente.get(self.url('grabacion_sesion'))
        contenido = b'x' * (3 * 1024 * 1024)
        respuesta = cliente.post(
            url, {'audio': SimpleUploadedFile('prueba.wav', contenido, content_type='audio/wav')},
            HTTP_X_CSRFTOKEN=cliente.cookies['csrftoken'].value,
        )
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        grabacion = GrabacionSesion.objects.get(cita=self.cita)
        with grabacion.audio.open('rb') as audio:
            self.assertEqual(audio.read(), contenido)

    @patch('core.grabaciones.transcribir')
    def test_audio_vincula_cita_y_expediente_y_exige_revision_antes_del_checkin(self, transcribir):
        self.client.force_login(self.paciente)
        self.assertEqual(self.subir().status_code, 403)
        self.client.force_login(self.doctor)
        self.assertContains(self.client.get(self.url('grabacion_sesion')), 'Cargar grabación')
        subida = self.subir()
        self.assertEqual(subida.status_code, 200, subida.content)
        estado = self.url('estado_grabacion_sesion')
        self.assertEqual(self.client.get(estado).json()['estado'], 'cargada')
        descarga = self.client.get(subida.json()['archivo_url'])
        self.assertEqual(b''.join(descarga.streaming_content), b'archivo sintetico')
        descarga.close()

        def motor(grabacion, avanzar, *args):
            self.assertEqual(GrabacionSesion.objects.get(cita=self.cita).estado, 'audio')
            avanzar(GrabacionSesion.Estado.TRANSCRIBIENDO)
            self.assertEqual(GrabacionSesion.objects.get(cita=self.cita).estado, 'transcribiendo')
            return 'Al inicio, se habló del descanso. Se practicó respiración. Recomendaciones: llevar un diario.'

        transcribir.side_effect = motor
        self.assertTrue(procesar_siguiente('ffmpeg', 'whisper', 'modelo'))
        self.assertFalse(procesar_siguiente('ffmpeg', 'whisper', 'modelo'))
        self.cita.refresh_from_db()
        self.assertEqual(self.cita.estado, 'Completada')
        self.assertEqual(self.cita.nota_clinica.notas_sesion, 'Se practicó respiración.')
        self.assertTrue(self.cita.nota_clinica.pendiente_revision_audio)
        self.assertFalse(_procesar_reporte_de_una_cita_detallado(self.cita.pk)[0])
        self.assertEqual(self.client.get(estado).json()['estado'], 'completada')
        expediente = self.client.get(reverse('detalle_paciente', args=[self.paciente.pk]))
        self.assertContains(expediente, 'Recomendaciones: llevar un diario.')
        guardado = self.client.post(reverse('guardar_historial'), {
            'paciente_id': self.paciente.pk, 'historial_id': self.cita.nota_clinica.pk,
            'notas_sesion': 'Versión revisada por el psicólogo.',
        })
        self.assertEqual(guardado.json()['status'], 'success')
        self.assertFalse(HistorialClinico.objects.get(cita=self.cita).pendiente_revision_audio)
        self.client.force_login(self.otro)
        self.assertEqual(self.client.get(estado).status_code, 404)
        self.assertEqual(self.client.get(subida.json()['archivo_url']).status_code, 404)

    @patch('core.grabaciones.transcribir')
    def test_reemplazo_fallido_preserva_bitacora_y_ultima_transcripcion(self, transcribir):
        self.client.force_login(self.doctor)
        self.assertEqual(self.subir().status_code, 200)
        transcribir.return_value = 'Transcripción original.'
        procesar_siguiente('ffmpeg', 'whisper', 'modelo')
        historial = HistorialClinico.objects.get(cita=self.cita)
        self.assertEqual(self.subir().status_code, 200)
        transcribir.side_effect = ValueError
        procesar_siguiente('ffmpeg', 'whisper', 'modelo')
        grabacion = GrabacionSesion.objects.get(cita=self.cita)
        self.assertEqual((grabacion.estado, grabacion.transcripcion), ('error', 'Transcripción original.'))
        self.assertEqual(HistorialClinico.objects.get(cita=self.cita).pk, historial.pk)

    @patch('groq.Groq')
    def test_escaneo_distribuye_cinco_campos_y_rechaza_respuesta_invalida(self, groq):
        self.client.force_login(self.doctor)
        url = reverse('procesar_archivo_ia')
        campos = dict(como_llega='', notas_sesion='Texto de prueba', aprendizaje_paciente='', como_se_va='', recomendaciones='')
        mensaje = SimpleNamespace(content=json.dumps(campos))
        groq.return_value.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=mensaje)],
        )

        def escaneo():
            return self.client.post(url, {'archivo': SimpleUploadedFile('foto.jpg', b'imagen')})

        self.assertEqual(escaneo().json()['data'], campos)
        self.assertFalse(HistorialClinico.objects.exists())
        mensaje.content = 'Respuesta sin estructura'
        self.assertEqual(escaneo().status_code, 502)

    @patch('groq.Groq')
    @patch.object(fitz.Page, 'get_textpage_ocr', side_effect=RuntimeError)
    def test_pdf_sin_texto_intenta_ocr_y_devuelve_error_si_no_esta_disponible(self, ocr, groq):
        self.client.force_login(self.doctor)
        with fitz.open() as pdf:
            pdf.new_page()
            archivo = SimpleUploadedFile('escaneo.pdf', pdf.tobytes())
        respuesta = self.client.post(reverse('procesar_archivo_ia'), {'archivo': archivo})
        self.assertContains(respuesta, 'requiere OCR', status_code=422)
        ocr.assert_called_once()
        groq.return_value.chat.completions.create.assert_not_called()


@override_settings(ALLOWED_HOSTS=['testserver'], PAYPAL_CLIENT_ID='prueba', PAYPAL_CLIENT_SECRET='simulado')
class AgendamientoSeguroTests(TestCase):
    def setUp(self):
        from .models import EsquemaHorarioPsicologo

        self.paciente = User.objects.create_user('pago', email='pago@example.test')
        UsuarioPerfil.objects.create(usuario=self.paciente, nombre='Paciente')
        Cita.objects.create(paciente=self.paciente, fecha=timezone.localdate() - timedelta(days=1),
                            hora='09:00', estado='Completada')
        self.doctor = PerfilPsicologo.objects.create(usuario=User.objects.create_user('doctor-pago'),
                                                     cedula_profesional='pago')
        self.fecha = timezone.localdate() + timedelta(days=2)
        EsquemaHorarioPsicologo.objects.create(
            psicologo=self.doctor, fecha_inicio=self.fecha, fecha_fin=self.fecha,
            hora_inicio='09:00', hora_fin='17:00',
        )
        self.datos = {'fecha': self.fecha.isoformat(), 'hora': '10:00'}
        self.client.force_login(self.paciente)

    def test_solicitud_manipulada_no_confirma_sin_cobertura_o_pago(self):
        respuesta = self.client.post(reverse('guardar_cita'), {
            **self.datos, 'es_tectum': '1', 'paypal_order_id': 'FALSA',
        })
        self.assertEqual(respuesta.status_code, 400)
        self.assertFalse(Cita.objects.filter(estado='Confirmada').exists())
        cliente = Client(enforce_csrf_checks=True)
        cliente.force_login(self.paciente)
        self.assertEqual(cliente.post(reverse('crear_orden_cita_paypal'), self.datos).status_code, 403)

    @patch('core.pagos_paypal.capturar_orden', return_value='CAPTURA1')
    @patch('core.pagos_paypal.crear_orden', return_value='ORDEN1')
    def test_reserva_y_captura_autorizada_idempotente(self, crear, capturar):
        from .models import PagoCitaPayPal

        crear_url = reverse('crear_orden_cita_paypal')
        capturar_url = reverse('capturar_orden_cita_paypal')
        primera = self.client.post(crear_url, self.datos)
        self.assertEqual(primera.json().get('orden'), 'ORDEN1', primera.content)
        self.assertEqual(self.client.post(crear_url, self.datos).json()['orden'], 'ORDEN1')
        crear.assert_called_once()
        pago = PagoCitaPayPal.objects.get()
        self.assertEqual(str(pago.monto), '110.00')
        self.assertEqual(pago.cita.estado, 'Pendiente')
        self.assertEqual(pago.cita.psicologo_id, self.doctor.pk)

        otro = User.objects.create_user('otro-pago')
        UsuarioPerfil.objects.create(usuario=otro, nombre='Otra cuenta')
        self.client.force_login(otro)
        self.assertEqual(self.client.post(capturar_url, {'orden': 'ORDEN1'}).status_code, 404)
        self.client.force_login(self.paciente)
        for _ in range(2):
            respuesta = self.client.post(capturar_url, {'orden': 'ORDEN1'})
            self.assertEqual(respuesta.json().get('status'), 'success', respuesta.content)
        capturar.assert_called_once()
        pago.refresh_from_db()
        self.assertEqual((pago.captura, pago.cita.estado), ('CAPTURA1', 'Confirmada'))

    @patch('core.pagos_paypal.crear_orden', return_value='ORDEN2')
    def test_cancelar_suelta_horario_sin_borrar_el_intento(self, crear):
        from .models import PagoCitaPayPal

        respuesta = self.client.post(reverse('crear_orden_cita_paypal'), self.datos)
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        respuesta = self.client.post(reverse('cancelar_reserva_paypal'), {'orden': 'ORDEN2'})
        self.assertEqual(respuesta.status_code, 200)
        cita = PagoCitaPayPal.objects.get().cita
        self.assertEqual(cita.estado, 'Cancelada')
        self.assertIsNone(cita.psicologo_id)

    @patch('core.views.COLABORADORES_TECTUM', {'pago@example.test'})
    def test_inhouse_exige_correo_verificado_y_limita_servicio(self):
        sesion = self.client.session
        sesion['convenio_tectum'] = True
        sesion.save()
        url = reverse('guardar_cita')
        self.assertEqual(self.client.post(url, self.datos).status_code, 403)
        UsuarioPerfil.objects.filter(usuario=self.paciente).update(correo_tectum_verificado=self.paciente.email)
        self.assertEqual(self.client.post(url, {**self.datos, 'tipo_servicio': 'pareja'}).status_code, 400)
        respuesta = self.client.post(url, self.datos)
        self.assertEqual(respuesta.json().get('status'), 'success', respuesta.content)
        self.assertTrue(Cita.objects.get(estado='Confirmada').es_tectum)

    @patch('core.pagos_paypal.crear_orden', return_value='ORDENVENCIDA')
    @patch('core.pagos_paypal.capturar_orden')
    def test_orden_vencida_no_captura_y_libera_el_horario(self, capturar, crear):
        from .models import PagoCitaPayPal

        self.assertEqual(self.client.post(reverse('crear_orden_cita_paypal'), self.datos).status_code, 200)
        PagoCitaPayPal.objects.update(vence=timezone.now() - timedelta(minutes=1))
        respuesta = self.client.post(reverse('capturar_orden_cita_paypal'), {'orden': 'ORDENVENCIDA'})
        self.assertEqual(respuesta.status_code, 409)
        capturar.assert_not_called()
        self.client.get(reverse('disponibilidad_por_tipo'))
        self.assertIsNone(PagoCitaPayPal.objects.get().cita.psicologo_id)

    @patch('core.pagos_paypal._orden')
    def test_captura_valida_moneda_importe_y_estado_del_proveedor(self, orden):
        from decimal import Decimal
        from .pagos_paypal import ErrorPayPal, capturar_orden

        pago = SimpleNamespace(pk=1, cita_id=2, orden='ORDEN', monto=Decimal('110.00'))
        captura = {'id': 'CAPTURA', 'status': 'COMPLETED', 'amount': {'currency_code': 'MXN', 'value': '110.00'}}
        orden.return_value = {
            'id': 'ORDEN', 'status': 'COMPLETED',
            'purchase_units': [{'reference_id': '2', 'payments': {'captures': [captura]}}],
        }
        self.assertEqual(capturar_orden(pago), 'CAPTURA')
        captura['amount']['value'] = '1.00'
        with self.assertRaises(ErrorPayPal):
            capturar_orden(pago)
