"""Donaciones PayPal registradas con el criterio de confirmación del navegador.

No consulta PayPal ni certifica el cargo en servidor. La validación aquí cubre
pertenencia, consistencia e idempotencia de los datos comunicados por el SDK.
"""
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from uuid import UUID, uuid4

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_GET, require_POST

from .models import Cita, Donacion

SESION = 'donaciones_propietario'


class ErrorDonacion(Exception):
    def __init__(self, mensaje, status=400):
        self.mensaje, self.status = mensaje, status


def _error(error):
    return JsonResponse({'status': 'error', 'message': error.mensaje}, status=error.status)


def _uuid(valor):
    try:
        return UUID(str(valor))
    except (ValueError, TypeError, AttributeError):
        raise ErrorDonacion('La referencia de la donación no es válida.')


def _propietario(request, crear=False):
    valor = request.session.get(SESION)
    if not valor and crear:
        valor = str(uuid4())
        request.session[SESION] = valor
    return _uuid(valor) if valor else None


def _acceso(request):
    permiso = Q(propietario=_propietario(request)) if _propietario(request) else Q(pk__in=[])
    if request.user.is_authenticated:
        permiso |= Q(usuario=request.user)
    return permiso


def _limitar(request, tipo, maximo, segundos):
    propietario = str(_propietario(request, crear=True))
    identidad = str(request.user.pk) if request.user.is_authenticated else propietario
    clave = 'donaciones:' + tipo + ':' + sha256(identidad.encode()).hexdigest()
    if cache.add(clave, 1, segundos):
        return
    try:
        actual = cache.incr(clave)
    except ValueError:
        cache.set(clave, 1, segundos)
        return
    if actual > maximo:
        raise ErrorDonacion('Has realizado muchas solicitudes. Espera un momento y consulta tu donación antes de reintentar.', 429)


def _monto(valor):
    try:
        monto = Decimal(str(valor))
        if not monto.is_finite() or not Decimal('0.01') <= monto <= Decimal('99999999.99') or monto != monto.quantize(Decimal('0.01')):
            raise ValueError
        return monto.quantize(Decimal('0.01'))
    except (InvalidOperation, ValueError, TypeError):
        raise ErrorDonacion('Indica un importe válido en pesos mexicanos, con un máximo de dos decimales.')


def _referencia(valor):
    if not isinstance(valor, str) or not 1 <= len(valor) <= 64 or not valor.isascii() or not valor.isalnum():
        raise ErrorDonacion('La referencia de PayPal no es válida.')
    return valor


def _serializar(donacion):
    return {
        'status': 'success', 'folio': str(donacion.folio), 'monto': str(donacion.monto),
        'moneda': donacion.moneda, 'estado': donacion.estado, 'origen': donacion.origen,
        'cita_id': donacion.cita_id, 'orden': donacion.orden_paypal,
        'captura': donacion.captura_paypal, 'captura_iniciada': donacion.captura_iniciada,
        'custom_id': f'HOPE-DON-{donacion.folio}', 'descripcion': donacion.concepto,
        'confirmacion': donacion.confirmacion,
    }


def _buscar(request, datos, bloquear=False):
    consulta = Donacion.objects.select_for_update() if bloquear else Donacion.objects
    try:
        return consulta.filter(_acceso(request)).get(folio=_uuid(datos.get('folio')))
    except Donacion.DoesNotExist:
        raise ErrorDonacion('No encontramos esa donación en tu sesión. Conserva tu comprobante y contacta a HOPE.', 404)


@never_cache
@csrf_protect
@require_POST
def iniciar(request):
    try:
        propietario = _propietario(request, crear=True)
        folio = _uuid(request.POST.get('folio'))
        monto = _monto(request.POST.get('monto'))
        origen = request.POST.get('origen')
        if origen not in Donacion.Origen.values:
            raise ErrorDonacion('El origen de la donación no es válido.')
        cita = None
        if origen == Donacion.Origen.POST_CITA:
            if not request.user.is_authenticated:
                raise ErrorDonacion('Abre tu panel para donar después de agendar.', 403)
            try:
                cita = Cita.objects.get(pk=int(request.POST.get('cita_id')), paciente=request.user)
            except (Cita.DoesNotExist, ValueError, TypeError, OverflowError):
                raise ErrorDonacion('La cita no corresponde a tu cuenta.', 403)
            if monto != Decimal('100.00'):
                raise ErrorDonacion('La invitación después de agendar corresponde a $100 MXN.')
        elif request.POST.get('cita_id'):
            raise ErrorDonacion('Esta donación no necesita vincular una cita.')
        existente = Donacion.objects.filter(folio=folio).first()
        if existente:
            existente = _buscar(request, request.POST)
            if (existente.monto, existente.origen, existente.cita_id) != (monto, origen, cita.pk if cita else None):
                raise ErrorDonacion('Esta referencia ya pertenece a otra selección. Consulta su estado antes de continuar.', 409)
            return JsonResponse(_serializar(existente))
        _limitar(request, 'iniciar', 20, 3600)
        with transaction.atomic():
            donacion = Donacion.objects.create(
                folio=folio, propietario=propietario, monto=monto, origen=origen, cita=cita,
                usuario=request.user if request.user.is_authenticated else None,
            )
        return JsonResponse(_serializar(donacion))
    except ErrorDonacion as error:
        return _error(error)
    except IntegrityError:
        # La primera solicitud puede haber terminado mientras llegaba su reintento.
        return _error(ErrorDonacion('La solicitud ya existe. Consulta el estado de tu donación antes de continuar.', 409))


