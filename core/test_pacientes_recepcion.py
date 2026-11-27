import json
from datetime import time, timedelta
from uuid import uuid4

from django.contrib.auth.models import User
from django.db import connection
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from . import agenda_recepcion as agenda
from .models import (BloqueoAgendaPsicologo, Cita, CuestionarioRegistro, DiaLibrePsicologo,
                     EsquemaHorarioPsicologo, EventoAuditoria, HistorialClinico,
                     OperacionAgenda, PagoCitaPayPal, PerfilPsicologo, SincronizacionAgenda,
                     TratamientoPaciente, UsuarioPerfil)


class PacientesRecepcionTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser('admin', email='admin@example.test')
        self.paciente = User.objects.create_user('ana', first_name='Ana', email='ana@example.test')
        self.fecha = timezone.localdate() + timedelta(days=7)
        self.origen = self.doctor('Origen')
        self.destino = self.doctor('Destino')
        self.perfil = UsuarioPerfil.objects.create(usuario=self.paciente, nombre='Ana Pérez', telefono='5512345678', psicologo_asignado=self.origen)
        self.tratamiento = TratamientoPaciente.objects.create(paciente=self.paciente, tipo_servicio='individual', psicologo_asignado=self.origen, historia_clinica='Secreto clínico')
        self.cita = self.crear_cita()
        self.client.force_login(self.admin)

    def doctor(self, nombre):
        d = PerfilPsicologo.objects.create(usuario=User.objects.create_user(nombre, first_name=nombre), cedula_profesional=nombre,
            genero='Mujer', esta_activo=True, modalidad_atencion='Mixto', atiende_pareja=True, atiende_familiar=True)
        EsquemaHorarioPsicologo.objects.create(psicologo=d, fecha_inicio=self.fecha-timedelta(days=7), fecha_fin=self.fecha+timedelta(days=180),
            hora_inicio=time(9), hora_fin=time(17), hora_comida_inicio=time(13), hora_comida_fin=time(14))
        return d

    def crear_cita(self, **extra):
        return Cita.objects.create(**{'paciente':self.paciente, 'psicologo':self.origen,
            'fecha':self.fecha, 'hora':time(10), 'modalidad':'Presencial', 'estado':'Confirmada', **extra})

    def candidatos(self):
        r = self.client.get(reverse('recepcion_pacientes_candidatos'), {'paciente':self.paciente.pk,'desde':str(self.fecha),'hasta':str(self.fecha+timedelta(days=6))})
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()

    def traslado(self):
        r = self.candidatos()
        d = next(d for d in r['psicologos'] if d['id']==self.destino.pk)
        return {'accion':'reasignar_paciente','paciente':self.paciente.pk,'psicologo':self.destino.pk,
            'version':r['version'],'version_psicologo':d['version'],
            'citas':[{'id':c['id'],'fecha':c['fecha'],'hora':c['hora']} for c in r['citas']]}

    def post(self, data, client=None):
        return (client or self.client).post(reverse('recepcion_agenda_ejecutar'), json.dumps({
            'clave':str(uuid4()),'motivo':'Cambio solicitado por paciente',**data}), content_type='application/json')

    def test_directorio_alfabetico_busqueda_filtros_y_sin_datos_clinicos(self):
        for nombre in ('Zulema', 'Beatriz'):
            u=User.objects.create_user(nombre, first_name=nombre)
            UsuarioPerfil.objects.create(usuario=u, nombre=nombre)
        url=reverse('recepcion_pacientes_datos')
        r=self.client.get(url);self.assertEqual(r.status_code,200,r.content)
        self.assertEqual([p['nombre'] for p in r.json()['pacientes']],['Ana Pérez','Beatriz','Zulema'])
        self.assertNotContains(r,'Secreto clínico')
        self.assertEqual(self.client.get(url,{'q':'Ana Pérez'}).json()['total'],1)
        self.assertEqual(self.client.get(url,{'q':'5512345678'}).json()['total'],1)
        self.assertEqual(self.client.get(url,{'q':"' OR 1=1 --"}).json()['total'],0)
        self.assertEqual(self.client.get(url,{'psicologo':self.origen.pk,'servicio':'individual','citas':'con'}).json()['total'],1)
        self.assertEqual(self.client.get(url,{'citas':'sin'}).json()['total'],2)
        self.assertEqual(self.client.get(reverse('recepcion_pacientes')).status_code,200)
        self.assertContains(self.client.get(reverse('recepcion_agenda')),reverse('recepcion_pacientes'))

    def test_paginacion_completa_y_fallback_inactivo_no_restaura_asignacion(self):
        for n in range(28):
            p=User.objects.create_user('p'+str(n));UsuarioPerfil.objects.create(usuario=p,nombre=f'Persona {n:02}')
        r=self.client.get(reverse('recepcion_pacientes_datos')).json()
        self.assertEqual(r['total'],29);self.assertEqual(len(r['pacientes']),24);self.assertEqual(r['paginas'],2)
        r2=self.client.get(reverse('recepcion_pacientes_datos'),{'pagina':2}).json()
        self.assertEqual(len(r2['pacientes']),5)
        self.assertFalse({p['id'] for p in r['pacientes']} & {p['id'] for p in r2['pacientes']})
        self.tratamiento.activo=False;self.tratamiento.save()
        self.assertEqual(self.client.get(reverse('recepcion_pacientes_datos'),{'psicologo':self.origen.pk}).json()['total'],0)

    def test_permisos_y_csrf(self):
        for user in (None,self.paciente,self.origen.usuario):
            c=Client()
            if user:c.force_login(user)
            for url in (reverse('recepcion_pacientes'),reverse('recepcion_pacientes_datos'),reverse('recepcion_pacientes_detalle',args=[self.paciente.pk]),reverse('recepcion_pacientes_candidatos'),reverse('recepcion_pacientes_disponibilidad')):
                self.assertEqual(c.get(url).status_code,403)
            self.assertEqual(self.post({'accion':'reasignar_paciente'},c).status_code,403)
        c=Client(enforce_csrf_checks=True);c.force_login(self.admin)
        self.assertEqual(self.post(self.traslado(),c).status_code,403)

    def test_detalle_citas_proximas_y_todas_sin_expediente(self):
        pasada=self.crear_cita(fecha=self.fecha-timedelta(days=30),estado='Completada')
        r=self.client.get(reverse('recepcion_pacientes_detalle',args=[self.paciente.pk]))
        self.assertEqual(r.status_code,200,r.content);self.assertEqual(len(r.json()['citas']),1)
        self.assertTrue(r.json()['citas'][0]['editable']);self.assertNotContains(r,'Secreto clínico')
        todas=self.client.get(reverse('recepcion_pacientes_detalle',args=[self.paciente.pk]),{'historial':1}).json()
        self.assertEqual(len(todas['citas']),2)
        self.assertFalse(next(c for c in todas['citas'] if c['id']==pasada.pk)['editable'])

    def test_traslado_multiservicio_atomico_autoria_y_auditoria(self):
        pareja=TratamientoPaciente.objects.create(paciente=self.paciente,tipo_servicio='pareja',psicologo_asignado=self.origen)
        cerrado=TratamientoPaciente.objects.create(paciente=self.paciente,tipo_servicio='familiar',psicologo_asignado=self.origen,activo=False)
        reserva=self.crear_cita(hora=time(11),tipo_sesion='pareja',estado='Pendiente')
        completada=self.crear_cita(fecha=self.fecha-timedelta(days=30),estado='Completada')
        nota=HistorialClinico.objects.create(paciente=self.paciente,psicologo=self.origen,cita=completada,notas_sesion='Autor original')
        r=self.post(self.traslado());self.assertEqual(r.status_code,200,r.content)
        for objeto in (self.tratamiento,pareja):
            objeto.refresh_from_db();self.assertEqual(objeto.psicologo_asignado_id,self.destino.pk)
        self.perfil.refresh_from_db();self.assertEqual(self.perfil.psicologo_asignado_id,self.destino.pk)
        for c in (self.cita,reserva):
            c.refresh_from_db();self.assertEqual(c.psicologo_id,self.destino.pk)
        for objeto in (cerrado,completada,nota):
            objeto.refresh_from_db()
        self.assertEqual(cerrado.psicologo_asignado_id,self.origen.pk)
        self.assertEqual(completada.psicologo_id,self.origen.pk);self.assertEqual(nota.psicologo_id,self.origen.pk)
        self.assertEqual(SincronizacionAgenda.objects.count(),2)
        e=EventoAuditoria.objects.get(accion='agenda.reasignar_paciente');self.assertEqual(e.usuario,self.admin)
        self.assertEqual(e.detalles['antes']['perfil'],self.origen.pk);self.assertEqual(e.detalles['despues']['perfil'],self.destino.pk)
        self.assertTrue(e.detalles['autores_clinicos_conservados'])

    def test_alertas_genero_admision_y_capacidad_son_informativas(self):
        self.destino.genero='Hombre';self.destino.esta_activo=False;self.destino.atiende_individual=False;self.destino.modalidad_atencion='En línea';self.destino.save()
        CuestionarioRegistro.objects.create(paciente=self.paciente,flujo_elegido='individual',respuestas={'preferencia_terapeuta':'Mujer'})
        d=next(d for d in self.candidatos()['psicologos'] if d['id']==self.destino.pk)
        self.assertGreaterEqual(len(d['avisos']),3)
        r=self.post(self.traslado());self.assertEqual(r.status_code,200,r.content)
        self.destino.refresh_from_db();self.assertFalse(self.destino.esta_activo);self.assertFalse(self.destino.atiende_individual)

    def test_conflicto_revierte_todas_las_referencias(self):
        self.crear_cita(hora=time(11),tipo_sesion='pareja')
        data=self.traslado()
        otro=User.objects.create_user('otro')
        self.crear_cita(paciente=otro,psicologo=self.destino,hora=time(11))
        r=self.post(data);self.assertEqual(r.status_code,409,r.content)
        self.perfil.refresh_from_db();self.tratamiento.refresh_from_db();self.cita.refresh_from_db()
        self.assertEqual(self.perfil.psicologo_asignado_id,self.origen.pk)
        self.assertEqual(self.tratamiento.psicologo_asignado_id,self.origen.pk);self.assertEqual(self.cita.psicologo_id,self.origen.pk)
        self.assertFalse(OperacionAgenda.objects.exists());self.assertFalse(SincronizacionAgenda.objects.exists())

    def test_version_citas_y_version_doctor_se_revalidan(self):
        data=self.traslado();self.crear_cita(hora=time(11))
        self.assertEqual(self.post(data).status_code,409)
        data=self.traslado();self.destino.esta_activo=False;self.destino.save()
        self.assertEqual(self.post(data).status_code,409)

    def test_idempotencia_no_repite_auditoria_ni_sincronizacion(self):
        data={**self.traslado(),'clave':str(uuid4())}
        self.assertEqual(self.post(data).status_code,200)
        self.assertEqual(self.post(data).status_code,200)
        self.assertEqual(EventoAuditoria.objects.count(),1);self.assertEqual(SincronizacionAgenda.objects.count(),1)
        self.assertEqual(self.post({**data,'motivo':'Un motivo diferente'}).status_code,409)

    def test_descanso_comida_bloqueo_y_cruce_rechazados(self):
        for escenario in ('descanso','comida','bloqueo','cruce'):
            with self.subTest(escenario=escenario):
                data=self.traslado()
                if escenario=='descanso':
                    DiaLibrePsicologo.objects.create(psicologo=self.destino,fecha=self.fecha,motivo='Descanso')
                elif escenario=='comida':
                    data['citas'][0]['hora']='13:00'
                elif escenario=='bloqueo':
                    BloqueoAgendaPsicologo.objects.create(psicologo=self.destino,fecha=self.fecha,hora_inicio=time(10),hora_fin=time(11),motivo='Bloqueo')
                else:
                    self.crear_cita(paciente=User.objects.create_user('cruce'),psicologo=self.destino,hora=time(10,30))
                r=self.post(data);self.assertEqual(r.status_code,409,r.content)
                DiaLibrePsicologo.objects.all().delete();BloqueoAgendaPsicologo.objects.all().delete()

    def test_disponibilidad_no_expone_cita_ajena_y_filtra_comida_cruces(self):
        otro=User.objects.create_user('otro')
        UsuarioPerfil.objects.create(usuario=otro,nombre='Otro')
        url=reverse('recepcion_pacientes_disponibilidad')
        params={'paciente':otro.pk,'cita':self.cita.pk,'psicologo':self.destino.pk,'desde':str(self.fecha),'hasta':str(self.fecha)}
        self.assertEqual(self.client.get(url,params).status_code,404)
        self.crear_cita(paciente=otro,psicologo=self.destino,hora=time(10,30))
        params['paciente']=self.paciente.pk
        r=self.client.get(url,params);self.assertEqual(r.status_code,200,r.content)
        hs=r.json()['dias'][str(self.fecha)]
        self.assertNotIn('10:00',hs);self.assertNotIn('11:00',hs);self.assertNotIn('13:00',hs);self.assertIn('12:00',hs)

    def test_sin_citas_y_sin_tratamientos_asigna_perfil_sin_inventar_servicios(self):
        self.cita.delete();self.tratamiento.delete()
        r=self.post(self.traslado());self.assertEqual(r.status_code,200,r.content)
        self.perfil.refresh_from_db();self.assertEqual(self.perfil.psicologo_asignado_id,self.destino.pk)
        self.assertFalse(TratamientoPaciente.objects.filter(paciente=self.paciente).exists())

    def test_reserva_pendiente_reagenda_sin_alterar_pago_ni_vencimiento(self):
        self.cita.estado='Pendiente';self.cita.save()
        pago=PagoCitaPayPal.objects.create(cita=self.cita,monto='110.00',orden='orden-prueba',vence=timezone.now()+timedelta(minutes=10))
        r=self.post({'accion':'reagendar','cita':self.cita.pk,'version':agenda.version(agenda.ficha(self.cita)),
            'citas':[{'id':self.cita.pk,'fecha':str(self.fecha),'hora':'11:00'}],'acepta_carga':True})
        self.assertEqual(r.status_code,200,r.content)
        self.cita.refresh_from_db();self.assertEqual(self.cita.estado,'Pendiente');self.assertEqual(self.cita.hora,time(11))
        anterior=pago.vence;pago.refresh_from_db();self.assertEqual(pago.vence,anterior);self.assertEqual(pago.orden,'orden-prueba')
        self.assertEqual(self.cita.psicologo_id,self.origen.pk)

    def test_reserva_vencida_no_se_traslada_ni_se_reagenda(self):
        self.cita.estado='Pendiente';self.cita.save()
        PagoCitaPayPal.objects.create(cita=self.cita,monto='110.00',vence=timezone.now()-timedelta(minutes=1))
        self.assertEqual(self.candidatos()['citas'],[])
        data={'accion':'reagendar','cita':self.cita.pk,'version':agenda.version(agenda.ficha(self.cita)),
            'citas':[{'id':self.cita.pk,'fecha':str(self.fecha),'hora':'11:00'}],'acepta_carga':True}
        self.assertEqual(self.post(data).status_code,404)
        self.assertEqual(self.post(self.traslado()).status_code,200)
        self.cita.refresh_from_db();self.assertEqual(self.cita.psicologo_id,self.origen.pk)

    def test_horario_comida_parcial_y_cita_del_paciente_con_otro_doctor(self):
        esquema=self.destino.esquemas_horarios.get();esquema.hora_comida_inicio=time(10,30);esquema.hora_comida_fin=time(11,30);esquema.save()
        self.assertEqual(self.post(self.traslado()).status_code,409)
        esquema.hora_comida_inicio=time(13);esquema.hora_comida_fin=time(14);esquema.save()
        # Una sesión completada sigue ocupando su intervalo aunque no sea trasladable.
        self.crear_cita(psicologo=self.doctor('Tercero'),hora=time(10,30),estado='Completada')
        self.assertEqual(self.post(self.traslado()).status_code,409)

    def test_no_reasignar_al_mismo_doctor_ni_guardar_sin_motivo(self):
        data=self.traslado();r=self.candidatos()
        origen=next(d for d in r['psicologos'] if d['id']==self.origen.pk)
        self.assertTrue(origen['ya_asignado'])
        self.assertEqual(self.post({**data,'psicologo':self.origen.pk,'version_psicologo':origen['version']}).status_code,400)
        self.assertEqual(self.post({**data,'motivo':''}).status_code,400)

    def test_busqueda_precarga_disponibilidad_sin_consultas_por_horario(self):
        for n in range(5):
            self.doctor('Equipo'+str(n))
        with CaptureQueriesContext(connection) as consultas:
            self.candidatos()
        self.assertLess(len(consultas),35)
