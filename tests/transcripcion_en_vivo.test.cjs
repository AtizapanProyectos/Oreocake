const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const nodos = new Map();
const nodo = s => { if (!nodos.has(s)) nodos.set(s, {value:'', textContent:'', checked:false, dataset:{}, addEventListener(){}}); return nodos.get(s); };
const panel = {querySelector:nodo};
const box = {querySelector:s=>s === '.gsn-vivo' ? panel : nodo(s), dataset:{urlTranscribir:'/audio',urlTexto:'/texto'}};
let fallo = true, fallaGuardado = true;
const guardados = [];
class Recorder {
 constructor(){this.mimeType='audio/webm';}
 start(){this.state='recording';}
 stop(){this.state='inactive'; this.ondataavailable({data:new Blob(['audio'])}); this.onstop();}
}
class AudioContext {
 createMediaStreamSource(){return {connect(){}};}
 createAnalyser(){return {getByteTimeDomainData(data){data.fill(128);}};}
 resume(){} async close(){}
}
const window = {AudioContext,addEventListener(){}};
vm.runInNewContext(fs.readFileSync('static/js/transcripcion_en_vivo.js','utf8'),{
 window,MediaRecorder:Recorder,Blob,FormData,AbortSignal,setTimeout,clearTimeout,
 requestAnimationFrame:()=>1,cancelAnimationFrame(){},document:{cookie:'csrftoken=test'},
 fetch:async(url, opciones)=>{
  if (url === '/texto') {
   guardados.push(JSON.parse(opciones.body));
   return {ok:!fallaGuardado,headers:{get:()=>'application/json'},json:async()=>({mensaje:'Prueba de guardado'})};
  }
  return {ok:!fallo,headers:{get:()=>'application/json'},json:async()=>fallo?{mensaje:'Fallo simulado'}:{texto:'Texto recuperado'}};
 }
});
(async()=>{
 const api=window.HopeTranscripcion(box);
 await nodo('[data-vivo-guardar]').onclick();
 assert.match(nodo('[data-vivo-guardado]').textContent,/Agrega o carga/);
 api.iniciar({}); await api.detener();
 await new Promise(r=>setTimeout(r,10));
 assert.equal(api.puedeIniciar(),false);
 assert.match(nodo('[data-vivo-estado]').textContent,/Fallo simulado/);
 api.guardado({version_audio:'archivo.wav'});
 fallo=false;
 await nodo('[data-vivo-reintentar]').onclick();
 assert.equal(nodo('textarea').value,'Texto recuperado\n');
 await new Promise(r=>setTimeout(r,10));
 assert.deepEqual(guardados, [{accion:'guardar',transcripcion:'Texto recuperado',version_audio:'archivo.wav'}]);
 assert.equal(nodo('[data-vivo-guardado]').dataset.estado,'error');
 assert.equal(api.puedeIniciar(),false);
 fallaGuardado=false;
 await nodo('[data-vivo-guardar]').onclick();
 assert.equal(nodo('[data-vivo-guardado]').dataset.estado,'listo');
 assert.equal(api.puedeIniciar(),true);
 await nodo('[data-vivo-guardar]').onclick();
 assert.match(nodo('[data-vivo-guardado]').textContent,/ya está guardada/);
 assert.equal(guardados.length, 2);

 // Recargar la página no exige grabar de nuevo para guardar un texto editado.
 box.dataset.versionAudio = 'archivo.wav';
 nodo('textarea').value = 'Texto corregido después de recargar';
 window.HopeTranscripcion(box);
 assert.equal(nodo('[data-vivo-guardar]').disabled, false);
 await nodo('[data-vivo-guardar]').onclick();
 assert.deepEqual(guardados.at(-1), {
  accion:'guardar', transcripcion:'Texto corregido después de recargar', version_audio:'archivo.wav',
 });
 assert.equal(nodo('[data-vivo-guardado]').dataset.estado,'listo');
 console.log('OK: guardado manual, reintento y confirmación después de recargar.');
})().catch(e=>{console.error(e);process.exitCode=1;});
