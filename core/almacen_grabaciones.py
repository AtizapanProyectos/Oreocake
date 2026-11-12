import os
from pathlib import Path

from django.conf import settings
from django.core.files.storage import FileSystemStorage, default_storage
from django.urls import reverse
from django.utils.deconstruct import deconstructible


@deconstructible
class AlmacenGrabaciones(FileSystemStorage):
    def __init__(self):
        super().__init__(file_permissions_mode=0o600, directory_permissions_mode=0o700)

    @property
    def location(self):
        return os.path.abspath(settings.GRABACIONES_ROOT)

    def _open(self, name, mode="rb"):
        try:
            return super()._open(name, mode)
        except FileNotFoundError:
            # Compatibilidad con grabaciones guardadas antes del almacén privado.
            return default_storage.open(name, mode)

    def delete(self, name):
        if self.exists(name):
            super().delete(name)
        else:
            default_storage.delete(name)

    def url(self, name):
        raise NotImplementedError("La descarga requiere autorización.")


class AlmacenClinico(FileSystemStorage):
    """Archivos privados del historial, separados del almacén multimedia general."""

    def __init__(self):
        super().__init__(file_permissions_mode=0o600, directory_permissions_mode=0o700)

    @property
    def location(self):
        montaje = getattr(settings, 'RAILWAY_VOLUME_MOUNT_PATH', None)
        if montaje:
            return str(Path(montaje).resolve() / 'notas_clinicas')
        base = getattr(settings, 'GRABACIONES_ROOT', settings.BASE_DIR / 'grabaciones_privadas')
        return str(Path(base).resolve() / 'notas_clinicas')

    def _save(self, name, content):
        montaje = getattr(settings, 'RAILWAY_VOLUME_MOUNT_PATH', None)
        en_railway = getattr(settings, 'RAILWAY_ENVIRONMENT_ID', None)
        if (en_railway or montaje) and (
            not montaje or not Path(montaje).is_absolute() or not Path(montaje).is_dir()
        ):
            raise OSError('No está disponible el volumen de archivos clínicos en Railway.')
        return super()._save(name, content)


@deconstructible
class AlmacenAdjuntosClinicos(AlmacenClinico):
    def _open(self, name, mode='rb'):
        try:
            return super()._open(name, mode)
        except FileNotFoundError:
            return default_storage.open(name, mode)

    def url(self, name):
        return reverse('descargar_adjunto_historial', args=[name])


@deconstructible
class AlmacenTranscripciones(AlmacenClinico):
    def _open(self, name, mode='rb'):
        try:
            return super()._open(name, mode)
        except FileNotFoundError:
            return almacen_grabaciones.open(name, mode)

    def url(self, name):
        return reverse('descargar_transcripcion_historial', args=[name])


almacen_grabaciones = AlmacenGrabaciones()
almacen_adjuntos_clinicos = AlmacenAdjuntosClinicos()
almacen_transcripciones = AlmacenTranscripciones()
