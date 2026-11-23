from datetime import date, time, timedelta
from io import StringIO
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.management import call_command, CommandError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .covisiones import GRUPOS, MOTIVO
from .models import (
    BloqueoAgendaPsicologo, Cita, ConsentimientoInformado, EsquemaHorarioPsicologo, PerfilPsicologo,
    ReglaCoVision, TratamientoPaciente, UsuarioPerfil,
)
from .views import obtener_slots_psicologo, obtener_slots_psicologo_para_dia


class BloqueosAgendaTests(TestCase):
    def setUp(self):
        self.fecha = timezone.localdate() + timedelta(days=3)
        self.psicologo = self.doctor('terapeuta')
        self.otro = self.doctor('otro')
        for doctor in (self.psicologo, self.otro):
            EsquemaHorarioPsicologo.objects.create(
                psicologo=doctor, fecha_inicio=self.fecha, fecha_fin=self.fecha + timedelta(days=60),
                hora_inicio=time(9), hora_fin=time(20),
            )
        self.bloqueo = BloqueoAgendaPsicologo.objects.create(
            psicologo=self.psicologo, fecha=self.fecha, hora_inicio=time(10), hora_fin=time(11), motivo='Co-Visión',
        )

    def doctor(self, cuenta):
        return PerfilPsicologo.objects.create(
            usuario=User.objects.create_user(cuenta), cedula_profesional=cuenta,
            modalidad_atencion='Mixto', atiende_pareja=True, atiende_familiar=True,
        )

    def horas(self, doctor=None, fecha=None, **kwargs):
        return obtener_slots_psicologo_para_dia(doctor or self.psicologo, fecha or self.fecha, **kwargs)

    def test_solo_psicologo_fecha_y_hora_configurados_en_todos_los_servicios(self):
        for servicio in ('individual', 'pareja', 'familiar'):
            for modalidad in ('En línea', 'Presencial'):
                horas = self.horas(tipo_sesion=servicio, modalidad=modalidad)
                self.assertNotIn('10:00 AM', horas)
                self.assertIn('09:00 AM', horas)
                self.assertIn('11:00 AM', horas)
        self.assertIn('10:00 AM', self.horas(self.otro))
        self.assertIn('10:00 AM', self.horas(fecha=self.fecha + timedelta(days=7)))
        self.assertIn('10:00 AM', self.horas(fecha=self.fecha + timedelta(days=45)))

    def test_solapamiento_parcial_y_edicion_desde_modelo(self):
        self.bloqueo.hora_inicio = time(10, 30)
        self.bloqueo.hora_fin = time(11, 30)
        self.bloqueo.save()
        self.assertNotIn('10:00 AM', self.horas())
        self.assertNotIn('11:00 AM', self.horas())
        self.assertIn('12:00 PM', self.horas())
        self.bloqueo.psicologo = self.otro
        self.bloqueo.fecha += timedelta(days=1)
        self.bloqueo.save()
        self.assertIn('10:00 AM', self.horas())
        self.assertNotIn('10:00 AM', self.horas(self.otro, self.fecha + timedelta(days=1)))
        self.bloqueo.activo = False
        self.bloqueo.save()
        self.assertIn('10:00 AM', self.horas(self.otro, self.fecha + timedelta(days=1)))

    def test_intervalo_invalido(self):
        self.bloqueo.hora_fin = time(9)
        with self.assertRaises(ValidationError):
            self.bloqueo.full_clean()

    @patch('core.views.generar_link_meet')
    def test_admin_y_recepcion_no_reservan_bloqueo_con_terapeuta_asignado(self, meet):
        paciente = User.objects.create_user('paciente')
        UsuarioPerfil.objects.create(usuario=paciente, nombre='Paciente')
        TratamientoPaciente.objects.create(paciente=paciente, psicologo_asignado=self.psicologo, tipo_servicio='individual')
        self.client.force_login(User.objects.create_superuser('admin'))
        for ruta in ('admin_guardar_cita_paciente', 'recepcion_confirmar_cita'):
            r = self.client.post(reverse(ruta), {
                'paciente_id': paciente.pk, 'fecha': self.fecha.isoformat(), 'hora': '10:00',
                'modalidad': 'En línea', 'tipo_servicio': 'individual',
            })
            self.assertEqual(r.status_code, 409, r.content)
        self.assertFalse(Cita.objects.exists())
        meet.assert_not_called()

    def test_bloqueo_no_modifica_cita_preexistente(self):
        paciente = User.objects.create_user('paciente')
        cita = Cita.objects.create(paciente=paciente, psicologo=self.psicologo, fecha=self.fecha, hora=time(10), estado='Confirmada')
        self.horas()
        cita.refresh_from_db()
        self.assertEqual(cita.estado, 'Confirmada')
        self.assertEqual(cita.hora, time(10))

    @patch('core.views.generar_link_meet')
    @patch('core.views._leer_y_validar_comprobante', return_value=(None, b'%PDF-prueba', 'pdf'))
    def test_transferencia_rechaza_antes_de_archivar_o_generar_meet(self, validar, meet):
        paciente = User.objects.create_user('paciente')
        UsuarioPerfil.objects.create(usuario=paciente, nombre='Paciente')
        TratamientoPaciente.objects.create(paciente=paciente, psicologo_asignado=self.psicologo, tipo_servicio='individual')
        self.client.force_login(User.objects.create_superuser('admin'))
        r = self.client.post(reverse('subir_comprobante_transferencia'), {
            'paciente_id': paciente.pk, 'fecha': self.fecha.isoformat(), 'hora': '10:00',
            'modalidad': 'En línea', 'tipo_servicio': 'individual',
        })
        self.assertEqual(r.status_code, 409, r.content)
        self.assertFalse(Cita.objects.exists())
        meet.assert_not_called()

    def test_cobro_recepcion_y_plan_mensual_no_reservan_bloqueo(self):
        import json
        from uuid import uuid4
        paciente = User.objects.create_user('paciente')
        UsuarioPerfil.objects.create(usuario=paciente, nombre='Paciente')
        ConsentimientoInformado.objects.create(paciente=paciente, nombre_firmante='Paciente', firma_base64='firma', archivo_pdf='consentimiento.pdf')
        TratamientoPaciente.objects.create(paciente=paciente, psicologo_asignado=self.psicologo, tipo_servicio='individual')
        self.client.force_login(User.objects.create_superuser('admin'))
        r = self.client.post(reverse('recepcion_confirmar_cita'), {
            'paciente_id': paciente.pk, 'fecha': self.fecha.isoformat(), 'hora': '10:00',
            'modalidad': 'En línea', 'tipo_servicio': 'individual',
            'metodo_pago_presencial': 'efectivo', 'es_tectum': '0',
        })
        self.assertEqual(r.status_code, 409, r.content)
        self.client.force_login(paciente)
        r = self.client.post(reverse('plan_mensual_reservar'), {
            'clave': str(uuid4()), 'tipo_servicio': 'individual', 'modalidad': 'En línea',
            'citas': json.dumps([{'fecha': (self.fecha + timedelta(days=d)).isoformat(), 'hora': '10:00 AM'} for d in (0, 7, 14, 21)]),
        })
        self.assertEqual(r.status_code, 409, r.content)
        self.assertFalse(Cita.objects.exists())


