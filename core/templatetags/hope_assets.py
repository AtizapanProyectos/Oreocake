"""URLs de estáticos que cambian cuando cambia su contenido, incluso con DEBUG=True."""
from functools import lru_cache
from hashlib import sha256
from pathlib import Path

from django import template
from django.contrib.staticfiles import finders
from django.templatetags.static import static

register = template.Library()


@lru_cache(maxsize=128)
def _huella(ruta, modificado, tamanio):
    # mtime y tamaño invalidan la caché al editar el archivo durante desarrollo.
    return sha256(Path(ruta).read_bytes()).hexdigest()[:16]


@register.simple_tag
def versioned_static(nombre):
    url = static(nombre)
    ruta = finders.find(nombre)
    if not ruta:
        # Si solo existe el almacén de collectstatic, conservar su URL manifest.
        return url
    estado = Path(ruta).stat()
    version = _huella(str(ruta), estado.st_mtime_ns, estado.st_size)
    return f'{url}{"&" if "?" in url else "?"}v={version}'
