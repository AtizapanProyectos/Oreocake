/* Registro de donaciones; el criterio de cobro es la respuesta COMPLETED del SDK. */
(() => {
    'use strict';
    if (window.HopeDonaciones) return;
    const config = document.getElementById('hope-donaciones-config');
    if (!config) return;
    const memoria = new Map();
    const controladores = new WeakMap();
    const dinero = valor => Number(valor).toLocaleString('es-MX', {style:'currency', currency:'MXN'}) + ' MXN';
    async function api(ruta, datos, post = false) {
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), 30000);
        try {
            const parametros = new URLSearchParams(datos);
            const token = config.querySelector('[name=csrfmiddlewaretoken]')?.value;
            const respuesta = await fetch(config.dataset[ruta] + (post ? '' : '?' + parametros), {
                method:post ? 'POST' : 'GET', credentials:'same-origin', signal:controller.signal,
                headers:post ? {'X-CSRFToken':token || '', 'Content-Type':'application/x-www-form-urlencoded'} : {},
                ...(post ? {body:parametros} : {})
            });
            const resultado = await respuesta.json();
            if (!respuesta.ok || resultado.status !== 'success') {
                const error = new Error(resultado.message || 'No pudimos consultar tu donación.');
                error.status = respuesta.status;
                throw error;
            }
            return resultado;
        } catch (error) {
            if (error.name === 'AbortError') throw new Error('La respuesta está tardando. Consulta tu donación antes de intentar otro pago.');
            throw error;
        } finally { clearTimeout(timer); }
    }
    function leer(clave) {
        try { return {...JSON.parse(localStorage.getItem(clave) || '{}'), ...memoria.get(clave)}; }
        catch (_) { return memoria.get(clave) || {}; }
    }
    function escribir(clave, datos) {
        memoria.set(clave, datos);
        try { localStorage.setItem(clave, JSON.stringify(datos)); } catch (_) {}
    }
    function borrar(clave) { memoria.delete(clave); try { localStorage.removeItem(clave); } catch (_) {} }

    function opciones(opts) {
        const contenedor = opts.contenedor;
        const controles = document.createElement('div'); controles.className = 'donacion-registro';
        const texto = opts.estado || document.createElement('p'); texto.setAttribute('role','status'); texto.setAttribute('aria-live','polite');
        if (opts.estado) texto.parentNode.insertBefore(controles, texto);
        else contenedor.parentNode.insertBefore(controles, contenedor.nextSibling);
        controles.append(texto);
        const reintentar = document.createElement('button'); reintentar.type = 'button'; reintentar.hidden = true; reintentar.textContent = 'Reintentar guardado';
        const consultar = document.createElement('button'); consultar.type = 'button'; consultar.hidden = true; consultar.textContent = 'Consultar mi donación';
        controles.append(reintentar, consultar);
        let donacion = null, scope = '', clave = '', cacheLocal = {}, acciones = null;
        let checkout = false, trabajando = false, capturando = false, bloqueo = false, agradecida = null;
        const contexto = () => ({origen:opts.origen, ...(opts.citaId ? {cita_id:String(opts.citaId())} : {})});
        function mensaje(valor) { texto.textContent = valor; }
        function guardar(extra = {}) {
            cacheLocal = {...cacheLocal, ...(donacion ? {folio:donacion.folio, orden:donacion.orden || cacheLocal.orden || null} : {}), ...extra};
            escribir(clave, cacheLocal);
        }
        function habilitar() {
            const cerrado = bloqueo || checkout || trabajando || donacion?.estado === 'Completada';
            if (acciones) { if (cerrado) acciones.disable(); else acciones.enable(); }
            reintentar.disabled = trabajando; consultar.disabled = trabajando;
        }
        function referencia() { return cacheLocal.cobro?.captura || donacion?.orden || donacion?.folio || cacheLocal.folio || ''; }
        function pendiente(error) {
            bloqueo = true;
            mensaje((error?.message ? error.message + '\n' : '') + 'Tu donación está pendiente de confirmar o guardar. No vuelvas a pagar si recibiste el cargo. Conserva esta referencia: ' + referencia());
            reintentar.hidden = !cacheLocal.cobro;
            consultar.hidden = !donacion;
            habilitar();
        }
        function exito() {
            bloqueo = true; reintentar.hidden = true; consultar.hidden = true;
            borrar(clave); cacheLocal = {};
            mensaje('Tu donación quedó registrada. Gracias por tu apoyo.');
            habilitar();
            if (agradecida !== donacion.folio) {
                agradecida = donacion.folio;
                if (opts.alCompletar) opts.alCompletar(donacion);
            }
        }
        async function actualizar(accion, datos = {}) {
            donacion = await api('actualizar', {folio:donacion.folio, accion, ...datos}, true);
            guardar();
            return donacion;
        }
        async function guardarCobro() {
            if (trabajando || !cacheLocal.cobro || !donacion) return;
            trabajando = true; habilitar(); mensaje('Guardando tu donación. No repitas el pago…');
            try { await actualizar('completar', cacheLocal.cobro); exito(); }
            catch (error) { await incidencia('registro_pendiente'); pendiente(error); }
            finally { trabajando = false; habilitar(); }
        }
        async function preparar() {
            const ctx = contexto();
            const nuevoScope = ctx.origen + ':' + (ctx.cita_id || '');
            if (scope === nuevoScope && donacion) return;
            scope = nuevoScope;
            clave = 'hope.donacion.' + config.dataset.usuario + ':' + scope;
            cacheLocal = leer(clave); donacion = null; bloqueo = false;
            try {
                if (cacheLocal.folio) donacion = await api('estado', {folio:cacheLocal.folio});
                else donacion = (await api('estado', ctx)).pendiente;
            } catch (error) {
                if (error.status === 404 && !cacheLocal.cobro && !cacheLocal.orden) { borrar(clave); cacheLocal = {}; }
                else { if (cacheLocal.cobro || cacheLocal.orden) pendiente(error); throw error; }
            }
            if (!donacion) return;
            if (donacion.estado === 'Cancelada') { borrar(clave); cacheLocal = {}; donacion = null; return; }
            guardar();
            if (donacion.estado === 'Completada') { exito(); return; }
            if (cacheLocal.cobro) { bloqueo = true; await guardarCobro(); return; }
            if (donacion.captura_iniciada) { pendiente(); return; }
            mensaje('Puedes retomar tu aporte de ' + dinero(donacion.monto) + '. Todavía no hay un cobro completado registrado.');
        }
        async function incidencia(codigo) {
            if (!donacion || donacion.estado !== 'Pendiente') return;
            try { await actualizar('incidencia', {incidencia:codigo}); } catch (_) { /* Mantener la referencia local para recuperación. */ }
        }
        reintentar.onclick = guardarCobro;
        consultar.onclick = async () => {
            if (trabajando || !donacion) return;
            trabajando = true; habilitar();
            try {
                donacion = await api('estado', {folio:donacion.folio});
                if (donacion.estado === 'Completada') exito();
                else if (donacion.captura_iniciada || cacheLocal.cobro) pendiente();
                else { bloqueo = false; mensaje('Tu donación sigue pendiente, sin captura iniciada. Puedes retomar PayPal.'); }
            } catch (error) { pendiente(error); }
            finally { trabajando = false; habilitar(); }
        };
        controladores.set(contenedor, async () => { await preparar(); habilitar(); });
        return {
            style:opts.style || {layout:'vertical',color:'blue',shape:'rect',label:'donate'},
            ...(opts.fundingSource ? {fundingSource:opts.fundingSource} : {}),
            async onInit(_data, sdkAcciones) {
                acciones = sdkAcciones; acciones.disable();
                try { await preparar(); }
                catch (error) { if (!bloqueo) mensaje(error.message); }
                finally { habilitar(); }
            },
            async onClick(_data, sdkAcciones) {
                if (checkout || trabajando) return sdkAcciones.reject();
                checkout = true;
                try { await preparar(); }
                catch (error) { checkout = false; mensaje(error.message); habilitar(); return sdkAcciones.reject(); }
                if (bloqueo || donacion?.captura_iniciada || donacion?.estado === 'Completada') { checkout = false; habilitar(); return sdkAcciones.reject(); }
                return sdkAcciones.resolve();
            },
            async createOrder(_data, sdkAcciones) {
                checkout = true; habilitar(); mensaje('Registrando tu aporte y preparando PayPal…');
                try {
                    const valor = Number(opts.monto());
                    if (!Number.isFinite(valor) || valor <= 0 || valor > 99999999.99) throw new Error('Indica un importe válido para donar.');
                    const monto = valor.toFixed(2);
                    if (donacion && donacion.monto !== monto) {
                        if (donacion.captura_iniciada) throw new Error('Consulta tu donación anterior antes de cambiar el importe.');
                        await actualizar('cancelar'); donacion = null; cacheLocal = {}; borrar(clave);
                    }
                    if (!donacion) {
                        cacheLocal.folio = cacheLocal.folio || crypto.randomUUID();
                        guardar();
                        donacion = await api('iniciar', {...contexto(), folio:cacheLocal.folio, monto}, true);
                        guardar();
                    }
                    if (donacion.estado !== 'Pendiente' || donacion.captura_iniciada) throw new Error('Consulta tu donación antes de continuar.');
                    let orden = donacion.orden || cacheLocal.orden;
                    if (!orden) {
                        orden = await sdkAcciones.order.create({
                            intent:'CAPTURE', purchase_units:[{description:donacion.descripcion, custom_id:donacion.custom_id,
                                amount:{currency_code:donacion.moneda, value:donacion.monto}}],
                            application_context:{shipping_preference:'NO_SHIPPING'}
                        });
                        guardar({orden});
                    }
                    await actualizar('orden', {orden});
                    mensaje('Autoriza tu aporte de ' + dinero(donacion.monto) + ' en PayPal.');
                    return orden;
                } catch (error) {
                    checkout = false; mensaje(error.message); await incidencia('creacion_fallida'); throw error;
                } finally { habilitar(); }
            },
            async onApprove(data, sdkAcciones) {
                if (capturando || trabajando) return;
                if (donacion?.estado === 'Completada') { exito(); return; }
                if (donacion?.captura_iniciada) {
                    if (cacheLocal.cobro) await guardarCobro(); else pendiente();
                    return;
                }
                capturando = true;
                checkout = true; habilitar(); mensaje('Confirmando tu donación con PayPal…');
                try {
                    await actualizar('capturar', {orden:data.orderID});
                    if (donacion.estado === 'Completada') { exito(); return; }
                    const orden = await sdkAcciones.order.capture();
                    const unidad = orden.purchase_units?.[0];
                    const captura = unidad?.payments?.captures?.[0];
                    if (orden.status !== 'COMPLETED' || captura?.status !== 'COMPLETED') throw new Error('PayPal todavía no informó un cobro completado.');
                    const pagador = orden.payer || orden.payment_source?.paypal || {};
                    const nombre = [pagador.name?.given_name, pagador.name?.surname].filter(Boolean).join(' ');
                    guardar({cobro:{resultado:'COMPLETED', orden:data.orderID, captura:captura.id,
                        monto:captura.amount?.value, moneda:captura.amount?.currency_code,
                        custom_id:unidad.custom_id || donacion.custom_id, nombre, correo:pagador.email_address || ''}});
                    await guardarCobro();
                } catch (error) {
                    await incidencia('captura_incierta'); pendiente(error);
                } finally { capturando = false; checkout = false; habilitar(); }
            },
            async onCancel() {
                checkout = false;
                if (donacion?.captura_iniciada || cacheLocal.cobro) { pendiente(); return; }
                if (donacion) {
                    try { await actualizar('cancelar'); donacion = null; cacheLocal = {}; borrar(clave); }
                    catch (error) { pendiente(error); return; }
                }
                mensaje('Cancelaste esta solicitud. Puedes volver a donar cuando quieras.'); habilitar();
            },
            onError() {
                checkout = false;
                if (donacion?.captura_iniciada || cacheLocal.cobro) pendiente(new Error('No pudimos resolver la respuesta de PayPal.'));
                else { mensaje('No pudimos completar el proceso. Consulta el estado antes de repetir un pago si recibiste algún cargo.'); habilitar(); }
            }
        };
    }
    window.HopeDonaciones = {opciones, refrescar:contenedor => controladores.get(contenedor)?.()};
})();
