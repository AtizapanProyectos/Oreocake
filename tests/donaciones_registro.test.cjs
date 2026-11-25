const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const vm = require('node:vm');
const {randomUUID} = require('node:crypto');
const fuente = fs.readFileSync('static/js/donaciones_registro.js', 'utf8');

class Elemento {
  constructor(tag='div') { this.tag=tag; this.children=[]; this.hidden=false; this.disabled=false; this.dataset={}; this.textContent=''; }
  setAttribute(nombre,valor) { this[nombre]=valor; }
  append(...hijos) { for(const h of hijos) { if(h.parentNode) h.parentNode.children=h.parentNode.children.filter(x=>x!==h); this.children.push(h); h.parentNode=this; } }
  insertBefore(nuevo,previo) { let i=this.children.indexOf(previo); if(i<0)i=this.children.length; this.children.splice(i,0,nuevo); nuevo.parentNode=this; }
  querySelector() { return {value:'csrf-prueba'}; }
}

function servidor() {
  const datos = {donacion:null, capturas:0, ordenes:0, guardados:0, fallarGuardados:0, peticiones:[], capturarFalla:false};
  datos.fetch = async (url, opciones) => {
    const u = new URL(url,'https://hope.test');
    const p = Object.fromEntries(new URLSearchParams(opciones.body || u.search));
    datos.peticiones.push({url:u.pathname,...p});
    let cuerpo;
    if(u.pathname==='/estado') cuerpo=p.folio ? datos.donacion : {status:'success',pendiente:datos.donacion?.estado==='Pendiente' ? datos.donacion : null};
    if(u.pathname==='/iniciar') {
      datos.donacion={status:'success',folio:p.folio,monto:p.monto,moneda:'MXN',estado:'Pendiente',origen:p.origen,cita_id:p.cita_id||null,orden:null,captura:null,captura_iniciada:false,custom_id:'HOPE-DON-'+p.folio,descripcion:'Donación HOPE'};
      cuerpo=datos.donacion;
    }
    if(u.pathname==='/actualizar') {
      if(p.accion==='completar') {
        datos.guardados++;
        if(datos.fallarGuardados>0) { datos.fallarGuardados--; return {ok:false,status:503,json:async()=>({status:'error',message:'Registro temporalmente no disponible'})}; }
        datos.donacion.estado='Completada'; datos.donacion.captura=p.captura;
      }
      if(p.accion==='orden') datos.donacion.orden=p.orden;
      if(p.accion==='capturar') datos.donacion.captura_iniciada=true;
      if(p.accion==='cancelar') datos.donacion.estado='Cancelada';
      cuerpo=datos.donacion;
    }
    const copia=JSON.parse(JSON.stringify(cuerpo));
    return {ok:true,status:200,json:async()=>copia};
  };
  datos.actions={
    disable(){},enable(){},resolve(){return true;},reject(){return false;},
    order:{
      create:async payload=>{datos.ordenes++;datos.payload=payload;return 'ORDEN123';},
      capture:async()=>{
        datos.capturas++;
        if(datos.capturarFalla)throw new Error('Conexión perdida al capturar');
        return {status:'COMPLETED',payer:{name:{given_name:'Persona',surname:'Prueba'},email_address:'persona@example.test'},purchase_units:[{custom_id:datos.donacion.custom_id,payments:{captures:[{id:'CAPTURA123',status:'COMPLETED',amount:{value:datos.donacion.monto,currency_code:'MXN'}}]}}]};
      }
    }
  };
  return datos;
}

function navegador(srv, opciones={}) {
  const storage=opciones.storage || new Map();
  const config=new Elemento();config.dataset={iniciar:'/iniciar',estado:'/estado',actualizar:'/actualizar',usuario:'visitante'};
  const doc={getElementById:id=>id==='hope-donaciones-config'?config:null,createElement:tag=>new Elemento(tag)};
  const window={};
  const localStorage={getItem:k=>{if(opciones.sinStorage)throw Error('bloqueado');return storage.get(k)||null;},setItem:(k,v)=>{if(opciones.sinStorage)throw Error('bloqueado');storage.set(k,v);},removeItem:k=>storage.delete(k)};
  vm.runInNewContext(fuente,{window,document:doc,localStorage,crypto:{randomUUID},fetch:srv.fetch,URLSearchParams,AbortController,setTimeout,clearTimeout,console});
  const padre=new Elemento(), contenedor=new Elemento();padre.append(contenedor);
  let exitos=0;
  const opts=window.HopeDonaciones.opciones({origen:opciones.origen||'donaciones',contenedor,monto:()=>opciones.monto||'400',...(opciones.cita ? {citaId:()=>opciones.cita}:{}),alCompletar:()=>exitos++});
  const wrapper=padre.children[1];
  return {opts,storage,wrapper,get exitos(){return exitos;}};
}

