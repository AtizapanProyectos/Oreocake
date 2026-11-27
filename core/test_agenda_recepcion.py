import json
from concurrent.futures import ThreadPoolExecutor
from datetime import time, timedelta
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import patch
from unittest import skipUnless
from uuid import uuid4

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import Client, RequestFactory, TestCase, TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.db import connection, close_old_connections
from django.urls import reverse
from django.utils import timezone

from . import agenda_recepcion as agenda
from .models import (BloqueoAgendaPsicologo, Cita, CuestionarioRegistro, DiaFestivo,
                     EsquemaHorarioPsicologo, EventoAuditoria, OperacionAgenda, PagoCitaPayPal,
                     PerfilPsicologo, PlanMensual, SincronizacionAgenda, TratamientoPaciente, UsuarioPerfil)


@override_settings(PAYPAL_CLIENT_ID='prueba', DEFAULT_FROM_EMAIL='hope@example.test')
class AgendaRecepcionTests(TestCase):
    def setUp(self):
        cache.clear()
        self.admin = User.objects.create_superuser('direccion', email='dir@example.test')
        self.paciente = User.objects.create_user('paciente', email='paciente@example.test', first_name='Paciente')
        self.perfil = UsuarioPerfil.objects.create(usuario=self.paciente, nombre='Paciente', telefono='5512345678', telefono_emergencia='5598765432')
        self.fecha = timezone.localdate() + timedelta(days=4)
        self.origen = self.doctor('origen', False)
        self.destino = self.doctor('destino', True)
        self.tratamiento = TratamientoPaciente.objects.create(paciente=self.paciente, tipo_servicio='individual', psicologo_asignado=self.origen)
        self.cita = self.crear_cita()
        self.client.force_login(self.admin)

    def doctor(self, nombre, activo):
        d = PerfilPsicologo.objects.create(usuario=User.objects.create_user(nombre, email=nombre+'@example.test', first_name=nombre), cedula_profesional=nombre,
            genero='Mujer', esta_activo=activo, modalidad_atencion='Mixto', atiende_pareja=True, atiende_familiar=True)
        EsquemaHorarioPsicologo.objects.create(psicologo=d, fecha_inicio=self.fecha, fecha_fin=self.fecha+timedelta(days=120), hora_inicio=time(9), hora_fin=time(17))
        return d

    def crear_cita(self, **extra):
        return Cita.objects.create(**{'paciente': self.paciente, 'psicologo': self.origen, 'fecha': self.fecha,
            'hora': time(10), 'estado':'Confirmada', 'modalidad':'Presencial', **extra})

    def post(self, datos, client=None):
        return (client or self.client).post(reverse('recepcion_agenda_ejecutar'), json.dumps({'clave':str(uuid4()), 'motivo':'Cambio solicitado por el consultante', **datos}), content_type='application/json')

    def traslado(self):
        r=self.client.get(reverse('recepcion_agenda_candidatos'), {'paciente':self.paciente.pk,'servicio':'individual','desde':str(self.fecha),'hasta':str(self.fecha+timedelta(days=7))}).json()
        d=next(d for d in r['psicologos'] if d['id']==self.destino.pk)
        return {'accion':'transferir','paciente':self.paciente.pk,'servicio':'individual','psicologo':self.destino.pk,
                'version':r['version'],'version_psicologo':d['version'],'citas':[{'id':c['id'],'fecha':c['fecha'],'hora':c['hora']} for c in r['citas']], 'acepta_carga':True}

    def reagendo(self, hora='11:00'):
        return {'accion':'reagendar','cita':self.cita.pk,'version':agenda.version(agenda.ficha(self.cita)),
                'citas':[{'id':self.cita.pk,'fecha':str(self.fecha),'hora':hora}], 'acepta_carga':True}

    def test_permisos_y_csrf(self):
        for usuario in (None,self.paciente,self.origen.usuario):
            c=Client()
            if usuario:c.force_login(usuario)
            for ruta in ('recepcion_agenda','recepcion_agenda_datos','recepcion_agenda_candidatos','recepcion_agenda_historial'):
                self.assertEqual(c.get(reverse(ruta)).status_code,403)
            self.assertEqual(self.post({'accion':'configurar'},c).status_code,403)
        c=Client(enforce_csrf_checks=True);c.force_login(self.admin)
        self.assertEqual(self.post({'accion':'configurar'},c).status_code,403)

    def test_panel_y_agenda_muestran_contactos_ambas_modalidades_y_libres(self):
        self.assertEqual(self.client.get(reverse('recepcion_agenda')).status_code,200)
        self.crear_cita(hora=time(12),modalidad='En línea')
        r=self.client.get(reverse('recepcion_agenda_datos'),{'desde':str(self.fecha),'hasta':str(self.fecha)});self.assertEqual(r.status_code,200,r.content)
        self.assertEqual(r['Cache-Control'],'private, no-store')
        doctor=next(d for d in r.json()['psicologos'] if d['id']==self.origen.pk)
        self.assertEqual(len(doctor['citas']),2)
        self.assertEqual(doctor['citas'][0]['telefono'],'5512345678')
        self.assertEqual(doctor['citas'][0]['email'],'paciente@example.test')
        self.assertNotIn('10:00',doctor['libres']['Presencial'][str(self.fecha)])
        self.assertIn('11:00',doctor['libres']['Presencial'][str(self.fecha)])

    def test_agenda_precarga_datos_sin_consultas_por_cada_horario(self):
        self.doctor('tercero',True)
        with CaptureQueriesContext(connection) as consultas:
            r=self.client.get(reverse('recepcion_agenda_datos'),{'desde':str(self.fecha),'hasta':str(self.fecha+timedelta(days=6))})
        self.assertEqual(r.status_code,200,r.content)
        self.assertLess(len(consultas),25)

    def test_reagendar_con_admisiones_cerradas_no_cambia_terapeuta(self):
        r=self.post(self.reagendo());self.assertEqual(r.status_code,200,r.content)
        self.cita.refresh_from_db();self.assertEqual(self.cita.psicologo_id,self.origen.pk);self.assertEqual(self.cita.hora,time(11))
        self.assertEqual(SincronizacionAgenda.objects.count(),1)
        e=EventoAuditoria.objects.get();self.assertEqual(e.usuario_id,self.admin.pk)
        self.assertEqual(e.detalles['antes']['citas'][0]['hora'],'10:00')
        self.assertEqual(e.detalles['despues']['citas'][0]['hora'],'11:00')

    def test_conflicto_bloqueo_y_festivo_no_modifican_cita(self):
        BloqueoAgendaPsicologo.objects.create(psicologo=self.origen,fecha=self.fecha,hora_inicio=time(11),hora_fin=time(12),motivo='Reunión')
        self.assertEqual(self.post(self.reagendo()).status_code,409)
        DiaFestivo.objects.create(fecha=self.fecha)
        self.assertEqual(self.post(self.reagendo('13:00')).status_code,409)
        self.cita.refresh_from_db();self.assertEqual(self.cita.hora,time(10));self.assertFalse(OperacionAgenda.objects.exists())

    def test_version_obsoleta_y_motivo_vacio(self):
        d=self.reagendo();d['version']='vieja'
        self.assertEqual(self.post(d).status_code,409)
        self.assertEqual(self.post({**self.reagendo(),'motivo':''}).status_code,400)
        self.assertFalse(EventoAuditoria.objects.exists())

    def test_idempotencia_no_repite_eventos(self):
        d={**self.reagendo(),'clave':str(uuid4())}
        self.assertEqual(self.post(d).status_code,200);self.assertEqual(self.post(d).status_code,200)
        self.assertEqual(EventoAuditoria.objects.count(),1);self.assertEqual(SincronizacionAgenda.objects.count(),1)
        self.assertEqual(self.post({**d,'motivo':'Un motivo distinto'}).status_code,409)

    def test_transferencia_incluye_reserva_y_no_toca_otro_servicio_ni_historia(self):
        pendiente=self.crear_cita(hora=time(11),estado='Pendiente')
        pago=PagoCitaPayPal.objects.create(cita=pendiente,monto='110',orden='ORDEN',vence=timezone.now()+timedelta(minutes=15),captura_iniciada=True)
        otra=self.crear_cita(hora=time(12),tipo_sesion='pareja')
        pasada=self.crear_cita(fecha=self.fecha-timedelta(days=20),estado='Completada')
        pago.refresh_from_db()
        plan_before=(pago.orden,pago.monto,pago.vence,pago.captura_iniciada)
        r=self.post(self.traslado());self.assertEqual(r.status_code,200,r.content)
        self.cita.refresh_from_db();pendiente.refresh_from_db();otra.refresh_from_db();pasada.refresh_from_db();pago.refresh_from_db();self.tratamiento.refresh_from_db()
        self.assertEqual(self.cita.psicologo_id,self.destino.pk);self.assertEqual(pendiente.psicologo_id,self.destino.pk)
        self.assertEqual(otra.psicologo_id,self.origen.pk);self.assertEqual(pasada.psicologo_id,self.origen.pk)
        self.assertEqual(self.tratamiento.psicologo_asignado_id,self.destino.pk)
        self.assertEqual(plan_before,(pago.orden,pago.monto,pago.vence,pago.captura_iniciada))

    def test_transferencia_completa_plan_mensual_preserva_referencias(self):
        self.cita.delete()
        plan=PlanMensual.objects.create(paciente=self.paciente,creado_por=self.admin,tipo_servicio='individual',modalidad='Presencial',monto='440',origen='paypal',referencia='PLAN-ORDEN',vence=timezone.now()+timedelta(minutes=15))
        for i in range(4):self.crear_cita(fecha=self.fecha+timedelta(days=7*i),plan_mensual=plan,estado='Pendiente')
        r=self.post(self.traslado());self.assertEqual(r.status_code,200,r.content)
        self.assertEqual(plan.citas.filter(psicologo=self.destino).count(),4)
        plan.refresh_from_db();self.assertEqual(plan.referencia,'PLAN-ORDEN');self.assertEqual(plan.estado,'reservado')

    def test_un_conflicto_revierte_todo_el_traslado(self):
        self.crear_cita(hora=time(11))
        BloqueoAgendaPsicologo.objects.create(psicologo=self.destino,fecha=self.fecha,hora_inicio=time(11),hora_fin=time(12),motivo='Bloqueado')
        r=self.post(self.traslado());self.assertEqual(r.status_code,409,r.content)
        self.assertFalse(Cita.objects.filter(psicologo=self.destino).exists())
        self.tratamiento.refresh_from_db();self.assertEqual(self.tratamiento.psicologo_asignado_id,self.origen.pk)
        self.assertFalse(OperacionAgenda.objects.exists())

    def test_candidato_cerrado_no_es_elegible_y_se_revalida(self):
        d=self.traslado();self.destino.esta_activo=False;self.destino.save()
        self.assertEqual(self.post(d).status_code,409)
        d['version_psicologo']=agenda.version(agenda.config(self.destino))
        self.assertEqual(self.post(d).status_code,409)

    def test_genero_exige_excepcion_explicita(self):
        self.destino.genero='Hombre';self.destino.save()
        CuestionarioRegistro.objects.create(paciente=self.paciente,flujo_elegido='individual',respuestas={'preferencia_terapeuta':'Mujer'})
        d=self.traslado();self.assertEqual(self.post(d).status_code,400)
        r=self.post({**d,'excepcion_genero':True});self.assertEqual(r.status_code,200,r.content)

    def test_no_omite_reservas_nuevas_aparecidas_despues_de_previsualizar(self):
        d=self.traslado();self.crear_cita(hora=time(11),estado='Pendiente')
        self.assertEqual(self.post(d).status_code,409)

    def test_bloqueo_rechaza_citas_y_preserva_historial_al_eliminar(self):
        d={'accion':'bloqueo','psicologo':self.origen.pk,'fecha':str(self.fecha),'hora_inicio':'10:30','hora_fin':'11:30'}
        self.assertEqual(self.post(d).status_code,409)
        self.assertFalse(BloqueoAgendaPsicologo.objects.exists())
        r=self.post({**d,'hora_inicio':'14:00','hora_fin':'15:00'});self.assertEqual(r.status_code,200,r.content)
        b=BloqueoAgendaPsicologo.objects.get();v=agenda.version(agenda.bloque_ficha(b))
        self.assertEqual(self.post({'accion':'eliminar_bloqueo','id':b.pk,'psicologo':self.origen.pk,'version':v}).status_code,200)
        b.refresh_from_db();self.assertFalse(b.activo);self.assertEqual(EventoAuditoria.objects.count(),2)

    def test_config_solo_modifica_esta_activo(self):
        d={'accion':'configurar','psicologo':self.destino.pk,'version':agenda.version(agenda.config(self.destino)),'esta_activo':False,'modalidad_atencion':'En línea'}
        r=self.post(d);self.assertEqual(r.status_code,200,r.content)
        self.destino.refresh_from_db();self.assertFalse(self.destino.esta_activo);self.assertEqual(self.destino.modalidad_atencion,'Mixto')

    @patch('core.agenda_recepcion.carga',return_value={'alerta':True,'porcentaje':90})
    def test_carga_alta_exige_confirmacion_y_no_es_bloqueo_duro(self,carga):
        d=self.reagendo();d['acepta_carga']=False
        self.assertEqual(self.post(d).status_code,400)
        self.assertEqual(self.post({**d,'acepta_carga':True}).status_code,200)

    def test_excepcion_plazos_no_permite_destino_pasado(self):
        d=self.reagendo();d['citas'][0]['fecha']=str(timezone.localdate()-timedelta(days=1));d['excepcion_plazo']=True
        self.assertEqual(self.post(d).status_code,400)

    @patch('core.management.commands.sincronizar_agenda_recepcion.send_mail',return_value=1)
    def test_sync_presencial_idempotente_y_no_envia_dos_veces(self,enviar):
        from .management.commands.sincronizar_agenda_recepcion import sincronizar
        self.post(self.reagendo());t=SincronizacionAgenda.objects.get()
        self.assertEqual(sincronizar(t.pk),'completado');self.assertEqual(sincronizar(t.pk),'completado');enviar.assert_called_once()

    @patch('core.management.commands.sincronizar_agenda_recepcion.send_mail',side_effect=TimeoutError)
    def test_correo_incierto_no_se_reenvia_automaticamente(self,enviar):
        from .management.commands.sincronizar_agenda_recepcion import sincronizar
        self.post(self.reagendo());t=SincronizacionAgenda.objects.get()
        self.assertEqual(sincronizar(t.pk),'revision_manual');sincronizar(t.pk);enviar.assert_called_once()
        self.assertEqual(self.client.post(reverse('recepcion_agenda_reintentar',args=[t.pk]),json.dumps({'motivo':'Revisión de entrega pendiente'}),content_type='application/json').status_code,409)

    @patch('core.management.commands.sincronizar_agenda_recepcion.send_mail',side_effect=SystemExit)
    def test_interrupcion_del_worker_conserva_marca_durable_de_envio(self,enviar):
        from .management.commands.sincronizar_agenda_recepcion import sincronizar
        self.post(self.reagendo());t=SincronizacionAgenda.objects.get()
        with self.assertRaises(SystemExit):sincronizar(t.pk)
        t.refresh_from_db();self.assertTrue(t.correo_iniciado);self.assertEqual(t.estado,'enviando')
        t.proximo_intento=timezone.now()-timedelta(minutes=1);t.save()
        self.assertEqual(sincronizar(t.pk),'revision_manual');enviar.assert_called_once()

    @patch('core.views._servicio_calendar')
    @patch('core.management.commands.sincronizar_agenda_recepcion.send_mail',return_value=1)
    def test_calendar_actualiza_horario_y_asistentes_sin_cambiar_enlace(self,enviar,calendar):
        from .management.commands.sincronizar_agenda_recepcion import sincronizar
        self.cita.modalidad='En línea';self.cita.id_evento_google='evento';self.cita.enlace_meet='https://meet.google.com/abc-defg-hij';self.cita.save()
        calendar.return_value.events.return_value.patch.return_value.execute.return_value={}
        r=self.post(self.traslado());self.assertEqual(r.status_code,200,r.content)
        t=SincronizacionAgenda.objects.get();self.assertEqual(sincronizar(t.pk),'completado')
        body=calendar.return_value.events.return_value.patch.call_args.kwargs['body']
        self.assertEqual({a['email'] for a in body['attendees']},{self.paciente.email,self.destino.usuario.email})
        self.cita.refresh_from_db();self.assertEqual(self.cita.enlace_meet,'https://meet.google.com/abc-defg-hij')

    @patch('core.views._servicio_calendar',side_effect=RuntimeError)
    def test_calendar_falla_sin_revertir_el_cambio_y_puede_reintentarse(self,calendar):
        from .management.commands.sincronizar_agenda_recepcion import sincronizar
        self.cita.modalidad='En línea';self.cita.id_evento_google='evento';self.cita.save()
        r=self.post(self.reagendo());self.assertEqual(r.status_code,200,r.content)
        t=SincronizacionAgenda.objects.get();self.assertEqual(sincronizar(t.pk),'pendiente')
        self.cita.refresh_from_db();self.assertEqual(self.cita.hora,time(11))
        t.refresh_from_db();self.assertFalse(t.correo_iniciado)

    def test_historial_filtrable_solo_lectura(self):
        self.post(self.reagendo())
        r=self.client.get(reverse('recepcion_agenda_historial'),{'paciente':self.paciente.pk})
        self.assertEqual(len(r.json()['eventos']),1)
        self.assertEqual(self.client.post(reverse('recepcion_agenda_historial')).status_code,405)