@never_cache
@require_GET
def estado(request):
    try:
        _limitar(request, 'consultar', 120, 60)
        if request.GET.get('folio'):
            return JsonResponse(_serializar(_buscar(request, request.GET)))
        origen = request.GET.get('origen')
        if origen not in Donacion.Origen.values:
            raise ErrorDonacion('El origen de la donación no es válido.')
        pendientes = Donacion.objects.filter(_acceso(request), origen=origen, estado=Donacion.Estado.PENDIENTE)
        if origen == Donacion.Origen.POST_CITA:
            try:
                pendientes = pendientes.filter(cita_id=int(request.GET.get('cita_id')))
            except (TypeError, ValueError, OverflowError):
                raise ErrorDonacion('Indica la cita de origen.')
        donacion = pendientes.order_by('-captura_iniciada', '-creada').first()
        return JsonResponse({'status': 'success', 'pendiente': _serializar(donacion) if donacion else None})
    except ErrorDonacion as error:
        return _error(error)


@never_cache
@csrf_protect
@require_POST
def actualizar(request):
    try:
        _limitar(request, 'actualizar', 120, 60)
        with transaction.atomic():
            donacion = _buscar(request, request.POST, bloquear=True)
            accion = request.POST.get('accion')
            if donacion.estado == Donacion.Estado.COMPLETADA:
                if accion == 'completar' and (request.POST.get('orden') != donacion.orden_paypal or request.POST.get('captura') != donacion.captura_paypal):
                    raise ErrorDonacion('La referencia no coincide con la donación registrada.', 409)
                return JsonResponse(_serializar(donacion))
            if donacion.estado == Donacion.Estado.CANCELADA:
                if accion == 'cancelar':
                    return JsonResponse(_serializar(donacion))
                raise ErrorDonacion('Esta donación fue cancelada. Inicia una nueva solicitud.', 409)
            if accion == 'orden':
                orden = _referencia(request.POST.get('orden'))
                if donacion.orden_paypal and donacion.orden_paypal != orden:
                    raise ErrorDonacion('Hay otra orden asociada a esta donación.', 409)
                donacion.orden_paypal = orden
            elif accion == 'capturar':
                if not donacion.orden_paypal or request.POST.get('orden') != donacion.orden_paypal:
                    raise ErrorDonacion('La orden no coincide con la donación.', 409)
                donacion.captura_iniciada = True
            elif accion == 'completar':
                if not donacion.captura_iniciada or request.POST.get('resultado') != 'COMPLETED':
                    raise ErrorDonacion('La donación todavía no tiene una captura completada informada por PayPal.', 409)
                if not donacion.orden_paypal or request.POST.get('orden') != donacion.orden_paypal:
                    raise ErrorDonacion('La orden no coincide con la donación.', 409)
                if _monto(request.POST.get('monto')) != donacion.monto or request.POST.get('moneda') != 'MXN' or request.POST.get('custom_id') != f'HOPE-DON-{donacion.folio}':
                    raise ErrorDonacion('El importe, moneda o folio no coincide con la donación.', 409)
                donacion.captura_paypal = _referencia(request.POST.get('captura'))
                nombre = request.POST.get('nombre', '').strip()[:200]
                correo = request.POST.get('correo', '').strip()[:254]
                if correo:
                    try:
                        validate_email(correo)
                    except ValidationError:
                        correo = ''
                donacion.nombre_paypal, donacion.correo_paypal = nombre, correo
                donacion.estado = Donacion.Estado.COMPLETADA
                donacion.completada = timezone.now()
                donacion.incidencia = ''
                if not donacion.usuario_id and request.user.is_authenticated:
                    donacion.usuario = request.user
            elif accion == 'cancelar':
                if donacion.captura_iniciada:
                    raise ErrorDonacion('El pago está pendiente de resolver. No repitas el cobro y conserva tu referencia.', 409)
                donacion.estado = Donacion.Estado.CANCELADA
            elif accion == 'incidencia':
                incidencia = request.POST.get('incidencia')
                if incidencia not in ['creacion_fallida', 'captura_incierta', 'registro_pendiente']:
                    raise ErrorDonacion('La incidencia no es válida.')
                donacion.incidencia = incidencia
            else:
                raise ErrorDonacion('La acción no es válida.')
            donacion.save()
            return JsonResponse(_serializar(donacion))
    except ErrorDonacion as error:
        return _error(error)
    except IntegrityError:
        return _error(ErrorDonacion('Esta orden o captura ya está registrada en otra donación. Conserva la referencia y contacta a HOPE.', 409))
