const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const source = fs.readFileSync('static/js/transcripcion_en_vivo.js', 'utf8');

function entorno(local = true) {
    const nodos = new Map();
    const elemento = selector => {
        if (!nodos.has(selector)) nodos.set(selector, {value:'', textContent:'', checked:false, disabled:false});
        return nodos.get(selector);
    };
    elemento('select').value = local ? 'local' : 'proveedor';
    const panel = {querySelector:elemento};
    const box = {querySelector:s => s === '.gsn-vivo' ? panel : elemento(s), dataset:{urlTexto:'/texto'}};
    const instancias = [];
    class Speech {
        constructor() { instancias.push(this); }
        start() { this.iniciado = true; this.onstart?.(); }
        stop() {
            if (!this.iniciado) throw Error('No iniciado');
            this.onresult?.({resultIndex:1, results:[Object.assign([{transcript:'inicio'}],{isFinal:true}), Object.assign([{transcript:'final'}],{isFinal:true})]});
            this.onend?.();
        }
        abort() { this.onend?.(); }
    }
    const peticiones = [];
    const window = {SpeechRecognition:Speech, addEventListener() {}};
    vm.runInNewContext(source, {window, setTimeout, clearTimeout, Blob, URL, AbortSignal,
        document:{cookie:'csrftoken=test'}, fetch:async (url, options) => {
            const datos = JSON.parse(options.body); peticiones.push(datos);
            return {ok:true, headers:{get:()=>'application/json'}, json:async()=> datos.accion === 'fragmento'
                ? {evidencia:'Evidencia corta'} : {notas:{notas_sesion:'Borrador'}}};
        }});
    return {api:window.HopeTranscripcion(box), elemento, instancias, peticiones};
}

(async () => {
    const sinLocal = entorno(true);
    await sinLocal.api.iniciar();
    assert.equal(sinLocal.instancias[0].iniciado, undefined, 'No debe usar proveedor sin selección explícita');
    assert.match(sinLocal.elemento('[data-vivo-estado]').textContent, /local no disponible/);
    await sinLocal.api.detener();

    const vivo = entorno(false);
    await vivo.api.iniciar();
    const motor = vivo.instancias[0];
    const evento = {resultIndex:0,results:[Object.assign([{transcript:'inicio'}],{isFinal:true})]};
    motor.onresult(evento); motor.onresult(evento);
    assert.equal(vivo.elemento('textarea').value, 'inicio\n', 'No duplicar resultados finales');
    await vivo.api.detener();
    assert.equal(vivo.elemento('textarea').value, 'inicio\nfinal\n', 'Esperar el resultado final al detener');
    vivo.api.guardado({version_audio:'audio-actual'});
    vivo.elemento('textarea').value = 'x'.repeat(21000);
    vivo.elemento('[data-vivo-revisado]').checked = true;
    await vivo.elemento('[data-vivo-generar]').onclick();
    assert.equal(vivo.peticiones.length, 4);
    assert.equal(vivo.peticiones.slice(0,3).map(p=>p.texto).join('').length, 21000);
    assert.equal(vivo.peticiones[3].transcripcion.length, 21000);
    assert.equal(vivo.elemento('progress').value, 100);
    assert.ok(vivo.peticiones.every(p=>p.version_audio === 'audio-actual'));
    console.log('OK: local sin fallback remoto, deduplicación, cierre y fragmentación sin pérdida.');
})().catch(error => { console.error(error); process.exitCode = 1; });