@skipUnless(connection.vendor == 'mysql', 'Requiere MySQL local desechable; nunca Railway.')
class ConcurrenciaAgendaTests(TransactionTestCase):
    def test_dos_reagendaciones_con_la_misma_version_no_se_sobrescriben(self):
        admin=User.objects.create_superuser('admin-agenda')
        paciente=User.objects.create_user('concurrente')
        doc=PerfilPsicologo.objects.create(usuario=User.objects.create_user('doc-concurrente'),cedula_profesional='concurrente',modalidad_atencion='Presencial')
        fecha=timezone.localdate()+timedelta(days=4)
        EsquemaHorarioPsicologo.objects.create(psicologo=doc,fecha_inicio=fecha,fecha_fin=fecha,hora_inicio=time(9),hora_fin=time(17))
        cita=Cita.objects.create(paciente=paciente,psicologo=doc,fecha=fecha,hora=time(10),modalidad='Presencial')
        v=agenda.version(agenda.ficha(cita));barrera=Barrier(2)
        def mover(hora):
            close_old_connections()
            try:
                r=RequestFactory().post('/recep-59/agenda/ejecutar/',json.dumps({'clave':str(uuid4()),'accion':'reagendar','cita':cita.pk,'version':v,
                    'motivo':'Cambio simultáneo solicitado','acepta_carga':True,'citas':[{'id':cita.pk,'fecha':str(fecha),'hora':hora}]}),content_type='application/json')
                r.user=admin;barrera.wait(timeout=10)
                return agenda.ejecutar(r).status_code
            finally:close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            estados=list(pool.map(mover,['11:00','12:00']))
        self.assertEqual(sorted(estados),[200,409])
        self.assertEqual(EventoAuditoria.objects.count(),1)
