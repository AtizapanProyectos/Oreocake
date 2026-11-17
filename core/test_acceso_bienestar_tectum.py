import json
from datetime import date, time
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs, urlencode, urlsplit

from django.apps import apps
from django.contrib.auth import SESSION_KEY
from django.contrib.auth.models import Group, User
from django.core.cache import cache
from django.db import connection
from django.test import Client, TestCase
from django.urls import reverse

from .acceso_bienestar_tectum import ERROR_CREDENCIALES, GRUPO_TECTUM, SESION_TECTUM
from .models import Cita, HistorialClinico, PerfilPsicologo, UsuarioPerfil


class AccesoBienestarTectumTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.grupo = Group.objects.create(name=GRUPO_TECTUM)
        cls.staff = User.objects.create_user('personal-tectum', 'bienestar@example.test', 'Una-clave-de-prueba')
        cls.staff.groups.add(cls.grupo)
        cls.hope = User.objects.create_superuser('direccion-hope')
        cls.paciente = User.objects.create_user('colaborador', first_name='Paciente', last_name='De Prueba')
        UsuarioPerfil.objects.create(usuario=cls.paciente, nombre='Paciente De Prueba', es_tectum=True)
        terapeuta = User.objects.create_user('terapeuta', first_name='Terapeuta', last_name='De Prueba')
        psicologo = PerfilPsicologo.objects.create(usuario=terapeuta, cedula_profesional='0001', genero='Mujer')
        cls.cita = Cita.objects.create(
            paciente=cls.paciente, psicologo=psicologo, fecha=date(2026, 5, 12), hora=time(15),
            estado='Completada', es_tectum=True,
        )
        HistorialClinico.objects.create(
            paciente=cls.paciente, psicologo=psicologo, cita=cls.cita,
            notas_sesion='La sesión exploró estrés por sobrecarga laboral.',
        )

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.panel = reverse('panel_bienestar_tectum')
        self.sentimiento = reverse('panel_bienestar_tectum_sentimiento')
        self.login_url = reverse('panel_bienestar_tectum_login')
        self.logout_url = reverse('panel_bienestar_tectum_logout')

    def ingresar(self, cliente=None, **datos):
        cliente = self.client if cliente is None else cliente
        formulario = {'username': self.staff.username, 'password': 'Una-clave-de-prueba', **datos}
        return cliente.post(self.login_url, formulario)

    def test_redireccion_publica_y_endpoint_protegido_antes_de_consultas(self):
        destino = self.panel + '?mes=5&anio=2026&refresh=1'
        with patch('core.panel_bienestar_tectum._periodo') as periodo:
            respuesta = self.client.get(destino)
            self.assertEqual(respuesta.status_code, 302)
            self.assertEqual(urlsplit(respuesta.url).path, self.login_url)
            self.assertEqual(parse_qs(urlsplit(respuesta.url).query), {'next': [destino]})
            self.assertEqual(self.client.get(self.sentimiento).status_code, 401)
            self.client.force_login(self.paciente)
            self.assertEqual(self.client.get(self.panel).status_code, 403)
            self.assertEqual(self.client.get(self.sentimiento).status_code, 403)
            periodo.assert_not_called()

    def test_login_por_usuario_y_correo_con_filtros(self):
        for identificador in (self.staff.username, 'BIENESTAR@EXAMPLE.TEST'):
            with self.subTest(identificador=identificador):
                cliente = Client()
                destino = self.panel + '?mes=5&anio=2026&refresh=1'
                login = cliente.get(self.login_url + '?' + urlencode({'next': destino}))
                self.assertTemplateUsed(login, 'tectum/bienestar_login.html')
                self.assertContains(login, 'Usuario o correo')
                self.assertContains(login, 'Contraseña')
                self.assertContains(login, 'Ingresar')
                self.assertContains(login, 'logo-completo.png')
                self.assertContains(login, 'logo_hope_extendido.png')
                self.assertIn('no-store', login['Cache-Control'])
                respuesta = self.ingresar(cliente, username=identificador, next=login.context['next'])
                self.assertRedirects(respuesta, destino, fetch_redirect_response=False)
                self.assertEqual(cliente.session[SESION_TECTUM], self.staff.pk)

    def test_usuario_tiene_precedencia_sobre_el_correo_de_otra_cuenta(self):
        cuenta = User.objects.create_user(self.staff.email, password='Una-clave-de-prueba')
        respuesta = self.ingresar(username=cuenta.username)
        self.assertContains(respuesta, ERROR_CREDENCIALES)
        cuenta.groups.add(self.grupo)
        respuesta = self.ingresar(username=cuenta.username)
        self.assertRedirects(respuesta, self.panel, fetch_redirect_response=False)
        self.assertEqual(self.client.session[SESION_TECTUM], cuenta.pk)

    def test_rechazos_comparten_mensaje_y_no_crean_sesion(self):
        User.objects.create_user('correo-duplicado', self.staff.email.upper(), 'Otra-clave-de-prueba')
        inactivo = User.objects.create_user('inactivo', password='Una-clave-de-prueba', is_active=False)
        inactivo.groups.add(self.grupo)
        User.objects.create_user('sin-grupo', password='Una-clave-de-prueba')
        for usuario, clave in [
            (inactivo.username, 'Una-clave-de-prueba'), ('sin-grupo', 'Una-clave-de-prueba'),
            (self.staff.email, 'Una-clave-de-prueba'), (self.staff.username, 'Incorrecta'),
        ]:
            with self.subTest(usuario=usuario):
                self.assertContains(self.ingresar(username=usuario, password=clave), ERROR_CREDENCIALES)
                self.assertNotIn(SESSION_KEY, self.client.session)

    def test_limite_por_identificador_normalizado(self):
        for _ in range(15):
            self.assertContains(self.ingresar(password='Incorrecta'), ERROR_CREDENCIALES)
        self.assertEqual(self.ingresar(username=self.staff.username.upper()).status_code, 429)
        self.assertRedirects(self.ingresar(username=self.staff.email), self.panel, fetch_redirect_response=False)

    def test_descarta_destinos_fuera_del_panel_y_parametros_ajenos(self):
        for destino in ('https://example.test' + self.panel, '//example.test' + self.panel,
                        '/panel-admin/', self.sentimiento, self.panel + '?otra=pagina'):
            with self.subTest(destino=destino):
                self.assertRedirects(self.ingresar(next=destino), self.panel, fetch_redirect_response=False)

    def test_csrf_y_cierre_de_sesion(self):
        cliente = Client(enforce_csrf_checks=True)
        self.assertEqual(self.ingresar(cliente).status_code, 403)
        cliente.get(self.login_url)
        token = cliente.cookies['csrftoken'].value
        self.assertEqual(self.ingresar(cliente, csrfmiddlewaretoken=token).status_code, 302)
        self.assertEqual(cliente.get(self.logout_url).status_code, 405)
        self.assertEqual(cliente.post(self.logout_url).status_code, 403)
        token = cliente.cookies['csrftoken'].value
        self.assertRedirects(cliente.post(self.logout_url, {'csrfmiddlewaretoken': token}), self.login_url)
        self.assertNotIn(SESSION_KEY, cliente.session)
        self.assertNotIn(SESION_TECTUM, cliente.session)
        self.assertEqual(cliente.get(self.sentimiento).status_code, 401)

    def test_rutas_ajenas_bloqueadas_con_o_sin_login_dedicado(self):
        self.staff.is_staff = True
        self.staff.is_superuser = True
        self.staff.save(update_fields=['is_staff', 'is_superuser'])
        self.client.force_login(self.staff)
        self.assertEqual(urlsplit(self.client.get(self.panel).url).path, self.login_url)
        self.assertEqual(self.client.get(self.sentimiento).status_code, 401)
        for dedicado in (False, True):
            if dedicado:
                self.assertEqual(self.ingresar().status_code, 302)
            for ruta in ('/error-59/', '/error-59/auth/user/', '/admin/', '/panel-admin/',
                         '/panel-admin/usuarios/', '/panel-admin/configuracion/', '/api/admin/stats/', '/panel/'):
                with self.subTest(dedicado=dedicado, ruta=ruta):
                    self.assertEqual(self.client.get(ruta).status_code, 403)

    def test_quitar_grupo_o_desactivar_invalida_sesion(self):
        for motivo in ('grupo', 'inactivo'):
            with self.subTest(motivo=motivo):
                self.assertEqual(self.ingresar().status_code, 302)
                if motivo == 'grupo':
                    self.staff.groups.remove(self.grupo)
                else:
                    User.objects.filter(pk=self.staff.pk).update(is_active=False)
                self.assertEqual(self.client.get(self.sentimiento).status_code, 401)
                self.assertNotIn(SESSION_KEY, self.client.session)
                self.assertNotIn(SESION_TECTUM, self.client.session)
                self.staff.groups.add(self.grupo)

    def test_datos_graficas_y_exportacion_disponibles_para_ambos_roles(self):
        direccion = Client()
        direccion.force_login(self.hope)
        self.assertFalse(self.staff.is_staff)
        self.assertFalse(self.staff.is_superuser)
        self.assertEqual(self.ingresar().status_code, 302)
        filtros = {'mes': 5, 'anio': 2026}
        panel_hope = direccion.get(self.panel, filtros)
        panel_tectum = self.client.get(self.panel, filtros)
        self.assertEqual(panel_hope.context['panel'], panel_tectum.context['panel'])
        self.assertEqual(panel_tectum.context['panel']['detalle_citas'][0]['nombre'], 'Paciente De Prueba')
        self.assertEqual(panel_tectum.context['panel']['detalle_citas'][0]['psicologo'], 'Terapeuta De Prueba')
        for respuesta in (panel_hope, panel_tectum):
            self.assertContains(respuesta, 'Paciente De Prueba')
            self.assertContains(respuesta, 'id="panel-data"')
            self.assertContains(respuesta, 'onclick="window.print()"')
            self.assertIn('no-store', respuesta['Cache-Control'])
        self.assertContains(panel_tectum, 'Cerrar sesión')
        self.assertNotContains(panel_hope, 'Cerrar sesión')

        clasificacion = {'clasificaciones': [
            {'id': f'b{self.cita.pk}', 'emocion': 'Estrés', 'manifestacion': 'Sobrecarga laboral'},
        ]}
        with patch('core.panel_bienestar_tectum._llamar_groq', return_value=(json.dumps(clasificacion), 'prueba', 0, 0)) as groq:
            ia_hope = direccion.get(self.sentimiento, filtros)
            ia_tectum = self.client.get(self.sentimiento, filtros)
        self.assertEqual(groq.call_count, 1)
        for respuesta in (ia_hope, ia_tectum):
            self.assertEqual(respuesta.status_code, 200)
            self.assertIn('no-store', respuesta['Cache-Control'])
        self.assertEqual(ia_hope.json(), ia_tectum.json())
        self.assertEqual(direccion.get(reverse('admin:index')).status_code, 200)

    def test_migracion_crea_grupo_sin_permisos_y_es_idempotente(self):
        self.grupo.delete()
        migracion = import_module('core.migrations.0081_grupo_staff_tectum')
        editor = SimpleNamespace(connection=connection)
        migracion.crear_grupo_staff_tectum(apps, editor)
        grupo = Group.objects.get(name=GRUPO_TECTUM)
        self.assertFalse(grupo.permissions.exists())
        self.staff.groups.add(grupo)
        migracion.crear_grupo_staff_tectum(apps, editor)
        self.assertTrue(self.staff.groups.filter(pk=grupo.pk).exists())
