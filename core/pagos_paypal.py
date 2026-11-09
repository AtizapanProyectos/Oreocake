from decimal import Decimal, InvalidOperation

import requests
from django.conf import settings


class ErrorPayPal(Exception):
    def __init__(self, detalle=""):
        self.detalle = detalle


def _cliente():
    if settings.PAYPAL_MODE not in ("live", "sandbox") or not settings.PAYPAL_CLIENT_SECRET:
        raise ErrorPayPal()
    host = "api-m.paypal.com" if settings.PAYPAL_MODE == "live" else "api-m.sandbox.paypal.com"
    base = f"https://{host}"
    try:
        respuesta = requests.post(
            f"{base}/v1/oauth2/token", auth=(settings.PAYPAL_CLIENT_ID, settings.PAYPAL_CLIENT_SECRET),
            data={"grant_type": "client_credentials"}, timeout=8,
        )
        respuesta.raise_for_status()
        return base, respuesta.json()["access_token"]
    except (requests.RequestException, ValueError, KeyError) as error:
        raise ErrorPayPal() from error


def _orden(ruta, identificador, datos=None):
    base, token = _cliente()
    try:
        respuesta = requests.post(
            f"{base}/v2/checkout/orders{ruta}", json=datos or {},
            headers={
                "Authorization": f"Bearer {token}",
                "PayPal-Request-Id": identificador,
                "Prefer": "return=representation",
            },
            timeout=12,
        )
        contenido = respuesta.json()
        if not respuesta.ok:
            detalles = contenido.get("details", [])
            raise ErrorPayPal(detalles[0].get("issue", "") if detalles else "")
        return contenido
    except (requests.RequestException, ValueError, TypeError, AttributeError) as error:
        raise ErrorPayPal() from error


def crear_orden(pago, *, permitir_tarjetas=False):
    datos = {
        "intent": "CAPTURE",
        "purchase_units": [{
            "reference_id": str(pago.cita_id),
            "amount": {"currency_code": "MXN", "value": f"{pago.monto:.2f}"},
        }],
    }
    if not permitir_tarjetas:
        datos["payment_source"] = {"paypal": {"experience_context": {"shipping_preference": "NO_SHIPPING"}}}
    contenido = _orden("", f"hope-cita-{pago.pk}-orden", datos)
    orden = contenido.get("id")
    if not isinstance(orden, str) or not orden.isascii() or not orden.isalnum() or len(orden) > 64:
        raise ErrorPayPal()
    return orden


def _consultar_orden(pago):
    base, token = _cliente()
    try:
        respuesta = requests.get(
            f"{base}/v2/checkout/orders/{pago.orden}",
            headers={"Authorization": f"Bearer {token}"}, timeout=8,
        )
        respuesta.raise_for_status()
        return respuesta.json()
    except (requests.RequestException, ValueError) as error:
        raise ErrorPayPal() from error


def capturar_orden(pago, solo_consultar=False):
    if solo_consultar:
        contenido = _consultar_orden(pago)
    else:
        try:
            contenido = _orden(f"/{pago.orden}/capture", f"hope-cita-{pago.pk}-captura")
        except ErrorPayPal as error:
            if error.detalle != "ORDER_ALREADY_CAPTURED":
                raise
            contenido = _consultar_orden(pago)
    try:
        unidad, = contenido["purchase_units"]
        captura, = unidad["payments"]["captures"]
        monto = captura["amount"]
        if (contenido["id"] != pago.orden or contenido["status"] != "COMPLETED" or
                unidad["reference_id"] != str(pago.cita_id) or captura["status"] != "COMPLETED" or
                monto["currency_code"] != "MXN" or Decimal(monto["value"]) != pago.monto):
            raise ErrorPayPal()
        identificador = captura["id"]
        if not isinstance(identificador, str) or not identificador or len(identificador) > 64:
            raise ErrorPayPal()
        return identificador
    except (ValueError, KeyError, TypeError, InvalidOperation) as error:
        raise ErrorPayPal() from error
