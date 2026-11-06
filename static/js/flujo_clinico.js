(() => {
  'use strict';
  const app = document.getElementById('flow-app');
  if (!app) return;
  const rows = JSON.parse(document.getElementById('flow-data').textContent);
  const stages = ['meet', 'lectura', 'ia', 'escritura', 'ipp', 'reporte', 'correo'];
  const orgs = {hope: 'HOPE', tectum_inhouse: 'TECTUM In-House', tectum_beneficios: 'TECTUM Beneficios'};
  const states = {exitoso: 'Completado', pendiente: 'Pendiente', error: 'Requiere atención'};
  const container = document.getElementById('flow-sessions');
  const csrf = document.querySelector('#flow-csrf input').value;
  let view = 'cards', busy = false, stopped = false, pending = null;
  const el = (tag, cls, text) => { const node = document.createElement(tag); if (cls) node.className = cls; if (text !== undefined) node.textContent = text; return node; };
  const date = value => value ? new Intl.DateTimeFormat('es-MX', {timeZone:'America/Mexico_City', day:'2-digit',month:'short',hour:'2-digit',minute:'2-digit'}).format(new Date(value)) : 'Sin registro';
  document.querySelectorAll('[data-date]').forEach(node => node.textContent = date(node.dataset.date));
  function action(text, callback, cls='') { const node = el('button', cls, text); node.type = 'button'; node.addEventListener('click', callback); node.disabled = busy; return node; }
  function card(row) {
    const article = el('article', 'session-card'); article.dataset.id = row.id;
    const head = el('div', 'session-head'), info = el('div');
    info.append(el('h3','session-title',row.paciente));
    info.append(el('div','session-meta',`${row.fecha} · ${row.hora} · ${row.psicologo}`));
    info.append(el('div','session-meta',`#${row.id} · ${orgs[row.org]} · Hace ${row.edad} · Prioridad ${row.prioridad}`));
    head.append(info, el('span',`session-badge ${row.estado}`,states[row.estado])); article.append(head);
    const steps = el('div','session-steps');
    row.pasos.forEach(step => {
      const node = el('div','session-step'); node.dataset.stage = step.key; node.title = step.detalle;
      const icon = el('i',`fa-solid fa-${step.icon}`); icon.setAttribute('aria-hidden','true');
      node.append(icon,el('span','step-label',step.label),el('span',`step-state ${step.estado}`,step.estado_label),el('time','step-date',date(step.fecha)));
      const button = action(step.estado === 'exitoso' ? 'Verificar / ejecutar' : 'Ejecutar paso', () => review([{id:row.id,url:row.api_url}],step.key));
      button.className = 'step-action'; button.disabled = busy || !row.elegible; node.append(button); steps.append(node);
    });
    article.append(steps);
    const footer = el('div','session-foot'); footer.append(el('span','',`${row.estado === 'exitoso' ? 'Envíos completos' : 'Siguiente: '+row.etapa}. ${row.fuente}${row.elegible ? '' : ' · En ventana de espera.'}`));
    const link = el('a','flow-link','Expediente ↗'); link.href = row.detalle_url; footer.append(link);
    footer.append(action('Ver bitácora',()=>detail(row)));
    const run = action('Ejecutar flujo',()=>review([{id:row.id,url:row.api_url}],'completo'),'flow-primary'); run.disabled = busy || !row.elegible || row.estado === 'exitoso'; footer.append(run); article.append(footer);
    return article;
  }
  function render() {
    container.replaceChildren();
    if (view === 'cards') rows.forEach(row => container.append(card(row)));
    if (view === 'agenda') {
      let day = '', section;
      rows.forEach(row => { if(row.fecha !== day) {day=row.fecha; section=el('section','agenda-day'); section.append(el('h3','',day));container.append(section);} section.append(card(row)); });
    }
    if (view === 'kanban') {
      const board=el('div','kanban-board');
      ['error','pendiente','exitoso'].forEach(state=> { const col=el('section','kanban-col');const items=rows.filter(row=>row.estado===state);col.append(el('h3','',`${states[state]} · ${items.length}`));items.forEach(row=>col.append(card(row)));board.append(col); }); container.append(board);
    }
  }
  function detail(row) {
    const body = document.getElementById('detail-body'); body.replaceChildren();
    body.append(el('p','',`Cita #${row.id} · ${row.paciente}`));
    row.pasos.forEach(step=>body.append(el('p','',`${step.label}: ${step.detalle}`)));
    if (!row.eventos.length) body.append(el('p','','Sin intentos registrados. La ausencia de logs no demuestra que una nota sea manual.'));
    row.eventos.slice().reverse().forEach(event=> {
      const item=el('div','detail-event');item.append(el('strong','',`${event.etapa_label} · ${event.estado}`),el('p','',event.detalle),el('small','',`${date(event.fecha)} · ${event.usuario} · ${event.origen} · Intento ${event.intento || '—'}`));
      if(event.error) item.append(el('p','',event.error));if(event.sugerida)item.append(el('p','',event.sugerida));body.append(item);
    }); document.getElementById('flow-detail').showModal();
  }
  async function post(url, data) {
    const response = await fetch(url,{method:'POST',credentials:'same-origin',headers:{'X-CSRFToken':csrf,'Accept':'application/json'},body:new URLSearchParams(data)});
    if (!response.headers.get('content-type')?.includes('application/json')) throw new Error('El servidor no confirmó el resultado. Actualice el panel antes de reintentar.');
    const result = await response.json(); if(!response.ok)throw new Error(result.mensaje || 'No se pudo completar la petición.');return result;
  }
  function review(items, stage) {
    if(busy)return;pending={items,stage};
    document.getElementById('confirm-summary').textContent = `${items.length} sesión(es), de la más antigua a la más reciente. Acción: ${stage === 'completo' ? 'flujo completo' : document.querySelector(`#batch-stage option[value="${stage}"]`).textContent}.`;
    document.getElementById('confirm-batch').disabled = !items.length;
    document.getElementById('batch-confirm').showModal();
  }
  function lock(value) {
    busy=value;
    document.querySelectorAll('.batch-buttons button,.flow-node-action,#flow-filters button,#flow-filters input,#flow-filters select,#batch-stage,.flow-tabs button').forEach(node=>node.disabled=value);
    render();
  }
  function log(text) {document.getElementById('progress-log').append(el('li','',text));}
  async function run({items,stage}) {
    lock(true);stopped=false;
    const progress=document.getElementById('flow-progress');progress.hidden=false;
    document.getElementById('progress-log').replaceChildren();
    const bar=document.getElementById('progress-bar');bar.max=items.length;bar.value=0;
    document.getElementById('stop-run').disabled=false;
    let done=0,failed=0;
    for (const item of items) {
      if(stopped)break;
      let failedItem=false;
      for(const key of stage==='completo'?stages:[stage]) {
        if(stopped)break;
        document.getElementById('progress-title').textContent=`Cita #${item.id} · ${key} · ${done}/${items.length} sesiones`;
        const node=container.querySelector(`[data-id="${item.id}"] [data-stage="${key}"]`);if(node)node.classList.add('running');
        try {
          const result=await post(item.url,{tipo:key});
          const index=rows.findIndex(row=>row.id===item.id);if(index>=0 && result.fila) rows[index]=result.fila;
          log(`#${item.id} · ${key}: ${result.mensaje}`);render();
          if(result.status!=='success'){failedItem=true;break;}
        } catch(error) {log(`#${item.id}: ${error.message}`);failedItem=true;stopped=true;break;}
      }
      if(failedItem)failed++;done++;bar.value=done;
    }
    document.getElementById('progress-title').textContent=`${stopped?'Ejecución detenida':'Ejecución finalizada'} · ${done}/${items.length} revisadas · ${failed} con bloqueo o error`;
    document.getElementById('stop-run').disabled=true;lock(false);
  }
  document.querySelectorAll('[data-view]').forEach(button=>button.addEventListener('click',()=>{view=button.dataset.view;document.querySelectorAll('[data-view]').forEach(b=>b.setAttribute('aria-pressed',String(b===button)));render();}));
  document.querySelectorAll('[data-stage-batch]').forEach(button=>button.addEventListener('click',()=>{document.getElementById('batch-stage').value=button.dataset.stageBatch;document.querySelector('.flow-controls').scrollIntoView({behavior:'smooth',block:'center'});document.querySelector('[data-limit="1"]').focus();}));
  document.querySelectorAll('[data-limit]').forEach(button=>button.addEventListener('click',async()=>{
    if(busy)return;lock(true);
    try {const data=new URLSearchParams(new FormData(document.getElementById('flow-filters')));data.set('limite',button.dataset.limit);if(button.dataset.failed)data.set('fallidos','1');const result=await post(app.dataset.batchUrl,data);lock(false);review(result.items,document.getElementById('batch-stage').value);}
    catch(error){lock(false);document.getElementById('flow-progress').hidden=false;document.getElementById('progress-title').textContent=error.message;}
  }));
  document.getElementById('cancel-batch').addEventListener('click',()=>document.getElementById('batch-confirm').close());
  document.getElementById('confirm-batch').addEventListener('click',()=>{document.getElementById('batch-confirm').close();run(pending);});
  document.getElementById('stop-run').addEventListener('click',()=>{stopped=true;document.getElementById('stop-run').disabled=true;});
  document.getElementById('close-detail').addEventListener('click',()=>document.getElementById('flow-detail').close());
  window.addEventListener('beforeunload',event=>{if(busy){event.preventDefault();event.returnValue='';}});
  render();
})();