class CargaCoVisionTests(TestCase):
    def setUp(self):
        self.doctores = {}
        for _, _, cuentas in GRUPOS:
            for cuenta in cuentas:
                self.doctores[cuenta] = PerfilPsicologo.objects.create(
                    usuario=User.objects.create_user(cuenta), cedula_profesional=cuenta,
                )
        self.karla = PerfilPsicologo.objects.create(
            usuario=User.objects.create_user('psi.kecheverria@espaciohope.com'), cedula_profesional='karla',
        )
        self.reglas = [ReglaCoVision.objects.create(
            dia_semana=dia, hora_inicio=time(hora), fecha_ancla=fecha,
        ) for dia, hora, fecha in ((1, 8, date(2026, 10, 27)), (2, 19, date(2026, 10, 28)))]

    def cargar(self, aplicar=False):
        salida = StringIO()
        call_command('cargar_covisiones_2026', aplicar=aplicar, stdout=salida)
        return salida.getvalue()

    def test_previsualizacion_no_escribe(self):
        self.assertIn('33 bloqueos', self.cargar())
        self.assertFalse(BloqueoAgendaPsicologo.objects.exists())
        self.assertEqual(ReglaCoVision.objects.filter(activo=True).count(), 2)

    def test_carga_idempotente_calendario_y_karla_pendiente(self):
        otra = ReglaCoVision.objects.create(nombre='Otra reunión', dia_semana=3, hora_inicio=time(12), fecha_ancla=date(2026, 11, 26))
        self.cargar(True)
        self.cargar(True)
        self.assertEqual(BloqueoAgendaPsicologo.objects.count(), 33)
        self.assertFalse(BloqueoAgendaPsicologo.objects.filter(psicologo=self.karla).exists())
        self.assertFalse(ReglaCoVision.objects.filter(pk__in=[r.pk for r in self.reglas], activo=True).exists())
        otra.refresh_from_db()
        self.assertTrue(otra.activo)
        self.assertEqual(set(BloqueoAgendaPsicologo.objects.values_list('fecha', flat=True)), {
            date(2026, 11, 24), date(2026, 11, 25), date(2026, 11, 26),
            date(2026, 12, 8), date(2026, 12, 9), date(2026, 12, 10),
            date(2026, 12, 22), date(2026, 12, 23), date(2026, 12, 24),
        })
        self.assertEqual(set(BloqueoAgendaPsicologo.objects.values_list('motivo', flat=True)), {MOTIVO})

    def test_cuenta_faltante_aborta_sin_desactivar_reglas(self):
        next(iter(self.doctores.values())).delete()
        with self.assertRaises(CommandError):
            self.cargar(True)
        self.assertFalse(BloqueoAgendaPsicologo.objects.exists())
        self.assertEqual(ReglaCoVision.objects.filter(activo=True).count(), 2)

    def test_reporta_solape_parcial_pendiente_y_conserva_cita(self):
        paciente = User.objects.create_user('paciente')
        cita = Cita.objects.create(
            paciente=paciente, psicologo=self.doctores['psi.jromero@espaciohope.com'],
            fecha=date(2026, 11, 26), hora=time(17, 30), estado='Pendiente',
        )
        self.assertIn(f'CONFLICTO: cita #{cita.pk}', self.cargar(True))
        cita.refresh_from_db()
        self.assertEqual(cita.estado, 'Pendiente')
        salida = StringIO()
        with patch('core.management.commands.reportar_conflictos_covisiones.timezone.localdate', return_value=date(2026, 11, 23)):
            call_command('reportar_conflictos_covisiones', stdout=salida)
        self.assertIn(f'CONFLICTO: cita #{cita.pk}', salida.getvalue())
