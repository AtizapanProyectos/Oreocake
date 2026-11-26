(() => {
 'use strict';
 const root=document.getElementById('operations'); if(!root)return;
 const $=id=>document.getElementById(id), form=$('oc-filters');
 let page=1,pages=1,busy=false,loading=false,rows=[];
 let detailId=null, detailRequest=null;
 const node=(tag,text,cls)=>{const e=document.createElement(tag);e.textContent=text??'';if(cls)e.className=cls;return e;};
 const params=()=>{const p=new URLSearchParams(new FormData(form));p.set('page',page);return p;};
 const fmt=value=>value?new Date(value).toLocaleString('es-MX',{timeZone:'America/Mexico_City'}):'Sin registro';
 async function request(url,options){const r=await fetch(url,options);if(!r.headers.get('content-type')?.includes('application/json'))throw Error('La sesión o conexión no está disponible.');const d=await r.json();if(!r.ok)throw Error(d.mensaje||'No se pudo completar la solicitud.');return d;}
 async function submit(id, etapa=$('oc-stage').value){if(busy)return;busy=true;$('oc-batch').disabled=true;try{const p=id?new URLSearchParams():params();p.set('tipo',etapa);p.set('limite',25);const url=id?`/panel-admin/api/reintento-automatizacion/${id}/`:root.dataset.batch;const d=await request(url,{method:'POST',credentials:'same-origin',headers:{'X-CSRFToken':root.querySelector('[name=csrfmiddlewaretoken]').value},body:p});$('oc-message').textContent=d.mensaje;await refresh();if(detailId===id)await refreshDetail();}catch(e){$('oc-message').textContent=e.message;if(detailId===id)$('oc-detail-status').textContent=e.message;}finally{busy=false;$('oc-batch').disabled=false;}}
 function detail(row) {
   detailId = row.id;
   $('oc-detail-title').textContent = `Sesión #${row.id} · ${row.paciente}`;
   $('oc-detail-status').textContent = 'Consultando avance…';
   $('oc-detail-body').replaceChildren();
   $('oc-test-pdf').disabled = $('oc-test-full').disabled = true;
   $('oc-detail').showModal();
   refreshDetail();
 }
 async function refreshDetail() {
   const id = detailId;
   if (!id || detailRequest === id) return;
   detailRequest = id;
   try {
     const {fila, eventos} = await request(`${root.dataset.url}${id}/`, {cache: 'no-store'});
     if (detailId !== id) return;
     $('oc-detail-status').textContent = `${fila.estado_label} · ${fila.siguiente}${fila.mensaje ? ' · ' + fila.mensaje : ''}`;
     $('oc-test-pdf').disabled = $('oc-test-full').disabled = !fila.elegible || ['procesando', 'completado'].includes(fila.estado);
     const pasos = fila.pasos.map(paso => {
       const tarjeta = node('article');
       tarjeta.append(node('strong', paso.etiqueta), node('p', `${paso.estado_label} · ${paso.intentos} intento(s)`));
       if (paso.mensaje) tarjeta.append(node('p', paso.mensaje));
       if (paso.error) {
         const siguiente = paso.proximo ? `Próximo intento: ${fmt(paso.proximo)}` : 'Sin reintento automático programado';
         tarjeta.append(node('p', `${paso.error} · ${siguiente}`), node('p', paso.accion));
       }
       return tarjeta;
     });
     const historial = eventos.map(evento => node('p', `${fmt(evento.inicio)} · ${evento.etapa} · ${evento.estado} · ${evento.responsable}${evento.duracion_ms === null ? '' : ` · ${evento.duracion_ms} ms`}${evento.error ? ' · ' + evento.error : ''}`));
     $('oc-detail-body').replaceChildren(
       node('p', `Asistencia: ${fila.asistencia} · Confirmación: ${fila.confirmacion}`),
       ...pasos, node('h3', 'Historial de ejecuciones'),
       ...(historial.length ? historial : [node('p', 'Todavía no hay intentos registrados.')])
     );
   } catch (error) {
     if (detailId === id) $('oc-detail-status').textContent = error.message;
   } finally {
     if (detailRequest === id) detailRequest = null;
   }
 }
 async function refresh(){if(loading)return;loading=true;try{const d=await request(root.dataset.url+'?'+params());rows=d.filas;pages=d.paginas;page=d.pagina;$('oc-sync').textContent=`Actualizado ${fmt(d.actualizado)}`;$('oc-capacity').textContent=d.activo?`Capacidad: ${d.capacidad} · ${d.ejecucion_activa?'Ejecución en curso':'En espera · último intento: '+fmt(d.ultimo_intento)}`:'Motor desactivado';$('oc-total').textContent=`· ${d.total}`;$('oc-page').textContent=`Página ${page} de ${pages}`;$('oc-prev').disabled=page<=1;$('oc-next').disabled=page>=pages;
 const k=d.kpi;$('oc-kpis').replaceChildren();[['Etapas terminadas hoy',k.ejecuciones],['Etapas exitosas',k.exitos],['Intentos con incidencia',k.incidencias],['Tasa de éxito',k.tasa===null?'Sin datos':k.tasa+'%'],['Duración promedio',k.duracion===null?'Sin datos':(k.duracion/1000).toFixed(1)+' s'],['Resolución promedio',k.resolucion===null?'Sin datos':(k.resolucion/60000).toFixed(1)+' min']].forEach(([label,value])=>{const a=node('article');a.append(node('small',label),node('strong',value));$('oc-kpis').append(a);});
 $('oc-rows').replaceChildren();rows.forEach(row=>{const tr=node('tr');const a=node('td',`#${row.id} · ${row.paciente}`);a.append(node('small',row.organizacion));const b=node('td',row.profesional);b.append(node('small',`${row.servicio} · ${row.modalidad}`));const c=node('td',row.fecha);c.append(node('small',`${row.edad} h · Prioridad ${row.prioridad}`));const e=node('td');e.append(node('span',row.estado_label,'oc-badge '+row.estado),node('small',row.siguiente),node('small',row.mensaje));const f=node('td');const view=node('button','Detalle');view.type='button';view.onclick=()=>detail(row);const run=node('button','Solicitar');run.type='button';run.disabled=!row.elegible||row.estado==='completado'||!d.activo;run.onclick=()=>submit(row.id);f.append(view,run);tr.append(a,b,c,e,f);$('oc-rows').append(tr);});if(!rows.length){const tr=node('tr'),td=node('td','No hay sesiones para estos filtros.');td.colSpan=5;tr.append(td);$('oc-rows').append(tr);}
 $('oc-activity').replaceChildren(...d.servicios.map(x=>node('p',`${x.tipo_sesion} · ${x.total} sesiones`)),...d.organizaciones.map(x=>node('p',`${x.es_beneficios?'TECTUM Beneficios':x.es_tectum?'TECTUM In-House':'HOPE'} · ${x.total} sesiones`)));$('oc-onsite').replaceChildren(...d.profesionales.map(x=>node('p',`${x.psicologo__usuario__first_name||'Sin asignar'} · ${x.registro_llegada} · ${x.total}`)));if(!d.profesionales.length)$('oc-onsite').append(node('p','Sin datos presenciales en este filtro.'));
 }catch(e){$('oc-sync').textContent='Actualización interrumpida';$('oc-message').textContent=e.message;}finally{loading=false;}}
 form.addEventListener('submit',e=>{e.preventDefault();page=1;refresh();});$('oc-batch').onclick=()=>submit();$('oc-prev').onclick=()=>{page--;refresh();};$('oc-next').onclick=()=>{page++;refresh();};$('oc-close').onclick=()=>$('oc-detail').close();$('oc-detail').addEventListener('close', () => { detailId = null; });
 $('oc-test-pdf').onclick = () => submit(detailId, 'pdf_psicologo');
 $('oc-test-full').onclick = () => {
   if (window.confirm('El flujo completo puede enviar correos reales a los destinatarios de esta cita. ¿Continuar?')) {
     submit(detailId, 'completo');
   }
 };
 setInterval(() => {
   if (!document.hidden) {
     refresh();
     refreshDetail();
   }
 }, 10000);
 refresh();
})();
