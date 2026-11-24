import json
import tempfile
from datetime import datetime, time, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import fitz
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

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

    def test_agenda_muestra_citas_de_todo_el_dia_y_limita_las_acciones(self):
        ahora = timezone.make_aware(datetime(2026, 11, 8, 19))
        citas = [
            Cita.objects.create(
                paciente=self.paciente, psicologo=self.psicologo, fecha=ahora.date(),
                hora=hora, estado=estado, modalidad='Presencial',
            )
            for hora, estado in (
                (time(9), 'Confirmada'), (time(10), 'Completada'),
                (time(11), 'Pendiente'), (time(12), 'Cancelada'), (time(20), 'Confirmada'),
            )
        ]
        self.client.force_login(self.doctor)
        with patch('core.views.timezone.now', return_value=ahora):
            respuesta = self.client.get(reverse('panel_doctor'))
        self.assertEqual(respuesta.status_code, 200)
        visibles = respuesta.context['citas_hoy']
        self.assertEqual(
            [(cita.pk, cita.agenda_activa) for cita in visibles],
            [(cita.pk, activa) for cita, activa in zip(citas[:3] + citas[4:], (False, False, False, True))],
        )
        self.assertNotContains(respuesta, 'Día despejado')
        self.assertContains(respuesta, 'Contestar IPP', count=1)
        self.assertContains(respuesta, 'Iniciar grabación', count=1)

    def test_agenda_muestra_meet_en_citas_en_linea_con_enlace(self):
        ahora = timezone.make_aware(datetime(2026, 11, 8, 19))
        casos = (
            (time(9), 'Confirmada', 'En línea', 'abc-defg-hij'),
            (time(10), 'Completada', 'En línea', 'bcd-efgh-ijk'),
            (time(11), 'Pendiente', 'En línea', 'cde-fghi-jkl'),
            (time(12), 'Cancelada', 'En línea', 'def-ghij-klm'),
            (time(13), 'Confirmada', 'Presencial', 'efg-hijk-lmn'),
            (time(20), 'Confirmada', 'En línea', ''),
        )
        enlaces = []
        for hora, estado, modalidad, codigo in casos:
            enlace = f'https://meet.google.com/{codigo}' if codigo else ''
            Cita.objects.create(
                paciente=self.paciente, psicologo=self.psicologo, fecha=ahora.date(),
                hora=hora, estado=estado, modalidad=modalidad, enlace_meet=enlace,
            )
            enlaces.append(enlace)

        self.client.force_login(self.doctor)
        with patch('core.views.timezone.now', return_value=ahora):
            respuesta = self.client.get(reverse('panel_doctor'))

        self.assertContains(respuesta, 'Iniciar Meet', count=3)
        for enlace in enlaces[:3]:
            self.assertContains(respuesta, f'href="{enlace}"')

    def test_archivos_clinicos_en_volumen_con_lectura_privada_y_compatibilidad(self):
        from pathlib import Path
        from django.http import Http404
        from django.test import RequestFactory
        from hope_project.urls import servir_media

        self.client.force_login(self.doctor)
        url = reverse('guardar_historial')
        datos = {'paciente_id': self.paciente.pk, 'cita_id': self.cita.pk, 'notas_sesion': 'Nota inicial'}
        with override_settings(RAILWAY_VOLUME_MOUNT_PATH=None, RAILWAY_ENVIRONMENT_ID='prueba'):
            respuesta = self.client.post(url, {
                **datos, 'archivo_adjunto': SimpleUploadedFile('apuntes.txt', b'Apuntes ficticios'),
            })
        self.assertEqual(respuesta.json()['status'], 'error')
        self.cita.refresh_from_db()
        self.assertEqual(self.cita.estado, 'Confirmada')

        with tempfile.TemporaryDirectory() as volumen, override_settings(
            RAILWAY_VOLUME_MOUNT_PATH=volumen, RAILWAY_ENVIRONMENT_ID='prueba', MEDIA_ROOT=volumen,
        ):
            respuesta = self.client.post(url, {
                **datos, 'archivo_adjunto': SimpleUploadedFile('apuntes.txt', b'Apuntes ficticios'),
            })
            self.assertEqual(respuesta.json()['status'], 'success')
            historial = HistorialClinico.objects.get(cita=self.cita)
            historial.transcripcion_meet.save('transcripcion.txt', SimpleUploadedFile('texto.txt', b'Texto ficticio'))
            self.assertEqual(self.client.post(url, {
                **datos, 'historial_id': historial.pk, 'notas_sesion': 'Nota editada',
            }).json()['status'], 'success')

            # Nueva sesión y lectura de la fila desde la base de datos, sin depender de la instancia anterior.
            nueva_sesion = Client()
            nueva_sesion.force_login(self.doctor)
            historial.refresh_from_db()
            self.assertEqual(historial.notas_sesion, 'Nota editada')
            for campo, contenido in ((historial.archivo_adjunto, b'Apuntes ficticios'),
                                     (historial.transcripcion_meet, b'Texto ficticio')):
                self.assertEqual((Path(volumen) / 'notas_clinicas' / campo.name).read_bytes(), contenido)
                descarga = nueva_sesion.get(campo.url)
                self.assertEqual(descarga.status_code, 200)
                self.assertEqual(b''.join(descarga.streaming_content), contenido)
                with self.assertRaises(Http404):
                    servir_media(RequestFactory().get('/media/'), '/notas_clinicas/' + campo.name, volumen)
            nueva_sesion.force_login(self.otro)
            self.assertEqual(nueva_sesion.get(historial.archivo_adjunto.url).status_code, 404)

            legado = Path(volumen) / 'bitacoras_adjuntos' / 'anterior.txt'
            nueva_sesion.force_login(self.doctor)
            legado.parent.mkdir()
            legado.write_bytes(b'Adjunto anterior')
            historial.archivo_adjunto = 'bitacoras_adjuntos/anterior.txt'
            historial.save(update_fields=['archivo_adjunto'])
            self.assertEqual(b''.join(self.client.get(historial.archivo_adjunto.url).streaming_content), b'Adjunto anterior')

            consentimiento = Path(volumen) / 'consentimientos' / 'anterior.pdf'
            consentimiento.parent.mkdir()
            consentimiento.write_bytes(b'Consentimiento ficticio')
            respuesta = servir_media(RequestFactory().get('/media/'), 'consentimientos/anterior.pdf', volumen)
            self.assertEqual(b''.join(respuesta.streaming_content), b'Consentimiento ficticio')
            respuesta.close()

    def respuestas(self):
        return {**{p['id']: 'siempre' for p in FORMULARIO_ORGANICO_PREGUNTAS},
                'sat_1': '2', 'sat_2': '3', 'sat_3': '4', 'comentarios_satisfaccion': 'Comentario de prueba'}

    @patch.dict('os.environ', {'GROQ_API_KEY': 'prueba'})
    @patch('core.transcripcion_navegador._solicitar')
    def test_expediente_recupera_adjunto_sin_audio_y_genera_desde_texto(self, solicitar):
        from django.core.files.base import ContentFile
        historial = HistorialClinico.objects.create(
            cita=self.cita, paciente=self.paciente, psicologo=self.psicologo,
            notas_sesion='Bitácora revisada.',
        )
        historial.transcripcion_meet.save('sesion.txt', ContentFile('Texto clínico de esta cita.'.encode('utf-8-sig')))
        self.client.force_login(self.doctor)
        url = self.url('notas_texto_grabacion')
        respuesta = self.client.get(url + '?expediente=1')
        self.assertEqual(respuesta.status_code, 200)
        datos = respuesta.json()
        self.assertEqual(datos['transcripcion'], 'Texto clínico de esta cita.')
        self.assertEqual(datos['fuente'], 'bitacora')
        self.assertFalse(GrabacionSesion.objects.filter(cita=self.cita).exists())
        expediente = self.client.get(reverse('detalle_paciente', args=[self.paciente.pk]))
        self.assertContains(expediente, 'Ver transcripción')
        self.assertNotContains(expediente, 'Generar Notas Automáticas')
        cuerpo = {'accion': 'guardar', 'transcripcion': datos['transcripcion'],
                  'version_audio': datos['version_audio'], 'revision': datos['revision']}
        guardado = self.client.post(url, cuerpo, content_type='application/json')
        self.assertEqual(guardado.status_code, 200, guardado.content)
        solicitar.return_value = json.dumps({'notas_sesion': 'Borrador del texto revisado.'})
        generado = self.client.post(url, {
            'accion': 'generar', 'version_audio': datos['version_audio'],
            'revision': guardado.json()['revision'],
        }, content_type='application/json')
        self.assertEqual(generado.status_code, 200, generado.content)
        historial.refresh_from_db()
        self.assertEqual(historial.notas_sesion, 'Bitácora revisada.')
        self.assertEqual(self.client.get(url + '?expediente=1').json()['notas']['notas_sesion'],
                         'Borrador del texto revisado.')
        self.assertEqual(self.subir().status_code, 200)
        self.assertTrue(self.client.get(url + '?expediente=1').json()['anterior'])

    def test_expediente_aisla_sesiones_y_rechaza_edicion_desactualizada(self):
        self.client.force_login(self.doctor)
        self.subir()
        grabacion = GrabacionSesion.objects.get(cita=self.cita)
        grabacion.asociar_transcripcion('Texto de la primera cita.')
        grabacion.save()
        segunda = Cita.objects.create(paciente=self.paciente, psicologo=self.psicologo,
            fecha=self.cita.fecha, hora=time(23), modalidad='Presencial')
        GrabacionSesion.objects.create(cita=segunda, transcripcion='Texto de la segunda cita.')
        url = self.url('notas_texto_grabacion')
        datos = self.client.get(url + '?expediente=1').json()
        self.assertEqual(datos['transcripcion'], 'Texto de la primera cita.')
        otro = self.client.get(reverse('notas_texto_grabacion', args=[segunda.pk]) + '?expediente=1').json()
        self.assertEqual(otro['transcripcion'], 'Texto de la segunda cita.')
        grabacion.transcripcion = 'Corrección más reciente.'
        grabacion.save()
        respuesta = self.client.post(url, {'accion': 'guardar', 'version_audio': datos['version_audio'],
            'revision': datos['revision'], 'transcripcion': 'Edición antigua.'}, content_type='application/json')
        self.assertEqual(respuesta.status_code, 409)
        grabacion.refresh_from_db()
        self.assertEqual(grabacion.transcripcion, 'Corrección más reciente.')
        self.client.force_login(self.otro)
        self.assertEqual(self.client.get(url + '?expediente=1').status_code, 404)

    def test_adjunto_inaccesible_informa_error_y_no_ausencia_de_texto(self):
        HistorialClinico.objects.create(cita=self.cita, paciente=self.paciente,
            psicologo=self.psicologo, notas_sesion='Nota', transcripcion_meet='archivo-inexistente.txt')
        self.client.force_login(self.doctor)
        respuesta = self.client.get(self.url('notas_texto_grabacion') + '?expediente=1')
        self.assertEqual(respuesta.status_code, 422)
        self.assertIn('transcripción vinculada', respuesta.json()['mensaje'])

    def subir(self, **campos):
        return self.client.post(self.url('api_guardar_grabacion_sesion'), {
            'audio': SimpleUploadedFile('prueba.wav', b'archivo sintetico', content_type='audio/wav'),
            **campos,
        })

    @patch('core.transcripcion_navegador._solicitar')
    def test_transcripcion_se_guarda_sin_ia_y_aparece_en_una_nueva_sesion(self, solicitar):
        self.doctor.first_name = 'Profesional de prueba'
        self.doctor.save(update_fields=['first_name'])
        self.client.force_login(self.doctor)
        audio = self.subir(texto_navegador='1')
        self.assertEqual(audio.status_code, 200, audio.content)
        version = audio.json()['version_audio']
        url = self.url('notas_texto_grabacion')
        datos = {'accion': 'guardar', 'version_audio': version,
                 'transcripcion': 'Primera línea.\nSegunda línea con <texto>.'}

        def guardar(cliente, **cambios):
            return cliente.post(url, {**datos, **cambios}, content_type='application/json')

        self.assertEqual(guardar(self.client, transcripcion='').status_code, 400)
        self.assertEqual(guardar(self.client, transcripcion='á' * 500001).status_code, 400)
        self.assertIn('no-store', guardar(self.client)['Cache-Control'])
        grabacion = GrabacionSesion.objects.get(cita=self.cita)
        self.assertEqual(grabacion.transcripcion, datos['transcripcion'])
        self.assertEqual(grabacion.transcripcion_audio, version)
        self.assertIsNotNone(grabacion.transcripcion_fecha)
        fecha = grabacion.transcripcion_fecha
        self.assertEqual(guardar(self.client, transcripcion='Texto corregido').status_code, 200)
        grabacion.refresh_from_db()
        self.assertEqual((grabacion.transcripcion, grabacion.transcripcion_fecha), ('Texto corregido', fecha))
        self.assertEqual(guardar(self.client).status_code, 200)
        self.assertFalse(HistorialClinico.objects.filter(cita=self.cita).exists())

        nueva_sesion = Client()
        nueva_sesion.force_login(self.doctor)
        expediente = reverse('detalle_paciente', args=[self.paciente.pk])
        respuesta = nueva_sesion.get(expediente)
        for texto in ('Ver transcripción', 'Profesional de prueba',
                      self.url('audio_grabacion_sesion'), self.url('descargar_transcripcion_presencial')):
            self.assertContains(respuesta, texto)
        self.assertEqual(nueva_sesion.get(url + '?expediente=1').json()['transcripcion'], datos['transcripcion'])

        self.assertEqual(self.subir().status_code, 200)
        self.assertEqual(guardar(self.client, transcripcion='Texto retrasado').status_code, 409)
        self.assertTrue(nueva_sesion.get(url + '?expediente=1').json()['anterior'])
        nueva_sesion.force_login(self.otro)
        self.assertEqual(guardar(nueva_sesion).status_code, 404)
        self.assertEqual(nueva_sesion.get(self.url('descargar_transcripcion_presencial')).status_code, 404)
        solicitar.assert_not_called()

    @patch.dict('os.environ', {'GROQ_API_KEY': 'prueba'})
    @patch('core.transcripcion_navegador._solicitar')
    def test_texto_navegador_no_requiere_worker_y_preserva_nota_clinica(self, solicitar):
        from .models import AnalisisGrabacionClinica
        self.client.force_login(self.doctor)
        audio = self.client.post(self.url('api_guardar_grabacion_sesion'), {
            'audio': SimpleUploadedFile('prueba.wav', b'archivo sintetico', content_type='audio/wav'),
            'texto_navegador': '1',
        })
        self.assertEqual(audio.status_code, 200)
        grabacion = GrabacionSesion.objects.get(cita=self.cita)
        self.assertEqual(grabacion.estado, 'archivada')
        historial = HistorialClinico.objects.create(cita=self.cita, paciente=self.paciente,
            psicologo=self.psicologo, notas_sesion='Nota manual intacta')
        datos = {'accion': 'fragmento', 'texto': 'Texto ficticio', 'version_audio': audio.json()['version_audio']}
        url = self.url('notas_texto_grabacion')
        solicitar.return_value = 'Evidencia ficticia'
        self.assertEqual(self.client.post(url, datos, content_type='application/json').json()['evidencia'], 'Evidencia ficticia')
        datos.update(accion='finalizar', transcripcion='Transcripción ficticia completa')
        solicitar.return_value = json.dumps({'notas_sesion': 'Borrador revisable'})
        respuesta = self.client.post(url, datos, content_type='application/json')
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        self.assertIn('no-store', respuesta['Cache-Control'])
        grabacion.refresh_from_db()
        historial.refresh_from_db()
        self.assertEqual(grabacion.transcripcion, datos['transcripcion'])
        self.assertEqual(grabacion.estado, 'completada')
        self.assertEqual(historial.notas_sesion, 'Nota manual intacta')
        self.assertEqual(AnalisisGrabacionClinica.objects.get(grabacion=grabacion).estado, 'listo')
        solicitar.reset_mock()
        self.client.force_login(self.otro)
        self.assertEqual(self.client.post(url, datos, content_type='application/json').status_code, 404)
        self.client.force_login(self.doctor)
        self.assertEqual(self.client.post(url, {**datos, 'version_audio': 'vieja'}, content_type='application/json').status_code, 409)
        self.assertEqual(self.client.post(url, {**datos, 'texto': 'x' * 10001}, content_type='application/json').status_code, 400)
        seguro = Client(enforce_csrf_checks=True)
        seguro.force_login(self.doctor)
        self.assertEqual(seguro.post(url, datos, content_type='application/json').status_code, 403)
        solicitar.assert_not_called()

    @patch.dict('os.environ', {'GROQ_API_KEY': 'prueba'})
    @patch('core.transcripcion_navegador._solicitar')
    def test_reabrir_texto_reintentar_y_archivar_borrador_privado(self, solicitar):
        self.client.force_login(self.doctor)
        version = self.subir(texto_navegador='1').json()['version_audio']
        url = self.url('notas_texto_grabacion')
        transcripcion = (
            'Al inicio: me siento tranquila.\n'
            + '\n'.join(f'Conversación de prueba {i} con contexto neutro.' for i in range(400))
            + '\nAprendí una técnica.\nPara la próxima sesión acordamos practicar.'
        )
        self.assertEqual(self.client.post(url, {
            'accion': 'guardar', 'version_audio': version, 'transcripcion': transcripcion,
        }, content_type='application/json').status_code, 200)
        self.client.logout()
        self.client.force_login(self.doctor)
        recuperada = self.client.get(url)
        self.assertEqual(recuperada.json(), {'transcripcion': transcripcion, 'version_audio': version})
        self.assertIn('no-store', recuperada['Cache-Control'])

        def generar():
            return self.client.post(url, {'accion': 'generar', 'version_audio': version},
                                    content_type='application/json')

        solicitar.side_effect = ValueError('Respuesta incompleta del modelo')
        self.assertEqual(generar().status_code, 502)
        self.assertEqual(self.client.get(url).json()['transcripcion'], transcripcion)
        self.assertFalse(HistorialClinico.objects.filter(cita=self.cita).exists())
        solicitar.reset_mock()
        solicitar.side_effect = [ValueError(), json.dumps({'notas_sesion': 'Borrador para revisar'})]
        respuesta = generar()
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        self.assertEqual(solicitar.call_count, 2)
        evidencia = solicitar.call_args.args[2]
        self.assertLessEqual(len(evidencia), 9000)
        self.assertIn('Al inicio', evidencia)
        self.assertIn('acordamos practicar', evidencia)
        historial = HistorialClinico.objects.get(cita=self.cita)
        self.assertTrue(historial.pendiente_revision_audio)
        self.assertEqual(historial.notas_sesion, 'Borrador para revisar')
        self.cita.refresh_from_db()
        self.assertEqual(self.cita.estado, 'Confirmada')
        archivo = historial.transcripcion_meet.url
        descarga = self.client.get(archivo)
        self.assertEqual(descarga.status_code, 200)
        self.assertEqual(b''.join(descarga.streaming_content).decode('utf-8-sig'), transcripcion)
        self.client.force_login(self.otro)
        self.assertEqual(self.client.get(archivo).status_code, 404)
        self.assertEqual(self.client.get(url).status_code, 404)

    @patch.dict('os.environ', {'GROQ_API_KEY': 'prueba'})
    @patch('groq.Groq')
    def test_fragmento_remoto_autorizacion_y_error(self, groq):
        self.client.force_login(self.doctor)
        url = self.url('transcribir_fragmento')
        groq.return_value.audio.transcriptions.create.return_value = SimpleNamespace(text='Texto de prueba')
        def enviar():
            return self.client.post(url, {'audio': SimpleUploadedFile('audio.webm', b'fake', content_type='audio/webm')})
        self.assertEqual(enviar().json()['texto'], 'Texto de prueba')
        groq.return_value.audio.transcriptions.create.side_effect = RuntimeError('fallo simulado')
        self.assertEqual(enviar().status_code, 502)
        self.client.force_login(self.otro)
        self.assertEqual(enviar().status_code, 404)

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
        texto = 'La paciente llegó tranquila.'
        archivo = SimpleUploadedFile('transcripcion.TXT', texto.encode('utf-8-sig'), content_type='text/plain')
        self.assertEqual(self.client.post(url, {'archivo': archivo}).json()['data'], campos)
        self.assertEqual(groq.return_value.chat.completions.create.call_args.kwargs['messages'][-1]['content'], texto)
        archivo = SimpleUploadedFile('invalido.txt', bytes([255]), content_type='text/plain')
        self.assertEqual(self.client.post(url, {'archivo': archivo}).status_code, 422)
        self.assertFalse(HistorialClinico.objects.exists())
        mensaje.content = 'Respuesta sin estructura'
        self.assertEqual(escaneo().status_code, 502)

    @patch('groq.Groq')
    @patch.object(fitz.Page, 'get_textpage_ocr', side_effect=RuntimeError)
    def test_pdf_vacio_no_inventa_texto_si_no_hay_ocr(self, ocr, groq):
        self.client.force_login(self.doctor)
        with fitz.open() as pdf:
            pdf.new_page()
            archivo = SimpleUploadedFile('escaneo.pdf', pdf.tobytes())
        respuesta = self.client.post(reverse('procesar_archivo_ia'), {'archivo': archivo})
        self.assertEqual(respuesta.status_code, 422)
        self.assertIn('vacío', respuesta.json()['error'])
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

    @patch('core.views._notificar_cita_paciente')
    def test_recepcion_confirma_efectivo_y_clip_sin_pasarela_y_sin_duplicados(self, notificar):
        from .models import PagoCitaPayPal

        self.doctor.modalidad_atencion = 'Mixto'
        self.doctor.save(update_fields=['modalidad_atencion'])
        admin = User.objects.create_superuser('recepcion')
        self.client.force_login(admin)
        url = reverse('recepcion_confirmar_cita')
        datos = {**self.datos, 'paciente_id': self.paciente.pk, 'animo': 'Normal',
                 'modalidad': 'Presencial', 'es_tectum': '0'}
        for hora, metodo in (('10:00', 'efectivo'), ('11:00', 'clip_presencial')):
            cuerpo = {**datos, 'hora': hora, 'metodo_pago_presencial': metodo}
            with self.captureOnCommitCallbacks(execute=True) as correos:
                primera = self.client.post(url, cuerpo)
                segunda = self.client.post(url, cuerpo)
            self.assertEqual(primera.status_code, 200, primera.content)
            self.assertEqual(primera.json(), segunda.json())
            cita = Cita.objects.get(pk=primera.json()['cita_id'])
            self.assertEqual((cita.estado, cita.metodo_pago_presencial), ('Confirmada', metodo))
            self.assertTrue(cita.codigo_pase)
            self.assertEqual(len(correos), 1)
        self.assertEqual(notificar.call_count, 2)
        self.assertFalse(PagoCitaPayPal.objects.exists())
        self.assertEqual(self.client.post(url, {**datos, 'metodo_pago_presencial': 'clip_presencial'}).status_code, 409)

    def test_recepcion_valida_metodo_permiso_y_reserva_activa(self):
        from .models import PagoCitaPayPal

        url = reverse('recepcion_confirmar_cita')
        datos = {**self.datos, 'paciente_id': self.paciente.pk, 'animo': 'Normal',
                 'modalidad': 'Presencial', 'es_tectum': '0', 'metodo_pago_presencial': 'efectivo'}
        self.assertEqual(self.client.post(url, datos).status_code, 302)
        admin = User.objects.create_superuser('recepcion')
        self.client.force_login(admin)
        con_csrf = Client(enforce_csrf_checks=True)
        con_csrf.force_login(admin)
        self.assertEqual(con_csrf.post(url, datos).status_code, 403)
        for cambio in ({'modalidad': 'Inválida'}, {'es_tectum': '1'}, {'metodo_pago_presencial': 'otro'}):
            self.assertEqual(self.client.post(url, {**datos, **cambio}).status_code, 400)
        cita = Cita.objects.create(
            paciente=self.paciente, psicologo=self.doctor, fecha=self.fecha, hora='10:00',
            modalidad='Presencial', estado='Pendiente',
        )
        pago = PagoCitaPayPal.objects.create(cita=cita, monto=110, vence=timezone.now() + timedelta(minutes=10))
        self.assertEqual(self.client.post(url, datos).status_code, 409)
        cita.refresh_from_db()
        self.assertEqual((cita.estado, cita.metodo_pago_presencial), ('Pendiente', 'sitio_web'))
        self.assertTrue(PagoCitaPayPal.objects.filter(pk=pago.pk).exists())

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
    def test_inhouse_activa_cobertura_por_padron_sin_verificacion_y_limita_servicio(self):
        sesion = self.client.session
        sesion['convenio_tectum'] = True
        sesion.save()
        url = reverse('guardar_cita')
        self.assertEqual(self.paciente.perfil.correo_tectum_verificado, '')
        self.assertEqual(self.client.post(url, {**self.datos, 'tipo_servicio': 'pareja'}).status_code, 400)
        respuesta = self.client.post(url, self.datos)
        self.assertEqual(respuesta.json().get('status'), 'success', respuesta.content)
        self.assertTrue(Cita.objects.get(estado='Confirmada').es_tectum)

    def test_norma_recibe_cobertura_con_correo_en_mayusculas_sin_verificar(self):
        from .views import _convenio_cita
        from django.test import RequestFactory

        self.paciente.email = 'Norma.tovar@gmail.com'
        self.paciente.save(update_fields=['email'])
        solicitud = RequestFactory().get('/panel-tectum/')
        solicitud.user, solicitud.session = self.paciente, {}
        self.assertEqual(_convenio_cita(solicitud), 'inhouse')
        self.assertEqual(self.paciente.perfil.correo_tectum_verificado, '')

        solicitud.path = '/panel-beneficios/'
        self.assertEqual(_convenio_cita(solicitud), 'beneficios')

    def test_fuera_del_padron_no_entra_ni_agenda_con_cobertura(self):
        self.assertEqual(self.client.get(reverse('panel_tectum')).status_code, 403)
        sesion = self.client.session
        sesion['convenio_tectum'] = True
        sesion.save()
        respuesta = self.client.post(reverse('guardar_cita'), self.datos)
        self.assertEqual(respuesta.status_code, 400)
        self.assertFalse(Cita.objects.filter(estado='Confirmada').exists())

    @patch('core.views.COLABORADORES_TECTUM', {'pago@example.test'})
    def test_colaborador_en_beneficios_sigue_requiriendo_pago(self):
        sesion = self.client.session
        sesion['convenio_tectum_beneficios'] = True
        sesion.save()
        respuesta = self.client.post(reverse('guardar_cita'), self.datos)
        self.assertEqual(respuesta.status_code, 400)
        self.assertFalse(Cita.objects.filter(estado='Confirmada').exists())

    @patch('core.views.send_mail')
    def test_login_norma_entra_directo_sin_verificacion(self, enviar):
        self.paciente.email = 'Norma.tovar@gmail.com'
        self.paciente.set_password('clave-prueba')
        self.paciente.save()
        self.client.logout()
        sesion = self.client.session
        sesion['verificacion_tectum_pendiente'] = 'enlace-antiguo'
        sesion.save()
        respuesta = self.client.post(reverse('login_usuario'), {
            'email': 'norma.tovar@gmail.com', 'password': 'clave-prueba', 'convenio': 'tectum',
        })
        self.assertEqual(respuesta.json()['redirect_url'], reverse('panel_tectum'))
        self.assertNotIn('verificacion_tectum_pendiente', self.client.session)
        self.paciente.perfil.refresh_from_db()
        self.assertTrue(self.paciente.perfil.es_tectum)
        self.assertEqual(self.paciente.perfil.correo_tectum_verificado, '')
        enviar.assert_not_called()

    @patch('core.views.send_mail')
    @patch('core.views.COLABORADORES_TECTUM', {'pago@example.test'})
    def test_enlaces_antiguos_no_exigen_verificar_ni_envian_correo(self, enviar):
        respuesta = self.client.post(reverse('enviar_verificacion_tectum'))
        self.assertEqual(respuesta.json()['status'], 'success')
        respuesta = self.client.get(reverse('verificar_correo_tectum', args=['enlace-antiguo']))
        self.assertRedirects(respuesta, reverse('panel_tectum'), fetch_redirect_response=False)
        enviar.assert_not_called()

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
