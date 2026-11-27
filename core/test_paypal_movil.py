"""Regresión de ventanas PayPal; ejecutar solo con settings_flujo_test."""

from unittest.mock import patch

from django.conf import settings
from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from .models import UsuarioPerfil


@override_settings(
    PAYPAL_CLIENT_ID='prueba',
    SECURE_CROSS_ORIGIN_OPENER_POLICY='same-origin',
    MIDDLEWARE=['django.middleware.security.SecurityMiddleware', *settings.MIDDLEWARE],
    ALLOWED_HOSTS=['testserver', 'tectuminhause.espaciohope.com',
                   'tectumbeneficios.espaciohope.com'],
)
class PayPalMovilTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.paciente = User.objects.create_user('paciente-paypal', email='paciente@example.test')
        UsuarioPerfil.objects.create(usuario=cls.paciente, nombre='Paciente PayPal')
        cls.colaborador = User.objects.create_user('colaborador-paypal', email='colaborador@example.test')
        UsuarioPerfil.objects.create(usuario=cls.colaborador, nombre='Colaborador PayPal', es_tectum=True)

    def setUp(self):
        disponibilidad = patch('core.views.obtener_slots_globales', return_value={})
        disponibilidad.start()
        self.addCleanup(disponibilidad.stop)
        padron = patch('core.views.COLABORADORES_TECTUM', {'colaborador@example.test'})
        padron.start()
        self.addCleanup(padron.stop)

    def test_panel_hope_permite_comunicacion_con_paypal_en_movil(self):
        self.client.force_login(self.paciente)
        for navegador in (
            'Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/131.0.0.0 Mobile Safari/537.36',
            'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1',
        ):
            with self.subTest(navegador=navegador):
                respuesta = self.client.get(reverse('panel_generico'), HTTP_USER_AGENT=navegador)
                self.assertEqual(respuesta.status_code, 200)
                self.assertTemplateUsed(respuesta, 'panel_generico.html')
                self.assertEqual(respuesta['Cross-Origin-Opener-Policy'], 'same-origin-allow-popups')

    def test_paneles_tectum_conservan_aislamiento_por_ruta_y_dominio(self):
        for usuario, ruta, host, plantilla in (
            (self.paciente, 'panel_beneficios', 'testserver', 'tectum_beneficios/panel-beneficios.html'),
            (self.paciente, 'panel_generico', 'tectumbeneficios.espaciohope.com', 'tectum_beneficios/panel-beneficios.html'),
            (self.colaborador, 'panel_tectum', 'testserver', 'tectum/panel-tectum.html'),
            (self.colaborador, 'panel_generico', 'tectuminhause.espaciohope.com', 'tectum/panel-tectum.html'),
        ):
            with self.subTest(ruta=ruta, host=host):
                cliente = Client()
                cliente.force_login(usuario)
                respuesta = cliente.get(reverse(ruta), HTTP_HOST=host)
                self.assertEqual(respuesta.status_code, 200)
                self.assertTemplateUsed(respuesta, plantilla)
                self.assertEqual(respuesta['Cross-Origin-Opener-Policy'], 'same-origin')

    def test_redirecciones_sin_sesion_conservan_aislamiento(self):
        for ruta in ('panel_generico', 'panel_tectum', 'panel_beneficios'):
            with self.subTest(ruta=ruta):
                respuesta = self.client.get(reverse(ruta))
                self.assertRedirects(respuesta, reverse('modulo_informativo'), fetch_redirect_response=False)
                self.assertEqual(respuesta['Cross-Origin-Opener-Policy'], 'same-origin')

    def test_rechazo_tectum_conserva_aislamiento(self):
        self.client.force_login(self.paciente)
        respuesta = self.client.get(reverse('panel_tectum'))
        self.assertEqual(respuesta.status_code, 403)
        self.assertEqual(respuesta['Cross-Origin-Opener-Policy'], 'same-origin')

    def test_pagina_de_acceso_conserva_aislamiento(self):
        respuesta = self.client.get(reverse('modulo_informativo'))
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta['Cross-Origin-Opener-Policy'], 'same-origin')
