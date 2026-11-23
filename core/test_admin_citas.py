from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import Permission, User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Cita, PagoCitaPayPal, PlanMensual


class AdministracionCitasTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser('admin-citas')
        self.paciente = User.objects.create_user('paciente-citas')
        self.client.force_login(self.admin)
        self.listado = reverse('admin:core_cita_changelist')

    def cita(self, **extra):
        return Cita.objects.create(paciente=self.paciente, fecha=timezone.localdate(), hora='10:00', **extra)

    def pago(self, cita, **extra):
        return PagoCitaPayPal.objects.create(cita=cita, monto=Decimal('150.00'), vence=timezone.now(), **extra)

    def visibles(self, **filtros):
        respuesta = self.client.get(self.listado, filtros)
        self.assertEqual(respuesta.status_code, 200)
        return set(respuesta.context['cl'].queryset.values_list('pk', flat=True))

    def test_eliminar_cita_conserva_pago_y_referencias(self):
        for capturada in (False, True):
            with self.subTest(capturada=capturada):
                cita = self.cita(estado='Confirmada' if capturada else 'Cancelada')
                pago = self.pago(cita, orden=f'ORDEN{capturada}', captura='CAPTURA' if capturada else None,
                                 captura_iniciada=capturada)
                url = reverse('admin:core_cita_delete', args=[cita.pk])
                vista = self.client.get(url)
                self.assertEqual(vista.status_code, 200)
                self.assertFalse(vista.context['perms_lacking'])
                self.assertFalse(vista.context['protected'])
                respuesta = self.client.post(url, {'post': 'yes'})
                self.assertEqual(respuesta.status_code, 302)
                self.assertFalse(Cita.objects.filter(pk=cita.pk).exists())
                pago.refresh_from_db()
                self.assertIsNone(pago.cita_id)
                self.assertEqual(pago.orden, f'ORDEN{capturada}')
                self.assertEqual(pago.captura, 'CAPTURA' if capturada else None)
                self.assertEqual(pago.monto, Decimal('150.00'))

    def test_borrado_masivo_conserva_pagos(self):
        citas = [self.cita(estado='Cancelada') for _ in range(2)]
        for i, cita in enumerate(citas):
            self.pago(cita, orden=f'MASIVO{i}')
        datos = {'action': 'delete_selected', '_selected_action': [c.pk for c in citas], 'post': 'yes'}
        respuesta = self.client.post(self.listado + '?reservas_inactivas=solo', datos)
        self.assertEqual(respuesta.status_code, 302)
        self.assertFalse(Cita.objects.filter(pk__in=[c.pk for c in citas]).exists())
        self.assertEqual(PagoCitaPayPal.objects.filter(cita__isnull=True).count(), 2)

    def test_no_necesita_permiso_borrar_pago_para_borrar_su_cita(self):
        staff = User.objects.create_user('staff-citas', is_staff=True)
        staff.user_permissions.add(*Permission.objects.filter(content_type__app_label='core',
                                  codename__in=['view_cita', 'delete_cita']))
        self.client.force_login(staff)
        cita = self.cita(estado='Cancelada')
        pago = self.pago(cita)
        self.assertEqual(self.client.post(reverse('admin:core_cita_delete', args=[cita.pk]), {'post': 'yes'}).status_code, 302)
        pago.refresh_from_db()
        self.assertIsNone(pago.cita_id)
        self.assertEqual(self.client.post(reverse('admin:core_pagocitapaypal_delete', args=[pago.pk]), {'post': 'yes'}).status_code, 403)

    def test_sin_permiso_no_puede_borrar_cita(self):
        staff = User.objects.create_user('lector-citas', is_staff=True)
        staff.user_permissions.add(Permission.objects.get(content_type__app_label='core', codename='view_cita'))
        self.client.force_login(staff)
        cita = self.cita()
        self.assertEqual(self.client.post(reverse('admin:core_cita_delete', args=[cita.pk]), {'post': 'yes'}).status_code, 403)
        self.assertTrue(Cita.objects.filter(pk=cita.pk).exists())

    def test_reservas_inactivas_separadas_sin_ocultar_citas_o_cobros_inciertos(self):
        abandonada = self.cita(estado='Cancelada')
        self.pago(abandonada)
        visibles = [self.cita(estado=estado) for estado in ['Pendiente', 'Confirmada', 'Completada', 'Cancelada']]
        incierta = self.cita(estado='Cancelada')
        self.pago(incierta, captura_iniciada=True)
        pagada = self.cita(estado='Cancelada')
        self.pago(pagada, captura='CAPTURAREAL')
        plan = PlanMensual.objects.create(paciente=self.paciente, creado_por=self.admin, tipo_servicio='individual',
                                         modalidad='En línea', monto=440, origen='paypal', estado='expirado',
                                         vence=timezone.now() - timedelta(minutes=1))
        reservas_plan = [self.cita(estado='Cancelada', plan_mensual=plan) for _ in range(4)]
        habituales = {c.pk for c in visibles} | {incierta.pk, pagada.pk}
        inactivas = {abandonada.pk} | {c.pk for c in reservas_plan}
        self.assertEqual(self.visibles(), habituales)
        self.assertEqual(self.visibles(reservas_inactivas='todas'), habituales | inactivas)
        self.assertEqual(self.visibles(reservas_inactivas='solo'), inactivas)
        canceladas = self.visibles(estado__exact='Cancelada')
        self.assertTrue(inactivas.issubset(canceladas))
        self.assertEqual(Cita.objects.count(), len(habituales | inactivas))

    @patch('core.pagos_paypal.capturar_orden')
    def test_orden_sin_cita_no_se_puede_capturar_ni_conciliar(self, capturar):
        pago = self.pago(None, orden='ORDENHUERFANA', captura_iniciada=True)
        respuesta = self.client.post(reverse('capturar_orden_cita_paypal'), {'orden': pago.orden, 'paciente_id': self.paciente.pk})
        self.assertEqual(respuesta.status_code, 404)
        pago.orden = 'recepcion:ORDENHUERFANA'
        pago.save(update_fields=['orden'])
        respuesta = self.client.post(reverse('recepcion_conciliar_paypal_manual', args=[pago.pk]),
                                     {'decision': 'confirmar', 'revisado': '1', 'transaccion': 'CAPTURAREAL'})
        self.assertEqual(respuesta.status_code, 404)
        capturar.assert_not_called()
