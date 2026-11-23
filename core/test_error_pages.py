"""Regresión de páginas públicas de error; ejecutar con settings_flujo_test."""

import tempfile
from datetime import date, time
from pathlib import Path
from types import ModuleType

from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied, SuspiciousOperation
from django.http import HttpResponse, JsonResponse
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import path, resolve
from django.urls.resolvers import URLPattern

from hope_project import error_views
from hope_project import urls as project_urls

from .models import Cita


def solicitud_invalida(request):
    raise SuspiciousOperation('DETALLE_INTERNO_PRIVADO')


def acceso_denegado(request):
    raise PermissionDenied('DETALLE_INTERNO_PRIVADO')


def fallo_interno(request):
    raise RuntimeError('DETALLE_INTERNO_PRIVADO')


def formulario(request):
    return HttpResponse('OK')


def error_json(request):
    return JsonResponse({'status': 'error', 'message': 'Elige otro horario.'}, status=409)


def contexto_no_disponible(request):
    raise AssertionError('Las pantallas de error no deben ejecutar procesadores de contexto.')


urlconf = ModuleType('errores_test_urls')
urlconf.urlpatterns = [
    path('solicitud/', solicitud_invalida),
    path('prohibido/', acceso_denegado),
    path('fallo/', fallo_interno),
    path('formulario/', formulario),
    path('api/', error_json),
]
for codigo in (400, 403, 404, 500):
    setattr(urlconf, f'handler{codigo}', getattr(error_views, f'error_{codigo}'))


@override_settings(DEBUG=False, ROOT_URLCONF=urlconf,
                   CSRF_FAILURE_VIEW='hope_project.error_views.error_csrf')
class PantallasErrorTests(SimpleTestCase):
    def test_errores_conservan_codigo_y_ocultan_detalles(self):
        cliente = Client(raise_request_exception=False)
        for ruta, codigo, titulo in (
            ('/solicitud/', 400, 'No pudimos procesar esta solicitud'),
            ('/prohibido/', 403, 'No tienes acceso a esta página'),
            ('/no-existe/?token=SECRETO_QUERY', 404, 'No encontramos esta página'),
            ('/fallo/', 500, 'Algo salió mal'),
        ):
            with self.subTest(codigo=codigo):
                respuesta = cliente.get(ruta)
                self.assertContains(respuesta, titulo, status_code=codigo)
                self.assertContains(respuesta, 'href="/"', status_code=codigo)
                self.assertTemplateUsed(respuesta, 'errors/error.html')
                self.assertIn('no-store', respuesta['Cache-Control'])
                self.assertEqual(respuesta['X-Robots-Tag'], 'noindex, nofollow')
                for detalle in ('DETALLE_INTERNO_PRIVADO', 'SECRETO_QUERY', 'Traceback',
                                'urlpatterns', 'DATABASES', 'DEBUG =', 'core.views'):
                    self.assertNotContains(respuesta, detalle, status_code=codigo)

    def test_csrf_muestra_pagina_segura_sin_motivo_interno(self):
        respuesta = Client(enforce_csrf_checks=True).post('/formulario/', {'dato': 'x'})
        self.assertContains(respuesta, 'Necesitamos que vuelvas a abrir la página', status_code=403)
        self.assertNotContains(respuesta, 'CSRF cookie not set', status_code=403)
        self.assertNotContains(respuesta, 'Reason given for failure', status_code=403)

    def test_pantalla_500_no_necesita_procesadores_de_contexto_ni_bd(self):
        from django.conf import settings
        plantillas = [{**settings.TEMPLATES[0], 'OPTIONS': {
            **settings.TEMPLATES[0]['OPTIONS'],
            'context_processors': ['core.test_error_pages.contexto_no_disponible'],
        }}]
        with override_settings(TEMPLATES=plantillas):
            respuesta = Client(raise_request_exception=False).get('/fallo/')
        self.assertContains(respuesta, 'Algo salió mal', status_code=500)

    def test_json_existente_no_se_transforma_en_html(self):
        respuesta = self.client.get('/api/')
        self.assertEqual(respuesta.status_code, 409)
        self.assertEqual(respuesta.json(), {'status': 'error', 'message': 'Elige otro horario.'})

    @override_settings(ALLOWED_HOSTS=['espaciohope.com', 'tectuminhause.espaciohope.com',
                                     'tectumbeneficios.espaciohope.com'], STATIC_URL='/static/')
    def test_logo_real_por_dominio_en_todos_los_errores(self):
        for host, logo in (
            ('espaciohope.com', 'img/logo_hope_extendido.png'),
            ('tectuminhause.espaciohope.com', 'img/tectum/logo-completo.png'),
            ('tectumbeneficios.espaciohope.com', 'img/tectum/logo-completo.png'),
        ):
            for ruta, codigo in (('/no-existe/', 404), ('/prohibido/', 403),
                                 ('/solicitud/', 400), ('/fallo/', 500)):
                with self.subTest(host=host, codigo=codigo):
                    respuesta = Client(raise_request_exception=False).get(ruta, HTTP_HOST=host)
                    self.assertContains(respuesta, f'src="/static/{logo}"', status_code=codigo)

    def test_host_invalido_no_impide_renderizar_pantalla_400(self):
        from django.test import RequestFactory
        respuesta = error_views.error_400(
            RequestFactory().get('/', HTTP_HOST='host-invalido.example'),
            SuspiciousOperation('DETALLE_INTERNO_PRIVADO'),
        )
        self.assertContains(respuesta, 'logo_hope_extendido.png', status_code=400)


