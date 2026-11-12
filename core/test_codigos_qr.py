from io import BytesIO
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse
from PIL import Image

from .models import CodigoQRHope


class CodigosQRTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser('direccion')
        self.panel = reverse('panel_codigos_qr')
        self.destino = 'https://espaciohope.com/donaciones/?campana=invierno#apoyar'
        self.codigo = CodigoQRHope.objects.create(nombre='Donaciones', destino=self.destino)

    def test_acceso_admin_y_csrf(self):
        imagen = reverse('imagen_codigo_qr', args=[self.codigo.codigo])
        estado = reverse('estado_codigo_qr', args=[self.codigo.codigo])
        self.client.force_login(User.objects.create_user('paciente'))
        for ruta in (self.panel, imagen, reverse('estadisticas_codigos_qr')):
            self.assertEqual(self.client.get(ruta).status_code, 302)
        self.assertEqual(self.client.post(estado, {'activo': '0'}).status_code, 302)
        self.codigo.refresh_from_db()
        self.assertTrue(self.codigo.activo)
        cliente = Client(enforce_csrf_checks=True)
        cliente.force_login(self.admin)
        self.assertEqual(cliente.post(self.panel).status_code, 403)
        self.assertEqual(cliente.post(estado, {'activo': '0'}).status_code, 403)

    def test_genera_png_con_el_enlace_de_escaneo_y_color_elegido(self):
        self.client.force_login(self.admin)
        respuesta = self.client.post(self.panel, {
            'nombre': 'Invierno', 'destino': self.destino, 'color': 'petroleo',
        }, follow=True)
        codigo = CodigoQRHope.objects.get(nombre='Invierno')
        directo = reverse('qr_hope_directo', args=[codigo.codigo])
        self.assertContains(respuesta, f'https://espaciohope.com{directo}')
        ruta = reverse('imagen_codigo_qr', args=[codigo.codigo])
        import segno
        with patch('core.codigos_qr.segno.make_qr', wraps=segno.make_qr) as codificar:
            png = self.client.get(ruta, {'descargar': '1'})
        escaneo = reverse('qr_hope_escaneo', args=[codigo.codigo])
        codificar.assert_called_once_with(f'https://espaciohope.com{escaneo}', error='m')
        self.assertEqual(png['Content-Type'], 'image/png')
        self.assertIn('attachment', png['Content-Disposition'])
        with Image.open(BytesIO(png.content)) as imagen:
            self.assertEqual(imagen.size[0], imagen.size[1])
            colores = {color for _, color in imagen.convert('RGB').getcolors()}
            self.assertIn((9, 84, 82), colores)
        codigo.refresh_from_db()
        self.assertEqual((codigo.escaneos, codigo.visitas_directas), (0, 0))

    def test_separa_conteos_e_ignora_head(self):
        qr = reverse('qr_hope_escaneo', args=[self.codigo.codigo])
        directo = reverse('qr_hope_directo', args=[self.codigo.codigo])
        for ruta in (qr, directo, qr):
            respuesta = self.client.get(ruta)
            self.assertRedirects(respuesta, self.destino, fetch_redirect_response=False)
            self.assertIn('no-store', respuesta['Cache-Control'])
        self.assertEqual(self.client.head(qr).status_code, 302)
        self.client.force_login(self.admin)
        stats = self.client.get(reverse('estadisticas_codigos_qr'), {'codigo': self.codigo.codigo}).json()
        self.assertEqual(stats, {
            'totales': {'escaneos': 2, 'visitas_directas': 1},
            'codigos': [{'codigo': str(self.codigo.codigo), 'escaneos': 2, 'visitas_directas': 1}],
        })

    def test_bloquea_destinos_fuera_de_hope_y_enlaces_de_seguimiento(self):
        self.client.force_login(self.admin)
        for destino in (
            'http://espaciohope.com/',
            'https://espaciohope.com.ejemplo.test/',
            'https://espaciohope.com@ejemplo.test/',
            'https://espaciohope.com:8080/',
            'https://espaciohope.com/qr/prueba/',
        ):
            with self.subTest(destino=destino):
                respuesta = self.client.post(self.panel, {
                    'nombre': 'Inválido', 'destino': destino, 'color': 'negro',
                })
                self.assertTrue(respuesta.context['formulario'].errors.get('destino'))
        self.assertEqual(CodigoQRHope.objects.count(), 1)

    def test_pausa_y_reactiva_ambos_enlaces(self):
        self.client.force_login(self.admin)
        estado = reverse('estado_codigo_qr', args=[self.codigo.codigo])
        self.assertRedirects(self.client.post(estado, {'activo': '0'}), self.panel, fetch_redirect_response=False)
        for nombre in ('qr_hope_escaneo', 'qr_hope_directo'):
            self.assertEqual(self.client.get(reverse(nombre, args=[self.codigo.codigo])).status_code, 404)
        self.assertRedirects(self.client.post(estado, {'activo': '1'}), self.panel, fetch_redirect_response=False)
        self.assertEqual(self.client.get(reverse('qr_hope_escaneo', args=[self.codigo.codigo])).status_code, 302)
        self.codigo.refresh_from_db()
        self.assertEqual((self.codigo.escaneos, self.codigo.visitas_directas), (1, 0))
