(() => {
  'use strict';
  const app = document.getElementById('agenda-app'), $ = id => document.getElementById(id);
  let doctores = [], accion = null, solicitud = {}, preparacion = Promise.resolve(), generacion = 0, consulta = 0, guardando = false;
  const el = (tag, text, cls) => { const e = document.createElement(tag); if (text !== undefined) e.textContent = text; if (cls) e.className = cls; return e; };
  const boton = (text, fn, cls = 'secondary') => { const b = el('button', text, cls); b.type = 'button'; b.addEventListener('click', fn); return b; };
  const hoy = () => new Intl.DateTimeFormat('en-CA', {timeZone: 'America/Mexico_City', year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());
  const sumar = (fecha, n) => { const d = new Date(fecha + 'T12:00:00Z'); d.setUTCDate(d.getUTCDate()+n); return d.toISOString().slice(0,10); };
  const fechaLabel = f => new Date(f+'T12:00:00Z').toLocaleDateString('es-MX', {timeZone:'UTC', weekday:'short',day:'numeric',month:'short'});
  const csrf = () => (document.cookie.match(/(?:^|; )csrftoken=([^;]*)/) || [,''])[1];
  async function api(url, params, data) {
    const u = new URL(url, location.origin); if (params) Object.entries(params).forEach(([k,v]) => u.searchParams.set(k,v));
    const r = await fetch(u, data ? {method:'POST', headers:{'Content-Type':'application/json','X-CSRFToken':decodeURIComponent(csrf())}, body:JSON.stringify(data)} : {cache:'no-store'});
    let result;try{result=await r.json();}catch(e){throw new Error('No pudimos obtener la respuesta del servidor. Intenta nuevamente.');} if (!r.ok || result.status !== 'success') throw new Error(result.message || 'No se pudo completar la consulta.'); return result;
  }
  function campo(text, tipo, valor, id) { const l = el('label',text), i=el('input'); i.type=tipo; i.value=valor || ''; if(id)i.id=id; l.append(i); return {l,i}; }
  function checkbox(text, id) { const l=el('label',undefined,'check'),i=el('input'); i.type='checkbox';i.id=id;l.append(i,document.createTextNode(text));return l; }
  function icono(nombre) { const svg=document.createElementNS('http://www.w3.org/2000/svg','svg'),use=document.createElementNS('http://www.w3.org/2000/svg','use');svg.setAttribute('class','ic');svg.setAttribute('aria-hidden','true');use.setAttribute('href','#i-'+nombre);svg.append(use);return svg; }
  function conIcono(texto, nombre, fn, cls='secondary') { const b=boton(texto,fn,cls);b.prepend(icono(nombre));return b; }
  function abrir(titulo, tipo, data={}) {
    generacion++;accion=tipo;solicitud={...data};preparacion=Promise.resolve();
    $('titulo-editor').textContent=titulo;$('campos-editor').replaceChildren();$('motivo').value='';$('motivo').required=!!tipo;
    $('error-editor').textContent='';$('confirmacion-editor').hidden=!tipo;$('guardar').disabled=!tipo;
    $('guardar').textContent=({bloqueo:'Guardar bloqueo',eliminar_bloqueo:'Eliminar bloqueo',configurar:'Guardar admisión',reagendar:'Confirmar reagendación',transferir:'Confirmar cambio',reintentar:'Reintentar sincronización'})[tipo]||'Guardar cambio';
    $('icono-editor').setAttribute('href','#i-'+(tipo==='configurar'?'users':tipo==='bloqueo'||tipo==='eliminar_bloqueo'?'lock':'cal'));
    if(!$('editor').open)$('editor').showModal();
  }
  function advertencias(genero=false) { const c=$('campos-editor'); c.append(checkbox('Confirmo una excepción de plazos si el horario lo requiere.','excepcion_plazo'),checkbox('He revisado la carga y autorizo el cambio si alcanza el 85 %.','acepta_carga')); if(genero)c.append(checkbox('Confirmo la excepción a la preferencia de género registrada.','excepcion_genero')); }
  function contacto(padre, titulo, valor, protocolo) { const p=el('p'); p.append(el('strong',titulo+': ')); if(valor){const a=el('a',valor);a.href=protocolo+valor;p.append(a);}else p.append('No registrado');padre.append(p); }
  function citaCard(c, doc) {
    const box=el('article',undefined,'cita'),row=el('div',undefined,'cita-heading'),tiempo=el('time',c.hora,'cita-time'),info=el('div',undefined,'cita-info');
    tiempo.dateTime=c.fecha+'T'+c.hora;info.append(el('strong',c.paciente,'cita-name'));
    const meta=el('div',undefined,'cita-meta');meta.append(el('span',c.modalidad==='En línea'?'En línea':'Presencial','badge '+(c.modalidad==='En línea'?'online':'presencial')),el('span',({individual:'Individual',pareja:'Pareja',familiar:'Familiar'})[c.servicio]||c.servicio),el('span','· '+c.estado));info.append(meta);
    const contactos=el('div',undefined,'contact');contacto(contactos,'Celular',c.telefono,'tel:');contacto(contactos,'Correo',c.email,'mailto:');
    if(c.emergencia){contacto(contactos,'Emergencia',c.emergencia,'tel:');contactos.lastElementChild.classList.add('emergency');}info.append(contactos);
    const opciones=el('details',undefined,'cita-options'),acciones=el('div',undefined,'actions');opciones.append(el('summary','Opciones de cita'));
    if(c.estado==='Confirmada')acciones.append(boton('Reagendar',()=>reagendar(c,doc)));
    if(['Confirmada','Pendiente'].includes(c.estado))acciones.append(boton('Cambiar psicólogo',()=>transferir(c)));
    if(c.sincronizacion&&!['completado','sustituido'].includes(c.sincronizacion.estado)){
      info.append(el('p','Actualización pendiente: '+c.sincronizacion.mensaje,'sync'));
      acciones.append(boton('Revisar sincronización',()=>{abrir('Revisar sincronización','reintentar',{id:c.sincronizacion.id});$('campos-editor').append(el('p',c.sincronizacion.mensaje||'Se reintentará la actualización pendiente.'),checkbox('Revisé la bandeja de enviados y confirmé que el aviso NO fue entregado; autorizo su reenvío si estaba incierto.','no_entregado_verificado'));}));
    }
    if(acciones.childElementCount){opciones.append(acciones);info.append(opciones);}row.append(tiempo,info);box.append(row);return box;
  }
  function dia(doc, fecha) {
    const lista=el('div',undefined,'day-sessions'),citas=doc.citas.filter(c=>c.fecha===fecha);
    lista.tabIndex=0;lista.setAttribute('role','region');lista.setAttribute('aria-label','Citas de '+doc.nombre+' · '+fechaLabel(fecha));
    if(!citas.length){const vacio=el('div',undefined,'empty-day');vacio.append(icono('cal'),el('p','Sin consultas programadas para este día. Puedes revisar sus horas libres o crear un bloqueo.'));lista.append(vacio);}
    citas.forEach(c=>lista.append(citaCard(c,doc)));return lista;
  }
  function horarios(doc){
    abrir('Horarios libres · '+doc.nombre,null);const box=$('campos-editor');box.append(el('p','Horarios de '+$('servicio').selectedOptions[0].textContent.toLowerCase()+'. Toca una hora para preparar un bloqueo.'));
    let cantidad=0;
    for(const [modalidad,dias] of Object.entries(doc.libres)){for(const [fecha,horas] of Object.entries(dias)){
      if(!horas.length)continue;cantidad+=horas.length;box.append(el('h3',fechaLabel(fecha)+' · '+modalidad));const lista=el('div',undefined,'slot-list');
      horas.forEach(hora=>{const [h,m]=hora.split(':').map(Number),fin=Math.min(1439,h*60+m+60),horaFin=String(Math.floor(fin/60)).padStart(2,'0')+':'+String(fin%60).padStart(2,'0');const b=boton(hora,()=>bloqueo(doc,null,{fecha,hora_inicio:hora,hora_fin:horaFin}));b.setAttribute('aria-label','Bloquear '+fecha+' a las '+hora);lista.append(b);});box.append(lista);
    }}
    if(!cantidad)box.append(el('p','No hay horarios libres para el servicio y las fechas consultadas.','empty'));
  }
  function verBloqueos(doc){
    abrir('Bloqueos · '+doc.nombre,null);const box=$('campos-editor'),gen=generacion,fecha=campo('Consultar bloqueos del día','date',$('fecha').value),lista=el('div');
    box.append(conIcono('Crear bloqueo','plus',()=>bloqueo(doc,null,{fecha:fecha.i.value})),fecha.l,lista);
    async function consultar(){lista.replaceChildren(el('p','Consultando bloqueos…','empty'));try{
      const res=await api(app.dataset.datos,{desde:fecha.i.value,hasta:fecha.i.value,psicologo:doc.id});if(gen!==generacion)return;
      const actual=res.psicologos[0];lista.replaceChildren();const bloques=actual?.bloqueos.filter(b=>b.activo)||[];
      if(!bloques.length)lista.append(el('p','Sin bloqueos activos para este día.','empty'));
      bloques.forEach(b=>{const row=el('section',undefined,'block-row');row.append(el('strong',b.hora_inicio+'–'+b.hora_fin),el('p',b.motivo));const acciones=el('div',undefined,'actions');acciones.append(boton('Editar',()=>bloqueo(actual,b)),boton('Eliminar',()=>eliminar(actual,b)));row.append(acciones);lista.append(row);});
      lista.append(conIcono('Ver historial de cambios','history',()=>historial(doc.id),'text-button'));
    }catch(e){if(gen===generacion)lista.replaceChildren(el('p',e.message,'sync'));}}
    fecha.i.addEventListener('change',consultar);consultar();
  }
  function render(desde,hasta) {
    const contenido=$('contenido');contenido.replaceChildren();
    if(!doctores.length)contenido.append(el('p','No encontramos psicólogos con estos filtros. Prueba con todo el equipo.','empty-results'));
    doctores.forEach(doc=>{
      const card=el('section',undefined,'doctor'),head=el('div',undefined,'doctor-head'),identidad=el('div',undefined,'doctor-identity'),titulo=el('h2',doc.nombre),iniciales=doc.nombre.trim().split(/\s+/).slice(0,2).map(p=>p[0]).join('').toUpperCase();
      titulo.id='doctor-titulo-'+doc.id;card.setAttribute('aria-labelledby',titulo.id);
      identidad.append(titulo,el('p',doc.modalidad_atencion==='Mixto'?'En línea y presencial':doc.modalidad_atencion,'doctor-modalidad'));
      const historia=conIcono('','history',()=>historial(doc.id),'icon-button');historia.setAttribute('aria-label','Historial de '+doc.nombre);historia.title='Historial de cambios';
      head.append(el('div',iniciales,'doctor-avatar'),identidad,historia);card.append(head);
      const total=doc.citas.length,presenciales=doc.citas.filter(c=>c.modalidad==='Presencial').length,virtuales=doc.citas.filter(c=>c.modalidad==='En línea').length,resumen=el('div',undefined,'doctor-summary');
      resumen.append(el('strong',total+' '+(total===1?'cita':'citas')+(desde===hasta?' este día':' esta semana')),el('span',presenciales+' presenciales · '+virtuales+' en línea'));card.append(resumen);
      const agenda=el('div',undefined,'doctor-citas');if(desde===hasta){agenda.append(dia(doc,desde));}else{
        for(let f=desde;f<=hasta;f=sumar(f,1)){const item=el('details',undefined,'week-day'),sum=el('summary');sum.append(el('span',fechaLabel(f)),el('span',doc.citas.filter(c=>c.fecha===f).length+' citas','badge off'));item.append(sum,dia(doc,f));if(f===$('fecha').value)item.open=true;agenda.append(item);}
      }card.append(agenda);
      const tools=el('div',undefined,'doctor-tools');tools.append(conIcono('Horas libres','clock',()=>horarios(doc),'text-button'),conIcono('Bloqueos ('+doc.bloqueos.filter(b=>b.activo).length+')','lock',()=>verBloqueos(doc),'text-button'));card.append(tools);
      const pie=el('div',undefined,'doctor-footer'),admitir=boton('',()=>configurar(doc),'secondary admission-button'),valor=el('span',doc.esta_activo?'Sí':'No','admission-state');valor.prepend(el('span',undefined,'admission-dot'+(doc.esta_activo?'':' off')));
      admitir.append(el('span','Aceptando nuevos pacientes'),valor);admitir.setAttribute('aria-haspopup','dialog');admitir.setAttribute('aria-label','Aceptando nuevos pacientes: '+(doc.esta_activo?'Sí':'No')+'. Configurar '+doc.nombre);
      pie.append(conIcono('Bloquear horario','lock',()=>bloqueo(doc),'block-button'),admitir);card.append(pie);
      if(doc.carga.alerta){const carga=el('p',undefined,'doctor-load');carga.append(el('span','Carga elevada','badge warning'),document.createTextNode(doc.carga.porcentaje+' % de ocupación semanal.'));card.append(carga);}contenido.append(card);
    });
  }
  async function cargar() {
    const revision=++consulta;$('estado').textContent='Consultando agendas…';$('estado').classList.remove('is-error');$('contenido').setAttribute('aria-busy','true');
    try{let desde=$('fecha').value,hasta=desde;if($('vista').value==='semana'){const weekday=new Date(desde+'T12:00:00Z').getUTCDay();desde=sumar(desde,-((weekday+6)%7));hasta=sumar(desde,6);}
      const res=await api(app.dataset.datos,{desde,hasta,psicologo:$('psicologo').value,modalidad:$('modalidad').value,servicio:$('servicio').value,canceladas:$('canceladas').checked?'1':'0'});if(revision!==consulta)return;doctores=res.psicologos;
      if($('psicologo').options.length===1)doctores.forEach(d=>{const o=el('option',d.nombre);o.value=d.id;$('psicologo').append(o);});render(desde,hasta);
      $('fecha-label').textContent=new Date($('fecha').value+'T12:00:00Z').toLocaleDateString('es-MX',{timeZone:'UTC',weekday:'long',day:'numeric',month:'long'});
      $('titulo-listado').textContent=desde!==hasta?'Las citas de la semana':desde===hoy()?'Las citas de hoy':'Las citas del '+fechaLabel(desde);
      $('estado').textContent=doctores.length+' '+(doctores.length===1?'psicólogo':'psicólogos')+' · Agenda actualizada';
    }catch(e){if(revision===consulta){$('estado').textContent=e.message+' Vuelve a consultar las agendas.';$('estado').classList.add('is-error');}}finally{if(revision===consulta)$('contenido').setAttribute('aria-busy','false');}
  }
  function configurar(doc){abrir('Admisión · '+doc.nombre,'configurar',{psicologo:doc.id,version:doc.version});const c=$('campos-editor');c.append(el('p','Este cambio afecta exclusivamente a consultantes nuevos. Los pacientes actuales pueden seguir agendando y reagendando con su terapeuta.'));
    const l=checkbox('Aceptando nuevos pacientes','acepta_nuevos');l.classList.add('admission-setting');l.querySelector('input').checked=doc.esta_activo;c.append(l,el('p','Modalidad de atención: '+doc.modalidad_atencion+'.'));
  }
  function bloqueo(doc,b,extra={}){abrir(b?'Editar bloqueo':'Crear bloqueo','bloqueo',{psicologo:doc.id,id:b?.id,version:b?.version});const box=$('campos-editor');box.append(el('p','Si intersecta una cita o reserva, no se guardará. Primero debes reagendar las citas afectadas.'));const row=el('div',undefined,'fields');[['Fecha','date',b?.fecha||extra.fecha||$('fecha').value,'bloque_fecha'],['Desde','time',b?.hora_inicio||extra.hora_inicio||'09:00','bloque_desde'],['Hasta','time',b?.hora_fin||extra.hora_fin||'10:00','bloque_hasta']].forEach(a=>{const f=campo(...a);f.i.required=true;row.append(f.l);});box.append(row);if(b)$('motivo').value=b.motivo; }
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
  $('form-editor').addEventListener('submit',async e=>{e.preventDefault();if(!accion||guardando)return;guardando=true;$('error-editor').textContent='';$('guardar').disabled=true;$('cerrar').disabled=true;try{await preparacion;const data={...solicitud,accion,clave:crypto.randomUUID(),motivo:$('motivo').value};if(accion==='configurar')data.esta_activo=$('acepta_nuevos').checked;
    if(['reagendar','transferir'].includes(accion)){if(accion==='transferir'&&!data.psicologo)throw new Error('Selecciona primero el psicólogo de destino.');data.citas=[...$('campos-editor').querySelectorAll('[data-cita]')].map(n=>n.leer());if(data.citas.some(c=>!c.fecha||!c.hora))throw new Error('Selecciona un horario disponible para cada cita.');['excepcion_plazo','excepcion_genero','acepta_carga'].forEach(k=>data[k]=$(k)?.checked||false);}
    if(accion==='bloqueo'){data.fecha=$('bloque_fecha').value;data.hora_inicio=$('bloque_desde').value;data.hora_fin=$('bloque_hasta').value;}
    // Conservar la clave si se pierde la respuesta: un reenvío idéntico no repite la acción.
    const hash=JSON.stringify({...data,clave:null});if($('form-editor').dataset.solicitud===hash)data.clave=$('form-editor').dataset.clave;else{$('form-editor').dataset.solicitud=hash;$('form-editor').dataset.clave=data.clave;}
    if(accion==='reintentar'){data.no_entregado_verificado=$('no_entregado_verificado').checked;await api('/recep-59/agenda/sincronizacion/'+data.id+'/',null,data);}else{await api(app.dataset.ejecutar,null,data);}if(accion==='bloqueo'){$('fecha').value=data.fecha;$('vista').value='dia';}$('editor').close();await cargar();$('estado').textContent+=' · Cambio guardado.';
  }catch(err){$('error-editor').textContent=err.message;}finally{guardando=false;$('guardar').disabled=false;$('cerrar').disabled=false;}});
  $('cerrar').addEventListener('click',()=>{if(guardando)return;generacion++;$('editor').close();});$('editor').addEventListener('cancel',e=>{if(guardando)e.preventDefault();else generacion++;});$('filtros').addEventListener('submit',e=>{e.preventDefault();cargar();});$('historial').addEventListener('click',()=>historial($('psicologo').value));$('dia-anterior').addEventListener('click',()=>{$('fecha').value=sumar($('fecha').value,$('vista').value==='semana'?-7:-1);cargar();});$('dia-siguiente').addEventListener('click',()=>{$('fecha').value=sumar($('fecha').value,$('vista').value==='semana'?7:1);cargar();});$('ir-hoy').addEventListener('click',()=>{$('fecha').value=hoy();$('vista').value='dia';cargar();});$('fecha').value=hoy();cargar();
})();
