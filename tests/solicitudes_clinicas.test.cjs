const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
let response;
let opciones;
const window = {};
vm.runInNewContext(fs.readFileSync('static/js/solicitudes_clinicas.js', 'utf8'), {
  window, document: { cookie: 'csrftoken=vigente', querySelector: () => ({value:'antiguo'}) },
  fetch: async (url, options) => { opciones = options; if (response instanceof Error) throw response; return response; },
});
(async () => {
  response = {ok:true,status:200,json:async()=>({status:'success',historial_id:1})};
  assert.equal((await window.solicitudClinica('/guardar', 'formulario')).historial_id, 1);
  assert.equal(opciones.headers['X-CSRFToken'], 'vigente');
  assert.equal(opciones.credentials, 'same-origin');
  const formulario = new Map([['csrfmiddlewaretoken','antiguo']]);
  await window.solicitudClinica('/guardar', formulario);
  assert.equal(formulario.get('csrfmiddlewaretoken'), 'vigente');
  response = {ok:false,status:403,json:async()=>{throw new Error('HTML');}};
  await assert.rejects(window.solicitudClinica('/guardar', 'formulario'), /sesión o el permiso/);
  response = {ok:false,status:413,json:async()=>{throw new Error('HTML');}};
  await assert.rejects(window.solicitudClinica('/guardar', 'formulario'), /10 MB/);
  response = {ok:false,status:500,json:async()=>{throw new Error('HTML');}};
  await assert.rejects(window.solicitudClinica('/guardar', 'formulario'), /HTTP 500/);
  response = {ok:false,status:409,json:async()=>({status:'error',message:'La sesión ya tiene una bitácora.'})};
  await assert.rejects(window.solicitudClinica('/guardar', 'formulario'), /ya tiene una bitácora/);
  response = {redirected:true,status:200};
  await assert.rejects(window.solicitudClinica('/guardar', 'formulario'), /sesión terminó/);
  response = new Error('NetworkError');
  await assert.rejects(window.solicitudClinica('/guardar', 'formulario'), /información sigue en el formulario/);
  console.log('Solicitudes clínicas: respuestas HTML, CSRF, red y conflictos verificados.');
})().catch(error => { console.error(error); process.exitCode=1; });
