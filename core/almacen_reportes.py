from uuid import uuid4
from django.core.files.base import ContentFile
from django.core.files.storage import Storage, default_storage
from django.utils.deconstruct import deconstructible


@deconstructible
class AlmacenReportes(Storage):
    """New PDFs in private DB storage; existing names remain readable."""
    def _open(self, name, mode='rb'):
        if not name.startswith('privado-db/'):
            return default_storage.open(name, mode)
        from .models import ArchivoProceso
        return ContentFile(bytes(ArchivoProceso.objects.get(nombre=name).contenido), name=name)

    def _save(self, name, content):
        from .models import ArchivoProceso
        name = 'privado-db/' + uuid4().hex + '.pdf'
        ArchivoProceso.objects.create(nombre=name, contenido=content.read())
        return name

    def exists(self, name):
        if not name.startswith('privado-db/'):
            return default_storage.exists(name)
        from .models import ArchivoProceso
        return ArchivoProceso.objects.filter(nombre=name).exists()

    def size(self, name):
        with self.open(name) as stream:
            return len(stream.read())

    def delete(self, name):
        if name.startswith('privado-db/'):
            from .models import ArchivoProceso
            ArchivoProceso.objects.filter(nombre=name).delete()
        else:
            default_storage.delete(name)

    def url(self, name):
        raise ValueError('Los reportes requieren descarga autenticada.')


almacen_reportes = AlmacenReportes()
