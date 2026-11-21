import json
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth.models import User
from django.db import IntegrityError
from django.template import Context, Template
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import Cita, ConsentimientoInformado, EsquemaHorarioPsicologo, EventoAuditoria, PerfilPsicologo, PlanMensual, TratamientoPaciente, UsuarioPerfil
from .planes_mensuales import liberar_planes_vencidos


class VersionEstáticosPlanTests(SimpleTestCase):
    def test_url_cambia_al_cambiar_archivo_sin_reiniciar_proceso(self):
        with TemporaryDirectory() as carpeta, override_settings(STATICFILES_DIRS=[carpeta]):
            archivo = Path(carpeta) / 'version-prueba.css'
            archivo.write_text('body{color:red}', encoding='utf-8')
            plantilla = Template("{% load hope_assets %}{% versioned_static 'version-prueba.css' %}")
            anterior = plantilla.render(Context())
            archivo.write_text('body{color:purple}', encoding='utf-8')
            nuevo = plantilla.render(Context())
        self.assertIn('?v=', anterior)
        self.assertNotEqual(anterior, nuevo)

    def test_version_estable_para_el_mismo_contenido(self):
        plantilla = Template("{% load hope_assets %}{% versioned_static 'css/plan_mensual.css' %}")
        self.assertEqual(plantilla.render(Context()), plantilla.render(Context()))


