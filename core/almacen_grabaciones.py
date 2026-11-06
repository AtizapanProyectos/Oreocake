import os

from django.conf import settings
from django.core.files.storage import FileSystemStorage, default_storage
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


almacen_grabaciones = AlmacenGrabaciones()
