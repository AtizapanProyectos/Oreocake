import posixpath
from io import BytesIO
from urllib.parse import unquote, urlsplit
from uuid import UUID

import segno
from django import forms
from django.contrib.auth.decorators import user_passes_test
from django.core.paginator import Paginator
from django.core.validators import URLValidator
from django.db.models import F, Sum
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods, require_POST, require_safe

from .models import CodigoQRHope


ORIGEN_HOPE = 'https://espaciohope.com'
DOMINIOS_HOPE = {
    'espaciohope.com', 'www.espaciohope.com',
    'tectuminhause.espaciohope.com', 'tectumbeneficios.espaciohope.com',
    'espacio-hope.com', 'www.espacio-hope.com',
    'espaciohope.info', 'www.espaciohope.info',
    'espaciohope.mx', 'www.espaciohope.mx',
    'espaciohope.org', 'www.espaciohope.org', 'oreocake.onrender.com',
}
COLORES = {'morado': '#392378', 'petroleo': '#095452', 'negro': '#161421'}
POR_PAGINA = 12
solo_admin = user_passes_test(lambda usuario: usuario.is_superuser, login_url='/')


def destino_hope(valor):
    if '\\' in valor or any(ord(caracter) < 32 or ord(caracter) == 127 for caracter in valor):
        raise forms.ValidationError('Usa un enlace HTTPS de Espacio HOPE.')
    validar_https = URLValidator(schemes=['https'])
    validar_https(valor)
    try:
        url = urlsplit(valor)
        valido = url.hostname in DOMINIOS_HOPE and url.port in (None, 443)
    except ValueError:
        valido = False
    if not valido or url.username is not None or url.password is not None:
        raise forms.ValidationError('Usa un enlace HTTPS de un dominio oficial de Espacio HOPE.')
    if posixpath.normpath(unquote(url.path)).lstrip('/').split('/', 1)[0] == 'qr':
        raise forms.ValidationError('Usa la página de destino de HOPE en lugar de otro enlace de seguimiento.')
    return valor


class CodigoQRForm(forms.ModelForm):
    destino = forms.CharField(
        label='Enlace de destino', max_length=2048,
        widget=forms.URLInput(attrs={'placeholder': 'https://espaciohope.com/donaciones/'}),
    )

    class Meta:
        model = CodigoQRHope
        fields = ('nombre', 'destino', 'color')
        widgets = {'nombre': forms.TextInput(attrs={'placeholder': 'Ej. Campaña Dona una terapia'})}

    def clean_destino(self):
        return destino_hope(self.cleaned_data['destino'])


def enlace_publico(codigo, directo=False):
    ruta = 'qr_hope_directo' if directo else 'qr_hope_escaneo'
    return ORIGEN_HOPE + reverse(ruta, args=[codigo])


def totales():
    return CodigoQRHope.objects.aggregate(
        escaneos=Sum('escaneos', default=0),
        visitas_directas=Sum('visitas_directas', default=0),
    )


@solo_admin
@never_cache
@require_http_methods(['GET', 'POST'])
def panel(request):
    formulario = CodigoQRForm(request.POST if request.method == 'POST' else None)
    if request.method == 'POST' and formulario.is_valid():
        codigo = formulario.save()
        return redirect(reverse('panel_codigos_qr') + f'#qr-{codigo.codigo}')
    pagina = Paginator(CodigoQRHope.objects.all(), POR_PAGINA).get_page(request.GET.get('pagina'))
    for codigo in pagina:
        codigo.enlace_directo = enlace_publico(codigo.codigo, directo=True)
    return render(request, 'panel_admin_codigos_qr.html', {
        'formulario': formulario, 'pagina': pagina, 'totales': totales(),
    })


@solo_admin
@never_cache
@require_GET
def estadisticas(request):
    valores = request.GET.getlist('codigo')
    if len(valores) > POR_PAGINA:
        return JsonResponse({'mensaje': 'Solicita solo los códigos visibles.'}, status=400)
    try:
        codigos = [UUID(valor) for valor in valores]
    except ValueError:
        return JsonResponse({'mensaje': 'Identificador inválido.'}, status=400)
    filas = list(CodigoQRHope.objects.filter(codigo__in=codigos).values(
        'codigo', 'escaneos', 'visitas_directas',
    ))
    return JsonResponse({'totales': totales(), 'codigos': filas})


@solo_admin
@never_cache
@require_GET
def imagen(request, codigo):
    registro = get_object_or_404(CodigoQRHope, codigo=codigo)
    buffer = BytesIO()
    segno.make_qr(enlace_publico(codigo), error='m').save(
        buffer, kind='png', scale=12, border=4, dark=COLORES[registro.color], light='#ffffff',
    )
    respuesta = HttpResponse(buffer.getvalue(), content_type='image/png')
    if request.GET.get('descargar') == '1':
        respuesta['Content-Disposition'] = f'attachment; filename="hope-qr-{codigo}.png"'
    return respuesta


@solo_admin
@require_POST
def cambiar_estado(request, codigo):
    estado = request.POST.get('activo')
    if estado not in ('0', '1'):
        return JsonResponse({'mensaje': 'Estado inválido.'}, status=400)
    registro = get_object_or_404(CodigoQRHope, codigo=codigo)
    CodigoQRHope.objects.filter(pk=registro.pk).update(activo=estado == '1')
    return redirect('panel_codigos_qr')


@never_cache
@require_safe
def abrir(request, codigo, directo=False):
    activos = CodigoQRHope.objects.filter(codigo=codigo, activo=True)
    destino = get_object_or_404(activos.only('destino')).destino
    try:
        destino = destino_hope(destino)
    except forms.ValidationError as error:
        raise Http404 from error
    if request.method == 'GET':
        campo = 'visitas_directas' if directo else 'escaneos'
        if not activos.update(**{campo: F(campo) + 1}):
            raise Http404
    respuesta = redirect(destino)
    respuesta['Referrer-Policy'] = 'no-referrer'
    return respuesta
