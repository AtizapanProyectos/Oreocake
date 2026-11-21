/* Plan Mensual: la captura PayPal continúa en el navegador, como la sesión suelta. */
(() => {
    'use strict';
    const dialog = document.getElementById('pm-dialog');
    if (!dialog) return;
    const $ = id => document.getElementById('pm-' + id);
    const admin = dialog.dataset.admin === 'true';
    const moneda = valor => Number(valor).toLocaleString('es-MX', {style: 'currency', currency: 'MXN'}) + ' MXN';
    let modo = admin ? 'admin' : 'paypal';
    let pacienteId = '';
    let terapeutas = [];
    let seleccion = [];
    let plan = null;
    let clave = null;
    let ocupado = false;
    let generacion = 0;
    let paypalRenderizado = false;
    let checkoutEnCurso = false;
    let configCotizada = '';
    let timerIntegrantes;
    let opener;
    const respaldo = new Map();
    const storageKey = () => 'hope-plan-mensual-' + dialog.dataset.usuario + '-' + (pacienteId || 'propio');
    function leer() { try { return {...JSON.parse(localStorage.getItem(storageKey()) || '{}'), ...respaldo.get(storageKey())}; } catch (_) { return respaldo.get(storageKey()) || {}; } }
    function guardar(extra = {}) {
        const datos = {...leer(), clave, plan_id: plan && plan.plan_id, ...extra};
        respaldo.set(storageKey(), datos);
        try { localStorage.setItem(storageKey(), JSON.stringify(datos)); } catch (_) { /* Conservar la referencia en esta pestaña aunque el navegador bloquee storage. */ }
    }
    function limpiarGuardado() { respaldo.delete(storageKey()); try { localStorage.removeItem(storageKey()); } catch (_) {} }
    function mensaje(texto = '', error = false) { $('mensaje').textContent = texto; $('mensaje').classList.toggle('pm-error', error); }
    function contexto() {
        const datos = {tipo_servicio: $('servicio').value, modalidad: $('modalidad').value, integrantes_familia: $('integrantes').value};
        if (pacienteId) datos.paciente_id = pacienteId;
        return datos;
    }
    function espera(valor, texto = 'Preparando tus horarios…') {
        ocupado = valor;
        $('espera').hidden = !valor;
        $('espera-texto').textContent = texto;
        dialog.setAttribute('aria-busy', String(valor));
        dialog.querySelectorAll('#pm-config select, #pm-config input, #pm-agregar, #pm-admin-confirmar').forEach(el => { el.disabled = valor || Boolean(plan); });
        $('cerrar').disabled = valor;
        $('seleccion').querySelectorAll('button').forEach(el => { el.disabled = valor || Boolean(plan); });
        ['reintentar', 'confirmar-recuperado', 'cancelar-reserva', 'recargar', 'revision-confirmar', 'revision-cancelar'].forEach(id => { if ($(id)) $(id).disabled = valor; });
        if (admin && !valor) $('admin-confirmar').disabled = seleccion.length !== 4 || Boolean(plan);
    }
    async function api(nombre, datos = {}, post = false) {
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), 45000);
        try {
            const params = new URLSearchParams({...datos, ...(pacienteId ? {paciente_id: pacienteId} : {})});
            const token = document.querySelector('[name=csrfmiddlewaretoken]');
            const response = await fetch(dialog.dataset[nombre] + (post ? '' : '?' + params), {
                method: post ? 'POST' : 'GET', credentials: 'same-origin', signal: controller.signal,
                headers: post ? {'X-CSRFToken': token ? token.value : '', 'Content-Type': 'application/x-www-form-urlencoded'} : {},
                ...(post ? {body: params} : {})
            });
            const data = await response.json();
            if (!response.ok || data.status !== 'success') throw new Error(data.message || 'No pudimos completar la solicitud.');
            return data;
        } catch (error) {
            if (error.name === 'AbortError') throw new Error('La respuesta está tardando. Consulta tu plan antes de volver a intentar el pago.');
            throw error;
        } finally { clearTimeout(timer); }
    }
    const fechaTexto = fecha => new Date(fecha + 'T12:00:00').toLocaleDateString('es-MX', {weekday: 'short', day: 'numeric', month: 'long'});
    const diasEntre = (a, b) => Math.abs((Date.parse(a + 'T12:00:00Z') - Date.parse(b + 'T12:00:00Z')) / 86400000);
    function compatibles() {
        return terapeutas.filter(t => seleccion.every(c => (t.dias[c.fecha] || []).includes(c.hora)));
    }
    function renderCalendario() {
        const dias = new Map();
        compatibles().forEach(t => Object.entries(t.dias).forEach(([fecha, horas]) => {
            if (seleccion.some(c => c.fecha === fecha) || seleccion.some(c => diasEntre(c.fecha, fecha) > 29)) return;
            if (!dias.has(fecha)) dias.set(fecha, new Set());
            horas.forEach(h => dias.get(fecha).add(h));
        }));
        const anterior = $('fecha').value;
        $('fecha').replaceChildren();
        [...dias.keys()].sort().forEach(fecha => $('fecha').add(new Option(fechaTexto(fecha), fecha)));
        if (dias.has(anterior)) $('fecha').value = anterior;
        function horas() {
            $('hora').replaceChildren();
            const orden = h => {
                const [hora, periodo] = h.split(' ');
                const [hh, mm] = hora.split(':').map(Number);
                return (hh % 12 + (periodo === 'PM' ? 12 : 0)) * 60 + mm;
            };
            [...(dias.get($('fecha').value) || [])].sort((a, b) => orden(a) - orden(b)).forEach(h => $('hora').add(new Option(h, h)));
            $('agregar').disabled = ocupado || Boolean(plan) || seleccion.length >= 4 || !$('hora').value;
        }
        $('fecha').onchange = horas;
        horas();
        if (!dias.size && seleccion.length < 4) mensaje('No hay más fechas compatibles con esta selección. Quita una fecha o prueba otra modalidad.', true);
    }
    function renderSeleccion() {
        $('contador').textContent = seleccion.length + ' de 4 citas';
        $('seleccion').replaceChildren();
        seleccion.forEach((cita, i) => {
            const li = document.createElement('li');
            li.textContent = fechaTexto(cita.fecha) + ' · ' + cita.hora + (cita.terapeuta ? ' · ' + cita.terapeuta : '');
            if (!plan) {
                const quitar = document.createElement('button');
                quitar.type = 'button'; quitar.className = 'pm-link'; quitar.textContent = 'Quitar';
                quitar.setAttribute('aria-label', 'Quitar sesión del ' + fechaTexto(cita.fecha));
                quitar.onclick = () => { if (!ocupado) { seleccion.splice(i, 1); clave = null; mensaje(); renderSeleccion(); renderCalendario(); } };
                li.append(quitar);
            }
            if (cita.pase_url) { const a = document.createElement('a'); a.href = cita.pase_url; a.textContent = 'Descargar pase'; li.append(a); }
            $('seleccion').append(li);
        });
        const completo = seleccion.length === 4;
        if (admin) $('admin-confirmar').disabled = !completo || ocupado || Boolean(plan);
        else {
            $('paypal').hidden = modo !== 'paypal' || !completo || Boolean(plan && plan.estado !== 'reservado');
            $('whatsapp').hidden = modo !== 'transferencia' || !completo || Boolean(plan);
            $('transferencia-aviso').hidden = modo !== 'transferencia' || Boolean(plan);
            if (!$('whatsapp').hidden) {
                const servicio = $('servicio').selectedOptions[0].textContent;
                const texto = ['Hola, quiero apartar mi horario con el Plan Mensual de Espacio HOPE.',
                    'Servicio: ' + servicio + ($('servicio').value === 'familiar' ? ' (' + $('integrantes').value + ' integrantes)' : ''),
                    'Modalidad: ' + $('modalidad').value,
                    ...seleccion.map((c, i) => (i + 1) + '. ' + c.fecha + ' a las ' + c.hora + ' (CDMX)'),
                    'Total: ' + $('total').textContent,
                    'Enviaré mi comprobante. Solicito revisión y confirmación de las cuatro citas, sujetas a disponibilidad.'].join('\n');
                $('whatsapp').href = 'https://wa.me/525521130230?text=' + encodeURIComponent(texto);
            }
            if (!$('paypal').hidden) renderPayPal();
        }
    }
    async function cargarDisponibilidad() {
        const version = ++generacion;
        const configuracion = contexto();
        seleccion = []; terapeutas = []; clave = null;
        configCotizada = '';
        $('total').textContent = 'Calculando total…';
        $('por-sesion').textContent = '';
        $('integrantes-label').hidden = $('servicio').value !== 'familiar';
        mensaje(); espera(true);
        renderSeleccion();
        try {
            const data = await api('disponibilidad', configuracion);
            if (version !== generacion) return;
            terapeutas = data.terapeutas;
            configCotizada = JSON.stringify(configuracion);
            $('total').textContent = moneda(data.monto);
            $('por-sesion').textContent = moneda(data.por_sesion) + ' por sesión';
            if (!terapeutas.length) mensaje('No hay horarios disponibles para esta modalidad por ahora.', true);
        } catch (error) { if (version === generacion) mensaje(error.message, true); }
        finally { if (version === generacion) { espera(false); renderCalendario(); renderSeleccion(); } }
    }
    function presentarPlan(data) {
        plan = data; clave = data.clave;
        seleccion = data.citas;
        $('servicio').value = data.tipo_servicio;
        $('modalidad').value = data.modalidad;
        $('integrantes').value = data.integrantes_familia || 2;
        $('integrantes-label').hidden = data.tipo_servicio !== 'familiar';
        $('config').hidden = true;
        $('sin-cobro').hidden = data.origen !== 'admin';
        $('total').textContent = moneda(data.monto);
        $('por-sesion').textContent = moneda(Number(data.monto) / 4) + ' por sesión';
        $('cancelar-reserva').hidden = data.estado !== 'reservado';
        $('reintentar').hidden = !['reservado', 'procesando'].includes(data.estado);
        $('confirmar-recuperado').hidden = data.estado !== 'procesando' || !leer().referencia;
        $('recargar').hidden = data.estado !== 'confirmado';
        if (admin) { $('admin-confirmar').hidden = true; $('revision').hidden = !['reservado', 'procesando'].includes(data.estado); }
        renderSeleccion();
        if (data.estado === 'confirmado') {
            limpiarGuardado();
            mensaje('¡Tus cuatro citas están confirmadas! Tu mes, tu espacio: ya tienes tus horarios apartados.');
        } else if (data.estado === 'procesando') {
            mensaje('Tu pago está pendiente de resolver. Tus cuatro horarios se conservan. No vuelvas a pagar; consulta el estado o contacta al equipo con tu referencia.', true);
            guardar();
        } else if (data.estado === 'reservado') {
            mensaje('Tus cuatro horarios están apartados hasta las ' + new Date(data.vence).toLocaleTimeString('es-MX', {timeZone: 'America/Mexico_City', hour: '2-digit', minute: '2-digit'}) + ' (CDMX).');
            guardar();
        } else {
            limpiarGuardado();
            mensaje('Esta reserva fue cancelada o venció. Cierra y vuelve a elegir tus fechas.');
        }
        espera(false);
    }
    async function consultar() {
        espera(true, 'Consultando tu plan…');
        try {
            const guardado = leer();
            if (plan || guardado.plan_id) {
                const data = await api('estado', {plan_id: plan ? plan.plan_id : guardado.plan_id});
                presentarPlan(data); return true;
            }
            const data = await api('estado');
            if (data.planes.length) { presentarPlan(data.planes[0]); return true; }
            return false;
        } finally { espera(false); }
    }
    async function reservar() {
        if (plan) return plan;
        if (seleccion.length !== 4) throw new Error('Elige tus cuatro fechas antes de continuar.');
        if (JSON.stringify(contexto()) !== configCotizada) throw new Error('Espera a que se actualice el importe y vuelve a elegir tus fechas.');
        const guardado = leer();
        clave = clave || guardado.clave || crypto.randomUUID();
        guardar();
        const data = await api('reservar', {...contexto(), clave, citas: JSON.stringify(seleccion)}, true);
        presentarPlan(data);
        return data;
    }
    async function actualizar(accion, extra = {}) {
        const data = await api('actualizar', {plan_id: plan.plan_id, accion, ...extra}, true);
        presentarPlan(data);
        return data;
    }
    async function confirmarGuardado() {
        espera(true, 'Confirmando tus cuatro citas…');
        try {
            await actualizar('confirmar', {referencia: leer().referencia || '', resultado: 'COMPLETED'});
        } catch (error) { mensaje(error.message + '\nNo repitas el pago. Puedes retomar la confirmación o contactar al equipo.', true); $('reintentar').hidden = false; }
        finally { espera(false); }
    }
    function renderPayPal() {
        if (paypalRenderizado || admin) return;
        if (!window.paypal) { mensaje('No se pudo cargar PayPal. Recarga la página o elige transferencia.', true); return; }
        paypalRenderizado = true;
        window.paypal.Buttons({
            onClick: (_data, actions) => {
                if (ocupado || checkoutEnCurso || seleccion.length !== 4 || plan && plan.estado !== 'reservado') return actions.reject();
                if (!plan && JSON.stringify(contexto()) !== configCotizada) return actions.reject();
                if (!window.tieneConsentimiento) {
                    dialog.close();
                    if (window.abrirModalConsentimiento) window.abrirModalConsentimiento();
                    return actions.reject();
                }
                return actions.resolve();
            },
            createOrder: async (_data, actions) => {
                checkoutEnCurso = true;
                espera(true, 'Apartando tus cuatro horarios y preparando el pago…');
                try {
                    const reserva = await reservar();
                    if (reserva.estado !== 'reservado') throw new Error('Consulta el estado de tu plan antes de continuar.');
                    espera(true, 'Esperando tu autorización en PayPal…');
                    return await actions.order.create({purchase_units: [{amount: {currency_code: 'MXN', value: Number(reserva.monto).toFixed(2)}}]});
                } catch (error) { checkoutEnCurso = false; mensaje(error.message, true); throw error; }
                finally { espera(checkoutEnCurso, 'Esperando tu autorización en PayPal…'); }
            },
            onApprove: async (data, actions) => {
                espera(true, 'Procesando tu pago. Estamos preparando tus cuatro citas…');
                try {
                    const iniciado = await actualizar('capturar');
                    if (iniciado.estado !== 'procesando') throw new Error('Tu reserva ya no está activa. Consulta el estado de tu plan.');
                    espera(true, 'Procesando tu pago. No cierres esta ventana…');
                    const pago = await actions.order.capture();
                    if (pago.status !== 'COMPLETED') throw new Error('PayPal aún no confirmó el pago. Contacta al equipo antes de volver a pagar.');
                    guardar({referencia: pago.id || data.orderID});
                    await confirmarGuardado();
                } catch (error) {
                    mensaje(error.message + '\nNo vuelvas a pagar si recibiste el cargo. Consulta tu plan o contacta al equipo.', true);
                    $('reintentar').hidden = false;
                } finally { checkoutEnCurso = false; espera(false); }
            },
            onCancel: async () => {
                checkoutEnCurso = false;
                espera(false);
                if (plan && plan.estado === 'reservado') {
                    try { await actualizar('cancelar'); } catch (error) { mensaje(error.message, true); }
                }
            },
            onError: () => {
                checkoutEnCurso = false;
                espera(false);
                mensaje('No pudimos completar el proceso PayPal. Consulta tu plan antes de intentar otro pago. Si recibiste el cargo, contacta al equipo.', true);
                if (plan || leer().clave) $('reintentar').hidden = false;
            }
        }).render('#pm-paypal-botones').catch(() => { paypalRenderizado = false; mensaje('No se pudo cargar PayPal. Recarga la página para continuar.', true); });
    }
    document.querySelectorAll('[data-pm-abrir]').forEach(btn => btn.addEventListener('click', async () => {
        if (ocupado) return;
        if (admin) {
            const ctx = window.contextoPlanMensualAdmin ? window.contextoPlanMensualAdmin() : {};
            if (!ctx.pacienteId || ctx.esTectum) { alert('Selecciona primero un paciente HOPE para agendar su Plan Mensual.'); return; }
            if (pacienteId !== String(ctx.pacienteId)) { plan = null; clave = null; seleccion = []; }
            pacienteId = String(ctx.pacienteId);
            $('paciente').hidden = false;
            $('paciente').textContent = 'Paciente: ' + ctx.nombre;
            $('servicio').value = ctx.tipoSesion || 'individual';
            $('modalidad').value = ctx.modalidad || 'En línea';
            $('integrantes').value = ctx.integrantesFamilia || 2;
        } else if (!plan) {
            $('servicio').value = window.tipoServicioAgendado || 'individual';
            $('modalidad').value = window.modalidadSeleccionada || 'En línea';
            $('integrantes').value = window.integrantesFamilia || 2;
        }
        modo = admin ? 'admin' : btn.dataset.pmAbrir === 'transferencia' ? 'transferencia' : 'paypal';
        if (plan && ['expirado', 'cancelado'].includes(plan.estado)) { plan = null; clave = null; seleccion = []; }
        opener = btn;
        dialog.showModal();
        $('config').hidden = Boolean(plan);
        if (admin) { $('admin-confirmar').hidden = Boolean(plan); $('revision').hidden = true; $('revisado').checked = false; }
        try {
            if (!await consultar()) {
                plan = null; $('config').hidden = false;
                $('cancelar-reserva').hidden = true; $('reintentar').hidden = true; $('confirmar-recuperado').hidden = true; $('recargar').hidden = true;
                await cargarDisponibilidad();
            }
        } catch (error) { mensaje(error.message, true); $('reintentar').hidden = false; }
    }));
    $('agregar').onclick = () => {
        if (ocupado || plan || seleccion.length >= 4 || !$('fecha').value || !$('hora').value) return;
        seleccion.push({fecha: $('fecha').value, hora: $('hora').value});
        seleccion.sort((a, b) => a.fecha.localeCompare(b.fecha));
        mensaje(); renderSeleccion(); renderCalendario();
    };
    ['servicio', 'modalidad'].forEach(id => $(id).addEventListener('change', () => { if (!plan) cargarDisponibilidad(); }));
    $('integrantes').addEventListener('input', () => {
        if (plan) return;
        clearTimeout(timerIntegrantes);
        seleccion = []; configCotizada = '';
        renderSeleccion();
        timerIntegrantes = setTimeout(cargarDisponibilidad, 350);
    });
    $('integrantes').addEventListener('change', () => {
        clearTimeout(timerIntegrantes);
        if (!plan && JSON.stringify(contexto()) !== configCotizada) cargarDisponibilidad();
    });
    $('cerrar').onclick = () => { if (!ocupado) dialog.close(); };
    dialog.addEventListener('cancel', event => { if (ocupado) event.preventDefault(); });
    dialog.addEventListener('close', () => {
        clearTimeout(timerIntegrantes);
        if (plan && ['confirmado', 'cancelado', 'expirado'].includes(plan.estado)) { plan = null; clave = null; seleccion = []; }
        if (opener) opener.focus();
    });
    $('reintentar').onclick = async () => { try { if (!await consultar()) { mensaje(); await cargarDisponibilidad(); } } catch (error) { mensaje(error.message, true); } };
    $('confirmar-recuperado').onclick = confirmarGuardado;
    $('cancelar-reserva').onclick = async () => { espera(true); try { await actualizar('cancelar'); } catch (error) { mensaje(error.message, true); } finally { espera(false); } };
    $('recargar').onclick = () => location.reload();
    if (admin) {
        $('admin-confirmar').onclick = async () => { espera(true, 'Agendando las cuatro citas sin cobro…'); try { await reservar(); } catch (error) { mensaje(error.message, true); $('reintentar').hidden = false; } finally { espera(false); } };
        async function revisar(accion) {
            if (!$('revisado').checked) { mensaje('Marca que revisaste el pago antes de resolver el plan.', true); return; }
            espera(true, 'Guardando la revisión…');
            try { await actualizar(accion, {revisado: '1', referencia: $('referencia').value}); }
            catch (error) { mensaje(error.message, true); }
            finally { espera(false); }
        }
        $('revision-confirmar').onclick = () => revisar('confirmar');
        $('revision-cancelar').onclick = () => revisar('cancelar');
    } else {
        // Mostrar recuperación aunque la página se haya cerrado durante la captura.
        api('estado').then(data => { $('pendiente-aviso').hidden = !data.planes.length; }).catch(() => {});
    }
})();
