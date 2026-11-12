(() => {
    document.querySelectorAll('.analisis-grabacion').forEach(panel => {
        const modal = panel.querySelector('dialog');
        const texto = panel.querySelector('[data-texto-contenido]');
        const estado = panel.querySelector('[data-texto-estado]');
        const revisado = panel.querySelector('[data-texto-revisado]');
        const guardar = panel.querySelector('[data-texto-guardar]');
        const generar = panel.querySelector('[data-texto-generar]');
        const resultados = panel.querySelector('[data-texto-resultados]');
        let version, revision, guardado = '', notas = {}, historialId;
        let ocupado = false, cargado = false, anterior = false;

        function actualizar() {
            texto.disabled = ocupado || !cargado;
            guardar.disabled = ocupado || !cargado || !texto.value.trim() || (anterior && !revisado.checked);
            generar.disabled = guardar.disabled || !revisado.checked;
            revisado.disabled = ocupado;
            panel.querySelector('[data-texto-aplicar]').disabled = ocupado;
        }

        async function solicitar(datos) {
            const opciones = { credentials: 'same-origin', signal: AbortSignal.timeout(30000) };
            if (datos) {
                const token = document.querySelector('#formBitacoraNueva [name=csrfmiddlewaretoken]').value;
                Object.assign(opciones, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': token },
                    body: JSON.stringify({ ...datos, version_audio: version, revision })
                });
            }
            const respuesta = await fetch(panel.dataset.url + (datos ? '' : '?expediente=1'), opciones);
            if (!respuesta.headers.get('content-type')?.includes('application/json')) {
                throw Error('No se pudo recuperar la respuesta. Comprueba tu conexión y tu sesión.');
            }
            const contenido = await respuesta.json();
            if (!respuesta.ok) throw Error(contenido.mensaje || 'No se pudo completar la operación.');
            return contenido;
        }

        function mostrarNotas(datos) {
            notas = datos.notas || {};
            historialId = datos.historial_id;
            resultados.hidden = !Object.keys(notas).length;
            panel.querySelectorAll('[data-nota]').forEach(campo => {
                campo.textContent = notas[campo.dataset.nota] || 'Sin evidencia en la transcripción.';
            });
        }

        async function abrir() {
            modal.showModal();
            if (cargado && texto.value.trim() !== guardado) return;
            ocupado = true;
            cargado = false;
            estado.textContent = 'Recuperando la transcripción de esta sesión…';
            actualizar();
            try {
                const datos = await solicitar();
                version = datos.version_audio;
                revision = datos.revision;
                anterior = datos.anterior;
                texto.value = datos.transcripcion;
                guardado = texto.value.trim();
                revisado.checked = false;
                cargado = true;
                mostrarNotas(datos);
                estado.textContent = anterior
                    ? 'Este texto pertenece al audio anterior. Revisa su correspondencia antes de guardarlo para el audio actual.'
                    : datos.fuente === 'bitacora'
                        ? 'Transcripción recuperada del archivo de esta bitácora.'
                        : guardado ? 'Transcripción recuperada.' : 'Puedes pegar y guardar la transcripción de esta sesión.';
            } catch (error) {
                estado.textContent = error.message;
            } finally {
                ocupado = false;
                actualizar();
            }
        }

        async function guardarTexto() {
            const datos = await solicitar({ accion: 'guardar', transcripcion: texto.value.trim() });
            revision = datos.revision;
            guardado = texto.value.trim();
            anterior = false;
            estado.textContent = datos.mensaje;
        }

        async function procesar(conNotas) {
            ocupado = true;
            actualizar();
            try {
                estado.textContent = 'Guardando transcripción…';
                await guardarTexto();
                if (conNotas) {
                    estado.textContent = 'Generando el borrador con el texto revisado…';
                    const datos = await solicitar({ accion: 'generar' });
                    mostrarNotas(datos);
                    estado.textContent = datos.mensaje;
                }
            } catch (error) {
                estado.textContent = error.name === 'TimeoutError'
                    ? 'La respuesta tardó demasiado. Tu texto sigue aquí. Puedes consultar de nuevo al reabrir.'
                    : error.message;
            } finally {
                ocupado = false;
                actualizar();
            }
        }

        panel.querySelectorAll('[data-texto-abrir]').forEach(boton => boton.addEventListener('click', abrir));
        panel.querySelector('[data-texto-cerrar]').addEventListener('click', () => {
            if (!ocupado) modal.close();
        });
        modal.addEventListener('cancel', event => { if (ocupado) event.preventDefault(); });
        guardar.addEventListener('click', () => procesar(false));
        generar.addEventListener('click', () => procesar(true));
        revisado.addEventListener('change', actualizar);
        texto.addEventListener('input', () => {
            revisado.checked = false;
            resultados.hidden = true;
            estado.textContent = 'Hay cambios sin guardar.';
            actualizar();
        });
        window.addEventListener('beforeunload', event => {
            if (ocupado || (cargado && texto.value.trim() !== guardado)) {
                event.preventDefault();
                event.returnValue = '';
            }
        });
        panel.querySelector('[data-texto-aplicar]').addEventListener('click', async () => {
            if (!window.confirm('¿Llevar este borrador al formulario de bitácora para revisarlo y guardarlo?')) return;
            ocupado = true;
            actualizar();
            try {
                if (historialId) {
                    if (!await editarBitacora(historialId)) return;
                } else {
                    abrirWizard(panel.dataset.cita);
                }
                const formulario = document.getElementById('formBitacoraNueva');
                Object.entries(notas).forEach(([campo, valor]) => {
                    if (formulario.elements.namedItem(campo)) formulario.elements.namedItem(campo).value = valor;
                });
                modal.close();
            } catch (error) {
                estado.textContent = 'No se pudo abrir la bitácora. El borrador sigue disponible.';
            } finally {
                ocupado = false;
                actualizar();
            }
        });
    });
})();