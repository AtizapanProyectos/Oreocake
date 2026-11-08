(() => {
    if (window.HopeTranscripcion) return;
    window.HopeTranscripcion = function (box) {
        const panel = box.querySelector('.gsn-vivo');
        const texto = panel.querySelector('textarea');
        const estado = panel.querySelector('[data-vivo-estado]');
        const generar = panel.querySelector('[data-vivo-generar]');
        const salida = panel.querySelector('[data-vivo-notas]');
        const barra = panel.querySelector('progress');
        let activo=false, grabando=false, ocupado=false, version='', recorder, timer, context, frame;
        const estadoGuardado = panel.querySelector('[data-vivo-guardado]');
        const guardar = panel.querySelector('[data-vivo-guardar]');
        let cola=[], enviando=false, numero=0, esperandoUltimo=false;
        let textoGuardado='', guardados=Promise.resolve(true);
        function estadoPersistencia(mensaje, tipo) {
            estadoGuardado.textContent = mensaje;
            estadoGuardado.dataset.estado = tipo;
        }
        function actualizarAcciones() {
            generar.disabled = ocupado || grabando || esperandoUltimo || enviando || cola.length > 0 || !version;
            guardar.disabled = ocupado || grabando || !version || !texto.value.trim();
        }
        function guardarTexto() {
            const actual = texto.value.trim(), origen = version;
            if (!actual || !origen || grabando) return Promise.resolve(false);
            // Serializa instantáneas: un fragmento tardío no pisa uno más nuevo.
            guardados = guardados.then(async () => {
                if (origen !== version) return false;
                if (actual === textoGuardado) return true;
                estadoPersistencia('Guardando transcripción…', 'cargando');
                try {
                    if (actual.length > 500000) throw Error('La transcripción supera el límite de 500.000 caracteres.');
                    await enviar({accion:'guardar', transcripcion:actual}, origen);
                    if (origen !== version) return false;
                    textoGuardado = actual;
                    estadoPersistencia('Transcripción guardada en el expediente.', 'listo');
                    return true;
                } catch (error) {
                    if (origen === version) estadoPersistencia(
                        (error.name === 'TimeoutError' ? 'El guardado tardó demasiado.' : error.message)
                        + ' Tu texto sigue aquí. Pulsa Guardar transcripción para reintentar.', 'error');
                    return false;
                }
            });
            return guardados;
        }
        guardar.onclick = guardarTexto;
        texto.addEventListener('input', () => {
            actualizarAcciones();
            if (version && texto.value.trim() !== textoGuardado) estadoPersistencia('Hay cambios sin guardar.', 'pendiente');
        });
        function mensaje(t, tipo = '') { estado.textContent=t; estado.dataset.estado=tipo; }
        async function consumir() {
            if(enviando) return;
            enviando=true;
            while(cola.length) {
                const item=cola[0];
                mensaje(`Transcribiendo fragmento ${item.numero} · ${cola.length} en espera`, 'cargando');
                try {
                    const fd=new FormData(); fd.append('audio',item.blob,'fragmento.webm');
                    const token=document.cookie.split('; ').find(c=>c.startsWith('csrftoken='))?.slice(10);
                    const r=await fetch(box.dataset.urlTranscribir,{method:'POST',body:fd,headers:{'X-CSRFToken':token||''},signal:AbortSignal.timeout(28000)});
                    if(!r.headers.get('content-type')?.includes('application/json')) throw Error('Servidor no disponible');
                    const d=await r.json(); if(!r.ok) throw Error(d.mensaje||'No se pudo transcribir');
                    texto.value += (d.texto||'')+'\n'; cola.shift();
                } catch(e) { mensaje(`Fragmento ${item.numero} pendiente: ${e.message}. Pulsa Reintentar. El audio se conserva.`, 'error'); break; }
            }
            enviando=false;
            actualizarAcciones();
            if (!grabando && version && texto.value.trim()) guardarTexto();
            if(!cola.length) mensaje(grabando?'Micrófono activo · esperando el siguiente fragmento':'Transcripción recibida. Revisa el texto antes de generar notas.', grabando ? 'cargando' : 'listo');
        }
        function segmento(stream) {
            const partes=[];
            recorder=new MediaRecorder(stream);
            recorder.ondataavailable=e=>{if(e.data.size) partes.push(e.data);};
            recorder.onstop=()=>{
                const blob=new Blob(partes,{type:recorder.mimeType});
                if(blob.size) cola.push({blob,numero:++numero});
                if(activo && cola.length<30) segmento(stream);
                else if(activo) {activo=false; mensaje('Transcripción pausada por acumulación. El audio completo sigue grabándose.');}
                if (!grabando) esperandoUltimo = false;
                consumir();
            };
            recorder.start(); timer=setTimeout(()=>{if(recorder.state!=='inactive')recorder.stop();},20000);
        }
        panel.querySelector('[data-vivo-reintentar]').onclick=consumir;
        const api={
            puedeIniciar() {return !esperandoUltimo && !enviando && cola.length===0 && (!version || texto.value.trim()===textoGuardado);},
            iniciar(stream) {
                grabando=activo=true; cola=[]; numero=0; texto.readOnly=true;
                api.limpiar();
                panel.querySelector('[data-vivo-revisado]').checked=false;
                mensaje('Capturando audio · primer texto en aproximadamente 20 segundos', 'cargando');
                try {
                    context=new (window.AudioContext||window.webkitAudioContext)();
                    const source=context.createMediaStreamSource(stream), analyser=context.createAnalyser();
                    source.connect(analyser); analyser.fftSize=256; const data=new Uint8Array(256);
                    const medir=()=>{analyser.getByteTimeDomainData(data); let sum=0; for(const n of data)sum+=(n-128)**2;
                        panel.querySelector('meter').value=Math.min(100,Math.sqrt(sum/data.length)*3); frame=requestAnimationFrame(medir);};
                    context.resume(); medir(); segmento(stream);
                } catch(e) {activo=false; mensaje('No se pudo iniciar la transcripción: '+e.message+'. La grabación completa continúa.', 'error');}
            },
            async detener() {grabando=activo=false; esperandoUltimo=!!recorder && recorder.state!=='inactive';
                clearTimeout(timer); cancelAnimationFrame(frame); if(context) await context.close();
                if(recorder && recorder.state!=='inactive') recorder.stop(); texto.readOnly=false;},
            guardado(data){
                version=data.version_audio||'';
                actualizarAcciones();
                if (texto.value.trim()) guardarTexto();
            },
            limpiar(){
                version=''; texto.value=''; textoGuardado='';
                estadoPersistencia('', '');
                actualizarAcciones();
            }
        };
        panel.querySelector('[data-vivo-descargar]').onclick = () => {
            const url = URL.createObjectURL(new Blob([texto.value], {type:'text/plain;charset=utf-8'}));
            const enlace = document.createElement('a'); enlace.href = url; enlace.download = 'transcripcion.txt'; enlace.click();
            setTimeout(() => URL.revokeObjectURL(url), 1000);
        };
        async function enviar(datos, origen = version) {
            const token = document.cookie.split('; ').find(c => c.startsWith('csrftoken='))?.slice(10);
            const res = await fetch(box.dataset.urlTexto, {method:'POST', credentials:'same-origin',
                headers:{'Content-Type':'application/json', 'X-CSRFToken':token || ''},
                body:JSON.stringify({...datos, version_audio:origen}), signal:AbortSignal.timeout(28000)});
            if (!res.headers.get('content-type')?.includes('application/json')) throw Error('El servidor no completó el paso. Conserva tu texto y reintenta.');
            const data = await res.json();
            if (!res.ok) throw Error(data.mensaje || 'No se pudo completar el paso.');
            return data;
        }
        generar.onclick = async () => {
            if (ocupado || !texto.value.trim()) { mensaje('Añade o revisa la transcripción antes de generar notas.'); return; }
            if (!panel.querySelector('[data-vivo-revisado]').checked) { mensaje('Confirma que revisaste la transcripción, incluidas sus posibles omisiones.'); return; }
            if (texto.value.length > 500000) { mensaje('La transcripción supera el límite de 500.000 caracteres.'); return; }
            ocupado = true; actualizarAcciones(); texto.readOnly = true;
            box.querySelector('.gsn-rec-start').disabled = true; box.querySelector('.gsn-rec-archivo').disabled = true;
            barra.hidden = false; barra.value = 0;
            const completo = texto.value;
            try {
                if (!(await guardarTexto())) {
                    mensaje('Guarda la transcripción antes de generar las notas.');
                    return;
                }
                let evidencia = [];
                const bloques = Math.ceil(completo.length / 10000);
                for (let i = 0; i < bloques; i++) {
                    mensaje(`Analizando texto ${i+1} de ${bloques} · ${Math.round(70*i/bloques)}%`);
                    evidencia.push((await enviar({accion:'fragmento',texto:completo.slice(i*10000,(i+1)*10000)})).evidencia);
                    barra.value = Math.round(70*(i+1)/bloques);
                }
                let resumen = evidencia.join('\n');
                while (resumen.length > 10000) {
                    mensaje('Consolidando evidencia de toda la sesión · 80%', 'cargando'); barra.value = 80;
                    let reducidos = [];
                    for (let i = 0; i < resumen.length; i += 10000) reducidos.push((await enviar({accion:'fragmento',texto:resumen.slice(i,i+10000)})).evidencia);
                    const siguiente = reducidos.join('\n');
                    if (siguiente.length >= resumen.length) throw Error('No se pudo consolidar el texto. Conserva la transcripción y reintenta.');
                    resumen = siguiente;
                }
                mensaje('Generando borrador clínico · 90%', 'cargando'); barra.value = 90;
                const datos = await enviar({accion:'finalizar', texto:resumen, transcripcion:completo});
                salida.textContent = Object.entries(datos.notas).map(([campo, valor]) => `${campo.replaceAll('_',' ')}\n${valor || 'Sin evidencia'}\n`).join('\n');
                barra.value = 100; mensaje('100% · Borrador guardado. Abre o recarga el expediente para revisar y guardar las notas clínicas.', 'listo');
            } catch (error) { mensaje(error.name === 'TimeoutError' ? 'El paso tardó demasiado. Tu texto sigue disponible; puedes reintentar.' : error.message, 'error'); }
            finally { ocupado = false; actualizarAcciones(); texto.readOnly = false;
                box.querySelector('.gsn-rec-start').disabled = false; box.querySelector('.gsn-rec-archivo').disabled = false; }
        };
        window.addEventListener('beforeunload', event => {
            if (grabando || activo || ocupado || esperandoUltimo || cola.length || texto.value.trim() !== textoGuardado) { event.preventDefault(); event.returnValue = ''; }
        });
        return api;
    };
})();
