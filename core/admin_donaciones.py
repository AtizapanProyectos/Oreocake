import csv
from decimal import Decimal

from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Sum
from django.http import HttpResponse
from django.urls import path, reverse
from django.utils import timezone

from .models import Donacion


def _celda(valor):
    texto = str(valor or '')
    return "'" + texto if texto.lstrip().startswith(('=', '+', '-', '@')) else texto


@admin.register(Donacion)
class DonacionAdmin(admin.ModelAdmin):
    change_list_template = 'admin/core/donacion/change_list.html'
    list_display = ('folio', 'donante', 'usuario', 'monto', 'moneda', 'medio', 'origen', 'estado', 'creada', 'completada')
    list_filter = ('estado', 'origen', 'medio', ('creada', admin.DateFieldListFilter), ('completada', admin.DateFieldListFilter))
    search_fields = ('folio', 'nombre_paypal', 'correo_paypal', 'usuario__username', 'usuario__email', 'orden_paypal', 'captura_paypal')
    exclude = ('propietario',)
    readonly_fields = tuple(campo.name for campo in Donacion._meta.fields if campo.name != 'propietario')
    list_select_related = ('usuario', 'cita')
    date_hierarchy = 'creada'
    actions = None

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_urls(self):
        return [path('exportar/', self.admin_site.admin_view(self.exportar), name='core_donacion_exportar')] + super().get_urls()

    def changelist_view(self, request, extra_context=None):
        respuesta = super().changelist_view(request, extra_context)
        contexto = getattr(respuesta, 'context_data', None)
        if contexto and 'cl' in contexto:
            confirmadas = contexto['cl'].queryset.filter(estado=Donacion.Estado.COMPLETADA)
            resumen = confirmadas.aggregate(cantidad=Count('pk'), total=Sum('monto'))
            contexto['cantidad_completadas'] = resumen['cantidad']
            contexto['total_completadas'] = resumen['total'] or Decimal('0.00')
            consulta = request.GET.urlencode()
            contexto['exportar_url'] = reverse('admin:core_donacion_exportar') + ('?' + consulta if consulta else '')
        return respuesta

    def exportar(self, request):
        if not self.has_view_permission(request):
            raise PermissionDenied
        registros = self.get_changelist_instance(request).queryset.select_related('usuario')
        respuesta = HttpResponse(content_type='text/csv; charset=utf-8')
        respuesta['Content-Disposition'] = 'attachment; filename="donaciones-hope.csv"'
        respuesta.write('\ufeff')
        escritor = csv.writer(respuesta)
        escritor.writerow(['Folio', 'Donante', 'Nombre PayPal', 'Correo PayPal', 'Usuario HOPE', 'Monto', 'Moneda', 'Medio', 'Origen', 'Estado', 'Orden PayPal', 'Captura PayPal', 'Cita de origen', 'Creada CDMX', 'Completada CDMX', 'Criterio de confirmación'])
        for d in registros.iterator():
            fila = [str(d.folio), d.donante, d.nombre_paypal, d.correo_paypal, d.usuario.username if d.usuario_id else '',
                    str(d.monto), d.moneda, d.medio, d.get_origen_display(), d.estado, d.orden_paypal, d.captura_paypal,
                    d.cita_id, timezone.localtime(d.creada).isoformat(), timezone.localtime(d.completada).isoformat() if d.completada else '',
                    d.get_confirmacion_display()]
            escritor.writerow([_celda(valor) for valor in fila])
        return respuesta
