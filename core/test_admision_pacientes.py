"""Continuidad terapéutica y admisión: ejecutar solo con settings_flujo_test."""
import json
from datetime import time, timedelta
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import (
    BloqueoAgendaPsicologo, Cita, ConsentimientoInformado, DiaFestivo,
    DiaLibrePsicologo, EsquemaHorarioPsicologo, PerfilPsicologo,
    ReglaCoVision, TratamientoPaciente, UsuarioPerfil,
)
from .views import (
    _psicologo_asignado_servicio, obtener_slots_globales,
    obtener_slots_psicologo_para_dia,
)


@override_settings(STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.InMemoryStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
})
class AdmisionPacientesTests(TestCase):
    def setUp(self):
        cache.clear()
        self.paciente = User.objects.create_user('paciente', email='paciente@example.test')
        self.perfil = UsuarioPerfil.objects.create(usuario=self.paciente, nombre='Paciente')
        self.admin = User.objects.create_superuser('administracion')
        self.fecha = timezone.localdate() + timedelta(days=4)
        self.doctor = self.crear_doctor('asignado', False)
        self.otro = self.crear_doctor('nuevo', True)
        ConsentimientoInformado.objects.create(
            paciente=self.paciente, nombre_firmante='Paciente', firma_base64='firma', archivo_pdf='firma.pdf',
        )
        self.client.force_login(self.paciente)
        self.datos = {
            'fecha': self.fecha.isoformat(), 'hora': '10:00',
            'tipo_servicio': 'individual', 'modalidad': 'En línea', 'paypal_order_id': 'LEGACY123',
        }
        self.addCleanup(patch.stopall)
        patch('core.views.generar_link_meet', return_value=None).start()
        patch('core.views._notificar_cita_paciente').start()

    def crear_doctor(self, nombre, abierto):
        doctor = PerfilPsicologo.objects.create(
            usuario=User.objects.create_user(nombre, first_name=nombre), cedula_profesional=nombre,
            esta_activo=abierto, modalidad_atencion='Mixto', atiende_pareja=True, atiende_familiar=True,
        )
        EsquemaHorarioPsicologo.objects.create(
            psicologo=doctor, fecha_inicio=self.fecha, fecha_fin=self.fecha + timedelta(days=40),
            hora_inicio=time(9), hora_fin=time(17),
        )
        return doctor

    def asignar(self, servicio='individual', doctor=None, **extra):
        return TratamientoPaciente.objects.create(
            paciente=self.paciente, tipo_servicio=servicio, psicologo_asignado=doctor or self.doctor, **extra,
        )

    def guardar(self, ruta='guardar_cita', **extra):
        return self.client.post(reverse(ruta), {**self.datos, **extra})

    def test_nuevo_solo_ve_y_reserva_con_admisiones_abiertas(self):
        self.assertIn('10:00 AM', obtener_slots_globales(self.fecha, self.fecha)[self.fecha.isoformat()])
        respuesta = self.guardar()
        self.assertEqual(respuesta.json()['status'], 'success', respuesta.content)
        self.assertEqual(Cita.objects.get().psicologo_id, self.otro.pk)

    def test_nuevo_no_puede_asignarse_al_cerrado_aunque_envie_su_id(self):
        self.otro.esta_activo = False
        self.otro.save()
        self.assertEqual(obtener_slots_globales(self.fecha, self.fecha), {})
        for ruta in ('guardar_cita', 'admin_guardar_cita_paciente', 'recepcion_confirmar_cita'):
            with self.subTest(ruta=ruta):
                self.client.force_login(self.paciente if ruta == 'guardar_cita' else self.admin)
                respuesta = self.guardar(ruta, paciente_id=self.paciente.pk, psicologo_id=self.doctor.pk)
                self.assertEqual(respuesta.json()['status'], 'error', respuesta.content)
        self.assertFalse(Cita.objects.exists())
        self.assertFalse(TratamientoPaciente.objects.exists())

    def test_asignacion_activa_no_exige_sesiones_y_conserva_terapeuta(self):
        tratamiento = self.asignar()
        for abiertas, previas in ((False, 0), (False, 1), (False, 3), (True, 0)):
            with self.subTest(abiertas=abiertas, previas=previas):
                Cita.objects.all().delete()
                self.doctor.esta_activo = abiertas
                self.doctor.save()
                for n in range(previas):
                    Cita.objects.create(paciente=self.paciente, psicologo=self.doctor,
                                        fecha=self.fecha - timedelta(days=10 + n), hora=time(9), estado='Completada')
                disponibilidad = self.client.get(reverse('disponibilidad_por_tipo')).json()
                self.assertTrue(disponibilidad['es_asignado'])
                self.assertEqual(disponibilidad['psicologo_asignado'], 'asignado')
                respuesta = self.guardar()
                self.assertEqual(respuesta.json()['status'], 'success', respuesta.content)
                self.assertEqual(Cita.objects.get(pk=respuesta.json()['cita_id']).psicologo_id, self.doctor.pk)
                tratamiento.refresh_from_db()
                self.assertEqual(tratamiento.psicologo_asignado_id, self.doctor.pk)

    def test_varios_servicios_activos_y_servicio_nuevo(self):
        self.asignar('individual')
        self.asignar('pareja')
        for n, servicio in enumerate(('individual', 'pareja', 'familiar')):
            respuesta = self.guardar(tipo_servicio=servicio, hora=f'{10+n}:00')
            self.assertEqual(respuesta.json()['status'], 'success', respuesta.content)
            esperado = self.otro if servicio == 'familiar' else self.doctor
            self.assertEqual(Cita.objects.get(pk=respuesta.json()['cita_id']).psicologo_id, esperado.pk)

    def test_perfil_heredado_solo_individual_y_tratamiento_explicito_prevalece(self):
        self.perfil.psicologo_asignado = self.doctor
        self.perfil.save()
        self.assertEqual(_psicologo_asignado_servicio(self.paciente, 'individual'), self.doctor)
        self.assertIsNone(_psicologo_asignado_servicio(self.paciente, 'pareja'))
        tratamiento = self.asignar(activo=False)
        self.assertIsNone(_psicologo_asignado_servicio(self.paciente, 'individual'))
        tratamiento.activo = True
        tratamiento.psicologo_asignado = None
        tratamiento.save()
        self.assertIsNone(_psicologo_asignado_servicio(self.paciente, 'individual'))
        tratamiento.psicologo_asignado = self.otro
        tratamiento.save()
        self.assertEqual(_psicologo_asignado_servicio(self.paciente, 'individual'), self.otro)

    def test_historial_no_reactiva_asignacion(self):
        tratamiento = self.asignar(activo=False)
        Cita.objects.create(paciente=self.paciente, psicologo=self.doctor, fecha=self.fecha-timedelta(days=10),
                            hora=time(9), estado='Completada')
        respuesta = self.guardar()
        self.assertEqual(respuesta.json()['status'], 'success', respuesta.content)
        self.assertEqual(Cita.objects.get(pk=respuesta.json()['cita_id']).psicologo_id, self.otro.pk)
        tratamiento.refresh_from_db()
        self.assertEqual(tratamiento.psicologo_asignado_id, self.otro.pk)

    def test_perfil_heredado_coincide_en_disponibilidad_y_guardado(self):
        self.perfil.psicologo_asignado = self.doctor
        self.perfil.save()
        self.assertTrue(self.client.get(reverse('disponibilidad_por_tipo')).json()['es_asignado'])
        self.assertEqual(self.client.get(reverse('plan_mensual_disponibilidad')).json()['psicologo_asignado']['id'], self.doctor.pk)
        respuesta = self.guardar()
        self.assertEqual(respuesta.json()['status'], 'success', respuesta.content)
        self.assertEqual(Cita.objects.get().psicologo_id, self.doctor.pk)

    def test_recepcion_no_reutiliza_perfil_individual_para_servicio_nuevo(self):
        self.perfil.psicologo_asignado = self.doctor
        self.perfil.save()
        self.client.force_login(self.admin)
        respuesta = self.guardar('recepcion_confirmar_cita', paciente_id=self.paciente.pk,
                                 tipo_servicio='pareja', metodo_pago_presencial='efectivo', es_tectum='0')
        self.assertEqual(respuesta.json()['status'], 'success', respuesta.content)
        self.assertEqual(Cita.objects.get().psicologo_id, self.otro.pk)

    def test_convenios_conservan_asignado_cerrado(self):
        from django.test import RequestFactory
        from .views import _guardar_cita_paciente

        self.asignar()
        with patch('core.views._correo_tectum_verificado', return_value=True):
            for convenio in ('inhouse', 'beneficios'):
                with self.subTest(convenio=convenio), patch('core.views._convenio_cita', return_value=convenio):
                    Cita.objects.all().delete()
                    solicitud = RequestFactory().post('/guardar-cita/', self.datos)
                    solicitud.user, solicitud.session = self.paciente, {}
                    resultado = _guardar_cita_paciente(solicitud, reservar_pago=convenio == 'beneficios')
                    if convenio == 'inhouse':
                        self.assertEqual(json.loads(resultado.content)['status'], 'success')
                    self.assertEqual(Cita.objects.get().psicologo_id, self.doctor.pk)

    def test_admin_recepcion_y_transferencia_conservan_asignado_cerrado(self):
        tratamiento = self.asignar()
        self.client.force_login(self.admin)
        with patch('core.views._leer_y_validar_comprobante', return_value=(None, b'%PDF-prueba', 'pdf')):
            for n, ruta in enumerate(('admin_guardar_cita_paciente', 'recepcion_confirmar_cita', 'subir_comprobante_transferencia')):
                with self.subTest(ruta=ruta):
                    disponibilidad = self.client.get(reverse('admin_disponibilidad_paciente'), {'paciente_id': self.paciente.pk}).json()
                    self.assertEqual(disponibilidad['psicologo_asignado'], 'asignado')
                    respuesta = self.guardar(ruta, paciente_id=self.paciente.pk, hora=f'{10+n}:00')
                    self.assertEqual(respuesta.json()['status'], 'success', respuesta.content)
                    self.assertEqual(Cita.objects.get(paciente=self.paciente, hora=time(10+n)).psicologo_id, self.doctor.pk)
        tratamiento.refresh_from_db()
        self.assertEqual(tratamiento.psicologo_asignado_id, self.doctor.pk)

    def test_reserva_paypal_conserva_asignado_cerrado(self):
        from .views import _guardar_cita_paciente
        from django.test import RequestFactory

        self.asignar()
        solicitud = RequestFactory().post('/guardar-cita/', self.datos)
        solicitud.user, solicitud.session = self.paciente, {}
        pago = _guardar_cita_paciente(solicitud, reservar_pago=True)
        self.assertEqual(pago.cita.psicologo_id, self.doctor.pk)
        self.assertEqual(pago.cita.estado, 'Pendiente')

    def test_reagendar_conserva_el_terapeuta_de_la_cita(self):
        cita = Cita.objects.create(paciente=self.paciente, psicologo=self.doctor,
                                   fecha=self.fecha, hora=time(9), estado='Confirmada')
        respuesta = self.client.post(reverse('reagendar_cita'), {
            'cita_id': cita.pk, 'fecha': self.fecha.isoformat(), 'hora': '10:00',
        })
        self.assertEqual(respuesta.json()['status'], 'success', respuesta.content)
        cita.refresh_from_db()
        self.assertEqual((cita.psicologo_id, cita.hora), (self.doctor.pk, time(10)))

    def test_bloqueo_u_ocupacion_no_reasigna(self):
        tratamiento = self.asignar()
        bloqueo = BloqueoAgendaPsicologo.objects.create(psicologo=self.doctor, fecha=self.fecha,
                                                       hora_inicio=time(10), hora_fin=time(11), motivo='Co-Visión')
        for ocupado in (False, True):
            if ocupado:
                bloqueo.delete()
                Cita.objects.create(paciente=self.admin, psicologo=self.doctor, fecha=self.fecha, hora=time(10))
            respuesta = self.guardar()
            self.assertEqual(respuesta.status_code, 409, respuesta.content)
            self.assertFalse(Cita.objects.filter(paciente=self.paciente).exists())
            tratamiento.refresh_from_db()
            self.assertEqual(tratamiento.psicologo_asignado_id, self.doctor.pk)

    def test_cerrado_respeta_restricciones_reales_de_agenda(self):
        self.assertIn('10:00 AM', obtener_slots_psicologo_para_dia(self.doctor, self.fecha))
        festivo = DiaFestivo.objects.create(fecha=self.fecha)
        self.assertEqual(obtener_slots_psicologo_para_dia(self.doctor, self.fecha), [])
        festivo.delete()
        libre = DiaLibrePsicologo.objects.create(psicologo=self.doctor, fecha=self.fecha)
        self.assertEqual(obtener_slots_psicologo_para_dia(self.doctor, self.fecha), [])
        libre.delete()
        regla = ReglaCoVision.objects.create(dia_semana=self.fecha.weekday(), hora_inicio=time(10), fecha_ancla=self.fecha)
        self.assertNotIn('10:00 AM', obtener_slots_psicologo_para_dia(self.doctor, self.fecha))
        regla.delete()
        esquema = self.doctor.esquemas_horarios.get()
        esquema.dias_descanso = [self.fecha.weekday()]
        esquema.save()
        self.assertEqual(obtener_slots_psicologo_para_dia(self.doctor, self.fecha), [])
        esquema.dias_descanso = []
        esquema.save()
        self.doctor.modalidad_atencion = 'En línea'
        self.assertEqual(obtener_slots_psicologo_para_dia(self.doctor, self.fecha, modalidad='Presencial'), [])
        self.doctor.atiende_pareja = False
        self.assertEqual(obtener_slots_psicologo_para_dia(self.doctor, self.fecha, tipo_sesion='pareja'), [])

    def datos_plan(self):
        return {'clave': str(uuid4()), 'tipo_servicio': 'individual', 'modalidad': 'En línea',
                'citas': json.dumps([{'fecha': (self.fecha+timedelta(days=d)).isoformat(), 'hora': '10:00'} for d in (0, 7, 14, 21)])}

    def test_plan_mensual_asignado_cerrado(self):
        self.asignar()
        disponibles = self.client.get(reverse('plan_mensual_disponibilidad')).json()
        self.assertEqual([doc['id'] for doc in disponibles['terapeutas']], [self.doctor.pk])
        respuesta = self.client.post(reverse('plan_mensual_reservar'), self.datos_plan())
        self.assertEqual(respuesta.json()['status'], 'success', respuesta.content)
        self.assertEqual(set(Cita.objects.values_list('psicologo_id', flat=True)), {self.doctor.pk})
        self.assertEqual(Cita.objects.count(), 4)

    def test_plan_nuevo_excluye_cerrado(self):
        self.otro.esta_activo = False
        self.otro.save()
        self.assertEqual(self.client.get(reverse('plan_mensual_disponibilidad')).json()['terapeutas'], [])
        respuesta = self.client.post(reverse('plan_mensual_reservar'), self.datos_plan())
        self.assertEqual(respuesta.status_code, 409, respuesta.content)
        self.assertFalse(Cita.objects.exists())

    def test_plan_asignado_sin_slot_no_cambia_a_otro(self):
        tratamiento = self.asignar()
        BloqueoAgendaPsicologo.objects.create(psicologo=self.doctor, fecha=self.fecha,
                                             hora_inicio=time(10), hora_fin=time(11), motivo='Co-Visión')
        respuesta = self.client.post(reverse('plan_mensual_reservar'), self.datos_plan())
        self.assertEqual(respuesta.status_code, 409, respuesta.content)
        self.assertFalse(Cita.objects.exists())
        tratamiento.refresh_from_db()
        self.assertEqual(tratamiento.psicologo_asignado_id, self.doctor.pk)

    def test_cierre_entre_consulta_de_slots_y_bloqueo_impide_nueva_asignacion(self):
        original = obtener_slots_psicologo_para_dia

        def cerrar(doctor, *args, **kwargs):
            horas = original(doctor, *args, **kwargs)
            PerfilPsicologo.objects.filter(pk=doctor.pk).update(esta_activo=False)
            return horas

        with patch('core.views.obtener_slots_psicologo_para_dia', side_effect=cerrar):
            respuesta = self.guardar()
        self.assertEqual(respuesta.json()['status'], 'error', respuesta.content)
        self.assertFalse(Cita.objects.exists())

    def test_plan_revalida_admision_despues_de_elegir_candidatos(self):
        from .planes_mensuales import _terapeutas

        def cerrar(*args):
            candidatos = list(_terapeutas(*args).values_list('pk', flat=True))
            PerfilPsicologo.objects.filter(pk__in=candidatos).update(esta_activo=False)
            # Simular la lista obtenida antes del cierre; la lectura bloqueada es real.
            return PerfilPsicologo.objects.filter(pk__in=candidatos)

        with patch('core.planes_mensuales._terapeutas', side_effect=cerrar):
            respuesta = self.client.post(reverse('plan_mensual_reservar'), self.datos_plan())
        self.assertEqual(respuesta.status_code, 409, respuesta.content)
        self.assertFalse(Cita.objects.exists())