@override_settings(DEBUG=False, ROOT_URLCONF='hope_project.urls')
class CitaNoDisponibleTests(TestCase):
    def test_cita_inexistente_y_estados_no_admitidos_muestran_el_mismo_mensaje(self):
        paciente = User.objects.create_user('paciente-error')
        respuestas = [self.client.get('/sesion-previa/2147483647/')]
        for estado in ('Pendiente', 'Cancelada', 'Completada'):
            cita = Cita.objects.create(
                paciente=paciente, fecha=date(2026, 11, 23), hora=time(10),
                estado=estado, modalidad='En línea',
            )
            respuestas.append(self.client.get(f'/sesion-previa/{cita.pk}/'))
        for respuesta in respuestas:
            self.assertContains(respuesta, 'Este enlace de sesión no está disponible', status_code=404)
            self.assertNotContains(respuesta, 'No Cita matches', status_code=404)
            self.assertNotContains(respuesta, paciente.username, status_code=404)
            self.assertEqual(respuesta.content, respuestas[0].content)


@override_settings(DEBUG=False)
class MultimediaSinDebugTests(SimpleTestCase):
    def test_archivos_publicos_funcionan_y_privados_y_traversal_se_bloquean(self):
        # Resolver la ruta real garantiza que está registrada con DEBUG=False.
        coincidencia = resolve('/media/publico.txt', urlconf=project_urls)
        self.assertIs(coincidencia.func, project_urls.servir_media)
        ruta_media = next(ruta for ruta in project_urls.urlpatterns
                          if getattr(ruta, 'callback', None) is project_urls.servir_media)
        with tempfile.TemporaryDirectory() as carpeta:
            raiz = Path(carpeta)
            multimedia = raiz / 'media'
            multimedia.mkdir()
            (multimedia / 'publico.txt').write_text('Archivo público', encoding='utf-8')
            (raiz / 'fuera.txt').write_text('SECRETO_FUERA', encoding='utf-8')
            privadas = multimedia / 'privadas'
            clinico = privadas / 'notas_clinicas'
            clinico.mkdir(parents=True)
            (clinico / 'nota.txt').write_text('SECRETO_CLINICO', encoding='utf-8')
            urls_media = ModuleType('multimedia_error_test_urls')
            urls_media.urlpatterns = [URLPattern(
                ruta_media.pattern, ruta_media.callback,
                default_args={'document_root': multimedia},
            )]
            urls_media.handler400 = error_views.error_400
            urls_media.handler404 = error_views.error_404
            with override_settings(ROOT_URLCONF=urls_media, GRABACIONES_ROOT=privadas,
                                   RAILWAY_VOLUME_MOUNT_PATH=None):
                respuesta = self.client.get('/media/publico.txt')
                self.assertEqual(respuesta.status_code, 200)
                try:
                    self.assertEqual(b''.join(respuesta.streaming_content).decode(), 'Archivo público')
                finally:
                    respuesta.close()
                for ruta, codigo in (
                    ('/media/privadas/notas_clinicas/nota.txt', 404),
                    ('/media/no-existe.txt', 404),
                    ('/media/../fuera.txt', 400),
                ):
                    with self.subTest(ruta=ruta):
                        respuesta = self.client.get(ruta)
                        self.assertEqual(respuesta.status_code, codigo)
                        self.assertTemplateUsed(respuesta, 'errors/error.html')
                        self.assertNotContains(respuesta, 'SECRETO_', status_code=codigo)
