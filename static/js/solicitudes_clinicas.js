(function (global) {
  'use strict';

  // Nunca convertir una respuesta HTML del servidor en un falso fallo de red.
  global.solicitudClinica = async function (url, body, form) {
    const cookie = document.cookie.split('; ').find(value => value.startsWith('csrftoken='));
    const token = (cookie && decodeURIComponent(cookie.slice('csrftoken='.length)))
      || form?.querySelector('[name=csrfmiddlewaretoken]')?.value
      || document.querySelector('[name=csrfmiddlewaretoken]')?.value;
    const headers = { 'X-Requested-With': 'XMLHttpRequest', Accept: 'application/json' };
    if (token) headers['X-CSRFToken'] = token;
    // Django prioriza el token del formulario sobre la cabecera.
    if (token && body && typeof body.set === 'function') body.set('csrfmiddlewaretoken', token);
    let respuesta;
    try {
      respuesta = await fetch(url, { method: 'POST', body, headers, credentials: 'same-origin' });
    } catch (_) {
      throw new Error('No se pudo conectar con el servidor. Tu información sigue en el formulario; intenta guardar de nuevo.');
    }
    const mensajes = {
      400: 'No se pudo procesar la solicitud. Revisa los datos y el tamaño del archivo.',
      401: 'Tu sesión terminó. Inicia sesión en otra pestaña y vuelve a intentar sin cerrar este formulario.',
      403: 'La sesión o el permiso de guardado cambió. Abre tu panel en otra pestaña y vuelve a intentar; conserva este formulario.',
      413: 'El archivo supera el tamaño permitido. Selecciona un archivo de hasta 10 MB.',
      502: 'El servicio de procesamiento no respondió correctamente. Tu información se conserva; intenta de nuevo.',
      504: 'El servidor tardó demasiado. Conserva el formulario e intenta con un documento más pequeño.',
    };
    if (respuesta.redirected) throw new Error(mensajes[401]);
    let datos;
    try { datos = await respuesta.json(); }
    catch (_) { throw new Error(mensajes[respuesta.status] || `El servidor no pudo completar la solicitud (HTTP ${respuesta.status}). Tu información se conserva.`); }
    if (!respuesta.ok || datos.status !== 'success') {
      throw new Error(datos.message || datos.error || mensajes[respuesta.status] || 'No se pudo completar la solicitud. Tu información se conserva.');
    }
    return datos;
  };
})(window);
