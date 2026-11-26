from datetime import timedelta, time
from unittest.mock import patch, MagicMock
from unittest import skipUnless
from contextlib import ExitStack
import smtplib
from django.test import TestCase, TransactionTestCase, override_settings, Client
from django.db import connection, close_old_connections
from django.contrib.auth.models import User
from django.utils import timezone
from django.urls import reverse
from .models import (Cita, PerfilPsicologo, UsuarioPerfil, HistorialClinico, RespuestaFormularioOrganica,
                     TrabajoProceso, EjecucionProceso, ReporteClinicoPDF, EstadoFlujoSesion, ControlOrquestador)
from . import orquestador as motor


@override_settings(ORQUESTADOR_ACTIVO=True, EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class OrquestadorTests(TestCase):
    def setUp(self):
        self.admin=User.objects.create_user('admin',is_superuser=True)
        self.pac=User.objects.create_user('pac',email='p@example.test')
        UsuarioPerfil.objects.create(usuario=self.pac,nombre='Paciente ficticio')
        self.doc=PerfilPsicologo.objects.create(usuario=User.objects.create_user('psi',email='d@example.test'),cedula_profesional='test')
        self.cita=Cita.objects.create(paciente=self.pac,psicologo=self.doc,fecha=timezone.localdate()-timedelta(days=1),hora=time(10),id_evento_google='test')
        self.client.force_login(self.admin)

    def nota(self,cita=None,**kwargs):
        return HistorialClinico.objects.create(cita=cita or self.cita,paciente=self.pac,psicologo=self.doc,notas_sesion='Nota del profesional',**kwargs)

    def ipp(self,cita=None):
        RespuestaFormularioOrganica.objects.create(cita=cita or self.cita,paciente=self.pac,respuestas={'1':4})

    def mocks(self):
        stack=ExitStack()
        for name,value in [('_construir_datos_checkin_paciente',{'sesion_numero':1}),('_construir_datos_checkin_psicologo',{'sesion_numero':1}),
                           ('_obtener_logo_base64',''),('convertir_html_a_pdf',b'%PDF-ficticio')]:
            stack.enter_context(patch('core.views.'+name,return_value=value))
        stack.enter_context(patch('core.orquestador.render_to_string',return_value='<html>test</html>'))
        return stack

    def vencer(self):
        TrabajoProceso.objects.filter(estado__in=['espera_reintento','espera_dependencia']).update(proximo_intento=timezone.now()-timedelta(seconds=1))

    def test_nota_existente_flujo_sin_google_y_estado_intacto(self):
        self.nota();self.ipp()
        with self.mocks(),patch('core.flujo_clinico._leer_meet') as google,patch('core.views._generar_bitacora_con_ia_de_texto') as ia:
            motor.correr()
            motor.correr()
        google.assert_not_called();ia.assert_not_called()
        self.cita.refresh_from_db()
        self.assertEqual(self.cita.estado,'Confirmada')
        self.assertTrue(self.cita.reporte_enviado)
        self.assertEqual(ReporteClinicoPDF.objects.count(),2)
        from django.core import mail
        self.assertEqual(len(mail.outbox),2)
        self.assertEqual(TrabajoProceso.objects.exclude(estado='completado').count(),0)

    def test_confirmadas_completadas_y_futuras(self):
        for estado in ('Confirmada','Completada'):
            self.cita.estado=estado
            self.assertTrue(motor.elegible(self.cita))
        for estado in ('Cancelada','Pendiente'):
            self.cita.estado=estado
            self.assertFalse(motor.elegible(self.cita))
        self.cita.estado='Confirmada'
        self.cita.fecha=timezone.localdate()+timedelta(days=1)
        self.assertFalse(motor.elegible(self.cita))

    def test_ventana_dos_horas(self):
        comienzo=motor.inicio(self.cita)
        with patch('core.orquestador.timezone.now',return_value=comienzo+timedelta(minutes=119)):
            self.assertFalse(motor.elegible(self.cita))
        with patch('core.orquestador.timezone.now',return_value=comienzo+timedelta(hours=2)):
            self.assertTrue(motor.elegible(self.cita))

    def test_72_horas_y_nota_posterior_reanuda(self):
        self.cita.fecha=timezone.localdate()-timedelta(days=4);self.cita.save()
        with patch('core.flujo_clinico._leer_meet') as google:
            motor.correr()
        google.assert_not_called()
        self.assertEqual(TrabajoProceso.objects.get(cita=self.cita,etapa='meet').estado,'revision_manual')
        self.nota();self.ipp();self.vencer()
        TrabajoProceso.objects.filter(cita=self.cita).update(proximo_intento=timezone.now())
        with self.mocks():motor.correr()
        self.cita.refresh_from_db();self.assertTrue(self.cita.reporte_enviado)

    def test_permiso_bloquea_sin_ia(self):
        from .flujo_clinico import PasoBloqueado
        with patch('core.flujo_clinico._leer_meet',side_effect=PasoBloqueado('Sin permiso','sin_permisos')) as google,patch('core.views._generar_bitacora_con_ia_de_texto') as ia:
            motor.correr();motor.correr()
        google.assert_called_once();ia.assert_not_called()
        self.assertEqual(TrabajoProceso.objects.get(etapa='meet').estado,'bloqueado')
        self.assertFalse(ControlOrquestador.objects.filter(clave='google',token='bloqueado').exists())

    def test_configuracion_ausente_pausa_integracion(self):
        from .flujo_clinico import PasoBloqueado
        with patch('core.flujo_clinico._leer_meet',side_effect=PasoBloqueado('Falta conexión','sin_permisos','google_configuracion')):
            motor.correr()
        self.assertTrue(ControlOrquestador.objects.filter(clave='google',token='bloqueado').exists())

    def test_bloqueo_de_acceso_a_72_horas_pide_nota_manual(self):
        motor.registrar(self.cita)
        self.cita.fecha=timezone.localdate()-timedelta(days=4);self.cita.save()
        TrabajoProceso.objects.filter(etapa='meet').update(estado='bloqueado',error='google_acceso')
        with patch('core.flujo_clinico._leer_meet') as google:motor.correr()
        google.assert_not_called()
        trabajo=TrabajoProceso.objects.get(etapa='meet')
        self.assertEqual(trabajo.estado,'revision_manual');self.assertEqual(trabajo.error,'nota_manual')

    def test_descarga_faltante_no_genera_ia_en_get(self):
        with patch('core.views._construir_datos_checkin_paciente') as ia:
            response=self.client.get(f'/panel-admin/descargar-reporte-pdf/{self.cita.pk}/paciente/')
        self.assertEqual(response.status_code,404);ia.assert_not_called()

    def test_pdf_reintento_reutiliza_sintesis(self):
        self.nota();self.ipp()
        with self.mocks(),patch('core.views._construir_datos_checkin_paciente',return_value={'sesion_numero':1}) as sintesis,patch('core.views.convertir_html_a_pdf',side_effect=RuntimeError('fallo')):
            motor.correr()
        sintesis.assert_called_once()
        self.vencer()
        with self.mocks(),patch('core.views._construir_datos_checkin_paciente') as sintesis:
            motor.correr()
        sintesis.assert_not_called()
        self.assertEqual(ReporteClinicoPDF.objects.count(),2)

    def test_smtp_temporal_solo_destinatario_pendiente(self):
        self.nota();self.ipp()
        with self.mocks(),patch('core.views._enviar_email_reporte_paciente_con_pdf') as pac,patch('core.views._enviar_email_reporte_psicologo_con_pdf',side_effect=smtplib.SMTPDataError(451,b'temporary')):
            motor.correr()
        pac.assert_called_once()
        self.cita.refresh_from_db();self.assertFalse(self.cita.reporte_enviado)
        self.vencer()
        with patch('core.views._enviar_email_reporte_paciente_con_pdf') as pac,patch('core.views._enviar_email_reporte_psicologo_con_pdf') as psi,patch('core.views.convertir_html_a_pdf') as pdf:
            motor.correr()
        pac.assert_not_called();psi.assert_called_once();pdf.assert_not_called()

    def test_smtp_incierto_no_se_reenvia_por_solicitud(self):
        self.nota();self.ipp()
        with self.mocks(),patch('core.views._enviar_email_reporte_paciente_con_pdf',side_effect=TimeoutError):motor.correr()
        trabajo=TrabajoProceso.objects.get(etapa='correo_paciente')
        self.assertEqual(trabajo.error,'entrega_incierta')
        motor.solicitar(self.cita,self.admin)
        with patch('core.views._enviar_email_reporte_paciente_con_pdf') as enviar:motor.correr()
        enviar.assert_not_called()

    def test_solicitud_persistente_idempotente_y_csrf(self):
        url=reverse('api_reintento_automatizacion',args=[self.cita.pk])
        with patch('core.flujo_clinico._leer_meet') as google:
            self.assertEqual(self.client.post(url,{'tipo':'completo'}).status_code,202)
            self.assertEqual(self.client.post(url,{'tipo':'completo'}).status_code,202)
        google.assert_not_called();self.assertEqual(TrabajoProceso.objects.count(),len(motor.ETAPAS))
        client=Client(enforce_csrf_checks=True);client.force_login(self.admin)
        self.assertEqual(client.post(url,{'tipo':'completo'}).status_code,403)
        self.client.force_login(self.pac);self.assertEqual(self.client.post(url).status_code,403)

    def test_presencial_no_vino_y_audio(self):
        self.cita.modalidad='Presencial';self.cita.registro_llegada='No Vino';self.cita.save()
        with patch('core.flujo_clinico._leer_meet') as google:motor.correr()
        google.assert_not_called();self.assertFalse(motor.elegible(self.cita))
        self.cita.registro_llegada='Vino';self.cita.save();self.nota(pendiente_revision_audio=True)
        with self.mocks():motor.correr()
        self.assertFalse(ReporteClinicoPDF.objects.exists())

    def test_panel_y_paginacion(self):
        self.assertContains(self.client.get(reverse('panel_admin_automatizacion_notas')),'Centro de orquestación')
        response=self.client.get(reverse('orquestacion_datos'))
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['total'],1)
        self.assertEqual(self.client.get(reverse('orquestacion_datos'),{'desde':'bad'}).status_code,400)

    def test_reserva_global_impide_segunda_ejecucion(self):
        ControlOrquestador.objects.create(clave='maestro',token='otro',vence=timezone.now()+timedelta(minutes=5))
        self.assertTrue(motor.correr()['ocupado'])
        self.assertFalse(TrabajoProceso.objects.exists())

    def test_generar_sin_enviar_y_descarga_privada(self):
        self.nota();self.ipp()
        motor.solicitar(self.cita,self.admin,'pdf_psicologo')
        with self.mocks():motor.correr()
        self.cita.refresh_from_db();self.assertFalse(self.cita.reporte_enviado)
        self.assertEqual(ReporteClinicoPDF.objects.count(),2)
        self.assertFalse(ReporteClinicoPDF.objects.filter(correo_enviado=True).exists())
        url=f'/panel-admin/descargar-reporte-pdf/{self.cita.pk}/paciente/'
        response=self.client.get(url)
        self.assertEqual(response.status_code,200)
        self.assertEqual(b''.join(response.streaming_content),b'%PDF-ficticio')
        self.client.force_login(self.pac)
        self.assertEqual(self.client.get(url).status_code,302)

    def test_limite_cinco_intentos(self):
        with patch('core.flujo_clinico._leer_meet',side_effect=TimeoutError) as google:
            for _ in range(8):
                motor.correr();self.vencer()
        self.assertEqual(google.call_count,5)
        self.assertEqual(TrabajoProceso.objects.get(etapa='meet').estado,'revision_manual')

    def test_nota_concurrente_conserva_campos(self):
        motor.registrar(self.cita)
        EstadoFlujoSesion.objects.create(cita=self.cita,datos_clinicos={c:'Texto IA' for c in motor.flujo.CAMPOS})
        self.nota(como_llega='Observación manual')
        trabajo=TrabajoProceso.objects.get(etapa='escritura')
        motor.procesar(trabajo)
        nota=HistorialClinico.objects.get(cita=self.cita)
        self.assertEqual(nota.como_llega,'Observación manual')
        self.assertEqual(nota.notas_sesion,'Nota del profesional')

    def test_reserva_vencida_correo_no_reenvia(self):
        self.nota();self.ipp();motor.registrar(self.cita)
        TrabajoProceso.objects.exclude(etapa__startswith='correo_').update(estado='completado')
        trabajo=TrabajoProceso.objects.get(etapa='correo_paciente')
        trabajo.estado='procesando';trabajo.token='vencido';trabajo.error='entrega_incierta'
        trabajo.vence=timezone.now()-timedelta(minutes=1);trabajo.save()
        EjecucionProceso.objects.create(trabajo=trabajo,token='vencido')
        with patch('core.views._enviar_email_reporte_paciente_con_pdf') as enviar:motor.correr()
        enviar.assert_not_called()
        trabajo.refresh_from_db();self.assertEqual(trabajo.estado,'revision_manual')

    def test_bloqueados_no_impiden_atender_siguiente_cita(self):
        motor.registrar(self.cita)
        TrabajoProceso.objects.filter(etapa='meet').update(estado='bloqueado',error='google_acceso')
        otra=Cita.objects.create(paciente=self.pac,psicologo=self.doc,fecha=self.cita.fecha,hora=time(11))
        self.nota(otra);self.ipp(otra)
        with self.mocks():motor.correr(limite=1)
        otra.refresh_from_db();self.assertTrue(otra.reporte_enviado)

    def test_documento_privado_nuevo_exporta_autenticado(self):
        with patch('core.flujo_clinico._credenciales'),patch('google.auth.transport.requests.AuthorizedSession') as session:
            response=MagicMock(status_code=200,headers={'Content-Type':'text/plain'},text='Notas nuevas privadas')
            get=session.return_value.__enter__.return_value.get
            get.return_value=response
            self.assertEqual(motor.flujo._leer_documentos([{'fileUrl':'https://docs.google.com/document/d/nuevo/edit'}]),'Notas nuevas privadas')
            self.assertIn('/drive/v3/files/nuevo/export',get.call_args.args[0])

    def test_nota_modificada_bloquea_envio_de_version_anterior(self):
        nota=self.nota();self.ipp();motor.solicitar(self.cita,self.admin,'pdf_psicologo')
        with self.mocks():motor.correr()
        nota.notas_sesion='Contenido cambiado';nota.save()
        motor.solicitar(self.cita,self.admin)
        with patch('core.views._enviar_email_reporte_paciente_con_pdf') as enviar:motor.correr()
        enviar.assert_not_called()
        self.assertEqual(TrabajoProceso.objects.get(etapa='correo_paciente').error,'nota_modificada')

    def test_lote_100_solo_25_citas(self):
        for i in range(99):
            Cita.objects.create(paciente=self.pac,psicologo=self.doc,fecha=self.cita.fecha-timedelta(days=i+1),hora=time(10))
        motor.correr(limite=25)
        self.assertEqual(TrabajoProceso.objects.values('cita_id').distinct().count(),25)
        self.assertEqual(EjecucionProceso.objects.count(),25)

    def test_cien_citas_completas_en_cuatro_lotes_sin_duplicar(self):
        citas=[self.cita]
        for i in range(99):
            citas.append(Cita.objects.create(paciente=self.pac,psicologo=self.doc,
                fecha=self.cita.fecha-timedelta(days=i+1),hora=time(10)))
        for cita in citas:self.nota(cita);self.ipp(cita)
        with self.mocks(),patch('core.views._enviar_email_reporte_paciente_con_pdf') as pac,patch('core.views._enviar_email_reporte_psicologo_con_pdf') as psi:
            for _ in range(5):motor.correr(limite=25)
        self.assertEqual(pac.call_count,100);self.assertEqual(psi.call_count,100)
        self.assertEqual(Cita.objects.filter(reporte_enviado=True).count(),100)
        self.assertEqual(ReporteClinicoPDF.objects.count(),200)
        self.assertEqual(TrabajoProceso.objects.exclude(estado='completado').count(),0)

    def test_nota_manual_desbloquea_permiso_sin_volver_a_google(self):
        motor.registrar(self.cita)
        TrabajoProceso.objects.filter(etapa='meet').update(estado='bloqueado',error='google_acceso')
        self.nota();self.ipp()
        with self.mocks(),patch('core.flujo_clinico._leer_meet') as google:motor.correr()
        google.assert_not_called();self.cita.refresh_from_db();self.assertTrue(self.cita.reporte_enviado)

    def test_reconciliar_no_contacta_servicios(self):
        self.cita.reporte_enviado=True;self.cita.save()
        with patch('core.orquestador.ejecutar') as ejecutar:
            resultado=motor.reconciliar()
        ejecutar.assert_not_called();self.assertEqual(resultado['reconciliadas'],1)
        self.assertEqual(TrabajoProceso.objects.get(etapa='meet').error,'historico_ambiguo')

    def test_filtro_pendientes_no_incluye_bloqueados_por_etapas_futuras(self):
        motor.registrar(self.cita)
        TrabajoProceso.objects.filter(etapa='meet').update(estado='bloqueado',error='google_acceso')
        response=self.client.get(reverse('orquestacion_datos'),{'estado':'pendiente'})
        self.assertEqual(response.json()['total'],0)

    @override_settings(ORQUESTADOR_ACTIVO=False)
    def test_desactivado_no_escribe(self):
        self.assertFalse(motor.correr()['activo'])
        self.assertFalse(TrabajoProceso.objects.exists())


