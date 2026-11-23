"""Errores públicos sin excepciones, datos de sesión ni consultas a la BD."""

from django.conf import settings
from django.core.exceptions import DisallowedHost
from django.http import HttpResponse
from django.template.loader import get_template
from django.views.decorators.cache import never_cache


def _pantalla(request, codigo, titulo, mensaje, *, etiqueta='Podemos ayudarte a continuar'):
    try:
        host = request.get_host().lower()
    except DisallowedHost:
        # Una cabecera Host inválida también debe poder mostrar el error 400.
        host = ''
    es_tectum = 'tectum' in host or 'tectum' in request.path.lower()
    marca = 'TECTUM' if es_tectum else 'Espacio HOPE'
    logo = 'img/tectum/logo-completo.png' if es_tectum else 'img/logo_hope_extendido.png'
    prefijo = settings.STATIC_URL
    if not prefijo.startswith(('/', 'http://', 'https://')):
        prefijo = '/' + prefijo
    # No pasar request: los procesadores de contexto pueden consultar la BD,
    # incluso cuando el fallo original es una conexión de base de datos caída.
    contenido = get_template('errors/error.html').render({
        'codigo': codigo,
        'titulo': titulo,
        'mensaje': mensaje,
        'etiqueta': etiqueta,
        'marca': marca,
        # URL pública original: no depender del manifiesto de estáticos al
        # atender un error 500. WhiteNoise conserva estos nombres originales.
        'logo_url': prefijo.rstrip('/') + '/' + logo,
    })
    respuesta = HttpResponse(contenido, status=codigo)
    respuesta['X-Robots-Tag'] = 'noindex, nofollow'
    return respuesta


@never_cache
def error_400(request, exception):
    return _pantalla(request, 400, 'No pudimos procesar esta solicitud',
                     'Vuelve al inicio e inténtalo desde allí. Si el problema continúa, contacta al equipo de HOPE.')


@never_cache
def error_403(request, exception):
    return _pantalla(request, 403, 'No tienes acceso a esta página',
                     'Entra desde tu panel con la cuenta correspondiente. Si necesitas ayuda, contacta al equipo de HOPE.')


@never_cache
def error_404(request, exception):
    if request.path.startswith('/sesion-previa/'):
        return _pantalla(request, 404, 'Este enlace de sesión no está disponible',
                         'Entra desde tu panel para consultar tu sesión o contacta al equipo de HOPE.',
                         etiqueta='Te acompañamos en el siguiente paso')
    return _pantalla(request, 404, 'No encontramos esta página',
                     'Puede que el enlace haya cambiado o ya no esté disponible. Puedes volver al inicio para continuar.')


@never_cache
def error_500(request):
    return _pantalla(request, 500, 'Algo salió mal',
                     'No pudimos completar tu solicitud en este momento. Inténtalo más tarde o contacta al equipo de HOPE.')


@never_cache
def error_csrf(request, reason=''):
    return _pantalla(request, 403, 'Necesitamos que vuelvas a abrir la página',
                     'Para continuar de forma segura, abre de nuevo la página desde tu panel y vuelve a intentarlo.')
