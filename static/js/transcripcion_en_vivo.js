(() => {
    if (window.HopeTranscripcion) return;
    window.HopeTranscripcion = function (box) {
        const panel = box.querySelector('.gsn-vivo');
        const modo = panel.querySelector('select');
        const texto = panel.querySelector('textarea');
        const estado = panel.querySelector('[data-vivo-estado]');
        const parcial = panel.querySelector('[data-vivo-parcial]');
        const generar = panel.querySelector('[data-vivo-generar]');
        const salida = panel.querySelector('[data-vivo-notas]');
        const barra = panel.querySelector('progress');
        const Speech = window.SpeechRecognition || window.webkitSpeechRecognition;
        let recognition, activo = false, version = '', reinicio, fallos = 0, cierre, cierreTimer;
        let grabando = false;
        let pendienteCierre = Promise.resolve();
        let ocupado = false;
        function mensaje(value) { estado.textContent = value; }
        panel.querySelector('[data-vivo-instalar]').onclick = async () => {
            if (grabando || ocupado) return;
            if (!Speech?.install) { mensaje('Este navegador no permite instalar reconocimiento local. Puedes elegir explícitamente el servicio del navegador o grabar solo audio.'); return; }
            mensaje('Preparando el paquete de español local…');
            try {
                const listo = await Speech.install({langs:['es-ES'], processLocally:true});
                mensaje(listo ? 'Español local preparado. Puedes iniciar la grabación.' : 'No se pudo preparar español local en este navegador.');
            } catch (_) { mensaje('No se pudo instalar el paquete local. Revisa la compatibilidad de tu navegador.'); }
        };
        function terminar() {
            activo = false;
            clearTimeout(reinicio);
            if (!recognition) return Promise.resolve();
            pendienteCierre = new Promise(resolve => {
                cierre = resolve;
                cierreTimer = setTimeout(() => {
                    if (cierre) {
                        const resolver = cierre; cierre = null;
                        recognition.abort();
                        mensaje('El reconocimiento no confirmó el cierre. Revisa si falta el final del texto.');
                        resolver();
                    }
                }, 4000);
            });
            try { recognition.stop(); } catch (_) { clearTimeout(cierreTimer); if (cierre) cierre(); cierre = null; }
            return pendienteCierre;
        }
        const api = {
            async iniciar() {
                grabando = true;
                recognition = null;
                panel.querySelector('[data-vivo-revisado]').checked = false;
                version = ''; texto.value = ''; salida.textContent = ''; parcial.textContent = '';
                generar.disabled = true; modo.disabled = true; texto.readOnly = true;
                barra.hidden = true; fallos = 0;
                if (modo.value === 'no') { mensaje('Solo audio. Puedes añadir una transcripción revisada después.'); return; }
                if (!Speech) { mensaje('Este navegador no admite transcripción en vivo. El audio sí se graba.'); return; }
                recognition = new Speech();
                recognition.lang = 'es-ES'; recognition.continuous = true; recognition.interimResults = true;
                if (modo.value === 'local') {
                    if (!('processLocally' in recognition) || !Speech.available) {
                        mensaje('Reconocimiento local no disponible. Continúa el audio; no se enviará al proveedor.'); return;
                    }
                    recognition.processLocally = true;
                    try {
                        if (await Speech.available({langs:['es-ES'], processLocally:true}) !== 'available') {
                            mensaje('Falta el paquete local de español. Continúa solo la grabación de audio.'); return;
                        }
                    } catch (_) { mensaje('No se pudo habilitar el reconocimiento local. Continúa solo el audio.'); return; }
                }
                if (!grabando) return;
                activo = true;
                let confirmados = new Set();
                recognition.onstart = () => { confirmados = new Set(); };
                recognition.onresult = event => {
                    let temporal = '';
                    for (let i = event.resultIndex; i < event.results.length; i++) {
                        if (event.results[i].isFinal) {
                            if (!confirmados.has(i)) texto.value += event.results[i][0].transcript + '\n';
                            confirmados.add(i);
                        }
                        else temporal += event.results[i][0].transcript;
                    }
                    parcial.textContent = temporal;
                    mensaje(`Transcribiendo · ${texto.value.trim().split(/\s+/).filter(Boolean).length} palabras confirmadas. Revisa posibles omisiones al terminar.`);
                };
                recognition.onerror = event => {
                    fallos++;
                    if (['not-allowed','service-not-allowed','audio-capture','language-not-supported'].includes(event.error) || fallos >= 3) activo = false;
                    mensaje(`Transcripción interrumpida (${event.error}). El audio continúa. Puede faltar texto; revisa antes de generar notas.`);
                };
                recognition.onend = () => {
                    parcial.textContent = '';
                    clearTimeout(cierreTimer);
                    if (cierre) { cierre(); cierre = null; }
                    if (activo) {
                        mensaje('Reconectando el reconocimiento; el audio continúa. Revisa posibles huecos.');
                        reinicio = setTimeout(() => {
                            try { recognition.start(); } catch (_) { activo = false; mensaje('No se pudo reanudar la transcripción. El audio continúa.'); }
                        }, 700);
                    }
                };
                try { recognition.start(); mensaje('Iniciando reconocimiento. Esperando voz…'); }
                catch (_) { activo = false; mensaje('No se pudo iniciar el reconocimiento. El audio continúa.'); }
            },
            async detener() { grabando = false; await terminar(); modo.disabled = false; texto.readOnly = false; },
            guardado(data) {
                version = data.version_audio || '';
                pendienteCierre.then(() => { generar.disabled = !version; });
            },
            limpiar() { version = ''; texto.value = ''; generar.disabled = true; panel.querySelector('[data-vivo-revisado]').checked = false; }
        };
        panel.querySelector('[data-vivo-descargar]').onclick = () => {
            const url = URL.createObjectURL(new Blob([texto.value], {type:'text/plain;charset=utf-8'}));
            const enlace = document.createElement('a'); enlace.href = url; enlace.download = 'transcripcion.txt'; enlace.click();
            setTimeout(() => URL.revokeObjectURL(url), 1000);
        };
        async function enviar(datos) {
            const token = document.cookie.split('; ').find(c => c.startsWith('csrftoken='))?.slice(10);
            const res = await fetch(box.dataset.urlTexto, {method:'POST', credentials:'same-origin',
                headers:{'Content-Type':'application/json', 'X-CSRFToken':token || ''},
                body:JSON.stringify({...datos, version_audio:version}), signal:AbortSignal.timeout(28000)});
            if (!res.headers.get('content-type')?.includes('application/json')) throw Error('El servidor no completó el paso. Conserva tu texto y reintenta.');
            const data = await res.json();
            if (!res.ok) throw Error(data.mensaje || 'No se pudo completar el paso.');
            return data;
        }
        generar.onclick = async () => {
            if (ocupado || !texto.value.trim()) { mensaje('Añade o revisa la transcripción antes de generar notas.'); return; }
            if (!panel.querySelector('[data-vivo-revisado]').checked) { mensaje('Confirma que revisaste la transcripción, incluidas sus posibles omisiones.'); return; }
            if (texto.value.length > 500000) { mensaje('La transcripción supera el límite de 500.000 caracteres.'); return; }
            ocupado = true; generar.disabled = true; texto.readOnly = true;
            box.querySelector('.gsn-rec-start').disabled = true; box.querySelector('.gsn-rec-archivo').disabled = true;
            barra.hidden = false; barra.value = 0;
            const completo = texto.value;
            try {
                let evidencia = [];
                const bloques = Math.ceil(completo.length / 10000);
                for (let i = 0; i < bloques; i++) {
                    mensaje(`Analizando texto ${i+1} de ${bloques} · ${Math.round(70*i/bloques)}%`);
                    evidencia.push((await enviar({accion:'fragmento',texto:completo.slice(i*10000,(i+1)*10000)})).evidencia);
                    barra.value = Math.round(70*(i+1)/bloques);
                }
                let resumen = evidencia.join('\n');
                while (resumen.length > 10000) {
                    mensaje('Consolidando evidencia de toda la sesión · 80%'); barra.value = 80;
                    let reducidos = [];
                    for (let i = 0; i < resumen.length; i += 10000) reducidos.push((await enviar({accion:'fragmento',texto:resumen.slice(i,i+10000)})).evidencia);
                    const siguiente = reducidos.join('\n');
                    if (siguiente.length >= resumen.length) throw Error('No se pudo consolidar el texto. Conserva la transcripción y reintenta.');
                    resumen = siguiente;
                }
                mensaje('Generando borrador clínico · 90%'); barra.value = 90;
                const datos = await enviar({accion:'finalizar', texto:resumen, transcripcion:completo});
                salida.textContent = Object.entries(datos.notas).map(([campo, valor]) => `${campo.replaceAll('_',' ')}\n${valor || 'Sin evidencia'}\n`).join('\n');
                barra.value = 100; mensaje('100% · Borrador guardado. Abre o recarga el expediente para revisar y guardar las notas clínicas.');
            } catch (error) { mensaje(error.name === 'TimeoutError' ? 'El paso tardó demasiado. Tu texto sigue disponible; puedes reintentar.' : error.message); }
            finally { ocupado = false; generar.disabled = false; texto.readOnly = false;
                box.querySelector('.gsn-rec-start').disabled = false; box.querySelector('.gsn-rec-archivo').disabled = false; }
        };
        window.addEventListener('beforeunload', event => {
            if (grabando || activo || ocupado || texto.value) { event.preventDefault(); event.returnValue = ''; }
        });
        return api;
    };
})();
