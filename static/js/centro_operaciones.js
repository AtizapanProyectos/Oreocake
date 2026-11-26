(() => {
 'use strict';
 const root=document.getElementById('operations'); if(!root)return;
 const $=id=>document.getElementById(id), form=$('oc-filters');
 let page=1,pages=1,busy=false,loading=false,rows=[];
 let detailId=null, detailRequest=null;
 let modoActual = 'manual', cambiandoModo = false;
 const node=(tag,text,cls)=>{const e=document.createElement(tag);e.textContent=text??'';if(cls)e.className=cls;return e;};
 const params=()=>{const p=new URLSearchParams(new FormData(form));p.set('page',page);return p;};
 const fmt=value=>value?new Date(value).toLocaleString('es-MX',{timeZone:'America/Mexico_City'}):'Sin registro';
 function mostrarModo(modo, activo) {
   modoActual = modo;
   $('oc-mode').value = modo;
   $('oc-mode').disabled = cambiandoModo || !activo;
   $('oc-mode-description').textContent = modo === 'automatico'
     ? 'El maestro busca citas elegibles y prioriza tus solicitudes.'
     : 'El maestro solo atiende las citas que solicites.';
 }
 function mostrarEntregas(conteo) {
   const grupos = [
     ['enviadas', 'Verde · Enviadas', 'Paciente y profesional tienen envío registrado'],
     ['faltan', 'Amarillo · Pendientes', 'Queda trabajo o al menos un envío pendiente'],
     ['problemas', 'Rojo · Con problemas', 'Hay una incidencia por resolver'],
   ];
   $('oc-deliveries').replaceChildren(...grupos.map(([clave, titulo, texto]) => {
     const tarjeta = node('article', null, `oc-delivery ${clave}`);
     tarjeta.append(node('h2', titulo), node('strong', conteo[clave]), node('p', texto));
     return tarjeta;
   }));
 }
 $('oc-mode').addEventListener('change', async () => {
   const modo = $('oc-mode').value;
   if (modo === 'automatico' && !window.confirm('Automático puede procesar citas pendientes y enviar correos reales. ¿Activarlo?')) {
     $('oc-mode').value = modoActual;
     return;
   }
   cambiandoModo = true;
   $('oc-mode').disabled = true;
   try {
     const respuesta = await request(root.dataset.modo, {
       method: 'POST', credentials: 'same-origin',
       headers: {'X-CSRFToken': root.querySelector('[name=csrfmiddlewaretoken]').value},
       body: new URLSearchParams({modo}),
     });
     mostrarModo(respuesta.modo, true);
     $('oc-message').textContent = 'Modo guardado.';
   } catch (error) {
     $('oc-mode').value = modoActual;
     $('oc-message').textContent = error.message;
   } finally {
     cambiandoModo = false;
     $('oc-mode').disabled = false;
   }
 });
 async function request(url,options){const r=await fetch(url,options);if(!r.headers.get('content-type')?.includes('application/json'))throw Error('La sesión o conexión no está disponible.');const d=await r.json();if(!r.ok)throw Error(d.mensaje||'No se pudo completar la solicitud.');return d;}
 async function submit(id, etapa) {
   if (!id || busy) return;
   busy = true;
   $('oc-test-pdf').disabled = $('oc-test-full').disabled = true;
   try {
     const respuesta = await request(`/panel-admin/api/reintento-automatizacion/${id}/`, {
       method: 'POST',
       credentials: 'same-origin',
       headers: {'X-CSRFToken': root.querySelector('[name=csrfmiddlewaretoken]').value},
       body: new URLSearchParams({tipo: etapa}),
     });
     $('oc-message').textContent = respuesta.mensaje;
     if (detailId === id) $('oc-detail-status').textContent = respuesta.mensaje;
     await refresh();
     await refreshDetail();
   } catch (error) {
     $('oc-message').textContent = error.message;
     if (detailId === id) $('oc-detail-status').textContent = error.message;
   } finally {
     busy = false;
   }
 }
 function detail(row) {
   detailId = row.id;
   $('oc-detail-title').textContent = `Sesión #${row.id} · ${row.paciente}`;
   $('oc-detail-status').textContent = 'Consultando avance…';
   $('oc-detail-body').replaceChildren();
   $('oc-copy-ipp').hidden = true;
   $('oc-ipp-link').hidden = true;
   $('oc-ipp-link').value = '';
   $('oc-ipp-status').textContent = '';
   $('oc-test-pdf').disabled = $('oc-test-full').disabled = true;
   $('oc-detail').showModal();
   refreshDetail();
 }
 async function refreshDetail() {
   const id = detailId;
   if (!id || detailRequest === id) return;
   detailRequest = id;
   try {
     const {fila, eventos, ipp} = await request(`${root.dataset.url}${id}/`, {cache: 'no-store'});
     if (detailId !== id) return;
     $('oc-detail-status').textContent = `${fila.estado_label} · ${fila.siguiente}${fila.mensaje ? ' · ' + fila.mensaje : ''}`;
     $('oc-test-pdf').disabled = $('oc-test-full').disabled = !fila.elegible || ['procesando', 'completado'].includes(fila.estado);
     if (ipp.url) {
       const liga = new URL(ipp.url, window.location.origin).href;
       const estado = ipp.respondido ? 'IPP respondido.' : 'IPP pendiente. El paciente deberá iniciar sesión para responder.';
       if ($('oc-ipp-link').value !== liga || $('oc-copy-ipp').hidden !== ipp.respondido) {
         $('oc-ipp-status').textContent = estado;
       }
       $('oc-ipp-link').value = liga;
       $('oc-copy-ipp').hidden = ipp.respondido;
       if (ipp.respondido) $('oc-ipp-link').hidden = true;
     }
     const flujo = node('ol', null, 'oc-flow');
     for (const paso of fila.pasos) {
       const tarjeta = node('li', null, `oc-flow-step ${paso.estado}`);
       tarjeta.append(node('span', paso.estado === 'completado' ? '✓' : paso.error ? '!' : '●', 'oc-flow-dot'));
       tarjeta.append(node('strong', paso.etiqueta), node('p', `${paso.estado_label} · ${paso.intentos} intento(s)`));
       if (paso.mensaje) tarjeta.append(node('p', paso.mensaje));
       if (paso.error) {
         const siguiente = paso.proximo ? `Próximo intento: ${fmt(paso.proximo)}` : 'Sin reintento automático programado';
         tarjeta.append(node('p', `${paso.error} · ${siguiente}`), node('p', paso.accion));
       }
       flujo.append(tarjeta);
     }
     const historial = eventos.map(evento => node('p', `${fmt(evento.inicio)} · ${evento.etapa} · ${evento.estado} · ${evento.responsable}${evento.duracion_ms === null ? '' : ` · ${evento.duracion_ms} ms`}${evento.error ? ' · ' + evento.error : ''}`));
     $('oc-detail-body').replaceChildren(
       node('p', `Asistencia: ${fila.asistencia} · Confirmación: ${fila.confirmacion}`),
       flujo, node('h3', 'Historial de ejecuciones'),
       ...(historial.length ? historial : [node('p', 'Todavía no hay intentos registrados.')])
     );
   } catch (error) {
     if (detailId === id) $('oc-detail-status').textContent = error.message;
   } finally {
     if (detailRequest === id) detailRequest = null;
   }
 }
 async function refresh(){if(loading)return;loading=true;try{const d=await request(root.dataset.url+'?'+params());rows=d.filas;pages=d.paginas;page=d.pagina;$('oc-sync').textContent=`Actualizado ${fmt(d.actualizado)}`;$('oc-capacity').textContent=d.activo?`Capacidad: ${d.capacidad} · ${d.ejecucion_activa?'Ejecución en curso':'En espera · último intento: '+fmt(d.ultimo_intento)}`:'Motor desactivado';$('oc-total').textContent=`· ${d.total}`;$('oc-page').textContent=`Página ${page} de ${pages}`;$('oc-prev').disabled=page<=1;$('oc-next').disabled=page>=pages;
 if (!cambiandoModo) mostrarModo(d.modo, d.activo);
 mostrarEntregas(d.resumen_citas);
 const k=d.kpi;$('oc-kpis').replaceChildren();[['Etapas terminadas hoy',k.ejecuciones],['Etapas exitosas',k.exitos],['Intentos con incidencia',k.incidencias],['Tasa de éxito',k.tasa===null?'Sin datos':k.tasa+'%'],['Duración promedio',k.duracion===null?'Sin datos':(k.duracion/1000).toFixed(1)+' s'],['Resolución promedio',k.resolucion===null?'Sin datos':(k.resolucion/60000).toFixed(1)+' min']].forEach(([label,value])=>{const a=node('article');a.append(node('small',label),node('strong',value));$('oc-kpis').append(a);});
 const tarjetas = rows.map(cita => {
   const tarjeta = node('article', null, 'oc-card');
   const cabecera = node('div', null, 'oc-card-head');
   cabecera.append(node('span', cita.organizacion, 'oc-org'), node('span', `#${cita.id}`));
   const estado = node('span', cita.estado_label, `oc-badge ${cita.estado}`);
   const accion = node('button', cita.estado === 'sin_solicitud' ? 'Abrir flujo' : 'Ver avance');
   accion.type = 'button';
   accion.onclick = () => detail(cita);
   tarjeta.append(cabecera, node('h3', cita.paciente),
     node('p', `${cita.profesional} · ${cita.servicio}`),
     node('p', `${cita.fecha} · ${cita.modalidad}`), estado,
     node('p', cita.mensaje || cita.siguiente), accion);
   return tarjeta;
 });
 $('oc-rows').replaceChildren(...(tarjetas.length ? tarjetas : [node('p', 'No hay citas para estos filtros.')]));
 $('oc-activity').replaceChildren(...d.servicios.map(x=>node('p',`${x.tipo_sesion} · ${x.total} sesiones`)),...d.organizaciones.map(x=>node('p',`${x.es_beneficios?'TECTUM Beneficios':x.es_tectum?'TECTUM In-House':'HOPE'} · ${x.total} sesiones`)));$('oc-onsite').replaceChildren(...d.profesionales.map(x=>node('p',`${x.psicologo__usuario__first_name||'Sin asignar'} · ${x.registro_llegada} · ${x.total}`)));if(!d.profesionales.length)$('oc-onsite').append(node('p','Sin datos presenciales en este filtro.'));
 }catch(e){$('oc-sync').textContent='Actualización interrumpida';$('oc-message').textContent=e.message;}finally{loading=false;}}
 form.addEventListener('submit',e=>{e.preventDefault();page=1;refresh();});$('oc-prev').onclick=()=>{page--;refresh();};$('oc-next').onclick=()=>{page++;refresh();};$('oc-close').onclick=()=>$('oc-detail').close();$('oc-detail').addEventListener('close', () => { detailId = null; });
 $('oc-test-pdf').onclick = () => submit(detailId, 'pdf_psicologo');
 $('oc-copy-ipp').onclick = async () => {
   const campo = $('oc-ipp-link');
   try {
     await navigator.clipboard.writeText(campo.value);
     $('oc-ipp-status').textContent = 'Liga copiada. Compártela con el paciente de esta cita.';
   } catch {
     campo.hidden = false;
     campo.select();
     $('oc-ipp-status').textContent = 'Selecciona y copia la liga que aparece abajo.';
   }
 };
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
