import posixpath
from pathlib import Path

from django.contrib import admin
from django.http import Http404
from django.views.static import serve

from core.almacen_grabaciones import almacen_adjuntos_clinicos
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

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


# Configuración correcta para archivos estáticos y multimedia en desarrollo
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, view=servir_media,
                          document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL,
                          document_root=settings.STATIC_ROOT)