async function iniciarCheckout(srv,ui) {
  await ui.opts.onInit({},srv.actions);
  assert.equal(await ui.opts.onClick({},srv.actions),true);
  assert.equal(await ui.opts.createOrder({},srv.actions),'ORDEN123');
}

test('registra antes de cobrar y vincula custom_id; no solicita otra captura al guardar',async()=>{
  const srv=servidor(),ui=navegador(srv);
  await iniciarCheckout(srv,ui);
  assert.equal(srv.donacion.estado,'Pendiente');
  assert.equal(srv.capturas,0);
  assert.equal(srv.payload.purchase_units[0].custom_id,srv.donacion.custom_id);
  await ui.opts.onApprove({orderID:'ORDEN123'},srv.actions);
  await ui.opts.onApprove({orderID:'ORDEN123'},srv.actions);
  assert.equal(srv.donacion.estado,'Completada');
  assert.equal(srv.capturas,1);assert.equal(srv.ordenes,1);assert.equal(ui.exitos,1);
  const guardar=srv.peticiones.find(p=>p.accion==='completar');
  assert.equal(guardar.nombre,'Persona Prueba');assert.equal(guardar.correo,'persona@example.test');
});

test('si falla el registro después del cargo, el botón reintenta guardar sin cobrar',async()=>{
  const srv=servidor(),ui=navegador(srv);srv.fallarGuardados=1;
  await iniciarCheckout(srv,ui);await ui.opts.onApprove({orderID:'ORDEN123'},srv.actions);
  assert.equal(ui.exitos,0);assert.equal(srv.donacion.estado,'Pendiente');
  assert.equal(await ui.opts.onClick({},srv.actions),false);
  const boton=ui.wrapper.children.find(x=>x.textContent==='Reintentar guardado');
  assert.equal(boton.hidden,false);await boton.onclick();
  assert.equal(srv.donacion.estado,'Completada');assert.equal(srv.capturas,1);assert.equal(srv.ordenes,1);assert.equal(ui.exitos,1);
});

test('recupera tras recargar con la respuesta guardada y no crea otro cargo',async()=>{
  const srv=servidor(),ui=navegador(srv);srv.fallarGuardados=1;
  await iniciarCheckout(srv,ui);await ui.opts.onApprove({orderID:'ORDEN123'},srv.actions);
  const recargada=navegador(srv,{storage:ui.storage});await recargada.opts.onInit({},srv.actions);
  assert.equal(srv.donacion.estado,'Completada');assert.equal(srv.capturas,1);assert.equal(srv.ordenes,1);assert.equal(recargada.exitos,1);
});

test('storage bloqueado conserva el cobro en memoria para reintentar',async()=>{
  const srv=servidor(),ui=navegador(srv,{sinStorage:true});srv.fallarGuardados=1;
  await iniciarCheckout(srv,ui);await ui.opts.onApprove({orderID:'ORDEN123'},srv.actions);
  await ui.wrapper.children.find(x=>x.textContent==='Reintentar guardado').onclick();
  assert.equal(srv.donacion.estado,'Completada');assert.equal(srv.capturas,1);
});

test('captura incierta conserva pendiente y bloquea otro pago',async()=>{
  const srv=servidor(),ui=navegador(srv);srv.capturarFalla=true;
  await iniciarCheckout(srv,ui);await ui.opts.onApprove({orderID:'ORDEN123'},srv.actions);
  assert.equal(srv.donacion.estado,'Pendiente');assert.equal(srv.guardados,0);
  assert.equal(await ui.opts.onClick({},srv.actions),false);assert.equal(srv.capturas,1);assert.equal(ui.exitos,0);
});

test('cancelar antes de captura no registra una donación completada',async()=>{
  const srv=servidor(),ui=navegador(srv);
  await iniciarCheckout(srv,ui);await ui.opts.onCancel();
  assert.equal(srv.donacion.estado,'Cancelada');assert.equal(srv.capturas,0);assert.equal(ui.exitos,0);
});

test('la invitación admite monto libre, conserva la cita y evita doble clic',async()=>{
  const srv=servidor(),ui=navegador(srv,{origen:'post_cita',cita:'42',monto:'235.50'});
  await ui.opts.onInit({},srv.actions);
  assert.equal(await ui.opts.onClick({},srv.actions),true);
  assert.equal(await ui.opts.onClick({},srv.actions),false);
  await ui.opts.createOrder({},srv.actions);
  assert.equal(srv.donacion.origen,'post_cita');assert.equal(srv.donacion.cita_id,'42');assert.equal(srv.donacion.monto,'235.50');
  await ui.opts.onApprove({orderID:'ORDEN123'},srv.actions);
  assert.equal(srv.donacion.estado,'Completada');assert.equal(srv.capturas,1);
  assert.equal(srv.payload.purchase_units[0].amount.value,'235.50');
});
