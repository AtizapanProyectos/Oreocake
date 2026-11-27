(() => {
  'use strict';
  const app = document.getElementById('agenda-app'), $ = id => document.getElementById(id);
  let doctores = [], accion = null, solicitud = {}, preparacion = Promise.resolve(), generacion = 0;
  const el = (tag, text, cls) => { const e = document.createElement(tag); if (text !== undefined) e.textContent = text; if (cls) e.className = cls; return e; };
  const boton = (text, fn, cls = 'secondary') => { const b = el('button', text, cls); b.type = 'button'; b.addEventListener('click', fn); return b; };
  const hoy = () => new Intl.DateTimeFormat('en-CA', {timeZone: 'America/Mexico_City', year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());
  const sumar = (fecha, n) => { const d = new Date(fecha + 'T12:00:00Z'); d.setUTCDate(d.getUTCDate()+n); return d.toISOString().slice(0,10); };
  const fechaLabel = f => new Date(f+'T12:00:00Z').toLocaleDateString('es-MX', {timeZone:'UTC', weekday:'short',day:'numeric',month:'short'});
  const csrf = () => (document.cookie.match(/(?:^|; )csrftoken=([^;]*)/) || [,''])[1];
  async function api(url, params, data) {
    const u = new URL(url, location.origin); if (params) Object.entries(params).forEach(([k,v]) => u.searchParams.set(k,v));
    const r = await fetch(u, data ? {method:'POST', headers:{'Content-Type':'application/json','X-CSRFToken':decodeURIComponent(csrf())}, body:JSON.stringify(data)} : {cache:'no-store'});
    const result = await r.json(); if (!r.ok || result.status !== 'success') throw new Error(result.message || 'No se pudo completar la consulta.'); return result;
  }
  function campo(text, tipo, valor, id) { const l = el('label',text), i=el('input'); i.type=tipo; i.value=valor || ''; if(id)i.id=id; l.append(i); return {l,i}; }
  function checkbox(text, id) { const l=el('label',undefined,'check'),i=el('input'); i.type='checkbox';i.id=id;l.append(i,document.createTextNode(text));return l; }
  function abrir(titulo, tipo, data={}) { generacion++; accion=tipo; solicitud={...data}; $('titulo-editor').textContent=titulo; $('campos-editor').replaceChildren(); $('motivo').value='';$('error-editor').textContent='';$('confirmacion-editor').hidden=!tipo;$('editor').showModal(); }
  function advertencias(genero=false) { const c=$('campos-editor'); c.append(checkbox('Confirmo una excepción de plazos si el horario lo requiere.','excepcion_plazo'),checkbox('He revisado la carga y autorizo el cambio si alcanza el 85 %.','acepta_carga')); if(genero)c.append(checkbox('Confirmo la excepción a la preferencia de género registrada.','excepcion_genero')); }
  function contacto(padre, titulo, valor, protocolo) { const p=el('p'); p.append(el('strong',titulo+': ')); if(valor){const a=el('a',valor);a.href=protocolo+valor;p.append(a);}else p.append('No registrado');padre.append(p); }
  function citaCard(c, doc) {
    const box=el('article',undefined,'cita');box.append(el('strong',c.hora+' · '+c.paciente),el('p',c.servicio+' · '+c.estado));
    const contactos=el('div',undefined,'contact');contacto(contactos,'Celular',c.telefono,'tel:');contacto(contactos,'Correo',c.email,'mailto:');contacto(contactos,'Emergencia',c.emergencia,'tel:');box.append(contactos);
    const acciones=el('div',undefined,'actions');
    if(c.estado==='Confirmada')acciones.append(boton('Reagendar',()=>reagendar(c,doc)));
    if(['Confirmada','Pendiente'].includes(c.estado))acciones.append(boton('Cambiar Psicólogo',()=>transferir(c)));
    if(c.sincronizacion && !['completado','sustituido'].includes(c.sincronizacion.estado)) {
      box.append(el('p','Sincronización: '+c.sincronizacion.estado+' · '+c.sincronizacion.mensaje,'sync'));
      acciones.append(boton('Revisar sincronización',()=>{abrir('Revisar sincronización','reintentar',{id:c.sincronizacion.id});$('campos-editor').append(el('p',c.sincronizacion.mensaje||'Se reintentará la sincronización pendiente.'),checkbox('Revisé la bandeja de enviados y confirmé que el aviso NO fue entregado; autorizo su reenvío si estaba incierto.','no_entregado_verificado'));}));
    }
    box.append(acciones);return box;
  }
  function dia(doc, f) {
    const gr=el('div',undefined,'grid');
    ['Presencial','En línea'].forEach(m=>{ const col=el('section',undefined,'column');col.append(el('h3',m==='En línea'?'Consultas virtuales':'Consultas presenciales'));
      const citas=doc.citas.filter(c=>c.fecha===f&&c.modalidad===m);if(!citas.length)col.append(el('p','Sin citas programadas.','empty'));citas.forEach(c=>col.append(citaCard(c,doc)));
      col.append(el('h3','Horas libres'));const free=el('div',undefined,'free'), horas=doc.libres[m]?.[f]||[];horas.forEach(h=>free.append(el('span',h)));if(!horas.length)free.append(el('p','Sin horarios libres para el servicio seleccionado.','empty'));col.append(free);gr.append(col);
    });return gr;
  }
  function render(desde,hasta) {
    const contenido=$('contenido');contenido.replaceChildren();
    if(!doctores.length)contenido.append(el('p','No hay psicólogos para los filtros seleccionados.'));
    doctores.forEach(doc=>{const card=el('section',undefined,'doctor'),head=el('div',undefined,'doctor-head');head.append(el('h2',doc.nombre),el('span',doc.esta_activo?'Acepta nuevos':'Solo pacientes asignados',doc.esta_activo?'badge':'badge off'));
      head.append(boton('Configurar admisión',()=>configurar(doc)),boton('Crear bloqueo',()=>bloqueo(doc)),boton('Historial',()=>historial(doc.id)));card.append(head);
      const c=doc.carga;card.append(el('p',doc.modalidad_atencion+' · '+doc.pacientes_activos+' pacientes activos · Semana '+fechaLabel(c.semana)+': '+(c.porcentaje===null?'sin capacidad configurada':c.porcentaje+' % de ocupación ('+c.ocupados+'/'+c.capacidad+')'),'metrics'));
      if(c.alerta)card.append(el('p','Carga elevada: revisa la ocupación antes de trasladar pacientes.','badge warning'));
      if(desde===hasta)card.append(dia(doc,desde));else{const semana=el('div',undefined,'week');for(let f=desde;f<=hasta;f=sumar(f,1)){const celda=el('section',undefined,'week-day');celda.append(el('h3',fechaLabel(f)),dia(doc,f));semana.append(celda);}card.append(semana);}
      const bloques=el('details'), summary=el('summary','Bloqueos del intervalo ('+doc.bloqueos.length+')');bloques.append(summary);
      doc.bloqueos.forEach(b=>{const row=el('p',fechaLabel(b.fecha)+' · '+b.hora_inicio+'–'+b.hora_fin+' · '+b.motivo+(b.activo?'':' · eliminado'));if(b.activo)row.append(' ',boton('Editar',()=>bloqueo(doc,b)),boton('Eliminar',()=>eliminar(doc,b)));bloques.append(row);});card.append(bloques);contenido.append(card);
    });
  }
  async function cargar() { $('estado').textContent='Consultando agendas…';try{let desde=$('fecha').value,hasta=desde;if($('vista').value==='semana'){const weekday=new Date(desde+'T12:00:00Z').getUTCDay();desde=sumar(desde,-((weekday+6)%7));hasta=sumar(desde,6);}
    const res=await api(app.dataset.datos,{desde,hasta,psicologo:$('psicologo').value,modalidad:$('modalidad').value,servicio:$('servicio').value,canceladas:$('canceladas').checked?'1':'0'});doctores=res.psicologos;
    if($('psicologo').options.length===1)doctores.forEach(d=>{const o=el('option',d.nombre);o.value=d.id;$('psicologo').append(o);});render(desde,hasta);$('estado').textContent='Agenda actualizada. '+fechaLabel(desde)+(desde!==hasta?' — '+fechaLabel(hasta):'');
  }catch(e){$('estado').textContent=e.message;} }
  function configurar(doc){abrir('Admisión · '+doc.nombre,'configurar',{psicologo:doc.id,version:doc.version});const c=$('campos-editor');c.append(el('p','Este cambio afecta exclusivamente a consultantes nuevos. Los pacientes actuales pueden seguir agendando y reagendando con su terapeuta.'));
    const l=checkbox('Aceptando nuevos pacientes','acepta_nuevos');l.querySelector('input').checked=doc.esta_activo;c.append(l,el('p','Modalidad de atención: '+doc.modalidad_atencion+' (compatibilidad real; no es un candado de admisión).'));
  }
  function bloqueo(doc,b){abrir(b?'Editar bloqueo':'Crear bloqueo','bloqueo',{psicologo:doc.id,id:b?.id,version:b?.version});const box=$('campos-editor');box.append(el('p','Si intersecta una cita o reserva, no se guardará. Primero debes reagendar las citas afectadas.'));const row=el('div',undefined,'fields');[['Fecha','date',b?.fecha||$('fecha').value,'bloque_fecha'],['Desde','time',b?.hora_inicio||'09:00','bloque_desde'],['Hasta','time',b?.hora_fin||'10:00','bloque_hasta']].forEach(a=>{const f=campo(...a);f.i.required=true;row.append(f.l);});box.append(row);if(b)$('motivo').value=b.motivo; }
  function eliminar(doc,b){abrir('Eliminar bloqueo','eliminar_bloqueo',{psicologo:doc.id,id:b.id,version:b.version});$('campos-editor').append(el('p',b.fecha+' · '+b.hora_inicio+'–'+b.hora_fin+' · '+b.motivo),el('p','Se conservará el registro y su historial.'));}
  async function filaHorario(c, target, traslado, gen) {
    const box=el('section',undefined,'candidate'),row=el('div',undefined,'fields');box.dataset.cita=c.id;box.append(el('strong','Cita #'+c.id+' · '+c.modalidad+' · '+c.estado+(c.plan_id?' · Plan #'+c.plan_id:'')));
    const f=campo('Nueva fecha','date',c.fecha);f.i.required=true;const l=el('label','Horario disponible'),s=el('select');s.required=true;l.append(s);row.append(f.l,l);box.append(row);
    async function actualizar(){s.replaceChildren(el('option','Consultando…'));s.disabled=true;try{const r=await api(app.dataset.disponibilidad,{cita:c.id,psicologo:target,traslado:traslado?'1':'0',desde:f.i.value,hasta:f.i.value});if(gen!==generacion)return;s.replaceChildren();const vacio=el('option','Selecciona un horario');vacio.value='';s.append(vacio);(r.dias[f.i.value]||[]).forEach(h=>{const o=el('option',h);o.value=h;s.append(o);});if([...s.options].some(o=>o.value===c.hora))s.value=c.hora;s.disabled=false;}catch(e){s.replaceChildren(el('option','No disponible'));s.options[0].value='';$('error-editor').textContent=e.message;s.disabled=false;}}
    f.i.addEventListener('change',()=>{preparacion=actualizar();});box.leer=()=>({id:c.id,fecha:f.i.value,hora:s.value});$('campos-editor').append(box);await actualizar();return box;
  }
  function reagendar(c,doc){abrir('Reagendar con '+doc.nombre,'reagendar',{cita:c.id,version:c.version});$('campos-editor').append(el('p','Horario actual: '+c.fecha+' '+c.hora+'. Se conserva terapeuta, servicio y modalidad.'));advertencias();preparacion=filaHorario(c,doc.id,false,generacion);}
  function transferir(c){abrir('Cambiar Psicólogo','transferir',{paciente:c.paciente_id,servicio:c.servicio,modalidad:c.modalidad});const box=$('campos-editor'),row=el('div',undefined,'fields'),g=el('label','Filtrar género'),s=el('select');['Todos','Mujer','Hombre'].forEach(v=>{const o=el('option',v);o.value=v==='Todos'?'':v;s.append(o);});g.append(s);row.append(g,boton('Buscar compatibles',()=>{preparacion=buscar();}));box.append(el('p','Se trasladan el tratamiento y todas las citas futuras de este servicio, incluidas reservas con pago pendiente. Revisa cada destino.'),row);const lista=el('div');box.append(lista);
    async function buscar(){try{const data=await api(app.dataset.candidatos,{paciente:c.paciente_id,servicio:c.servicio,genero:s.value,modalidad:c.modalidad,desde:hoy(),hasta:sumar(hoy(),89)});solicitud.version=data.version;lista.replaceChildren();data.psicologos.forEach(doc=>{const item=el('section',undefined,'candidate');item.append(el('strong',doc.nombre+' · '+doc.genero),el('p',doc.razones.join(' ')));if(doc.requiere_excepcion_genero)item.append(el('p','Requiere excepción de preferencia de género.','sync'));item.append(el('p','Ocupación semanal: '+(doc.carga.porcentaje===null?'Sin capacidad':doc.carga.porcentaje+' %')));const elegir=boton('Seleccionar y revisar citas',async()=>{solicitud.psicologo=doc.id;solicitud.version_psicologo=doc.version;box.replaceChildren(el('h3','Destino: '+doc.nombre));advertencias(doc.requiere_excepcion_genero);const gen=generacion;preparacion=Promise.all(data.citas.map(ci=>filaHorario(ci,doc.id,true,gen)));await preparacion;if(!data.citas.length)box.append(el('p','Sin citas futuras. Se cambiará únicamente el tratamiento de este servicio.'));});elegir.disabled=!doc.elegible||doc.id===data.asignado;item.append(elegir);lista.append(item);});}catch(e){$('error-editor').textContent=e.message;}}
    preparacion=buscar();
  }
  async function historial(psicologo=''){abrir('Historial de cambios',null);try{const r=await api(app.dataset.historial,{psicologo});if(!r.eventos.length)$('campos-editor').append(el('p','Sin cambios registrados desde este módulo.'));r.eventos.forEach(e=>{const d=el('details');d.append(el('summary',new Date(e.fecha).toLocaleString('es-MX',{timeZone:'America/Mexico_City'})+' · '+e.usuario+' · '+e.accion),el('pre',JSON.stringify(e.detalles,null,2),'history'));$('campos-editor').append(d);});}catch(e){$('error-editor').textContent=e.message;}}
  $('form-editor').addEventListener('submit',async e=>{e.preventDefault();$('guardar').disabled=true;try{await preparacion;const data={...solicitud,accion,clave:crypto.randomUUID(),motivo:$('motivo').value};if(accion==='configurar')data.esta_activo=$('acepta_nuevos').checked;
    if(['reagendar','transferir'].includes(accion)){if(accion==='transferir'&&!data.psicologo)throw new Error('Selecciona primero el psicólogo de destino.');data.citas=[...$('campos-editor').querySelectorAll('[data-cita]')].map(n=>n.leer());if(data.citas.some(c=>!c.fecha||!c.hora))throw new Error('Selecciona un horario disponible para cada cita.');['excepcion_plazo','excepcion_genero','acepta_carga'].forEach(k=>data[k]=$(k)?.checked||false);}
    if(accion==='bloqueo'){data.fecha=$('bloque_fecha').value;data.hora_inicio=$('bloque_desde').value;data.hora_fin=$('bloque_hasta').value;}
    if(!window.confirm('¿Confirmas el cambio mostrado? Se registrará tu usuario, el motivo y los valores anteriores y nuevos.'))return;
    // Conservar la clave si se pierde la respuesta: un reenvío idéntico no repite la acción.
    const hash=JSON.stringify({...data,clave:null});if($('form-editor').dataset.solicitud===hash)data.clave=$('form-editor').dataset.clave;else{$('form-editor').dataset.solicitud=hash;$('form-editor').dataset.clave=data.clave;}
    if(accion==='reintentar'){data.no_entregado_verificado=$('no_entregado_verificado').checked;await api('/recep-59/agenda/sincronizacion/'+data.id+'/',null,data);}else{await api(app.dataset.ejecutar,null,data);}$('editor').close();await cargar();$('estado').textContent+=' Cambio guardado; los avisos y Calendar se sincronizan por separado.';
  }catch(err){$('error-editor').textContent=err.message;}finally{$('guardar').disabled=false;}});
  $('cerrar').addEventListener('click',()=>{generacion++;$('editor').close();});$('filtros').addEventListener('submit',e=>{e.preventDefault();cargar();});$('historial').addEventListener('click',()=>historial($('psicologo').value));$('fecha').value=hoy();cargar();
})();
