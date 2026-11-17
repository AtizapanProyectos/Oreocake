from functools import wraps
from hashlib import sha256
from urllib.parse import parse_qs, urlencode

from django import forms
from django.contrib.auth import authenticate, get_user_model, login, logout
from django.contrib.auth.forms import UsernameField
from django.contrib.auth.hashers import make_password
from django.core.cache import cache
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_http_methods, require_POST


GRUPO_TECTUM = 'tectum_staff'
SESION_TECTUM = 'tectum_bienestar_user_id'
RUTA_PANEL = 'panel_bienestar_tectum'
RUTA_LOGIN = 'panel_bienestar_tectum_login'
RUTAS_TECTUM = (RUTA_PANEL, RUTA_LOGIN, 'panel_bienestar_tectum_sentimiento',
                'panel_bienestar_tectum_logout')
ERROR_CREDENCIALES = 'El usuario, el correo o la contraseña no son válidos para este acceso.'
LIMITE_INTENTOS = 15
VENTANA_INTENTOS = 15 * 60


def es_staff_tectum(usuario):
    return usuario.is_authenticated and usuario.groups.filter(name=GRUPO_TECTUM).exists()


class AccesoExclusivoTectumMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        usuario = request.user
        request.es_staff_tectum = es_staff_tectum(usuario)
        usuario_dedicado = request.session.get(SESION_TECTUM)
        if usuario_dedicado is not None and (
            usuario_dedicado != usuario.pk or not usuario.is_active or not request.es_staff_tectum
        ):
            logout(request)

        if request.es_staff_tectum and request.path not in (reverse(nombre) for nombre in RUTAS_TECTUM):
            respuesta = HttpResponseForbidden('Acceso restringido a Tectum Bienestar.')
            respuesta['Cache-Control'] = 'private, no-store'
            return respuesta
        return self.get_response(request)


def requiere_bienestar_tectum(*, json=False):
    def decorar(vista):
        @wraps(vista)
        @never_cache
        def protegida(request, *args, **kwargs):
            usuario = request.user
            pertenece_al_grupo = request.es_staff_tectum
            if usuario.is_authenticated and usuario.is_active:
                if pertenece_al_grupo and request.session.get(SESION_TECTUM) == usuario.pk:
                    return vista(request, *args, **kwargs)
                if not pertenece_al_grupo and usuario.is_superuser:
                    return vista(request, *args, **kwargs)

            necesita_login = not usuario.is_authenticated or pertenece_al_grupo
            if json:
                if necesita_login:
                    return JsonResponse({'error': 'Inicia sesión en Tectum Bienestar.'}, status=401)
                return JsonResponse({'error': 'Acceso restringido a Tectum Bienestar.'}, status=403)
            if necesita_login:
                return redirect(f'{reverse(RUTA_LOGIN)}?{urlencode({"next": request.get_full_path()})}')
            return HttpResponseForbidden('Acceso restringido a Tectum Bienestar.')

        return protegida
    return decorar


class FormularioAccesoTectum(forms.Form):
    username = UsernameField(
        label='Usuario o correo', max_length=254,
        error_messages={'required': 'Ingresa tu usuario o correo.'},
    )
    password = forms.CharField(
        label='Contraseña', strip=False,
        widget=forms.PasswordInput(attrs={'autocomplete': 'current-password'}),
        error_messages={'required': 'Ingresa tu contraseña.'},
    )


def _autenticar_staff(request, identificador, password):
    usuarios = get_user_model()._default_manager
    username = identificador
    if '@' in identificador and not usuarios.filter(username=identificador).exists():
        correos = list(usuarios.filter(email__iexact=identificador).values_list('username', flat=True)[:2])
        if len(correos) > 1:
            make_password(password)  # Igual que ModelBackend cuando no encuentra una cuenta.
            return None
        if correos:
            username = correos[0]

    usuario = authenticate(request, username=username, password=password)
    if usuario is not None and usuario.is_active and es_staff_tectum(usuario):
        return usuario
    return None


def _destino_panel(destino):
    panel = reverse(RUTA_PANEL)
    ruta, _, consulta = destino.partition('?')
    if ruta != panel or '#' in consulta:
        return panel
    try:
        parametros = parse_qs(consulta, max_num_fields=32)
    except ValueError:
        return panel
    filtros = {campo: parametros[campo][-1] for campo in ('mes', 'anio', 'refresh') if campo in parametros}
    return f'{panel}?{urlencode(filtros)}' if filtros else panel


@sensitive_post_parameters('password')
@never_cache
@csrf_protect
@require_http_methods(['GET', 'POST'])
def panel_bienestar_tectum_login(request):
    datos = request.POST if request.method == 'POST' else request.GET
    destino = _destino_panel(datos.get('next', ''))
    formulario = FormularioAccesoTectum(request.POST if request.method == 'POST' else None)
    estado = 200
    if formulario.is_valid():
        identificador = formulario.cleaned_data['username']
        huella = sha256(identificador.casefold().encode()).hexdigest()
        clave = f'tectum-bienestar:login:{huella}'
        if cache.get(clave, 0) >= LIMITE_INTENTOS:
            formulario.add_error(None, 'Demasiados intentos. Espera unos minutos e inténtalo de nuevo.')
            estado = 429
        else:
            usuario = _autenticar_staff(request, identificador, formulario.cleaned_data['password'])
            if usuario is not None:
                request.session.flush()
                login(request, usuario)
                request.session[SESION_TECTUM] = usuario.pk
                cache.delete(clave)
                return redirect(destino)

            if not cache.add(clave, 1, VENTANA_INTENTOS):
                try:
                    cache.incr(clave)
                except ValueError:
                    cache.add(clave, 1, VENTANA_INTENTOS)
            formulario.add_error(None, ERROR_CREDENCIALES)

    return render(request, 'tectum/bienestar_login.html', {'form': formulario, 'next': destino}, status=estado)


@never_cache
@csrf_protect
@require_POST
def panel_bienestar_tectum_logout(request):
    if request.es_staff_tectum:
        logout(request)
    return redirect(RUTA_LOGIN)