@skipUnless(connection.vendor == 'mysql', 'Requiere MySQL aislado; SQLite no verifica select_for_update.')
@override_settings(ORQUESTADOR_ACTIVO=True)
class ConcurrenciaOrquestadorTests(TransactionTestCase):
    def test_dos_workers_comparten_reserva_global(self):
        from threading import Thread, Event
        pac=User.objects.create_user('concurrencia')
        Cita.objects.create(paciente=pac,fecha=timezone.localdate()-timedelta(days=1),hora=time(10))
        iniciado, liberar = Event(), Event()
        resultados, errores = [], []

        def etapa(*args):
            iniciado.set()
            if not liberar.wait(10):raise RuntimeError('La prueba no liberó el worker.')
            return {'prueba':True}

        def worker():
            close_old_connections()
            try:resultados.append(motor.correr())
            except Exception as exc:errores.append(exc)
            finally:close_old_connections()

        with patch('core.orquestador.ejecutar',side_effect=etapa):
            hilo=Thread(target=worker)
            hilo.start()
            try:
                self.assertTrue(iniciado.wait(10))
                self.assertTrue(motor.correr()['ocupado'])
            finally:
                liberar.set();hilo.join(15)
        self.assertFalse(hilo.is_alive());self.assertFalse(errores)
        self.assertEqual(EjecucionProceso.objects.count(),len(motor.ETAPAS))
