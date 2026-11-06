from datetime import timedelta, time
from unittest.mock import patch, MagicMock

from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.test import TestCase, Client, override_settings
from django.urls import reverse
from django.utils import timezone

from .flujo_clinico import _base, evaluar, _leer_documentos, PasoBloqueado
from .models import (Cita, PerfilPsicologo, UsuarioPerfil, HistorialClinico,
                     RespuestaFormularioOrganica, EstadoFlujoSesion,
                     ReporteClinicoPDF, LogAutomatizacionNotas)


@override_settings(STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.InMemoryStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
})
class FlujoClinicoTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user('admin', is_superuser=True)
        self.paciente = User.objects.create_user('paciente', first_name='Paciente ficticio', email='paciente@example.test')
        UsuarioPerfil.objects.create(usuario=self.paciente, nombre='Paciente ficticio')
        doctor = User.objects.create_user('doctor', email='doctor@example.test')
        self.doctor = PerfilPsicologo.objects.create(usuario=doctor, cedula_profesional='TEST')
        self.cita = Cita.objects.create(paciente=self.paciente, psicologo=self.doctor,
                                       fecha=timezone.localdate()-timedelta(days=3), hora=time(10),
                                       id_evento_google='evento-ficticio')
        self.client.force_login(self.admin)

    def nota(self, cita=None, **kwargs):
        return HistorialClinico.objects.create(cita=cita or self.cita, paciente=self.paciente,
                                               psicologo=self.doctor, notas_sesion='Nota original del psicólogo', **kwargs)

    def ipp(self, cita=None):
        return RespuestaFormularioOrganica.objects.create(cita=cita or self.cita, paciente=self.paciente, respuestas={'1': 4})

    def paso(self, tipo, cita=None):
        return self.client.post(reverse('api_reintento_automatizacion', args=[(cita or self.cita).pk]), {'tipo': tipo})

    def pdf(self, destinatario, enviado=False, cita=None):
        row = ReporteClinicoPDF(cita=cita or self.cita, paciente=self.paciente,
                                psicologo=self.doctor, tipo_destinatario=destinatario,
                                correo_enviado=enviado)
        row.archivo_pdf.save(destinatario+'.pdf', ContentFile(b'%PDF-ficticio'))
        return row

    def test_panel_solo_lectura_y_permiso(self):
        response = self.client.get(reverse('panel_admin_automatizacion_notas'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Un recorrido, siete etapas')
        self.assertEqual(EstadoFlujoSesion.objects.count(), 0)
        self.client.force_login(self.paciente)
        self.assertEqual(self.paso('meet').status_code, 403)
        self.assertEqual(self.client.get(reverse('panel_admin_automatizacion_notas')).status_code, 403)

    def test_csrf_y_metodo(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.admin)
        url = reverse('api_reintento_automatizacion', args=[self.cita.pk])
        self.assertEqual(client.post(url, {'tipo':'meet'}).status_code, 403)
        self.assertEqual(self.client.get(url).status_code, 405)

    def test_nota_existente_evade_google_y_preserva_campos(self):
        nota = self.nota(como_llega='Texto del terapeuta')
        with patch('core.flujo_clinico._leer_meet') as google, patch('core.views._generar_bitacora_con_ia_de_texto') as groq:
            for tipo in ('meet','lectura','ia','escritura'):
                self.assertEqual(self.paso(tipo).json()['status'], 'success')
            google.assert_not_called()
            groq.assert_not_called()
        nota.refresh_from_db()
        self.assertEqual(nota.notas_sesion, 'Nota original del psicólogo')
        self.assertEqual(nota.como_llega, 'Texto del terapeuta')
        self.assertEqual(EstadoFlujoSesion.objects.get(cita=self.cita).fuente, 'nota_existente')

    def test_etapas_independientes_y_escritura_conservadora(self):
        with patch('core.flujo_clinico._leer_meet', return_value=[{'fileUrl':'https://docs.google.com/document/d/abc/edit','title':'Notas'}]):
            self.assertEqual(self.paso('meet').json()['status'], 'success')
        with patch('core.flujo_clinico._leer_documentos', return_value='Transcripción ficticia'):
            self.assertEqual(self.paso('lectura').json()['status'], 'success')
        self.assertFalse(HistorialClinico.objects.exists())
        datos = {c:'Contenido extraído' for c in ('como_llega','notas_sesion','aprendizaje_paciente','como_se_va','recomendaciones')}
        with patch('core.views._generar_bitacora_con_ia_de_texto', return_value=datos):
            self.assertEqual(self.paso('ia').json()['status'], 'success')
        self.assertFalse(HistorialClinico.objects.exists())
        HistorialClinico.objects.create(cita=self.cita,paciente=self.paciente,psicologo=self.doctor,notas_sesion='',como_llega='No sobrescribir')
        self.assertEqual(self.paso('escritura').json()['status'], 'success')
        nota = HistorialClinico.objects.get(cita=self.cita)
        self.assertEqual(nota.como_llega,'No sobrescribir')
        self.assertEqual(nota.notas_sesion,'Contenido extraído')

    def test_ipp_de_otra_cita_no_desbloquea_reporte(self):
        self.nota()
        otra = Cita.objects.create(paciente=self.paciente,psicologo=self.doctor,fecha=self.cita.fecha,hora=time(11))
        self.ipp(otra)
        with patch('core.views._procesar_reporte_de_una_cita_detallado') as generar:
            result=self.paso('reporte').json()
            self.assertEqual(result['status'],'error')
            generar.assert_not_called()

    def test_reporte_independiente_con_cita_anterior_pendiente(self):
        self.nota()
        self.ipp()
        anterior = Cita.objects.create(
            paciente=self.paciente, psicologo=self.doctor,
            fecha=self.cita.fecha - timedelta(days=10), hora=time(10),
        )
        LogAutomatizacionNotas.objects.create(
            cita=self.cita, etapa='reporte', estado='conflicto',
            detalle=f'Primero complete la cita #{anterior.pk}.',
        )
        self.assertEqual(evaluar(_base().get(pk=self.cita.pk))['pasos'][-2]['estado'], 'pendiente')
        with patch('core.views._procesar_reporte_de_una_cita_detallado',return_value=(True,'Generados',{})) as generar:
            self.assertEqual(self.paso('reporte').json()['status'],'success')
            generar.assert_called_once_with(self.cita.pk,forzar=True,solo_generar=True)

    def test_correo_solo_pendiente_sin_regenerar(self):
        self.nota();self.ipp();self.pdf('paciente',True);self.pdf('psicologo')
        with patch('core.views._enviar_email_reporte_paciente_con_pdf') as pac, patch('core.views._enviar_email_reporte_psicologo_con_pdf') as psi, patch('core.views._procesar_reporte_de_una_cita_detallado') as gen:
            self.assertEqual(self.paso('correo').json()['status'],'success')
            self.assertEqual(self.paso('correo').json()['status'],'success')
            pac.assert_not_called();psi.assert_called_once();gen.assert_not_called()

    def test_fallo_parcial_persiste_exito_y_no_duplica_reintento(self):
        self.nota();self.ipp();self.pdf('paciente');self.pdf('psicologo')
        with patch('core.views._enviar_email_reporte_paciente_con_pdf') as pac, patch('core.views._enviar_email_reporte_psicologo_con_pdf',side_effect=RuntimeError('smtp')):
            self.assertEqual(self.paso('correo').json()['status'],'error')
            pac.assert_called_once()
        self.assertTrue(ReporteClinicoPDF.objects.get(tipo_destinatario='paciente').correo_enviado)
        with patch('core.views._enviar_email_reporte_paciente_con_pdf') as pac, patch('core.views._enviar_email_reporte_psicologo_con_pdf'):
            self.assertEqual(self.paso('correo').json()['status'],'success')
            pac.assert_not_called()

    def test_flag_reporte_enviado_no_equivale_a_correo(self):
        self.cita.reporte_enviado=True;self.cita.save()
        fila=evaluar(_base().get(pk=self.cita.pk))
        self.assertNotEqual(fila['estado'],'exitoso')

    def test_archivo_perdido_no_envia_ni_regenera(self):
        self.nota();self.ipp();row=self.pdf('paciente');self.pdf('psicologo')
        row.archivo_pdf.delete(save=False)
        with patch('core.views._procesar_reporte_de_una_cita_detallado') as gen,patch('core.views._enviar_email_reporte_paciente_con_pdf') as pac:
            self.assertEqual(self.paso('correo').json()['status'],'error')
            gen.assert_not_called();pac.assert_not_called()

    def test_lote_sin_truncado_con_orden_y_filtros(self):
        otra=Cita.objects.create(paciente=self.paciente,psicologo=self.doctor,fecha=self.cita.fecha-timedelta(days=1),hora=time(10),es_beneficios=True)
        url=reverse('api_lote_automatizacion')
        self.assertEqual([x['id'] for x in self.client.post(url,{'limite':'todos'}).json()['items']],[otra.pk,self.cita.pk])
        self.assertEqual([x['id'] for x in self.client.post(url,{'limite':'1','org':'hope'}).json()['items']],[self.cita.pk])
        LogAutomatizacionNotas.objects.create(cita=self.cita,etapa='lectura',estado='sin_permisos')
        self.assertEqual([x['id'] for x in self.client.post(url,{'limite':'todos','fallidos':'1'}).json()['items']],[self.cita.pk])
        self.assertEqual(self.client.post(url,{'desde':'invalida'}).status_code,400)

    def test_privado_no_se_confunde_con_notas(self):
        with patch('core.flujo_clinico._credenciales'), patch('google.auth.transport.requests.AuthorizedSession') as session:
            response=MagicMock(status_code=403)
            session.return_value.__enter__.return_value.get.return_value=response
            with self.assertRaises(PasoBloqueado) as error:
                _leer_documentos([{'fileUrl':'https://docs.google.com/document/d/abc/edit'}])
            self.assertEqual(error.exception.estado,'sin_permisos')

    def test_sesion_futura_y_nota_pendiente_revision_no_ejecutan(self):
        self.cita.fecha=timezone.localdate()+timedelta(days=1);self.cita.save()
        with patch('core.flujo_clinico._leer_meet') as google:
            self.assertEqual(self.paso('meet').json()['status'],'error');google.assert_not_called()
        self.cita.fecha=timezone.localdate()-timedelta(days=1);self.cita.save()
        self.nota(pendiente_revision_audio=True)
        self.assertEqual(self.paso('escritura').json()['estado'],'conflicto')

    def test_error_reciente_no_se_oculta_por_notas_anteriores(self):
        EstadoFlujoSesion.objects.create(cita=self.cita,texto_notas='Notas anteriores',datos_clinicos={'notas_sesion':'Borrador'})
        LogAutomatizacionNotas.objects.create(cita=self.cita,etapa='lectura',estado='sin_permisos')
        fila=evaluar(_base().get(pk=self.cita.pk))
        self.assertEqual(fila['pasos'][1]['estado'],'sin_permisos')

    def test_nueva_extraccion_invalida_ia_anterior(self):
        EstadoFlujoSesion.objects.create(cita=self.cita,texto_notas='Nuevas notas')
        LogAutomatizacionNotas.objects.create(cita=self.cita,etapa='ia',estado='exitoso')
        fila=evaluar(_base().get(pk=self.cita.pk))
        self.assertEqual(fila['pasos'][2]['estado'],'pendiente')

    def test_generacion_real_pipeline_no_envia_correo(self):
        self.nota();self.ipp()
        with patch('core.views._construir_datos_checkin_paciente',return_value={'sesion_numero':1}), patch('core.views._construir_datos_checkin_psicologo',return_value={'sesion_numero':1}), patch('core.views._obtener_logo_base64',return_value=''), patch('core.views.convertir_html_a_pdf',return_value=b'%PDF-ficticio'), patch('core.views.render_to_string',return_value='<html>Reporte ficticio</html>'), patch('core.views._enviar_email_reporte_paciente_con_pdf') as pac, patch('core.views._enviar_email_reporte_psicologo_con_pdf') as psi:
            result=self.paso('reporte').json()
            self.assertEqual(result['status'],'success',result['mensaje'])
            self.assertEqual(ReporteClinicoPDF.objects.count(),2)
            pac.assert_not_called();psi.assert_not_called()
        self.cita.refresh_from_db()
        self.assertTrue(self.cita.reporte_enviado)  # Se respeta la semántica legacy.
        self.assertEqual(evaluar(_base().get(pk=self.cita.pk))['pasos'][-1]['estado'],'pendiente')

    def test_html_de_login_no_se_acepta_como_texto(self):
        with patch('core.flujo_clinico._credenciales'),patch('google.auth.transport.requests.AuthorizedSession') as session:
            response=MagicMock(status_code=200,headers={'Content-Type':'text/html'},text='<html>Inicie sesión</html>')
            session.return_value.__enter__.return_value.get.return_value=response
            with self.assertRaises(PasoBloqueado):
                _leer_documentos([{'fileUrl':'https://docs.google.com/document/d/abc/edit'}])
