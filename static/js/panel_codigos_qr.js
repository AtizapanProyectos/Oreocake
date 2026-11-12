(() => {
    const panel = document.querySelector('[data-estadisticas]');
    if (!panel) return;

    const tarjetas = new Map(Array.from(panel.querySelectorAll('[data-qr-id]'), tarjeta => [
        tarjeta.dataset.qrId, tarjeta,
    ]));
    const campos = ['escaneos', 'visitas_directas'];
    const formato = new Intl.NumberFormat('es-MX');
    const estado = panel.querySelector('[data-estado-estadisticas]');
    const url = new URL(panel.dataset.estadisticas, window.location.origin);
    tarjetas.forEach((_, codigo) => url.searchParams.append('codigo', codigo));
    let consultando = false;

    async function actualizar() {
        if (document.hidden || consultando) return;
        consultando = true;
        try {
            const respuesta = await fetch(url, {
                credentials: 'same-origin', cache: 'no-store', signal: AbortSignal.timeout(8000),
            });
            if (!respuesta.ok) throw Error('Estadísticas no disponibles');
            const datos = await respuesta.json();
            campos.forEach(campo => {
                panel.querySelector(`[data-total="${campo}"]`).textContent = formato.format(datos.totales[campo]);
            });
            datos.codigos.forEach(fila => {
                const tarjeta = tarjetas.get(fila.codigo);
                if (!tarjeta) return;
                campos.forEach(campo => {
                    tarjeta.querySelector(`[data-metrica="${campo}"]`).textContent = formato.format(fila[campo]);
                });
            });
            estado.textContent = 'Actualización cada 10 segundos mientras el panel está visible.';
        } catch (error) {
            estado.textContent = 'No se pudieron actualizar los conteos. Mostrando el último dato disponible.';
        } finally {
            consultando = false;
        }
    }

    panel.querySelectorAll('[data-copiar]').forEach(boton => {
        boton.addEventListener('click', async () => {
            const campo = document.getElementById(boton.dataset.copiar);
            const mensaje = boton.closest('[data-qr-id]').querySelector('[data-estado-copia]');
            try {
                await navigator.clipboard.writeText(campo.value);
                mensaje.textContent = 'Enlace copiado.';
            } catch (error) {
                campo.focus();
                campo.select();
                mensaje.textContent = 'Enlace seleccionado para copiar.';
            }
        });
    });

    setInterval(actualizar, 10000);
    document.addEventListener('visibilitychange', actualizar);
})();
