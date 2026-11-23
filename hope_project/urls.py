import posixpath
import re
from pathlib import Path
from urllib.parse import urlsplit

from django.contrib import admin
from django.http import Http404
from django.views.static import serve

from core.almacen_grabaciones import almacen_adjuntos_clinicos
from django.urls import path, include, re_path
from django.conf import settings
from django.conf.urls.static import static

handler400 = 'hope_project.error_views.error_400'
handler403 = 'hope_project.error_views.error_403'
handler404 = 'hope_project.error_views.error_404'
handler500 = 'hope_project.error_views.error_500'

urlpatterns = [
    path('error-59/', admin.site.urls),
    # Enrutamos toda la plataforma a través de la app principal
    path('', include('core.urls')),
]

def servir_media(request, path, document_root):
    # Si el volumen está montado dentro de MEDIA_ROOT, sus archivos clínicos
    # siguen requiriendo autorización incluso cuando DEBUG está habilitado.
    relativo = posixpath.normpath(path).lstrip('/')
    destino = (Path(document_root) / relativo).resolve()
    privado = Path(almacen_adjuntos_clinicos.location).resolve()
    if destino.is_relative_to(privado):
        raise Http404
    return serve(request, path, document_root=document_root)


# Los enlaces multimedia existentes también funcionan con DEBUG desactivado.
# La vista conserva el bloqueo del almacén clínico y la validación de rutas.
media_url = urlsplit(settings.MEDIA_URL)
if not media_url.scheme and not media_url.netloc and media_url.path.strip('/'):
    urlpatterns += [re_path(
        r'^' + re.escape(media_url.path.lstrip('/')) + r'(?P<path>.*)$',
        servir_media, {'document_root': settings.MEDIA_ROOT},
    )]

# Los estáticos de producción los sirve WhiteNoise.
if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL,
                          document_root=settings.STATIC_ROOT)


