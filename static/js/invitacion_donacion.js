/* Invitación compartida por HOPE y TECTUM después de confirmar la cita. */
(() => {
    'use strict';
    const seccion = document.getElementById('donar-terapia-confirmacion');
    if (!seccion || window.mostrarDonacionTerapia) return;
    const monto = seccion.querySelector('#donar-terapia-monto');
    const impacto = seccion.querySelector('#donar-terapia-impacto');
    const botonesMonto = [...seccion.querySelectorAll('[data-monto]')];
    const mensajes = new Map([
        ['100', '“Dona una terapia a una persona.”'], ['400', '“Dona un mes de terapia.”'],
        ['1200', '“Terapia para una persona.”'], ['7200', '“Media beca de terapia.”'],
        ['14500', '“Una beca completa.”']
    ]);
    function reflejarMonto(otro = false) {
        botonesMonto.forEach(b => b.setAttribute('aria-pressed', String(otro ? b.dataset.monto === '' : b.dataset.monto !== '' && Number(b.dataset.monto) === Number(monto.value))));
        impacto.textContent = mensajes.get(String(Number(monto.value))) || '“Cada aporte ayuda a una persona.”';
    }
    botonesMonto.forEach(b => b.addEventListener('click', () => {
        monto.value = b.dataset.monto;
        reflejarMonto(b.dataset.monto === '');
        if (b.dataset.monto === '') monto.focus();
    }));
    monto.addEventListener('input', () => reflejarMonto(!mensajes.has(String(Number(monto.value)))));
    seccion.addEventListener('close', () => document.querySelector('#alerta-exito-cita button')?.focus());

    window.mostrarDonacionTerapia = async citaId => {
        if (!citaId || seccion.dataset.cita === String(citaId)) return;
        const clave = 'hope.donacion.mostrada.' + citaId;
        try {
            if (sessionStorage.getItem(clave)) return;
            sessionStorage.setItem(clave, '1');
        } catch (_) { /* La invitación funciona también sin almacenamiento. */ }
        seccion.dataset.cita = citaId;
        seccion.showModal();
        const contenedor = seccion.querySelector('#paypal-donar-terapia');
        const estado = seccion.querySelector('#estado-donar-terapia');
        if (seccion.dataset.paypalInicializado) {
            contenedor.hidden = false;
            await HopeDonaciones.refrescar(contenedor);
            return;
        }
        try {
            const botones = window.paypal.Buttons(HopeDonaciones.opciones({
                origen:'post_cita', contenedor, estado, citaId:() => seccion.dataset.cita,
                monto:() => {
                    if (!monto.checkValidity() || !monto.value) {
                        monto.reportValidity();
                        throw new Error('Indica un importe válido para donar, con un máximo de dos decimales.');
                    }
                    return monto.value;
                },
                style:{layout:'vertical', color:'blue', shape:'rect', label:'donate'},
                alCompletar:() => {
                    contenedor.hidden = true;
                    estado.textContent = 'Gracias por tu aporte y por ayudar a otra persona.';
                }
            }));
            if (!botones.isEligible()) throw new Error('PayPal no disponible');
            await botones.render(contenedor);
            seccion.dataset.paypalInicializado = '1';
        } catch (_) {
            estado.textContent = 'PayPal no está disponible para la donación en este momento.';
        }
    };
})();
