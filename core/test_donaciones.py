import csv
import io
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from django.contrib.auth.models import Permission, User
from django.core.cache import cache
from django.template.loader import render_to_string
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import Cita, Donacion, UsuarioPerfil


class DonacionesTests(TestCase):
    def setUp(self):
        cache.clear()
        self.usuario = User.objects.create_user('donante', first_name='Persona', email='cuenta@example.test')
        UsuarioPerfil.objects.create(usuario=self.usuario, nombre='Persona')
        self.admin = User.objects.create_superuser('admin')
        self.cita = Cita.objects.create(paciente=self.usuario, fecha=timezone.localdate()+timedelta(days=3), hora='12:00')
        self.datos = {'folio':str(uuid4()), 'monto':'400.00', 'origen':'donaciones'}

    def iniciar(self, cliente=None, **datos):
        return (cliente or self.client).post(reverse('donacion_iniciar'), {**self.datos, **datos})

    def actualizar(self, accion, cliente=None, **datos):
        return (cliente or self.client).post(reverse('donacion_actualizar'), {'folio':self.datos['folio'], 'accion':accion, **datos})

    def completar(self, orden='ORDEN1', captura='CAPTURA1', **datos):
        self.actualizar('orden', orden=orden)
        self.actualizar('capturar', orden=orden)
        return self.actualizar('completar', orden=orden, captura=captura, resultado='COMPLETED', monto='400.00', moneda='MXN', custom_id='HOPE-DON-'+self.datos['folio'], **datos)

    def test_crea_pendiente_para_visitante_sin_cita(self):
        respuesta = self.iniciar()
        self.assertEqual(respuesta.status_code, 200)
        d = Donacion.objects.get()
        self.assertIsNone(d.usuario_id)
        self.assertIsNone(d.cita_id)
        self.assertEqual(d.donante, 'Donante sin identificar')
        self.assertEqual(d.estado, 'Pendiente')
        self.assertEqual(d.monto, Decimal('400'))
        self.assertEqual(respuesta.json()['custom_id'], 'HOPE-DON-'+str(d.folio))
        self.assertNotIn('propietario', respuesta.json())

    def test_usuario_y_donante_paypal_se_conservan_separados(self):
        self.client.force_login(self.usuario)
        self.iniciar()
        respuesta = self.completar(nombre='Otra Persona', correo='pagador@example.test')
        self.assertEqual(respuesta.status_code, 200)
        d = Donacion.objects.get()
        self.assertEqual(d.usuario_id, self.usuario.pk)
        self.assertEqual(d.nombre_paypal, 'Otra Persona')
        self.assertEqual(d.correo_paypal, 'pagador@example.test')
        self.assertEqual(d.confirmacion, 'navegador')
        self.assertIsNotNone(d.completada)

    def test_todos_los_origenes_y_conceptos(self):
        for origen in ['inicio','donaciones','venezuela','colombia']:
            respuesta = self.iniciar(folio=str(uuid4()), origen=origen)
            self.assertEqual(respuesta.status_code, 200)
            self.assertIn('Donación', respuesta.json()['descripcion'])
        self.client.force_login(self.usuario)
        respuesta = self.iniciar(folio=str(uuid4()), origen='post_cita', cita_id=self.cita.pk, monto='100.00')
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.json()['cita_id'], self.cita.pk)
        self.assertEqual(respuesta.json()['descripcion'], 'Donación de una sesión de terapia HOPE')

    def test_post_cita_exige_propietario_y_permite_importe_voluntario(self):
        self.assertEqual(self.iniciar(origen='post_cita', cita_id=self.cita.pk, monto='100').status_code, 403)
        otro = User.objects.create_user('otro')
        self.client.force_login(otro)
        self.assertEqual(self.iniciar(origen='post_cita', cita_id=self.cita.pk, monto='100').status_code, 403)
        self.client.force_login(self.usuario)
        for monto in ['100', '400', '1200', '7200', '14500', '235.50']:
            respuesta = self.iniciar(folio=str(uuid4()), origen='post_cita', cita_id=self.cita.pk, monto=monto)
            self.assertEqual(respuesta.status_code, 200, respuesta.content)
            self.assertEqual(respuesta.json()['monto'], f'{Decimal(monto):.2f}')

    def test_importes_no_validos(self):
        for monto in ['0','-1','NaN','Infinity','1.001','100000000','texto','']:
            self.assertEqual(self.iniciar(monto=monto).status_code, 400, monto)
        self.assertFalse(Donacion.objects.exists())

    def test_reintento_iniciar_idempotente_y_no_reasigna_folio(self):
        self.assertEqual(self.iniciar().status_code, 200)
        self.assertEqual(self.iniciar().status_code, 200)
        self.assertEqual(self.iniciar(monto='100').status_code, 409)
        self.assertEqual(Donacion.objects.count(), 1)

    def test_visitante_no_consulta_ni_modifica_donacion_ajena(self):
        self.iniciar()
        ajeno = Client()
        self.assertEqual(ajeno.get(reverse('donacion_estado'), {'folio':self.datos['folio']}).status_code, 404)
        self.assertEqual(self.actualizar('cancelar', cliente=ajeno).status_code, 404)
        self.assertEqual(self.iniciar(cliente=ajeno).status_code, 404)
        self.assertNotIn('nombre_paypal', self.client.get(reverse('donacion_estado'), {'folio':self.datos['folio']}).json())

    def test_usuario_recupera_desde_otra_sesion_propia(self):
        self.client.force_login(self.usuario)
        self.iniciar()
        otra = Client(); otra.force_login(self.usuario)
        self.assertEqual(otra.get(reverse('donacion_estado'), {'folio':self.datos['folio']}).status_code, 200)

    def test_completar_es_idempotente(self):
        self.iniciar()
        self.assertEqual(self.completar().status_code, 200)
        d = Donacion.objects.get(); fecha = d.completada
        self.assertEqual(self.completar().status_code, 200)
        d.refresh_from_db()
        self.assertEqual(d.estado, 'Completada')
        self.assertEqual(d.completada, fecha)
        self.assertEqual(Donacion.objects.filter(estado='Completada').count(), 1)

    def test_captura_no_puede_reutilizarse(self):
        self.iniciar(); self.completar()
        self.datos['folio'] = str(uuid4())
        self.iniciar()
        respuesta = self.completar(orden='ORDEN2', captura='CAPTURA1')
        self.assertEqual(respuesta.status_code, 409)
        self.assertEqual(Donacion.objects.filter(estado='Completada').count(), 1)
        self.assertEqual(Donacion.objects.get(folio=self.datos['folio']).estado, 'Pendiente')

    def test_orden_no_puede_reutilizarse(self):
        self.iniciar(); self.actualizar('orden', orden='ORDEN1')
        self.datos['folio'] = str(uuid4()); self.iniciar()
        self.assertEqual(self.actualizar('orden', orden='ORDEN1').status_code, 409)

    def test_incierto_permanece_pendiente_y_no_se_cancela(self):
        self.iniciar(); self.actualizar('orden', orden='ORDEN1'); self.actualizar('capturar', orden='ORDEN1')
        self.assertEqual(self.actualizar('incidencia', incidencia='captura_incierta').status_code, 200)
        self.assertEqual(self.actualizar('cancelar').status_code, 409)
        self.assertEqual(Donacion.objects.get().estado, 'Pendiente')
        respuesta = self.client.get(reverse('donacion_estado'), {'origen':'donaciones'})
        self.assertTrue(respuesta.json()['pendiente']['captura_iniciada'])

    def test_cancelar_no_suma_y_no_permite_completar(self):
        self.iniciar(); self.actualizar('orden', orden='ORDEN1')
        self.assertEqual(self.actualizar('cancelar').status_code, 200)
        self.assertEqual(self.actualizar('cancelar').status_code, 200)
        self.assertEqual(self.actualizar('capturar', orden='ORDEN1').status_code, 409)
        self.assertIsNone(self.client.get(reverse('donacion_estado'), {'origen':'donaciones'}).json()['pendiente'])

    def test_validacion_de_coherencia_antes_de_completar(self):
        self.iniciar()
        self.assertEqual(self.actualizar('capturar', orden='ORDEN1').status_code, 409)
        self.actualizar('orden', orden='ORDEN1'); self.actualizar('capturar', orden='ORDEN1')
        base = {'orden':'ORDEN1','captura':'CAPTURA1','resultado':'COMPLETED','monto':'400','moneda':'MXN','custom_id':'HOPE-DON-'+self.datos['folio']}
        for campo, valor in [('resultado','PENDING'),('monto','1'),('moneda','USD'),('custom_id','otro'),('orden','OTRA')]:
            self.assertEqual(self.actualizar('completar', **{**base,campo:valor}).status_code, 409)
        self.assertEqual(Donacion.objects.get().estado,'Pendiente')

    def test_identidad_incompleta_no_pierde_donacion(self):
        self.iniciar()
        self.assertEqual(self.completar(correo='no-es-correo').status_code, 200)
        d = Donacion.objects.get()
        self.assertEqual(d.correo_paypal,'')
        self.assertEqual(d.donante,'Donante sin identificar')

    def test_limite_de_inicios_y_reintento_permitido(self):
        for _ in range(20):
            self.assertEqual(self.iniciar(folio=str(uuid4())).status_code,200)
        self.assertEqual(self.iniciar().status_code,429)

    def test_csrf_y_metodos(self):
        seguro = Client(enforce_csrf_checks=True)
        self.assertEqual(self.iniciar(cliente=seguro).status_code,403)
        self.assertEqual(self.client.get(reverse('donacion_iniciar')).status_code,405)
        self.assertEqual(self.client.get(reverse('donacion_actualizar')).status_code,405)

    @override_settings(PAYPAL_CLIENT_ID='prueba')
    def test_visitante_puede_crear_con_csrf_de_la_pagina_publica(self):
        cliente = Client(enforce_csrf_checks=True)
        respuesta = cliente.get(reverse('donaciones'))
        self.assertEqual(respuesta.status_code, 200)
        token = cliente.cookies['csrftoken'].value
        respuesta = cliente.post(reverse('donacion_iniciar'), self.datos, HTTP_X_CSRFTOKEN=token)
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(cliente.get(reverse('donacion_estado'), {'folio':self.datos['folio']}).status_code, 200)

    def test_admin_suma_solo_completadas_y_respeta_filtros(self):
        self.iniciar(); self.completar()
        self.iniciar(folio=str(uuid4()), monto='900')
        self.iniciar(folio=str(uuid4()), monto='300', origen='inicio')
        self.client.force_login(self.admin)
        respuesta = self.client.get(reverse('admin:core_donacion_changelist'))
        self.assertEqual(respuesta.context['cantidad_completadas'],1)
        self.assertEqual(respuesta.context['total_completadas'],Decimal('400'))
        filtrada = self.client.get(reverse('admin:core_donacion_changelist'), {'origen__exact':'inicio'})
        self.assertEqual(filtrada.context['cantidad_completadas'],0)
        self.assertEqual(filtrada.context['total_completadas'],Decimal('0'))
        self.assertContains(respuesta,'sin verificación adicional')

    def test_csv_filtrado_y_sin_formulas_ni_token_sesion(self):
        self.iniciar(); self.completar(nombre='=SUM(1,2)',correo='prueba@example.test')
        self.iniciar(folio=str(uuid4()), monto='800', origen='inicio')
        self.client.force_login(self.admin)
        respuesta = self.client.get(reverse('admin:core_donacion_exportar'), {'estado__exact':'Completada','origen__exact':'donaciones'})
        self.assertEqual(respuesta.status_code,200)
        filas = list(csv.reader(io.StringIO(respuesta.content.decode('utf-8-sig'))))
        self.assertEqual(len(filas),2)
        self.assertTrue(filas[1][1].startswith("'="))
        self.assertNotIn('propietario',filas[0])
        self.assertIn('Informada por el navegador',filas[1][-1])

    def test_admin_solo_lectura_y_exportacion_protegida(self):
        self.iniciar()
        d = Donacion.objects.get()
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post(reverse('admin:core_donacion_add'),{}).status_code,403)
        self.assertEqual(self.client.post(reverse('admin:core_donacion_change',args=[d.pk]),{'monto':'1'}).status_code,403)
        self.assertEqual(self.client.post(reverse('admin:core_donacion_delete',args=[d.pk]),{}).status_code,403)
        staff = User.objects.create_user('staff',is_staff=True)
        self.client.force_login(staff)
        self.assertEqual(self.client.get(reverse('admin:core_donacion_exportar')).status_code,403)
        staff.user_permissions.add(Permission.objects.get(codename='view_donacion'))
        self.assertEqual(self.client.get(reverse('admin:core_donacion_exportar')).status_code,200)

    @override_settings(PAYPAL_CLIENT_ID='prueba')
    def test_plantillas_integradas_y_recursos_versionados(self):
        request = RequestFactory().get('/donaciones/',HTTP_HOST='localhost')
        request.user = self.usuario
        for archivo in ['inicio.html','landing_colombia.html','donaciones.html','donaciones_venezuela.html','snippets/donar_terapia_confirmacion.html']:
            with self.subTest(archivo=archivo):
                html = render_to_string(archivo, {'request':request,'user':self.usuario,'paypal_client_id':'prueba'})
                self.assertIn('donaciones_registro.js?v=',html)
                self.assertIn('donaciones_registro.css?v=',html)
                if archivo == 'snippets/donar_terapia_confirmacion.html':
                    self.assertRegex(html, r'invitacion_donacion\.js\?v=[a-f0-9]{16}')
                    self.assertRegex(html, r'invitacion_donacion\.css\?v=[a-f0-9]{16}')
                    self.assertIn('Dona una terapia a una persona.', html)
                else:
                    self.assertIn('HopeDonaciones.opciones',html)
                self.assertNotIn('actions.order.capture()',html)
                # Los callbacks compartidos viven en el archivo versionado.
        fuente = (Path(__file__).resolve().parent.parent/'static/js/donaciones_registro.js').read_text(encoding='utf-8')
        self.assertIn('custom_id:donacion.custom_id',fuente)
