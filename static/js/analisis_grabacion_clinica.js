(() => {
    const pendientes = new Set(['pendiente', 'procesando']);

    function iniciar(panel) {
        const generar = panel.querySelector('.analisis-generar');
        const revisar = panel.querySelector('.analisis-revisar');
        const estado = panel.querySelector('.analisis-estado');
        const resultados = panel.querySelector('.analisis-resultados');
        let temporizador;
        let notas = {};
        let historialId;

        function mostrar(datos) {
            estado.textContent = datos.mensaje;
            estado.toggleAttribute('data-error', datos.estado === 'error');
            const ocupado = pendientes.has(datos.estado);
            generar.disabled = ocupado || datos.estado === 'listo';
            generar.setAttribute('aria-busy', String(ocupado));
            const proceso = datos.procesamiento;
            generar.querySelector('i').classList.toggle('fa-spin', Boolean(proceso?.activo));
            const cola = panel.querySelector('.analisis-cola');
            cola.hidden = !proceso;
            if (proceso) {
                const porcentaje = Number.isInteger(proceso.porcentaje) ? `${proceso.porcentaje}% · ` : '';
                panel.querySelector('.analisis-cola-texto').textContent = porcentaje + proceso.mensaje;
                const barraProceso = panel.querySelector('.analisis-cola-barra');
                barraProceso.hidden = !Number.isInteger(proceso.porcentaje);
                barraProceso.value = proceso.porcentaje ?? 0;
                panel.querySelector('.analisis-cola-aviso').textContent = proceso.aviso || '';
            }
            resultados.hidden = datos.estado !== 'listo';
            if (datos.estado === 'listo') {
                notas = datos.notas;
                historialId = datos.historial_id;
                panel.querySelectorAll('[data-nota]').forEach(campo => {
                    campo.textContent = notas[campo.dataset.nota] || 'Sin evidencia en la transcripción.';
                });
                const progreso = datos.progreso;
                const barra = panel.querySelector('.analisis-progreso progress');
                barra.hidden = !Number.isInteger(progreso.porcentaje);
                if (!barra.hidden) barra.value = progreso.porcentaje;
                panel.querySelector('[data-progreso-nivel]').textContent = progreso.nivel || '';
                panel.querySelector('[data-progreso-valor]').textContent = barra.hidden
                    ? 'Sin evidencia suficiente para estimar un porcentaje.'
                    : `${progreso.porcentaje}% estimado`;
                panel.querySelector('[data-progreso-compromiso]').textContent =
                    'Compromiso: ' + (progreso.compromiso || 'Sin evidencia suficiente.');
                panel.querySelector('[data-progreso-evolucion]').textContent =
                    'Evolución: ' + (progreso.evolucion || 'Sin evidencia suficiente.');
            }
            if (ocupado) temporizador = setTimeout(() => consultar('GET'), 5000);
        }

        async function consultar(method) {
            clearTimeout(temporizador);
            generar.disabled = true;
            if (method === 'POST') estado.textContent = 'Solicitando análisis…';
            try {
                const token = document.querySelector('#formBitacoraNueva [name=csrfmiddlewaretoken]').value;
                const respuesta = await fetch(panel.dataset.url, {
                    method, credentials: 'same-origin',
                    headers: method === 'POST' ? {'X-CSRFToken': token} : {},
                });
                if (!respuesta.headers.get('content-type')?.includes('application/json')) {
                    throw new Error(respuesta.status >= 500
                        ? 'El servidor no pudo consultar el análisis. Administración debe revisar las migraciones y los registros de Railway.'
                        : 'No se pudo acceder al análisis. Actualiza la página y verifica tu sesión.');
                }
                const datos = await respuesta.json();
                if (!respuesta.ok) throw new Error(datos.mensaje || 'No se pudo consultar el análisis.');
                mostrar(datos);
            } catch (error) {
                mostrar({estado: 'error', mensaje: error.message || 'No se pudo consultar el análisis. Intenta de nuevo.'});
            }
        }

        generar.addEventListener('click', () => consultar('POST'));
        revisar.addEventListener('click', async () => {
            revisar.disabled = true;
            try {
                if (historialId) {
                    if (!window.confirm('¿Usar el borrador en el formulario de edición? Los cambios se aplicarán al guardar.')) return;
                    if (!await editarBitacora(historialId)) return;
                } else {
                    abrirWizard(panel.dataset.cita);
                }
                const formulario = document.getElementById('formBitacoraNueva');
                Object.entries(notas).forEach(([nombre, contenido]) => {
                    if (contenido && formulario.elements.namedItem(nombre)) {
                        formulario.elements.namedItem(nombre).value = contenido;
                    }
                });
            } catch (error) {
                estado.textContent = 'No se pudo abrir la bitácora para edición.';
            } finally {
                revisar.disabled = false;
            }
        });
        consultar('GET');
    }

    const observador = new IntersectionObserver(entradas => {
        entradas.forEach(entrada => {
            if (!entrada.isIntersecting) return;
            observador.unobserve(entrada.target);
            iniciar(entrada.target);
        });
    });
    document.querySelectorAll('.analisis-grabacion').forEach(panel => observador.observe(panel));
})();
