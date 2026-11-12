from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import (
    Cita, ComprobanteTransferencia, DiaFestivo, EsquemaHorarioPsicologo, EventoAuditoria, PagoCitaPayPal,
    PerfilPsicologo, TratamientoPaciente, UsuarioPerfil,
)


@override_settings(PAYPAL_CLIENT_ID='prueba', PAYPAL_CLIENT_SECRET='simulado', PAYPAL_MODE='sandbox')
class PagosRecepcionTests(TestCase):
    def setUp(self):
        self.recepcion = User.objects.create_superuser('recepcion')
        self.paciente = User.objects.create_user('paciente', email='paciente@example.test')
        UsuarioPerfil.objects.create(usuario=self.paciente, nombre='Paciente')
        self.doctor = PerfilPsicologo.objects.create(
            usuario=User.objects.create_user('terapeuta'), cedula_profesional='recepcion',
            modalidad_atencion='Mixto', atiende_familiar=True,
        )
        fecha = timezone.localdate() + timedelta(days=3)
        EsquemaHorarioPsicologo.objects.create(
            psicologo=self.doctor, fecha_inicio=fecha, fecha_fin=fecha,
            hora_inicio='09:00', hora_fin='17:00',
        )
        self.datos = {
            'paciente_id': self.paciente.pk, 'fecha': fecha.isoformat(), 'hora': '10:00',
            'animo': 'Bien', 'tipo_servicio': 'familiar', 'integrantes_familia': '4',
        }
        self.client.force_login(self.recepcion)

    @patch('core.views._notificar_cita_paciente')
    @patch('core.pagos_paypal.capturar_orden', side_effect=['CAPTURA0', 'CAPTURA1'])
    @patch('core.pagos_paypal._orden')
    def test_confirma_y_notifica_una_vez_por_modalidad(self, crear, capturar, notificar):
        Cita.objects.create(
            paciente=self.paciente, fecha=timezone.localdate() - timedelta(days=1),
            hora='09:00', modalidad='En línea', estado='Confirmada',
        )
        for i, modalidad in enumerate(('Presencial', 'En línea')):
            with self.subTest(modalidad=modalidad):
                orden = f'ORDEN{i}'
                crear.return_value = {'id': orden}
                datos = dict(self.datos, modalidad=modalidad, hora=f'{10 + i}:00')
                if modalidad == 'En línea':
                    datos.pop('animo')
                respuesta = self.client.post(reverse('crear_orden_cita_paypal'), datos)
                self.assertEqual(respuesta.json().get('orden'), orden, respuesta.content)
                pago = PagoCitaPayPal.objects.get(orden=orden)
                self.assertEqual(pago.cita.estado, 'Pendiente')
                self.assertEqual(pago.cita.psicologo_id, self.doctor.pk)
                cuerpo_paypal = crear.call_args.args[2]
                self.assertNotIn('payment_source', cuerpo_paypal)
                self.assertEqual(cuerpo_paypal['purchase_units'][0]['amount']['value'], f'{pago.monto:.2f}')

                with self.captureOnCommitCallbacks(execute=True) as callbacks:
                    for _ in range(2):
                        respuesta = self.client.post(reverse('capturar_orden_cita_paypal'), {
                            'orden': orden, 'paciente_id': self.paciente.pk,
                        })
                        self.assertEqual(respuesta.json().get('modalidad'), modalidad, respuesta.content)
                self.assertEqual(len(callbacks), 1)
                self.assertEqual('pase_url' in respuesta.json(), modalidad == 'Presencial')
                pago.refresh_from_db()
                cita = pago.cita
                self.assertEqual(cita.paciente_id, self.paciente.pk)
                self.assertEqual(cita.estado, 'Confirmada')
                self.assertEqual(cita.estado_animo, 'Bien' if modalidad == 'Presencial' else 'No especificó')
                self.assertEqual(cita.integrantes_familia, 4)
                self.assertEqual(TratamientoPaciente.objects.get(paciente=self.paciente).psicologo_asignado, self.doctor)
        self.assertEqual(capturar.call_count, 2)
        self.assertEqual(notificar.call_count, 2)

    def test_rechaza_una_carita_invalida_en_recepcion(self):
        datos = dict(self.datos, animo='Valor inválido')
        for ruta in ('crear_orden_cita_paypal', 'subir_comprobante_transferencia', 'recepcion_confirmar_cita'):
            respuesta = self.client.post(reverse(ruta), datos)
            self.assertEqual(respuesta.status_code, 400)
            self.assertIn('estado de ánimo', respuesta.json()['message'])
        self.assertFalse(Cita.objects.exists())

    @patch('core.views.generar_link_meet', return_value={'link': 'https://meet.google.com/abc-defg-hij', 'id_evento': 'prueba'})
    def test_citas_en_linea_con_efectivo_y_tarjeta_sin_carita(self, generar_meet):
        from django.core import mail

        url = reverse('recepcion_confirmar_cita')
        datos = {**self.datos, 'modalidad': 'En línea', 'es_tectum': '0'}
        datos.pop('animo')
        for hora, metodo in (('10:00', 'efectivo'), ('11:00', 'clip_presencial')):
            with self.subTest(metodo=metodo):
                cuerpo = {**datos, 'hora': hora, 'metodo_pago_presencial': metodo}
                with self.captureOnCommitCallbacks(execute=True) as callbacks:
                    primera = self.client.post(url, cuerpo)
                    reintento = self.client.post(url, cuerpo)
                self.assertEqual(primera.status_code, 200, primera.content)
                self.assertEqual(primera.json(), reintento.json())
                self.assertNotIn('pase_url', primera.json())
                self.assertEqual(len(callbacks), 1)
                cita = Cita.objects.get(pk=primera.json()['cita_id'])
                self.assertEqual((cita.estado, cita.metodo_pago_presencial), ('Confirmada', metodo))
                self.assertEqual(cita.estado_animo, 'No especificó')
                self.assertEqual(cita.enlace_meet, 'https://meet.google.com/abc-defg-hij')
                self.assertIn(reverse('formulario_previo_meet', args=[cita.pk]), mail.outbox[-1].alternatives[0].content)
        self.assertEqual(generar_meet.call_count, 2)
        self.assertEqual(len(mail.outbox), 2)
        self.assertFalse(PagoCitaPayPal.objects.exists())

    @patch('core.pagos_paypal.crear_orden')
    def test_ignora_horario_bloqueado_y_rechaza_suplantacion(self, crear):
        DiaFestivo.objects.create(fecha=self.datos['fecha'])
        respuesta = self.client.post(reverse('crear_orden_cita_paypal'), self.datos)
        self.assertEqual(respuesta.json()['status'], 'error')
        intruso = User.objects.create_user('intruso')
        self.client.force_login(intruso)
        for ruta in ('crear_orden_cita_paypal', 'capturar_orden_cita_paypal', 'cancelar_reserva_paypal'):
            self.assertEqual(self.client.post(reverse(ruta), self.datos).status_code, 403)
        crear.assert_not_called()
        self.assertFalse(Cita.objects.exists())

    @override_settings(STORAGES={
        'default': {'BACKEND': 'django.core.files.storage.InMemoryStorage'},
        'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
    })
    @patch('core.views._enviar_correo_pase_admision')
    def test_transferencia_sigue_archivando_comprobante_y_confirmando(self, notificar):
        archivo = SimpleUploadedFile('comprobante.pdf', b'%PDF-1.7 comprobante')
        respuesta = self.client.post(reverse('subir_comprobante_transferencia'), {
            **self.datos, 'animo': '', 'modalidad': 'Presencial', 'comprobante': archivo,
        })
        self.assertEqual(respuesta.json().get('status'), 'success', respuesta.content)
        comprobante = ComprobanteTransferencia.objects.get()
        self.assertEqual(comprobante.usuario_id, self.paciente.pk)
        self.assertEqual(comprobante.cita.estado, 'Confirmada')
        self.assertEqual(comprobante.cita.estado_animo, 'No especificó')
        notificar.assert_called_once()

    def test_agendador_reutiliza_checkout_y_caritas(self):
        for ruta in ('recepcion_agendar_online', 'recepcion_agendar_presencial'):
            respuesta = self.client.get(reverse(ruta), follow=True)
            self.assertContains(respuesta, 'enable-funding=card')
            self.assertContains(respuesta, 'mood-wizard-btn" data-animo=', count=5)
            self.assertContains(respuesta, 'Estado de ánimo (opcional)')
            self.assertContains(respuesta, 'Transferencia bancaria BBVA')

    @override_settings(PAYPAL_CLIENT_SECRET='')
    @patch('core.pagos_paypal.crear_orden')
    def test_sin_secreto_muestra_checkout_manual_y_transferencia(self, crear):
        respuesta = self.client.get(reverse('recepcion_agendar_cita'))
        self.assertContains(respuesta, 'const paypalManual = true;')
        self.assertContains(respuesta, 'enable-funding=card')
        self.assertNotContains(respuesta, 'value="paypal" disabled')
        self.assertContains(respuesta, 'value="transferencia" checked')
        with self.assertLogs('core.views', level='ERROR') as registro:
            respuesta = self.client.post(reverse('crear_orden_cita_paypal'), self.datos)
        self.assertEqual(respuesta.status_code, 503)
        self.assertIn('PAYPAL_CLIENT_SECRET', registro.output[0])
        self.assertEqual(respuesta.json()['variables_paypal'], ['PAYPAL_CLIENT_SECRET'])
        self.assertFalse(Cita.objects.exists())
        crear.assert_not_called()

    @patch('core.pagos_paypal.capturar_orden')
    def test_recepcion_rechaza_captura_recibida_del_navegador(self, capturar):
        respuesta = self.client.post(reverse('capturar_orden_cita_paypal'), {
            'paciente_id': self.paciente.pk, 'orden': 'ORDENSDK', 'captura_sdk': 'CAPTURASDK',
        })
        self.assertEqual(respuesta.status_code, 400)
        capturar.assert_not_called()

    @override_settings(PAYPAL_CLIENT_SECRET='')
    @patch('core.views._notificar_cita_paciente')
    def test_checkout_manual_reserva_y_solo_recepcion_confirma(self, notificar):
        reservar = reverse('recepcion_reservar_paypal_manual')
        preparar = reverse('recepcion_preparar_paypal_manual')
        respuesta = self.client.post(reservar, {**self.datos, 'orden': 'ORDENMANUAL', 'modalidad': 'Presencial'})
        self.assertEqual(respuesta.json()['status'], 'success', respuesta.content)
        referencia = respuesta.json()['referencia']
        pago = PagoCitaPayPal.objects.get(orden=referencia)
        datos_orden = {'orden': referencia, 'paciente_id': self.paciente.pk}
        conciliar = reverse('recepcion_conciliar_paypal_manual', args=[pago.pk])
        verificacion = {'decision': 'confirmar', 'transaccion': 'TRANSACCIONREAL', 'revisado': '1'}
        self.assertEqual(self.client.post(conciliar, verificacion).status_code, 404)
        self.assertEqual(self.client.post(preparar, datos_orden).json()['status'], 'success')
        self.assertEqual(self.client.post(preparar, datos_orden).status_code, 409)
        self.assertEqual(self.client.post(reverse('capturar_orden_cita_paypal'), datos_orden).status_code, 409)
        self.assertEqual(pago.cita.estado, 'Pendiente')
        self.assertIsNone(pago.captura)
        notificar.assert_not_called()

        pagina = self.client.get(reverse('recepcion_agendar_cita'))
        self.assertContains(pagina, 'Pagos de PayPal por verificar')
        self.assertContains(pagina, 'ORDENMANUAL')
        self.assertEqual(self.client.post(conciliar, {**verificacion, 'revisado': ''}).status_code, 400)

        self.client.force_login(self.paciente)
        for url, datos in ((reservar, self.datos), (preparar, datos_orden), (conciliar, verificacion)):
            self.assertEqual(self.client.post(url, datos).status_code, 403)

        self.client.force_login(self.recepcion)
        with self.captureOnCommitCallbacks(execute=True):
            for _ in range(2):
                respuesta = self.client.post(conciliar, verificacion)
                self.assertEqual(respuesta.json()['status'], 'success', respuesta.content)
        pago.refresh_from_db()
        self.assertEqual(pago.cita.estado, 'Confirmada')
        self.assertEqual(pago.captura, 'TRANSACCIONREAL')
        evento = EventoAuditoria.objects.get(accion='recepcion_paypal_confirmar')
        self.assertEqual(evento.usuario, self.recepcion)
        self.assertEqual(evento.detalles['orden'], 'ORDENMANUAL')
        notificar.assert_called_once()

    def test_liberar_orden_revisada_suelta_el_horario(self):
        respuesta = self.client.post(reverse('recepcion_reservar_paypal_manual'), {
            **self.datos, 'orden': 'ORDENCANCELADA',
        })
        referencia = respuesta.json()['referencia']
        pago = PagoCitaPayPal.objects.get(orden=referencia)
        respuesta = self.client.post(reverse('recepcion_preparar_paypal_manual'), {
            'orden': referencia, 'paciente_id': self.paciente.pk,
        })
        self.assertEqual(respuesta.status_code, 200)
        respuesta = self.client.post(reverse('recepcion_conciliar_paypal_manual', args=[pago.pk]), {
            'decision': 'liberar', 'revisado': '1',
        })
        self.assertEqual(respuesta.json()['status'], 'success')
        pago.refresh_from_db()
        self.assertEqual(pago.cita.estado, 'Cancelada')
        self.assertIsNone(pago.cita.psicologo_id)
        self.assertIsNone(pago.captura)
