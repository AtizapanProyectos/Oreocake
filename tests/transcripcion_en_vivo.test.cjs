const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const nodos = new Map();
const nodo = s => { if (!nodos.has(s)) nodos.set(s, {value:'', textContent:'', checked:false}); return nodos.get(s); };
const panel = {querySelector:nodo};
const box = {querySelector:s=>s === '.gsn-vivo' ? panel : nodo(s), dataset:{urlTranscribir:'/audio'}};
let fallo = true;
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
 fetch:async()=>({ok:!fallo,headers:{get:()=>'application/json'},json:async()=>fallo?{mensaje:'Fallo simulado'}:{texto:'Texto recuperado'}})
});
(async()=>{
 const api=window.HopeTranscripcion(box);
 api.iniciar({}); await api.detener();
 await new Promise(r=>setTimeout(r,10));
 assert.equal(api.puedeIniciar(),false);
 assert.match(nodo('[data-vivo-estado]').textContent,/Fallo simulado/);
 fallo=false;
 await nodo('[data-vivo-reintentar]').onclick();
 assert.equal(nodo('textarea').value,'Texto recuperado\n');
 assert.equal(api.puedeIniciar(),true);
 console.log('OK: fragmento retenido tras fallo y recuperado mediante reintento.');
})().catch(e=>{console.error(e);process.exitCode=1;});
