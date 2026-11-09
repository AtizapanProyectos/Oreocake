from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import (
    Cita, ComprobanteTransferencia, DiaFestivo, EsquemaHorarioPsicologo, PagoCitaPayPal,
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
                self.assertEqual(cita.estado_animo, 'Bien')
                self.assertEqual(cita.integrantes_familia, 4)
                self.assertEqual(TratamientoPaciente.objects.get(paciente=self.paciente).psicologo_asignado, self.doctor)
        self.assertEqual(capturar.call_count, 2)
        self.assertEqual(notificar.call_count, 2)

    def test_requiere_animo_en_los_tres_metodos(self):
        datos = dict(self.datos, animo='')
        for ruta in ('crear_orden_cita_paypal', 'subir_comprobante_transferencia', 'recepcion_confirmar_cita'):
            respuesta = self.client.post(reverse(ruta), datos)
            self.assertEqual(respuesta.status_code, 400)
            self.assertIn('estado de ánimo', respuesta.json()['message'])
        self.assertFalse(Cita.objects.exists())

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
            **self.datos, 'modalidad': 'Presencial', 'comprobante': archivo,
        })
        self.assertEqual(respuesta.json().get('status'), 'success', respuesta.content)
        comprobante = ComprobanteTransferencia.objects.get()
        self.assertEqual(comprobante.usuario_id, self.paciente.pk)
        self.assertEqual(comprobante.cita.estado, 'Confirmada')
        self.assertEqual(comprobante.cita.estado_animo, 'Bien')
        notificar.assert_called_once()

    def test_agendador_reutiliza_checkout_y_caritas(self):
        for ruta in ('recepcion_agendar_online', 'recepcion_agendar_presencial'):
            respuesta = self.client.get(reverse(ruta), follow=True)
            self.assertContains(respuesta, 'enable-funding=card')
            self.assertContains(respuesta, 'mood-wizard-btn" data-animo=', count=5)
            self.assertContains(respuesta, 'Transferencia bancaria BBVA')
