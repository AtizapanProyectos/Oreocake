"""Ejecutar exclusivamente con hope_project.settings_flujo_test."""
from datetime import time, timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import Cita, EsquemaHorarioPsicologo, PerfilPsicologo, TratamientoPaciente, UsuarioPerfil
from .views import _modalidad_previa, obtener_slots_psicologo_para_dia, psicologo_atiende_modalidad


@override_settings(STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.InMemoryStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
})
class AtencionHibridaTests(TestCase):
    def setUp(self):
        self.paciente = User.objects.create_user('consultante')
        self.perfil = UsuarioPerfil.objects.create(usuario=self.paciente, nombre='Consultante', es_beneficios=True)
        self.doctor = PerfilPsicologo.objects.create(
            usuario=User.objects.create_user('presencial', first_name='Terapeuta'),
            cedula_profesional='presencial', modalidad_atencion='Presencial', esta_activo=False,
        )
        self.fecha = timezone.localdate() + timedelta(days=5)
        EsquemaHorarioPsicologo.objects.create(psicologo=self.doctor, fecha_inicio=self.fecha,
            fecha_fin=self.fecha + timedelta(days=30), hora_inicio=time(9), hora_fin=time(17))
        TratamientoPaciente.objects.create(paciente=self.paciente, tipo_servicio='individual', psicologo_asignado=self.doctor)
        self.admin = User.objects.create_superuser('admin-hibrido')
        self.client.force_login(self.admin)
        self.addCleanup(patch.stopall)
        self.meet = patch('core.views.generar_link_meet', return_value={'link': 'https://meet.google.com/prueba', 'id_evento': 'evento'}).start()
        patch('core.views.send_mail').start()
        patch('core.views._notificar_cita_paciente').start()

    def previa(self, **extra):
        return Cita.objects.create(**{'paciente': self.paciente, 'psicologo': self.doctor,
            'fecha': timezone.localdate() - timedelta(days=2), 'hora': time(10),
            'estado': 'Completada', 'modalidad': 'Presencial', **extra})

    def guardar(self, **extra):
        return self.client.post(reverse('admin_guardar_cita_paciente'), {
            'paciente_id': self.paciente.pk, 'fecha': self.fecha.isoformat(), 'hora': '10:00',
            'tipo_servicio': 'individual', 'modalidad': 'En línea', 'es_beneficios': '1', 'es_tectum': '0', **extra})

    def test_solo_completada_presencial_pasada_del_mismo_psicologo_habilita(self):
        for extra in ({'estado': 'Pendiente'}, {'estado': 'Cancelada'}, {'estado': 'Confirmada'},
                      {'modalidad': 'En línea'}, {'fecha': self.fecha}, {'psicologo': None},
                      {'paciente': self.admin}):
            with self.subTest(extra=extra):
                cita = self.previa(**extra)
                self.assertFalse(psicologo_atiende_modalidad(self.doctor, 'En línea', self.paciente))
                cita.delete()
        self.previa()
        self.assertTrue(psicologo_atiende_modalidad(self.doctor, 'En línea', self.paciente))
        self.assertFalse(psicologo_atiende_modalidad(self.doctor, 'En línea'))
        self.assertTrue(psicologo_atiende_modalidad(self.doctor, 'Presencial'))

    def test_rechazo_no_reasigna_ni_genera_meet(self):
        self.assertEqual(self.guardar().status_code, 409)
        self.meet.assert_not_called()
        self.assertFalse(Cita.objects.exists())
        self.assertEqual(TratamientoPaciente.objects.get().psicologo_asignado_id, self.doctor.pk)
        r = self.client.get(reverse('admin_disponibilidad_paciente'), {'paciente_id': self.paciente.pk})
        self.assertFalse(r.json()['virtual_habilitada'])

    def test_reserva_virtual_conserva_doctor_cerrado_y_programa_sin_editar_perfil(self):
        self.previa()
        slots = obtener_slots_psicologo_para_dia(self.doctor, self.fecha, paciente=self.paciente)
        self.assertIn('10:00 AM', slots)
        self.assertEqual(self.guardar(es_beneficios='0', es_tectum='1').json()['status'], 'success')
        cita = Cita.objects.get(fecha=self.fecha)
        self.assertEqual(cita.psicologo_id, self.doctor.pk)
        self.assertEqual(cita.modalidad, 'En línea')
        self.assertTrue(cita.enlace_meet)
        self.assertTrue(cita.es_tectum)
        self.assertFalse(cita.es_beneficios)
        self.perfil.refresh_from_db()
        self.assertTrue(self.perfil.es_beneficios)
        self.meet.assert_called_once()

    def test_ocupado_no_reasigna(self):
        self.previa()
        self.previa(fecha=self.fecha, estado='Confirmada')
        self.assertEqual(self.guardar().status_code, 409)
        self.meet.assert_not_called()

    def test_precarga_por_servicio_y_programa_desde_perfil(self):
        self.assertEqual(_modalidad_previa(self.paciente), 'En línea')
        self.previa()
        self.previa(fecha=timezone.localdate()-timedelta(days=1), modalidad='En línea', tipo_sesion='pareja')
        self.assertEqual(_modalidad_previa(self.paciente), 'Presencial')
        self.assertEqual(_modalidad_previa(self.paciente, 'pareja'), 'En línea')
        self.assertEqual(_modalidad_previa(self.paciente, 'familiar'), 'En línea')
        r = self.client.get(reverse('admin_buscar_pacientes'), {'q': 'Consultante'})
        self.assertTrue(r.json()['resultados'][0]['es_beneficios'])
        self.assertEqual(r.json()['resultados'][0]['modalidad_predeterminada'], 'Presencial')

    def test_planes_mensuales_respetan_hibrida_y_admisiones(self):
        from .planes_mensuales import _terapeutas
        self.assertFalse(_terapeutas(self.paciente, 'individual', 'En línea').exists())
        self.previa()
        self.assertEqual(list(_terapeutas(self.paciente, 'individual', 'En línea')), [self.doctor])
        TratamientoPaciente.objects.all().delete()
        self.assertFalse(_terapeutas(self.paciente, 'individual', 'En línea').exists())

    def test_panel_administrativo_renderiza_caracteristicas(self):
        r = self.client.get(reverse('panel_admin_agendar_cita'))
        self.assertContains(r, 'Características especiales del consultante')
        self.assertContains(r, 'Tectum Beneficios')

    def test_disponibilidad_paciente_hibrida_y_retorno_a_presencial(self):
        self.previa()
        self.previa(fecha=timezone.localdate()-timedelta(days=1), modalidad='En línea')
        self.client.force_login(self.paciente)
        for modalidad in ('En línea', 'Presencial'):
            data = self.client.get(reverse('disponibilidad_por_tipo'), {'modalidad': modalidad}).json()
            self.assertEqual(data['status'], 'success')
            self.assertIn('10:00 AM', data['dias'][self.fecha.isoformat()])
            self.assertEqual(data['psicologo_asignado'], 'Terapeuta')

    def test_reserva_de_pago_y_recepcion_conservan_presencial(self):
        from .views import _guardar_cita_paciente
        self.previa()
        solicitud = RequestFactory().post('/guardar-cita/', {
            'fecha': self.fecha.isoformat(), 'hora': '10:00', 'modalidad': 'En línea', 'tipo_servicio': 'individual'})
        solicitud.user, solicitud.session = self.paciente, {}
        pago = _guardar_cita_paciente(solicitud, reservar_pago=True)
        self.assertEqual(pago.cita.psicologo_id, self.doctor.pk)
        self.assertEqual(pago.cita.modalidad, 'En línea')
        self.assertEqual(pago.cita.estado, 'Pendiente')
        r = self.client.post(reverse('recepcion_confirmar_cita'), {
            'paciente_id': self.paciente.pk, 'fecha': self.fecha.isoformat(), 'hora': '11:00',
            'modalidad': 'En línea', 'tipo_servicio': 'individual', 'es_tectum': '0',
            'metodo_pago_presencial': 'efectivo'})
        self.assertEqual(r.json()['status'], 'success', r.content)
        self.assertEqual(Cita.objects.get(fecha=self.fecha, hora=time(11)).psicologo_id, self.doctor.pk)

    def test_reagendar_virtual_con_psicologo_presencial(self):
        self.previa()
        cita = self.previa(fecha=self.fecha, modalidad='En línea', estado='Confirmada')
        self.client.force_login(self.paciente)
        r = self.client.post(reverse('reagendar_cita'), {
            'cita_id': cita.pk, 'fecha': self.fecha.isoformat(), 'hora': '11:00'})
        self.assertEqual(r.json()['status'], 'success', r.content)
        cita.refresh_from_db()
        self.assertEqual(cita.psicologo_id, self.doctor.pk)
        self.assertEqual(cita.hora, time(11))

    def test_primera_asignacion_virtual_excluye_presencial_aunque_acepte_nuevos(self):
        from .views import obtener_slots_globales
        self.doctor.esta_activo = True
        self.doctor.save(update_fields=['esta_activo'])
        TratamientoPaciente.objects.all().delete()
        self.assertEqual(obtener_slots_globales(self.fecha, self.fecha), {})

    def test_transferencia_hibrida_conserva_terapeuta(self):
        self.previa()
        with patch('core.views._leer_y_validar_comprobante', return_value=(None, b'%PDF-prueba', 'pdf')):
            r = self.client.post(reverse('subir_comprobante_transferencia'), {
                'paciente_id': self.paciente.pk, 'fecha': self.fecha.isoformat(), 'hora': '10:00',
                'tipo_servicio': 'individual', 'modalidad': 'En línea'})
        self.assertEqual(r.json()['status'], 'success', r.content)
        self.assertEqual(Cita.objects.get(fecha=self.fecha).psicologo_id, self.doctor.pk)

    def test_agenda_recepcion_respeta_historial_y_exclusion_de_la_cita(self):
        from .agenda_recepcion import slots
        self.previa()
        cita = self.previa(fecha=self.fecha, modalidad='En línea', estado='Confirmada')
        self.assertEqual(slots(self.doctor, self.fecha, self.fecha, 'individual', 'En línea'), {})
        libres = slots(self.doctor, self.fecha, self.fecha, 'individual', 'En línea',
                       excluir=[cita.pk], paciente=self.paciente)
        self.assertIn('10:00', libres[self.fecha.isoformat()])