class PlanMensualTests(TestCase):
    def setUp(self):
        self.paciente = User.objects.create_user('paciente', email='paciente@example.test')
        UsuarioPerfil.objects.create(usuario=self.paciente, nombre='Paciente')
        ConsentimientoInformado.objects.create(paciente=self.paciente, nombre_firmante='Paciente', firma_base64='firma', archivo_pdf='consentimiento.pdf')
        self.admin = User.objects.create_superuser('admin')
        self.doctor = PerfilPsicologo.objects.create(
            usuario=User.objects.create_user('terapeuta'), cedula_profesional='plan',
            modalidad_atencion='Mixto', atiende_pareja=True, atiende_familiar=True,
        )
        self.inicio = timezone.localdate() + timedelta(days=3)
        EsquemaHorarioPsicologo.objects.create(
            psicologo=self.doctor, fecha_inicio=self.inicio, fecha_fin=self.inicio + timedelta(days=90),
            hora_inicio='09:00', hora_fin='17:00',
        )
        self.seleccion = [{'fecha': (self.inicio + timedelta(days=d)).isoformat(), 'hora': '10:00 AM'} for d in (0, 7, 14, 21)]
        self.datos = {'clave': str(uuid4()), 'tipo_servicio': 'individual', 'modalidad': 'En línea', 'citas': json.dumps(self.seleccion)}
        self.client.force_login(self.paciente)

    def reservar(self, **cambios):
        return self.client.post(reverse('plan_mensual_reservar'), {**self.datos, **cambios})

    def actualizar(self, plan, accion, **datos):
        return self.client.post(reverse('plan_mensual_actualizar'), {'plan_id': plan.pk, 'accion': accion, **datos})

    def test_precio_servidor_y_cuatro_pendientes_sin_notificar(self):
        with patch('core.views._notificar_cita_paciente') as notificar:
            respuesta = self.reservar(monto='0.01')
        self.assertEqual(respuesta.status_code, 200)
        plan = PlanMensual.objects.get()
        self.assertEqual(plan.monto, Decimal('440'))
        self.assertEqual(plan.citas.count(), 4)
        self.assertEqual(set(plan.citas.values_list('psicologo_id', flat=True)), {self.doctor.pk})
        self.assertEqual(set(plan.citas.values_list('estado', flat=True)), {'Pendiente'})
        notificar.assert_not_called()

    def test_precios_pareja_familiar(self):
        for servicio, integrantes, esperado in [('pareja', '', '860'), ('familiar', '2', '860'), ('familiar', '4', '1720')]:
            with self.subTest(servicio=servicio, integrantes=integrantes):
                respuesta = self.client.get(reverse('plan_mensual_disponibilidad'), {'tipo_servicio': servicio, 'integrantes_familia': integrantes})
                self.assertEqual(respuesta.status_code, 200)
                self.assertEqual(Decimal(respuesta.json()['monto']), Decimal(esperado))

    def test_fechas_invalidas_no_crean_plan(self):
        casos = [self.seleccion[:3], self.seleccion + self.seleccion[:1], [self.seleccion[0]] * 4,
                 [*self.seleccion[:3], {'fecha': (self.inicio + timedelta(days=30)).isoformat(), 'hora': '10:00'}],
                 [{'fecha': 'incorrecta', 'hora': 'no'}] * 4, [None] * 4]
        for citas in casos:
            with self.subTest(citas=citas):
                self.assertEqual(self.reservar(citas=json.dumps(citas)).status_code, 400)
        self.assertFalse(PlanMensual.objects.exists())
        self.assertFalse(Cita.objects.exists())

    def test_limite_29_dias_permitido(self):
        citas = [*self.seleccion[:3], {'fecha': (self.inicio + timedelta(days=29)).isoformat(), 'hora': '10:00'}]
        self.assertEqual(self.reservar(citas=json.dumps(citas)).status_code, 200)

    def test_anticipacion_y_limite_90_dias(self):
        for fecha in (timezone.localdate() - timedelta(days=1), timezone.localdate() + timedelta(days=91)):
            seleccion = [{'fecha': (fecha + timedelta(days=i)).isoformat(), 'hora': '10:00'} for i in range(4)]
            self.assertEqual(self.reservar(citas=json.dumps(seleccion)).status_code, 400)

    def test_horario_ocupado_no_crea_reserva_parcial(self):
        Cita.objects.create(paciente=User.objects.create_user('otro'), psicologo=self.doctor, fecha=self.inicio + timedelta(days=14), hora='10:00')
        self.assertEqual(self.reservar().status_code, 409)
        self.assertFalse(PlanMensual.objects.exists())
        self.assertEqual(Cita.objects.count(), 1)

    def test_error_en_tercera_cita_revierte_todo(self):
        crear = Cita.objects.create
        intentos = 0

        def fallar(**kwargs):
            nonlocal intentos
            intentos += 1
            if intentos == 3:
                raise IntegrityError('conflicto simulado')
            return crear(**kwargs)

        with patch.object(Cita.objects, 'create', side_effect=fallar):
            self.assertEqual(self.reservar().status_code, 409)
        self.assertFalse(PlanMensual.objects.exists())
        self.assertFalse(Cita.objects.exists())

    def test_exige_mismo_terapeuta_para_todas(self):
        self.doctor.esquemas_horarios.update(fecha_fin=self.inicio + timedelta(days=8))
        otro = PerfilPsicologo.objects.create(usuario=User.objects.create_user('otrodoc'), cedula_profesional='otro')
        EsquemaHorarioPsicologo.objects.create(psicologo=otro, fecha_inicio=self.inicio + timedelta(days=9), fecha_fin=self.inicio + timedelta(days=30), hora_inicio='09:00', hora_fin='17:00')
        self.assertEqual(self.reservar().status_code, 409)
        self.assertFalse(Cita.objects.exists())

    def test_respeta_terapeuta_asignado(self):
        TratamientoPaciente.objects.create(paciente=self.paciente, tipo_servicio='individual', psicologo_asignado=self.doctor)
        self.doctor.esquemas_horarios.update(fecha_fin=self.inicio + timedelta(days=8))
        otro = PerfilPsicologo.objects.create(usuario=User.objects.create_user('alternativo'), cedula_profesional='alternativo')
        EsquemaHorarioPsicologo.objects.create(psicologo=otro, fecha_inicio=self.inicio, fecha_fin=self.inicio + timedelta(days=30), hora_inicio='09:00', hora_fin='17:00')
        self.assertEqual(self.reservar().status_code, 409)

    def test_idempotencia_y_bloqueo_de_segundo_plan(self):
        primera = self.reservar().json()
        segunda = self.reservar().json()
        self.assertEqual(primera['plan_id'], segunda['plan_id'])
        self.assertEqual(self.reservar(clave=str(uuid4())).status_code, 409)
        self.assertEqual(Cita.objects.count(), 4)

    def test_no_reutiliza_clave_para_otra_seleccion(self):
        self.reservar()
        self.assertEqual(self.reservar(tipo_servicio='pareja').status_code, 409)
        self.assertEqual(PlanMensual.objects.count(), 1)

    def test_pago_no_se_reutiliza_en_otro_plan(self):
        self.reservar()
        primero = PlanMensual.objects.get()
        self.actualizar(primero, 'capturar')
        self.actualizar(primero, 'confirmar', resultado='COMPLETED', referencia='PAGO123')
        seleccion = [{**c, 'hora': '11:00 AM'} for c in self.seleccion]
        self.reservar(clave=str(uuid4()), citas=json.dumps(seleccion))
        segundo = PlanMensual.objects.latest('pk')
        self.actualizar(segundo, 'capturar')
        self.assertEqual(self.actualizar(segundo, 'confirmar', resultado='COMPLETED', referencia='PAGO123').status_code, 409)
        segundo.refresh_from_db()
        self.assertEqual(segundo.estado, 'procesando')
        self.assertEqual(segundo.citas.filter(estado='Pendiente').count(), 4)

    def test_captura_confirmacion_y_reintento_notifican_una_vez(self):
        self.reservar()
        plan = PlanMensual.objects.get()
        self.assertEqual(self.actualizar(plan, 'capturar').json()['estado'], 'procesando')
        with patch('core.views._notificar_cita_paciente') as notificar:
            with self.captureOnCommitCallbacks(execute=True):
                respuesta = self.actualizar(plan, 'confirmar', resultado='COMPLETED', referencia='PAYPAL123')
                repetida = self.actualizar(plan, 'confirmar', resultado='COMPLETED', referencia='PAYPAL123')
            self.assertEqual(notificar.call_count, 4)
        self.assertEqual(respuesta.json()['estado'], 'confirmado')
        self.assertEqual(repetida.json()['estado'], 'confirmado')
        self.assertEqual(plan.citas.filter(estado='Confirmada').count(), 4)
        self.assertEqual(TratamientoPaciente.objects.get().psicologo_asignado_id, self.doctor.pk)

    def test_no_confirma_sin_captura_completa(self):
        self.reservar()
        plan = PlanMensual.objects.get()
        self.assertEqual(self.actualizar(plan, 'confirmar', resultado='COMPLETED', referencia='PAYPAL123').status_code, 409)
        self.actualizar(plan, 'capturar')
        self.assertEqual(self.actualizar(plan, 'confirmar', resultado='PENDING', referencia='PAYPAL123').status_code, 409)
        self.assertEqual(plan.citas.filter(estado='Pendiente').count(), 4)

    def test_expiracion_libera_las_cuatro_y_no_captura(self):
        self.reservar()
        plan = PlanMensual.objects.get()
        PlanMensual.objects.filter(pk=plan.pk).update(vence=timezone.now() - timedelta(minutes=1))
        respuesta = self.actualizar(plan, 'capturar')
        self.assertEqual(respuesta.json()['estado'], 'expirado')
        self.assertEqual(plan.citas.filter(estado='Cancelada', psicologo=None).count(), 4)

    def test_reserva_expirada_aparece_disponible_en_agenda_normal(self):
        self.reservar()
        PlanMensual.objects.update(vence=timezone.now() - timedelta(minutes=1))
        respuesta = self.client.get(reverse('disponibilidad_por_tipo'), {'tipo_sesion': 'individual'})
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(PlanMensual.objects.get().estado, 'expirado')
        self.assertEqual(Cita.objects.filter(psicologo=None).count(), 4)

    def test_captura_incierta_no_se_libera_ni_se_cancela_por_paciente(self):
        self.reservar()
        plan = PlanMensual.objects.get()
        self.actualizar(plan, 'capturar')
        PlanMensual.objects.update(vence=timezone.now() - timedelta(days=1))
        liberar_planes_vencidos()
        self.assertEqual(self.actualizar(plan, 'cancelar').status_code, 409)
        plan.refresh_from_db()
        self.assertEqual(plan.estado, 'procesando')
        self.assertEqual(plan.citas.filter(psicologo=self.doctor, estado='Pendiente').count(), 4)

    def test_cancelacion_idempotente_libera(self):
        self.reservar()
        plan = PlanMensual.objects.get()
        self.assertEqual(self.actualizar(plan, 'cancelar').json()['estado'], 'cancelado')
        self.assertEqual(self.actualizar(plan, 'cancelar').json()['estado'], 'cancelado')
        self.assertEqual(plan.citas.filter(estado='Cancelada', psicologo=None).count(), 4)
        self.assertEqual(self.actualizar(plan, 'capturar').status_code, 409)

    def test_admin_agenda_sin_cobrar_y_sin_consentimiento(self):
        ConsentimientoInformado.objects.all().delete()
        self.client.force_login(self.admin)
        with patch('core.views._notificar_cita_paciente') as notificar, self.captureOnCommitCallbacks(execute=True):
            respuesta = self.reservar(paciente_id=self.paciente.pk)
        self.assertEqual(respuesta.json()['estado'], 'confirmado')
        self.assertEqual(PlanMensual.objects.get().origen, 'admin')
        self.assertIsNone(PlanMensual.objects.get().referencia)
        self.assertEqual(notificar.call_count, 4)

    def test_admin_resuelve_captura_tras_revision(self):
        self.reservar()
        plan = PlanMensual.objects.get()
        self.actualizar(plan, 'capturar')
        self.client.force_login(self.admin)
        self.assertEqual(self.actualizar(plan, 'cancelar', paciente_id=self.paciente.pk).status_code, 409)
        self.assertEqual(self.actualizar(plan, 'cancelar', paciente_id=self.paciente.pk, revisado='1').json()['estado'], 'cancelado')
        self.assertTrue(EventoAuditoria.objects.filter(usuario=self.admin, accion='plan_mensual_revision_cancelar').exists())

    def test_admin_confirma_pago_revisado(self):
        self.reservar()
        plan = PlanMensual.objects.get()
        self.actualizar(plan, 'capturar')
        self.client.force_login(self.admin)
        respuesta = self.actualizar(plan, 'confirmar', paciente_id=self.paciente.pk, revisado='1', referencia='REVISION123')
        self.assertEqual(respuesta.json()['estado'], 'confirmado')
        self.assertTrue(EventoAuditoria.objects.filter(usuario=self.admin, accion='plan_mensual_revision_confirmar').exists())

    def test_paciente_no_opera_planes_ajenos(self):
        self.reservar()
        plan = PlanMensual.objects.get()
        otro = User.objects.create_user('ajeno')
        UsuarioPerfil.objects.create(usuario=otro, nombre='Ajeno')
        self.client.force_login(otro)
        self.assertEqual(self.actualizar(plan, 'capturar').status_code, 404)
        self.assertEqual(self.actualizar(plan, 'capturar', paciente_id=self.paciente.pk).status_code, 403)
        self.assertEqual(self.reservar(clave=self.datos['clave']).status_code, 400)  # sin consentimiento

    def test_sin_consentimiento_y_tenant_incorrecto(self):
        ConsentimientoInformado.objects.all().delete()
        self.assertEqual(self.reservar().status_code, 400)
        UsuarioPerfil.objects.filter(usuario=self.paciente).update(es_beneficios=True)
        self.assertEqual(self.client.get(reverse('plan_mensual_disponibilidad')).status_code, 403)

    def test_presencial_regla_existente_y_pases(self):
        self.assertEqual(self.reservar(modalidad='Presencial').status_code, 200)
        plan = PlanMensual.objects.get()
        self.actualizar(plan, 'capturar')
        respuesta = self.actualizar(plan, 'confirmar', resultado='COMPLETED', referencia='PRESENCIAL1')
        self.assertTrue(all(c['pase_url'] for c in respuesta.json()['citas']))
        self.assertTrue(all(plan.citas.values_list('codigo_pase', flat=True)))

    def test_presencial_bloqueado_por_historial_online(self):
        Cita.objects.create(paciente=self.paciente, psicologo=self.doctor, fecha=self.inicio - timedelta(days=10), hora='10:00', modalidad='En línea')
        self.assertEqual(self.reservar(modalidad='Presencial').status_code, 400)

    def test_estado_recupera_plan_pendiente(self):
        self.reservar()
        respuesta = self.client.get(reverse('plan_mensual_estado')).json()
        self.assertEqual(len(respuesta['planes']), 1)
        self.assertEqual(len(respuesta['planes'][0]['citas']), 4)

    def test_post_exige_csrf(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.paciente)
        self.assertEqual(client.post(reverse('plan_mensual_reservar'), self.datos).status_code, 403)

    def test_vistas_muestran_banner_y_confirmacion_inferior(self):
        self.client.force_login(self.admin)
        respuesta = self.client.get(reverse('panel_admin_agendar_cita'))
        self.assertContains(respuesta, 'Apartar mi horario')
        self.assertContains(respuesta, 'Dale continuidad a eso que estás construyendo.')
        for recurso in ['css/plan_mensual.css', 'js/plan_mensual.js']:
            fuente = Path(__file__).resolve().parent.parent / 'static' / recurso
            version = sha256(fuente.read_bytes()).hexdigest()[:16]
            self.assertContains(respuesta, recurso + '?v=' + version)
        contenido = respuesta.content.decode()
        self.assertLess(contenido.index('hpa-card-resumen'), contenido.index('class="hpa-footer"'))
        self.assertLess(contenido.index('</form>'), contenido.index('id="hpa-exito"'))

    @override_settings(PAYPAL_CLIENT_ID='prueba')
    def test_panel_muestra_cuatro_citas_sin_pedir_otro_pago(self):
        self.reservar()
        plan = PlanMensual.objects.get()
        self.actualizar(plan, 'capturar')
        self.actualizar(plan, 'confirmar', resultado='COMPLETED', referencia='PANEL123')
        respuesta = self.client.get(reverse('panel_generico'))
        self.assertContains(respuesta, 'No necesitas repetir el pago.')
        self.assertContains(respuesta, 'Otras citas confirmadas', count=1)
        self.assertNotContains(respuesta, 'Tus siguientes espacios')
        self.assertEqual(len(respuesta.context['otras_citas_proximas']), 3)

    def _historial(self, modalidad, dias=10, servicio='individual', estado='Completada', llegada='No registrado'):
        return Cita.objects.create(
            paciente=self.paciente, psicologo=self.doctor, fecha=timezone.localdate() - timedelta(days=dias),
            hora='12:00', modalidad=modalidad, tipo_sesion=servicio, estado=estado, registro_llegada=llegada,
        )

    def _competidor(self, modalidad='Mixto'):
        doctor = PerfilPsicologo.objects.create(
            usuario=User.objects.create_user('competidor'), cedula_profesional='competidor',
            modalidad_atencion=modalidad, atiende_pareja=True,
        )
        EsquemaHorarioPsicologo.objects.create(
            psicologo=doctor, fecha_inicio=self.inicio, fecha_fin=self.inicio + timedelta(days=90),
            hora_inicio='19:00', hora_fin='22:00',
        )
        return doctor

    def test_modalidad_inicial_sin_historial_es_online(self):
        data = self.client.get(reverse('plan_mensual_estado')).json()
        self.assertEqual(data['modalidad_predeterminada'], 'En línea')

    def test_modalidad_inicial_presencial_por_sesion_atendida(self):
        self._historial('Presencial')
        data = self.client.get(reverse('plan_mensual_estado')).json()
        self.assertEqual(data['modalidad_predeterminada'], 'Presencial')

    def test_historial_mixto_toma_ultima_atendida_e_ignora_futuras(self):
        self._historial('En línea', dias=20)
        self._historial('Presencial', dias=5)
        self._historial('En línea', dias=-10, estado='Confirmada')
        self._historial('En línea', dias=1, estado='Cancelada')
        data = self.client.get(reverse('plan_mensual_estado')).json()
        self.assertEqual(data['modalidad_predeterminada'], 'Presencial')
        respuesta = self.reservar(modalidad='Presencial')
        self.assertEqual(respuesta.status_code, 200)

    def test_historial_mixto_ultima_online_predetermina_online(self):
        self._historial('Presencial', dias=20)
        self._historial('En línea', dias=5)
        data = self.client.get(reverse('plan_mensual_estado')).json()
        self.assertEqual(data['modalidad_predeterminada'], 'En línea')

    def test_llegada_presencial_confirmada_cuenta_como_asistida(self):
        self._historial('Presencial', estado='Confirmada', llegada='Vino')
        self.assertEqual(self.client.get(reverse('plan_mensual_estado')).json()['modalidad_predeterminada'], 'Presencial')

    def test_default_respeta_historial_del_servicio(self):
        self._historial('Presencial', dias=20)
        self._historial('En línea', dias=5, servicio='pareja')
        self.assertEqual(self.client.get(reverse('plan_mensual_estado')).json()['modalidad_predeterminada'], 'Presencial')
        data = self.client.get(reverse('plan_mensual_estado'), {'tipo_servicio':'pareja'}).json()
        self.assertEqual(data['modalidad_predeterminada'], 'En línea')

    def test_asignacion_del_perfil_filtra_ambas_modalidades(self):
        self._competidor()
        UsuarioPerfil.objects.filter(usuario=self.paciente).update(psicologo_asignado=self.doctor)
        for modalidad in ['Presencial', 'En línea']:
            with self.subTest(modalidad=modalidad):
                data = self.client.get(reverse('plan_mensual_disponibilidad'), {'modalidad':modalidad}).json()
                self.assertEqual([doc['id'] for doc in data['terapeutas']], [self.doctor.pk])
                self.assertEqual(data['psicologo_asignado']['id'], self.doctor.pk)
                horas = [hora for dia in data['terapeutas'][0]['dias'].values() for hora in dia]
                self.assertNotIn('07:00 PM', horas)

    def test_asignado_incompatible_no_muestra_ni_reserva_con_otro(self):
        for modalidad_doc, modalidad_cita in [('En línea','Presencial'), ('Presencial','En línea')]:
            with self.subTest(modalidad=modalidad_cita):
                self.doctor.modalidad_atencion = modalidad_doc
                self.doctor.save(update_fields=['modalidad_atencion'])
                UsuarioPerfil.objects.filter(usuario=self.paciente).update(psicologo_asignado=self.doctor)
                competidor = self._competidor(modalidad_cita)
                data = self.client.get(reverse('plan_mensual_disponibilidad'), {'modalidad':modalidad_cita}).json()
                self.assertEqual(data['terapeutas'], [])
                seleccion = [{**c,'hora':'07:00 PM'} for c in self.seleccion]
                self.assertEqual(self.reservar(modalidad=modalidad_cita, citas=json.dumps(seleccion)).status_code, 409)
                competidor.esquemas_horarios.all().delete()
                competidor.delete()
                User.objects.filter(username='competidor').delete()
        self.assertFalse(PlanMensual.objects.exists())

    def test_tratamiento_tiene_prioridad_sobre_perfil(self):
        competidor = self._competidor()
        UsuarioPerfil.objects.filter(usuario=self.paciente).update(psicologo_asignado=competidor)
        TratamientoPaciente.objects.create(paciente=self.paciente, tipo_servicio='individual', psicologo_asignado=self.doctor)
        data = self.client.get(reverse('plan_mensual_disponibilidad')).json()
        self.assertEqual([doc['id'] for doc in data['terapeutas']], [self.doctor.pk])

    def test_asignacion_individual_no_se_reutiliza_en_pareja(self):
        self.doctor.atiende_pareja = False
        self.doctor.save(update_fields=['atiende_pareja'])
        UsuarioPerfil.objects.filter(usuario=self.paciente).update(psicologo_asignado=self.doctor)
        competidor = self._competidor()
        data = self.client.get(reverse('plan_mensual_disponibilidad'), {'tipo_servicio':'pareja'}).json()
        self.assertEqual([doc['id'] for doc in data['terapeutas']], [competidor.pk])

    def test_asignado_sin_horarios_no_cae_al_pool(self):
        self._competidor()
        UsuarioPerfil.objects.filter(usuario=self.paciente).update(psicologo_asignado=self.doctor)
        self.doctor.esquemas_horarios.all().delete()
        data = self.client.get(reverse('plan_mensual_disponibilidad')).json()
        self.assertEqual(data['terapeutas'], [])

    def test_disponibilidad_no_ofrece_filas_que_rompen_unicidad(self):
        Cita.objects.create(paciente=self.paciente, psicologo=self.doctor, fecha=self.inicio, hora='11:00', estado='Cancelada')
        data = self.client.get(reverse('plan_mensual_disponibilidad')).json()
        self.assertNotIn('11:00 AM', data['terapeutas'][0]['dias'][self.inicio.isoformat()])
