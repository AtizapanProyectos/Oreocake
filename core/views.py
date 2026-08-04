from core import cuestionario_data
from core import cuestionario_data
from requests import request
# pyrefly: ignore [missing-import]
import threading
from django.shortcuts import render, redirect
from django.contrib.auth.models import User
import re
from urllib.parse import quote
from django.core.paginator import Paginator
import logging
from django.db.models import Avg, Case, When, Value, IntegerField, Q, Prefetch
from django.utils import timezone
from django.http import JsonResponse
from django.shortcuts import render
from django.core.paginator import Paginator
# pyrefly: ignore [missing-import]
from django.contrib.auth.decorators import user_passes_test
import json
from django.core.cache import cache
# pyrefly: ignore [missing-import]
from django.shortcuts import render, redirect, get_object_or_404  

from django.core.mail import send_mail
from django.contrib.auth.tokens import default_token_generator
from django.utils.http import urlsafe_base64_encode, urlsafe_base64_decode
from django.utils.encoding import force_bytes, force_str
from django.contrib import messages
from django.urls import reverse
# pyrefly: ignore [missing-import]
from django.contrib.auth import authenticate
from django.middleware.csrf import get_token
from django.http import JsonResponse
from django.template.loader import render_to_string
from django.utils.html import strip_tags
from django.contrib.auth import login

from django.db.models import Q, Count
from datetime import datetime, timedelta, time
import json
from django.db import transaction  # <--- Agrega esto en tus imports de hasta arriba
import os
import uuid
from django.contrib.auth.decorators import user_passes_test


# --- LIBRERÍAS DE GOOGLE PARA MEET ---
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from django.conf import settings

from .models import *
from .cuestionario_data import CUESTIONARIO_CLINICO
from django.utils import timezone
from django.utils import timezone as tz



from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string


from django.utils.timezone import localtime, now

import re

import base64
import os
import requests

import fitz          # PyMuPDF  — para leer PDFs
import docx          # python-docx — para leer .doc/.docx
from groq import Groq

from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.decorators import login_required
 
 
TIPOS_IMAGEN  = {'jpg', 'jpeg', 'png', 'webp'}
TIPOS_PDF     = {'pdf'}
TIPOS_DOC     = {'doc', 'docx'}
TAMANO_MAX_MB = 10  # límite de seguridad


from datetime import datetime, timedelta, time
from django.db.models import Count, Q

from django.contrib.auth import logout

def logout_usuario(request):
    logout(request)
    return redirect('inicio')

# =========================================================================
# 🔥 NUEVO: MODALIDADES DE SESIÓN (Individual / Pareja / Familiar)
# =========================================================================
# Mapea cada tipo de sesión al campo booleano del psicólogo que indica si
# puede atenderla. Única fuente de verdad usada tanto en la búsqueda de
# disponibilidad como en la asignación automática.
CAPACIDAD_POR_TIPO_SESION = {
    'individual': 'atiende_individual',
    'pareja': 'atiende_pareja',
    'familiar': 'atiende_familiar',
}
TIPOS_SESION_VALIDOS = tuple(CAPACIDAD_POR_TIPO_SESION.keys())

# =========================================================================
# 💰 NUEVO: PRECIOS Y COMISIONES (única fuente de verdad del backend)
# =========================================================================
# El monto que se cobra SIEMPRE se recalcula en el servidor a partir de
# estas constantes; nunca se confía en el monto que mande el navegador.
PRECIO_BASE_SESION = {
    'individual': 100,
    'pareja': 200,
    'familiar': 200,
}
# Comisión de plataforma, expresada como % sobre el subtotal (mantiene la
# proporción usada históricamente: $10 sobre $100 y $15 sobre $200).
COMISION_PORCENTAJE_SESION = {
    'individual': 0.10,
    'pareja': 0.075,
    'familiar': 0.075,
}
INCREMENTO_POR_INTEGRANTE_FAMILIAR = 100  # MXN por integrante adicional
MIN_INTEGRANTES_FAMILIAR = 2  # una terapia familiar implica mínimo 2 personas


def calcular_precio_sesion(tipo_sesion, integrantes_familia=None):
    """
    Calcula subtotal, comisión y total para cualquier tipo de sesión.
    Es la ÚNICA función que debe usarse para mostrar o cobrar un precio,
    de modo que el resumen de costos y el cobro real jamás se desincronicen.
    """
    tipo_sesion = tipo_sesion if tipo_sesion in PRECIO_BASE_SESION else 'individual'
    base = PRECIO_BASE_SESION[tipo_sesion]
    integrantes_normalizados = None

    if tipo_sesion == 'familiar':
        try:
            n = int(integrantes_familia)
        except (TypeError, ValueError):
            n = MIN_INTEGRANTES_FAMILIAR
        n = max(n, MIN_INTEGRANTES_FAMILIAR)
        integrantes_normalizados = n
        integrantes_adicionales = n - MIN_INTEGRANTES_FAMILIAR
        subtotal = base + (integrantes_adicionales * INCREMENTO_POR_INTEGRANTE_FAMILIAR)
    else:
        subtotal = base

    comision = round(subtotal * COMISION_PORCENTAJE_SESION[tipo_sesion], 2)
    total = round(subtotal + comision, 2)

    return {
        'tipo_sesion': tipo_sesion,
        'integrantes_familia': integrantes_normalizados,
        'subtotal': subtotal,
        'comision': comision,
        'total': total,
    }

from datetime import datetime, timedelta, time, date
from collections import defaultdict
from .models import DiaFestivo, Cita, PerfilPsicologo

from datetime import datetime, timedelta, time
from collections import defaultdict

def _buscar_slots_globales_con_filtros(filtros, fecha_inicio, fecha_fin, tipo_sesion):
    """
    Helper interno: corre la búsqueda global de slots con el diccionario de
    filtros que le pases (puede o no incluir 'genero').
    """
    # 1. ACTUALIZACIÓN: Usamos el nuevo EsquemaHorarioPsicologo
    esquemas_en_rango = EsquemaHorarioPsicologo.objects.filter(
        activo=True, fecha_inicio__lte=fecha_fin, fecha_fin__gte=fecha_inicio,
    ).order_by('fecha_inicio')

    # 2. ACTUALIZACIÓN: Ajustamos el Prefetch para usar el related_name 'esquemas_horarios'
    psicologos_activos = PerfilPsicologo.objects.filter(**filtros).select_related('usuario').prefetch_related(
        Prefetch('esquemas_horarios', queryset=esquemas_en_rango, to_attr='_esquemas_rango')
    )

    slots_globales = {}

    for psicologo in psicologos_activos:
        slots_psicologo = obtener_slots_psicologo(psicologo, fecha_inicio, fecha_fin, tipo_sesion)

        for fecha_str, lista_horas in slots_psicologo.items():
            if fecha_str not in slots_globales:
                slots_globales[fecha_str] = set()
            slots_globales[fecha_str].update(lista_horas)

    return {
        fecha: sorted(list(horas), key=lambda x: datetime.strptime(x, '%I:%M %p'))
        for fecha, horas in sorted(slots_globales.items()) if horas # <--- Agregamos sorted() aquí
    }


def obtener_slots_globales(fecha_inicio, fecha_fin, preferencia=None, tipo_sesion="individual"):
    """
    🔥 BÚSQUEDA GLOBAL: usada cuando el paciente TODAVÍA NO tiene psicólogo asignado.

    El género es una PREFERENCIA, no un requisito: primero intentamos respetarla,
    pero si con ese género no hay ni un solo horario disponible, hacemos un
    segundo intento sin el filtro de género en vez de devolver "no hay horarios"
    cuando en realidad sí hay citas (solo que con el otro género).
    """
    campo_capacidad = CAPACIDAD_POR_TIPO_SESION.get(tipo_sesion, 'atiende_individual')

    filtros_base = {
        campo_capacidad: True,
        'esta_activo': True,
    }

    genero_preferido = None
    if preferencia:
        preferencia_norm = preferencia.strip().lower()
        if 'mujer' in preferencia_norm:
            genero_preferido = 'Mujer'
        elif 'hombre' in preferencia_norm:
            genero_preferido = 'Hombre'
        # Cualquier otro valor (Indistinto, Cualquiera, "", etc.) => no se filtra por género.

    if genero_preferido:
        filtros_con_genero = {**filtros_base, 'genero': genero_preferido}
        resultado = _buscar_slots_globales_con_filtros(filtros_con_genero, fecha_inicio, fecha_fin, tipo_sesion)
        if resultado:
            return resultado
        # 🔥 No hubo nada con el género preferido: mejor ofrecer el otro género
        # que dejar al paciente sin ninguna opción. Caemos al filtro base (sin genero).

    return _buscar_slots_globales_con_filtros(filtros_base, fecha_inicio, fecha_fin, tipo_sesion)


def obtener_slots_psicologo(psicologo, fecha_inicio, fecha_fin, tipo_sesion="individual"):
    """
    🔥 BÚSQUEDA POR PSICÓLOGO: usada una vez que el paciente YA tiene un psicólogo asignado.
    """
    campo_capacidad = CAPACIDAD_POR_TIPO_SESION.get(tipo_sesion, 'atiende_individual')
    if not psicologo.esta_activo or not getattr(psicologo, campo_capacidad, False):
        return {}

    esquemas_rango = getattr(psicologo, '_esquemas_rango', None)
    if esquemas_rango is None:
        esquemas_rango = list(
            EsquemaHorarioPsicologo.objects.filter(
                psicologo=psicologo, activo=True,
                fecha_inicio__lte=fecha_fin, fecha_fin__gte=fecha_inicio,
            ).order_by('fecha_inicio')
        )

    if not esquemas_rango:
        return {}

    def _esquema_del_dia(dia):
        for e in esquemas_rango:
            if e.fecha_inicio <= dia <= e.fecha_fin:
                return e
        return None

    festivos = set(
        DiaFestivo.objects.filter(fecha__gte=fecha_inicio, fecha__lte=fecha_fin)
        .values_list('fecha', flat=True)
    )
    dias_libres = set(
        psicologo.dias_libres.filter(fecha__gte=fecha_inicio, fecha__lte=fecha_fin)
        .values_list('fecha', flat=True)
    )

    citas_ocupadas = set(
        Cita.objects.filter(
            psicologo=psicologo, fecha__gte=fecha_inicio, fecha__lte=fecha_fin
        )
        .exclude(estado__in=['Cancelada', 'No asistió'])
        .values_list('fecha', 'hora')
    )

    slots_por_fecha = {}
    dia_actual = fecha_inicio
    dias_procesados = 0
    max_iteraciones = 120

    while dia_actual <= fecha_fin and dias_procesados < max_iteraciones:
        esquema = _esquema_del_dia(dia_actual)

        if (
            esquema is None
            or dia_actual.weekday() in (esquema.dias_descanso or [])
            or dia_actual in festivos
            or dia_actual in dias_libres
        ):
            dia_actual += timedelta(days=1)
            dias_procesados += 1
            continue

        bloques_dia = esquema.horario_para_dia(dia_actual, tipo_sesion=tipo_sesion)

        if not bloques_dia:
            dia_actual += timedelta(days=1)
            dias_procesados += 1
            continue

        slots_del_dia = []
        for bloque in bloques_dia:
            h_inicio, h_fin = bloque['hora_inicio'], bloque['hora_fin']
            h_comida_ini = bloque['hora_comida_inicio']
            h_comida_fin = bloque['hora_comida_fin']
            tiene_comida = bool(h_comida_ini and h_comida_fin)

            slot_actual = datetime.combine(dia_actual, h_inicio)
            fin_turno = datetime.combine(dia_actual, h_fin)

            while slot_actual < fin_turno:
                hora_slot = slot_actual.time()
                es_hora_comida = False
                if tiene_comida and h_comida_ini <= hora_slot < h_comida_fin:
                    es_hora_comida = True

                if not es_hora_comida and (dia_actual, hora_slot) not in citas_ocupadas:
                    slots_del_dia.append(hora_slot.strftime('%I:%M %p'))

                slot_actual += timedelta(hours=1)

        if slots_del_dia:
            fecha_str = dia_actual.strftime('%Y-%m-%d')
            slots_por_fecha[fecha_str] = sorted(
                slots_del_dia,
                key=lambda x: datetime.strptime(x, '%I:%M %p')
            )

        dia_actual += timedelta(days=1)
        dias_procesados += 1

    return slots_por_fecha

def obtener_slots_psicologo_para_dia(psicologo, fecha, tipo_sesion="individual"):
    """
    Función auxiliar para consultar los espacios de un solo día.
    Llama a la función principal que ya actualizamos con el nuevo modelo.
    """
    slots_map = obtener_slots_psicologo(psicologo, fecha, fecha, tipo_sesion=tipo_sesion)
    return slots_map.get(fecha.strftime('%Y-%m-%d'), [])
# =========================================================================
# 🧠 FUNCIÓN MAESTRA: CREAR ENLACE DE GOOGLE MEET
# =========================================================================



_EMAIL_REGEX = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
 
 
def _email_valido(email):
    """True solo si el string parece un email real y bien formado."""
    return bool(email) and bool(_EMAIL_REGEX.match(email.strip()))


def generar_link_meet(fecha_obj, hora_obj, paciente_nombre, psicologo_nombre, paciente_email, psicologo_email):
    SCOPES = ['https://www.googleapis.com/auth/calendar.events']
 
    # ✅ leer desde variable de entorno en lugar de archivo
    token_json_str = os.environ.get('GOOGLE_TOKEN_JSON')
    if not token_json_str:
        print("ERROR: No existe la variable de entorno GOOGLE_TOKEN_JSON.")
        return None
 
    try:
        token_data = json.loads(token_json_str)
        creds = Credentials.from_authorized_user_info(token_data, SCOPES)
 
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            # Nota: en Railway no se persiste el refresh automáticamente,
            # así que actualiza GOOGLE_TOKEN_JSON manualmente si expira.
            print("⚠️ Token refrescado. Actualiza GOOGLE_TOKEN_JSON en Railway si hace falta.")
 
        service = build('calendar', 'v3', credentials=creds)
 
        inicio_datetime = datetime.combine(fecha_obj, hora_obj)
        fin_datetime = inicio_datetime + timedelta(minutes=50)
 
        start_format = inicio_datetime.isoformat() + '-06:00'
        end_format = fin_datetime.isoformat() + '-06:00'
 
        # 🔧 FIX: armamos la lista de attendees SOLO con emails válidos.
        # Si alguno viene vacío o mal formado, se omite (no truena el evento)
        # pero el nombre de esa persona sigue apareciendo en el título.
        attendees = []
        if _email_valido(paciente_email):
            attendees.append({'email': paciente_email.strip()})
        else:
            print(f"⚠️ Meet sin invitar por correo al paciente (email inválido/vacío): {paciente_email!r}")
 
        if _email_valido(psicologo_email):
            attendees.append({'email': psicologo_email.strip()})
        else:
            print(f"⚠️ Meet sin invitar por correo al psicólogo (email inválido/vacío): {psicologo_email!r}")
 
        event = {
            'summary': f'Sesión HOPE: {paciente_nombre} y Psic. {psicologo_nombre}',
            'description': 'Sesión psicológica online generada desde la plataforma HOPE.',
            'start': {
                'dateTime': start_format,
                'timeZone': 'America/Mexico_City',
            },
            'end': {
                'dateTime': end_format,
                'timeZone': 'America/Mexico_City',
            },
            'conferenceData': {
                'createRequest': {
                    'requestId': f"hope_meet_{uuid.uuid4().hex[:10]}",
                    'conferenceSolutionKey': {'type': 'hangoutsMeet'}
                }
            }
        }
 
        # 🔧 Solo incluimos la clave 'attendees' si hay al menos uno válido.
        # (No es obligatorio omitirla si la lista está vacía, pero así
        # queda más limpio el payload).
        if attendees:
            event['attendees'] = attendees
 
        event_result = service.events().insert(
            calendarId='primary',
            body=event,
            conferenceDataVersion=1,
            sendUpdates='none'
        ).execute()
 
        return {
            'link': event_result.get('hangoutLink'),
            'id_evento': event_result.get('id')
        }
 
    except Exception as e:
        print(f"Error generando Meet: {e}")
        return None

# =========================================================================
# 🏠 NUEVA VISTA: PÁGINA DE INICIO (LANDING PAGE)
def inicio(request):

    articulos = ArticuloPrensa.objects.filter(publicado=True)[:6]
    context = {
        'cuestionario_json': json.dumps(CUESTIONARIO_CLINICO),
        'paypal_client_id': settings.PAYPAL_CLIENT_ID,
        'articulos_prensa': articulos,
    }
    
    # ¡AQUÍ ESTÁ LA MAGIA! Pasamos el 'context' a la plantilla
    return render(request, 'inicio.html', context)

def modulo_informativo(request):
    context = {
        'cuestionario_json': json.dumps(CUESTIONARIO_CLINICO)
    }
    return render(request, 'informativo.html', context)


@transaction.atomic
def registrar_usuario(request):
    if request.method == 'POST':
        nombre = request.POST.get('nombre')
        email = request.POST.get('email')
        password = request.POST.get('password')
        telefono = request.POST.get('telefono')
        telefono_emergencia = request.POST.get('telefono_emergencia', '')
        flujo_elegido = request.POST.get('flujo_elegido', 'individual')
        respuestas_raw = request.POST.get('respuestas_json', '{}')

        try:
            respuestas_dict = json.loads(respuestas_raw)
        except json.JSONDecodeError:
            respuestas_dict = {}

        if User.objects.filter(email=email).exists():
            return JsonResponse({'status': 'error', 'message': 'Este correo ya está registrado.'})

        user = User.objects.create_user(
            username=email, 
            email=email, password=password)
        user.first_name = nombre
        
        # 🔥 El usuario se activa de inmediato para omitir el correo
        user.is_active = True
        user.save()

        es_padre_bool = False
        respuesta_padre = respuestas_dict.get('es_padre', '')
        respuesta_pareja = respuestas_dict.get('son_padres', '')
        
        if 'Sí' in respuesta_padre or 'Sí' in respuesta_pareja:
            es_padre_bool = True

        UsuarioPerfil.objects.create(
            usuario=user, 
            nombre=nombre, 
            telefono=telefono,
            telefono_emergencia=telefono_emergencia,
            es_padre=es_padre_bool
        )

        CuestionarioRegistro.objects.create(
            paciente=user,
            flujo_elegido=flujo_elegido,
            respuestas=respuestas_dict,
        )

        # 🔥 Dejamos el envío de correo desactivado en bloque
        """
        uid = urlsafe_base64_encode(force_bytes(user.pk))
        token = default_token_generator.make_token(user)
        link_activacion = request.build_absolute_uri(
            reverse('activar_cuenta', kwargs={'uidb64': uid, 'token': token})
        )

        asunto = 'Verifica tu cuenta en HOPE - El primer paso a tu bienestar'
        contexto = {'nombre': nombre, 'link_activacion': link_activacion}
        mensaje_html = render_to_string('verificacion_email.html', contexto)
        mensaje_plano = strip_tags(mensaje_html)

        send_mail(
            subject=asunto,
            message=mensaje_plano,
            from_email=None,
            recipient_list=[email],
            html_message=mensaje_html,
            fail_silently=False
        )
        """

        # 🔥 Iniciamos sesión automáticamente en el servidor
        login(request, user)
        
        # 🔥 Mandamos la URL directa para que el JS del frontend redirija al instante
        return JsonResponse({'status': 'success', 'redirect_url': reverse('panel_generico')})

def activar_cuenta(request, uidb64, token):
    try:
        uid = force_str(urlsafe_base64_decode(uidb64))
        user = User.objects.get(pk=uid)
    except (TypeError, ValueError, OverflowError, User.DoesNotExist):
        user = None

    if user is not None and default_token_generator.check_token(user, token):
        user.is_active = True
        user.save()
        return render(request, 'verificacion_resultado.html', {'exito': True})
    else:
        return render(request, 'verificacion_resultado.html', {'exito': False})

def login_usuario(request):
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'Método no permitido.'}, status=405)

    email = request.POST.get('login_email', '').strip()
    password = request.POST.get('login_password', '')

    if not email or not password:
        return JsonResponse({
            'status': 'error',
            'error_type': 'invalid',
            'message': 'Por favor ingresa tu correo y contraseña.'
        })

    try:
        # iexact makes the lookup case-insensitive — works as long as
        # your registro view saves email as the username field.
        user = User.objects.get(username__iexact=email)
    except User.DoesNotExist:
        return JsonResponse({
            'status': 'error',
            'error_type': 'invalid',
            'message': 'El correo o la contraseña son incorrectos.'
        })
    except User.MultipleObjectsReturned:
        # Defensive: shouldn't happen if username is unique, but just in case
        return JsonResponse({
            'status': 'error',
            'error_type': 'invalid',
            'message': 'El correo o la contraseña son incorrectos.'
        })

    if not user.check_password(password):
        return JsonResponse({
            'status': 'error',
            'error_type': 'invalid',
            'message': 'El correo o la contraseña son incorrectos.'
        })

    if not user.is_active:
        return JsonResponse({
            'status': 'error',
            'error_type': 'unverified',
            'message': 'Aún no verificas tu cuenta. Por favor, revisa tu bandeja de entrada.'
        })

    login(request, user)

    if user.is_superuser:
        redirect_url = reverse('panel_admin')
    elif hasattr(user, 'perfil_psicologo'):
        redirect_url = reverse('panel_doctor')
    else:
        redirect_url = reverse('panel_generico')

    return JsonResponse({'status': 'success', 'redirect_url': redirect_url})



def panel_generico(request):
    if not request.user.is_authenticated:
        return redirect('modulo_informativo')

    # Perfil del usuario
    try:
        perfil_usuario = request.user.perfil
    except Exception:
        logout(request)
        return redirect('modulo_informativo')

# Tipo de servicio y Preferencia desde cuestionario
    tipo_servicio = "individual"
    preferencia = ""  # 🔥 INICIAMOS LA VARIABLE VACÍA
    
    ultimo_cuestionario = CuestionarioRegistro.objects.filter(paciente=request.user).last()
    if ultimo_cuestionario and ultimo_cuestionario.respuestas:
        respuestas = ultimo_cuestionario.respuestas
        if isinstance(respuestas, str):
            try:
                respuestas = json.loads(respuestas)
            except:
                respuestas = {}
        tipo_servicio = respuestas.get("servicio_solicitado", "individual")
        preferencia = respuestas.get("preferencia_terapeuta", "") # 🔥 EXTRAEMOS LA PREFERENCIA

    # Tiempos actuales
    now_local = timezone.localtime(timezone.now())
    hoy = now_local.date()
    hora_actual = now_local.time().replace(second=0, microsecond=0)

    psicologo_asignado = perfil_usuario.psicologo_asignado
    hora_limite_paciente = (now_local - timedelta(hours=1)).time().replace(second=0, microsecond=0)
    # Cita próxima
    # 🔥 OPTIMIZACIÓN: select_related evita un query extra al acceder a
    # cita_proxima.psicologo.usuario más abajo en el template.
    cita_proxima = Cita.objects.select_related('psicologo__usuario').filter(
        Q(fecha__gt=hoy) | Q(fecha=hoy, hora__gte=hora_limite_paciente), # ¡Cambio aquí!
        paciente=request.user,
        estado='Confirmada'
    ).order_by('fecha', 'hora').first()

    # ========================================================= hkgf
    # =========================================================
    fecha_limite = hoy + timedelta(days=90)

    # 🔥 Normalizamos el tipo de sesión inicial (viene del cuestionario) para
    # que la búsqueda de disponibilidad respete la modalidad desde el arranque.
    tipo_sesion_inicial = tipo_servicio if tipo_servicio in TIPOS_SESION_VALIDOS else 'individual'

    if psicologo_asignado:
        # Paciente con psicólogo asignado: solo sus horarios
        dias_json = obtener_slots_psicologo(psicologo_asignado, hoy, fecha_limite, tipo_sesion=tipo_sesion_inicial)
    else:
        # 🔥 EL MOMENTO MÁGICO: Le pasamos la 'preferencia' a la función global
        dias_json = obtener_slots_globales(hoy, fecha_limite, preferencia, tipo_sesion=tipo_sesion_inicial)

    # Convertir a formato que usa el template (dias_html con objetos fecha y hora)
    dias_html = {}
    for fecha_str, horas_str in dias_json.items():
        fecha_obj = datetime.strptime(fecha_str, '%Y-%m-%d').date()
        horas_obj = [datetime.strptime(h, '%I:%M %p').time() for h in horas_str]
        dias_html[fecha_obj] = horas_obj

    # =========================================================
    # TALLERES E INSCRIPCIONES (igual que antes)
    # =========================================================
    talleres_futuros = Taller.objects.filter(fecha__gte=hoy).order_by('fecha', 'hora')
    mis_inscripciones_ids = InscripcionTaller.objects.filter(paciente=request.user).values_list('taller_id', flat=True)
    mis_talleres = InscripcionTaller.objects.filter(
        paciente=request.user,
        taller__fecha__gte=hoy
    ).order_by('taller__fecha')

    taller_solicitado_obj = None
    if tipo_servicio not in ['individual', 'terapia_individual', 'terapia_pareja', '']:
        taller_solicitado_obj = Taller.objects.filter(nombre=tipo_servicio, fecha__gte=hoy).first()

    return render(request, 'panel_generico.html', {
        'dias_disponibles_json': dias_json,
        'dias_disponibles': dias_html,
        'cita_proxima': cita_proxima,
        'perfil': perfil_usuario,
        'tipo_servicio': tipo_servicio,
        'taller_solicitado_obj': taller_solicitado_obj,
        'talleres_padres': talleres_futuros.filter(tipo='padres'),
        'talleres_pareja': talleres_futuros.filter(tipo='pareja'),
        'talleres_grupales': talleres_futuros.filter(tipo='grupal'),
        'talleres_autoestima': talleres_futuros.filter(tipo='autoestima'),
        'mis_inscripciones_ids': list(mis_inscripciones_ids),
        'mis_talleres': mis_talleres,
        'paypal_client_id': settings.PAYPAL_CLIENT_ID,
        'psicologo_asignado': psicologo_asignado,
        # 🔥 NUEVO: se manda al frontend para que el resumen de costos en
        # tiempo real (JS) use exactamente los mismos números que el backend.
        'precios_config_json': json.dumps({
            'base': PRECIO_BASE_SESION,
            'comision_pct': COMISION_PORCENTAJE_SESION,
            'incremento_integrante_familiar': INCREMENTO_POR_INTEGRANTE_FAMILIAR,
            'min_integrantes_familiar': MIN_INTEGRANTES_FAMILIAR,
        }),
    })


def inscribir_taller_ajax(request):
    if request.method == 'POST' and request.user.is_authenticated:
        taller_id = request.POST.get('taller_id')
        try:
            taller = Taller.objects.get(id=taller_id)
            if taller.cupos_disponibles > 0:
                InscripcionTaller.objects.get_or_create(paciente=request.user, taller=taller)
                
                # =========================================================
                # 📧 ENVIAR CORREO HTML DE CONFIRMACIÓN DEL TALLER
                # =========================================================
                asunto = f'Confirmación de inscripción: {taller.nombre}'
                link_meet = taller.enlace_meet if taller.enlace_meet else "El enlace se te compartirá en tu panel."
                
                contexto = {
                    'nombre': request.user.first_name,
                    'taller_nombre': taller.nombre,
                    'fecha': taller.fecha.strftime('%d/%m/%Y'),
                    'hora': taller.hora.strftime('%H:%M'),
                    'link_meet': link_meet
                }
                
                mensaje_html = render_to_string('correo_taller.html', contexto)
                mensaje_plano = strip_tags(mensaje_html)
                
                try:
                    send_mail(asunto, mensaje_plano, 'Espacio HOPE <no-reply@espaciohope.com>', [request.user.email], html_message=mensaje_html, fail_silently=True)
                except Exception as e:
                    print(f"Error al enviar correo del taller: {e}")
                # =========================================================

                return JsonResponse({'status': 'success', 'message': '¡Inscripción exitosa!'})
            else:
                return JsonResponse({'status': 'error', 'message': 'Lo sentimos, el cupo está lleno.'})
        except Taller.DoesNotExist:
            return JsonResponse({'status': 'error', 'message': 'El programa no existe.'})
    return JsonResponse({'status': 'error', 'message': 'Petición no válida.'})

def obtener_disponibilidad_por_tipo_ajax(request):
    """
    🔥 NUEVO: Cuando el paciente cambia de modalidad en el wizard
    (individual / pareja / familiar), el frontend llama a este endpoint
    para refrescar el calendario mostrando SOLO los horarios de los
    psicólogos habilitados para esa modalidad.
    """
    if not request.user.is_authenticated:
        return JsonResponse({'status': 'error', 'message': 'Debes iniciar sesión.'}, status=403)

    tipo_sesion = request.GET.get('tipo_sesion', 'individual')
    if tipo_sesion not in TIPOS_SESION_VALIDOS:
        tipo_sesion = 'individual'

    try:
        perfil_usuario = request.user.perfil
    except Exception:
        return JsonResponse({'status': 'error', 'message': 'Perfil no encontrado.'}, status=400)

    preferencia = ""
    ultimo_cuestionario = CuestionarioRegistro.objects.filter(paciente=request.user).last()
    if ultimo_cuestionario and ultimo_cuestionario.respuestas:
        respuestas = ultimo_cuestionario.respuestas
        if isinstance(respuestas, str):
            try:
                respuestas = json.loads(respuestas)
            except Exception:
                respuestas = {}
        preferencia = respuestas.get("preferencia_terapeuta", "")

    hoy = timezone.localtime(timezone.now()).date()
    fecha_limite = hoy + timedelta(days=90)
    psicologo_asignado = perfil_usuario.psicologo_asignado

    if psicologo_asignado:
        dias_json = obtener_slots_psicologo(psicologo_asignado, hoy, fecha_limite, tipo_sesion=tipo_sesion)
        if not dias_json:
            return JsonResponse({
                'status': 'success',
                'dias': {},
                'aviso': 'Tu terapeuta asignado no atiende esta modalidad todavía. Contáctanos para reasignarte.',
            })
    else:
        dias_json = obtener_slots_globales(hoy, fecha_limite, preferencia, tipo_sesion=tipo_sesion)

    return JsonResponse({'status': 'success', 'dias': dias_json})


def calcular_precio_sesion_ajax(request):
    """🔥 NUEVO: fuente de verdad del precio para el resumen en tiempo real del frontend."""
    tipo_sesion = request.GET.get('tipo_sesion', 'individual')
    integrantes_familia = request.GET.get('integrantes_familia')
    resultado = calcular_precio_sesion(tipo_sesion, integrantes_familia)
    return JsonResponse({'status': 'success', **resultado})


from django.db import transaction
@transaction.atomic
def guardar_cita_ajax(request):
    if request.method == 'POST':
        if not request.user.is_authenticated:
            return JsonResponse({'status': 'error', 'message': 'Debes iniciar sesión.'})

        fecha_str = request.POST.get('fecha')
        hora_str = request.POST.get('hora')
        animo = request.POST.get('animo', 'No especificó')
        modalidad_str = request.POST.get('modalidad', 'En línea')
        tipo_sesion_str = request.POST.get('tipo_servicio', 'individual')
        # 💳 Referencia de la orden de PayPal (si el guardado viene después de un
        # pago ya capturado). La registramos en el log INMEDIATAMENTE, antes de
        # cualquier posible excepción, para que un pago cobrado nunca quede sin
        # rastro aunque falle el resto del guardado de la cita.
        paypal_order_id = request.POST.get('paypal_order_id')
        if paypal_order_id:
            logging.getLogger(__name__).info(
                'Pago de PayPal recibido (orden %s) para usuario %s - fecha %s %s',
                paypal_order_id, request.user, fecha_str, hora_str
            )

        if tipo_sesion_str not in TIPOS_SESION_VALIDOS:
            tipo_sesion_str = 'individual'

        integrantes_familia = None
        if tipo_sesion_str == 'familiar':
            precio_info = calcular_precio_sesion(tipo_sesion_str, request.POST.get('integrantes_familia'))
            integrantes_familia = precio_info['integrantes_familia']

        campo_capacidad = CAPACIDAD_POR_TIPO_SESION[tipo_sesion_str]

        try:
            fecha_obj = datetime.strptime(fecha_str, '%Y-%m-%d').date()
            hora_obj = datetime.strptime(hora_str, '%H:%M').time()
            perfil = request.user.perfil
            psicologo = perfil.psicologo_asignado

            if psicologo and not getattr(psicologo, campo_capacidad, False):
                return JsonResponse({'status': 'error', 'message': 'Tu terapeuta asignado no atiende esta modalidad. Contáctanos para asignarte a un especialista.'})

            # Lógica de asignación automática (si no tiene psicólogo)
            if not psicologo:
                preferencia = ""
                try:
                    cuestionario = request.user.cuestionario_inicial
                    preferencia = cuestionario.respuestas.get('preferencia_terapeuta', '')
                except:
                    pass

                # 🔥 OPTIMIZACIÓN VITAL PARA EL PAGO: 
                # Precargamos los esquemas de horario del día exacto para no hacer N consultas.
                esquemas_del_dia = EsquemaHorarioPsicologo.objects.filter(
                    activo=True, fecha_inicio__lte=fecha_obj, fecha_fin__gte=fecha_obj
                )
                psicologos_candidatos = PerfilPsicologo.objects.filter(
                    esta_activo=True, **{campo_capacidad: True}
                ).prefetch_related(
                    Prefetch('esquemas_horarios', queryset=esquemas_del_dia, to_attr='_esquemas_rango')
                )

                # 1. Filtramos doctores que tengan el slot válido
                doctores_con_slot_valido = []
                for psicologo_temp in psicologos_candidatos:
                    slots_del_dia = obtener_slots_psicologo_para_dia(psicologo_temp, fecha_obj, tipo_sesion=tipo_sesion_str)
                    if hora_obj.strftime('%I:%M %p') in slots_del_dia:
                        doctores_con_slot_valido.append(psicologo_temp.id)

                # 2. Bloqueamos para evitar empalmes y anotamos carga histórica
                psicologos_libres = PerfilPsicologo.objects.filter(
                    id__in=doctores_con_slot_valido
                ).select_for_update().annotate(
                    carga_historica=Count('citas_agendadas', filter=~Q(citas_agendadas__estado='Cancelada'))
                )

                if not psicologos_libres.exists():
                    return JsonResponse({'status': 'error', 'message': 'Lo sentimos, este horario acaba de ser ocupado o no hay especialistas disponibles para esta modalidad. Elige otro horario.'})

                # 3. Asignación con preferencia y desempate
                if 'Mujer' in preferencia:
                    psicologo = psicologos_libres.filter(genero='Mujer').order_by('carga_historica', '?').first()
                elif 'Hombre' in preferencia:
                    psicologo = psicologos_libres.filter(genero='Hombre').order_by('carga_historica', '?').first()

                if not psicologo:
                    psicologo = psicologos_libres.order_by('carga_historica', '?').first()

                perfil.psicologo_asignado = psicologo
                perfil.save()
                
                # Verificación final
                if Cita.objects.filter(psicologo=psicologo, fecha=fecha_obj, hora=hora_obj, estado='Confirmada').exists():
                    return JsonResponse({'status': 'error', 'message': 'El horario fue ocupado mientras procesabas. Elige otro.'})
                    
            else:
                if Cita.objects.filter(psicologo=psicologo, fecha=fecha_obj, hora=hora_obj, estado='Confirmada').exists():
                    return JsonResponse({'status': 'error', 'message': 'Tu terapeuta ya tiene una cita en ese horario. Elige otro.'})

            # Generar Meet si es en línea
            link_final = None
            id_google = None
            if modalidad_str == 'En línea':
                datos_meet = generar_link_meet(
                    fecha_obj=fecha_obj,
                    hora_obj=hora_obj,
                    paciente_nombre=request.user.first_name,
                    psicologo_nombre=psicologo.usuario.first_name,
                    paciente_email=request.user.email,             
                    psicologo_email=psicologo.usuario.email        
                )
                if datos_meet:
                    link_final = datos_meet['link']
                    id_google = datos_meet['id_evento']

            # Crear la cita
            Cita.objects.create(
                paciente=request.user,
                psicologo=psicologo,
                fecha=fecha_obj,
                hora=hora_obj,
                estado_animo=animo,
                modalidad=modalidad_str,
                tipo_sesion=tipo_sesion_str,
                integrantes_familia=integrantes_familia,
                motivo='Primera Sesión' if not perfil.psicologo_asignado else 'Sesión de Seguimiento',
                estado='Confirmada',
                enlace_meet=link_final,
                id_evento_google=id_google  
            )

            # Enviar correo (asumiendo que las funciones render_to_string, strip_tags y send_mail están importadas)
            asunto = 'Confirmación de tu sesión en HOPE'
            link_correo = link_final if link_final else "Cita Presencial (Revisa tu panel para ver la dirección)"
            contexto = {
                'nombre': request.user.first_name,
                'psicologo_nombre': psicologo.usuario.first_name,
                'fecha': fecha_obj.strftime('%d/%m/%Y'),
                'hora': hora_obj.strftime('%H:%M'),
                'link_meet': link_correo
            }
            mensaje_html = render_to_string('correo_cita.html', contexto)
            mensaje_plano = strip_tags(mensaje_html)
            send_mail(asunto, mensaje_plano, 'Espacio HOPE <no-reply@espaciohope.com>', [request.user.email], html_message=mensaje_html, fail_silently=True)

            return JsonResponse({'status': 'success'})
        except Exception as e:
            if paypal_order_id:
                logging.getLogger(__name__).error(
                    '⚠️ PAGO COBRADO SIN CITA GUARDADA - orden PayPal %s, usuario %s: %s',
                    paypal_order_id, request.user, e
                )
            return JsonResponse({'status': 'error', 'message': str(e)})

    return JsonResponse({'status': 'error'})




@transaction.atomic
def reagendar_cita_ajax(request):
    if request.method != 'POST' or not request.user.is_authenticated:
        return JsonResponse({'status': 'error', 'message': 'Petición no válida.'})

    cita_id = request.POST.get('cita_id')
    nueva_fecha_str = request.POST.get('fecha')
    nueva_hora_str = request.POST.get('hora')

    try:
        cita = Cita.objects.select_for_update().select_related('psicologo').get(
            id=cita_id, paciente=request.user, estado='Confirmada'
        )
    except Cita.DoesNotExist:
        return JsonResponse({'status': 'error', 'message': 'No encontramos esa cita.'})

    # Regla: solo se puede reagendar hasta 1 hora antes de la sesión ACTUAL
    ahora = timezone.localtime(timezone.now())
    inicio_actual = timezone.make_aware(datetime.combine(cita.fecha, cita.hora))
    if inicio_actual - ahora < timedelta(hours=1):
        return JsonResponse({'status': 'error', 'message': 'Ya no puedes reagendar: falta menos de 1 hora para tu sesión.'})

    try:
        nueva_fecha = datetime.strptime(nueva_fecha_str, '%Y-%m-%d').date()
        nueva_hora = datetime.strptime(nueva_hora_str, '%H:%M').time()
    except (TypeError, ValueError):
        return JsonResponse({'status': 'error', 'message': 'Fecha u hora inválida.'})

    if not cita.psicologo:
        return JsonResponse({'status': 'error', 'message': 'Esta cita no tiene terapeuta asignado.'})

    # 🔒 MISMA LÓGICA que usamos al agendar: solo slots realmente libres del doctor
    # (respetando la modalidad de la cita original: individual/pareja/familiar)
    slots_del_dia = obtener_slots_psicologo_para_dia(cita.psicologo, nueva_fecha, tipo_sesion=cita.tipo_sesion)
    if nueva_hora.strftime('%I:%M %p') not in slots_del_dia:
        return JsonResponse({'status': 'error', 'message': 'Ese horario ya no está disponible.'})

    # doble-check anti condición-de-carrera
    if Cita.objects.filter(psicologo=cita.psicologo, fecha=nueva_fecha, hora=nueva_hora,
                            estado='Confirmada').exclude(id=cita.id).exists():
        return JsonResponse({'status': 'error', 'message': 'Ese horario acaba de ser ocupado.'})

    cita.fecha = nueva_fecha
    cita.hora = nueva_hora
    cita.save(update_fields=['fecha', 'hora'])

    return JsonResponse({
        'status': 'success',
        'nueva_fecha': nueva_fecha.strftime('%d/%m/%Y'),
        'nueva_hora': nueva_hora.strftime('%H:%M'),
    })

    

def panel_doctor(request):
    if not request.user.is_authenticated or not hasattr(request.user, 'perfil_psicologo'):
        return redirect('modulo_informativo')

    psicologo = request.user.perfil_psicologo

    # ✅ FIX 1: Hora local México, no UTC
    now_local = timezone.localtime(timezone.now())
    hoy = now_local.date()
    hora_actual = now_local.time().replace(second=0, microsecond=0)

    # ✅ FIX 2: Tolerancia de 60 min — una cita "sigue activa" hasta 1 hora después de su hora
    from datetime import timedelta as td
    hora_limite = (now_local - td(hours=1)).time().replace(second=0, microsecond=0)

    # ✅ FIX 3: Citas de hoy que NO han expirado (incluye las que empezaron hace menos de 60 min)
    citas_hoy = Cita.objects.filter(
        psicologo=psicologo,
        fecha=hoy,
        estado='Confirmada',
        hora__gte=hora_limite  # <- muestra citas desde 1 hora atrás
    ).order_by('hora')

    # 🔥 NUEVO: Talleres/Grupos de hoy que NO han expirado
    talleres_hoy = Taller.objects.filter(
        psicologo=psicologo,
        fecha=hoy,
        hora__gte=hora_limite
    ).order_by('hora')


    # ✅ FIX 4: Calendario también con hora local
    # Agregamos select_related para traer los datos del paciente y su perfil más rápido
    citas_todas = Cita.objects.filter(psicologo=psicologo).select_related('paciente', 'paciente__perfil')
    
    eventos_calendario = []
    for c in citas_todas:
        # Extraemos el teléfono validando que el perfil exista
        telefono_pac = c.paciente.perfil.telefono if hasattr(c.paciente, 'perfil') and c.paciente.perfil.telefono else 'Sin registrar'
        
        eventos_calendario.append({
            'title': f"{c.paciente.first_name} ({c.hora.strftime('%H:%M')})",
            'start': f"{c.fecha.isoformat()}T{c.hora.strftime('%H:%M:%S')}",
            'backgroundColor': '#297E7E' if c.fecha >= hoy else '#D1D5DB',
            'borderColor': '#297E7E' if c.fecha >= hoy else '#D1D5DB',
            'extendedProps': {
                'estado': c.estado,
                'psicologo': psicologo.usuario.first_name,
                'modalidad': c.modalidad,
                'enlace_meet': c.enlace_meet or '',
                'email': c.paciente.email or 'Sin registrar',
                'telefono': telefono_pac
            }
        })



    mis_pacientes_db = User.objects.filter(perfil__psicologo_asignado=psicologo).annotate(
        total_citas_paciente=Count('citas_como_paciente', filter=Q(citas_como_paciente__psicologo=psicologo))
    ).distinct()

    pacientes_data = [
        {
            'usuario': p,
            'total_citas': p.total_citas_paciente
        }
        for p in mis_pacientes_db
    ]

    mis_talleres_impartidos = Taller.objects.filter(psicologo=psicologo).order_by('fecha', 'hora')

    for taller in mis_talleres_impartidos:
        eventos_calendario.append({
            'title': f"★ Grupo: {taller.nombre}",
            'start': f"{taller.fecha.isoformat()}T{taller.hora.strftime('%H:%M:%S')}",
            'backgroundColor': '#B5992D' if taller.fecha >= hoy else '#D1D5DB',
            'borderColor': '#B5992D' if taller.fecha >= hoy else '#D1D5DB'
        })

    return render(request, 'panel_doctor.html', {
        'psicologo': psicologo,
        'citas_hoy': citas_hoy,
        'eventos_calendario_json': json.dumps(eventos_calendario),
        'pacientes_data': pacientes_data,
        'talleres_impartidos': mis_talleres_impartidos,
        'talleres_hoy': talleres_hoy, # <-- ¡NO OLVIDES ESTA LÍNEA!
        'hoy': hoy,
        # ✅ FIX 5: Pasar hora actual al template por si quieres resaltar la cita en curso
        'hora_actual': hora_actual,
    })

def obtener_expediente_ajax(request):
    if not request.user.is_authenticated: return JsonResponse({'status': 'error'})
    paciente_id = request.GET.get('paciente_id')
    paciente = User.objects.get(id=paciente_id)
    perfil = paciente.perfil
    
    # BUSCAMOS LAS CITAS PASADAS EN LUGAR DE SOLO LOS HISTORIALES
    now_local = timezone.localtime(timezone.now())
    citas = Cita.objects.filter(
        paciente=paciente, 
        psicologo=request.user.perfil_psicologo,
        fecha__lte=now_local.date()
    ).order_by('-fecha', '-hora').select_related('nota_clinica')
    
    hist_data = []
    for c in citas:
        if hasattr(c, 'nota_clinica') and c.nota_clinica:
            h = c.nota_clinica
            hist_data.append({
                'fecha': c.fecha.strftime('%d/%m/%Y'), 
                'notas': h.notas_sesion, 
                'aprendizaje': h.aprendizaje_paciente
            })
        else:
            # PINTA LA CITA AUNQUE NO TENGA NOTAS
            hist_data.append({
                'fecha': c.fecha.strftime('%d/%m/%Y'), 
                'notas': '⚠️ Sesión pendiente de captura de bitácora.', 
                'aprendizaje': 'Sin registro'
            })
            
    return JsonResponse({'status': 'success', 'data': {
        'telefono_emergencia': perfil.telefono_emergencia or '',
        'focos_rojos': perfil.focos_rojos or '',
        'historia_clinica': perfil.historia_clinica or '',
        'recomendaciones': perfil.recomendaciones_generales or '',
        'notas_alta': perfil.notas_alta or '',
        'historiales': hist_data
    }})


# =========================================================================
# 📝 GUARDAR EXPEDIENTE GLOBAL (Perfil, Alertas, Historia y Alta)
# =========================================================================
def guardar_expediente_global_ajax(request):
    if request.method == 'POST' and request.user.is_authenticated:
        paciente_id = request.POST.get('paciente_id')
        try:
            paciente = User.objects.get(id=paciente_id)
            perfil = paciente.perfil
            
            if 'telefono_emergencia' in request.POST:
                perfil.telefono_emergencia = request.POST.get('telefono_emergencia')
            if 'focos_rojos' in request.POST:
                perfil.focos_rojos = request.POST.get('focos_rojos')
            if 'historia_clinica' in request.POST:
                perfil.historia_clinica = request.POST.get('historia_clinica')
            if 'recomendaciones_generales' in request.POST:
                perfil.recomendaciones_generales = request.POST.get('recomendaciones_generales')
            if 'notas_alta' in request.POST:
                perfil.notas_alta = request.POST.get('notas_alta')
                
            perfil.save()
            return JsonResponse({'status': 'success', 'message': 'Actualizado con éxito.'})
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': str(e)})
            
    return JsonResponse({'status': 'error'})

# =========================================================================
# 📝 GUARDAR BITÁCORA DE SESIÓN (Línea del tiempo)
# =========================================================================
def guardar_historial_ajax(request):
    if request.method == 'POST' and request.user.is_authenticated:
        paciente_id = request.POST.get('paciente_id')
        cita_id = request.POST.get('cita_id')
        historial_id = request.POST.get('historial_id') # 🔥 NUEVO: Atrapamos el ID
        
        try:
            paciente = User.objects.get(id=paciente_id)
            psicologo = request.user.perfil_psicologo
            archivo = request.FILES.get('archivo_adjunto')
            
            if historial_id:
                # MODO EDICIÓN: Actualizamos la existente
                historial = HistorialClinico.objects.get(id=historial_id, psicologo=psicologo)
                historial.como_llega = request.POST.get('como_llega', '')
                historial.notas_sesion = request.POST.get('notas_sesion', '')
                historial.aprendizaje_paciente = request.POST.get('aprendizaje_paciente', '')
                historial.como_se_va = request.POST.get('como_se_va', '')
                historial.recomendaciones = request.POST.get('recomendaciones', '')
                if archivo:
                    historial.archivo_adjunto = archivo
                historial.save()
            else:
                # MODO CREACIÓN: Lo que ya tenías
                historial = HistorialClinico(
                    paciente=paciente, psicologo=psicologo,
                    como_llega=request.POST.get('como_llega', ''),
                    notas_sesion=request.POST.get('notas_sesion', ''),
                    aprendizaje_paciente=request.POST.get('aprendizaje_paciente', ''),
                    como_se_va=request.POST.get('como_se_va', ''),
                    recomendaciones=request.POST.get('recomendaciones', ''),
                    archivo_adjunto=archivo
                )
                if cita_id and cita_id.strip() != "":
                    cita = Cita.objects.filter(id=cita_id, psicologo=psicologo).first()
                    if cita:
                        historial.cita = cita
                        cita.estado = 'Completada'
                        cita.save()
                historial.save()

            return JsonResponse({'status': 'success', 'message': 'Bitácora guardada con éxito.'})
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': str(e)})
            
    return JsonResponse({'status': 'error'})

# =========================================================================
# 📂 NUEVA VISTA: EXPEDIENTE COMPLETO DEL PACIENTE
# =========================================================================


def detalle_paciente(request, paciente_id):
    if not request.user.is_authenticated or not hasattr(request.user, 'perfil_psicologo'):
        return redirect('modulo_informativo')
 
    psicologo = request.user.perfil_psicologo
 
    try:
        paciente = User.objects.get(id=paciente_id)
    except User.DoesNotExist:
        return redirect('panel_doctor')
 
    if getattr(paciente.perfil, 'psicologo_asignado', None) != psicologo:
        return redirect('panel_doctor')
 
    # =========================================================================
    # CUESTIONARIO INICIAL (TRIAGE)
    # =========================================================================
    cuestionario = None
    respuestas_formateadas = {}
 
    if hasattr(paciente, 'cuestionario_inicial'):
        cuestionario = paciente.cuestionario_inicial
        if cuestionario.respuestas:
            for clave, valor in cuestionario.respuestas.items():
                clave_limpia = str(clave).replace('_', ' ').capitalize()
                valor_limpio = ", ".join(str(i) for i in valor) if isinstance(valor, list) else str(valor)
                respuestas_formateadas[clave_limpia] = valor_limpio
 
    # =========================================================================
    # CONSTRUCCIÓN DE LA LISTA UNIFICADA DE SESIONES
    # =========================================================================
    now_local = timezone.localtime(timezone.now())
 
    citas_pasadas = Cita.objects.filter(
        paciente=paciente, psicologo=psicologo, fecha__lte=now_local.date(),
    ).order_by('-fecha', '-hora').select_related('nota_clinica')
 
    historiales_vinculados_ids = set()
    sesiones = []
 
    for cita in citas_pasadas:
        historial = None
        try:
            historial = cita.nota_clinica
            historiales_vinculados_ids.add(historial.id)
        except Exception:
            pass
 
        tiene_meet = bool(cita.id_evento_google)
        tiene_historial = historial is not None
 
        sesiones.append({
            'tipo': 'completa',          
            'cita': cita,
            'historial': historial,
            'tiene_meet': tiene_meet,
            'tiene_historial': tiene_historial,
            'fecha_orden': cita.fecha,
            'hora_orden': cita.hora,
            'slug': f"c-{cita.id}", 
        })
 
    historiales_huerfanos = HistorialClinico.objects.filter(
        paciente=paciente, psicologo=psicologo, cita__isnull=True,
    ).exclude(id__in=historiales_vinculados_ids).order_by('-fecha_registro')
 
    for h in historiales_huerfanos:
        sesiones.append({
            'tipo': 'solo_historial',
            'cita': None,
            'historial': h,
            'tiene_meet': False,
            'tiene_historial': True,
            'fecha_orden': h.fecha_registro.date(),
            'hora_orden': h.fecha_registro.time(),
            'slug': f"h-{h.id}",
        })
 
    sesiones.sort(key=lambda s: (s['fecha_orden'], s['hora_orden']), reverse=True)
    total_sesiones = Cita.objects.filter(paciente=paciente, psicologo=psicologo).count()
 
    # =========================================================================
    # 🔥 NUEVO: DATOS DEL IPP PARA LA GRÁFICA
    # =========================================================================
    # Obtenemos todos los formularios semanales en orden cronológico (del más viejo al más nuevo)
    respuestas_ipp = RespuestaFormularioOrganica.objects.filter(paciente=paciente).order_by('fecha_respuesta')
    
    ipp_labels = []
    ipp_data = []
    
    for r in respuestas_ipp:
        # Usamos tu misma fórmula de IPT (Puntaje bruto - 10 / 40 * 100)
        ipt = round(((r.puntaje - 10) / 40) * 100)
        # Extraemos día y mes para los labels de la gráfica
        ipp_labels.append(r.fecha_respuesta.strftime('%d/%m'))
        ipp_data.append(ipt)
 
    return render(request, 'detalle_paciente.html', {
        'paciente': paciente,
        'sesiones': sesiones,
        'total_sesiones': total_sesiones,
        'cuestionario': cuestionario,
        'respuestas_formateadas': respuestas_formateadas,
        'ipp_labels': json.dumps(ipp_labels), # Pasamos los datos como JSON al frontend
        'ipp_data': json.dumps(ipp_data),
    })

# =========================================================================
# 🔥 NUEVA FUNCIÓN AJAX PARA ANALIZAR LA GRÁFICA CON GROQ
# No olvides agregar esta URL en tu urls.py: path('analizar-grafica-ipp/<int:paciente_id>/', views.analizar_grafica_ipp_ajax, name='analizar_grafica_ipp'),
# =========================================================================
@csrf_exempt
def analizar_grafica_ipp_ajax(request, paciente_id):
    if not request.user.is_authenticated or not hasattr(request.user, 'perfil_psicologo'):
        return JsonResponse({'status': 'error', 'message': 'No autorizado'})

    try:
        paciente = User.objects.get(id=paciente_id)
        respuestas = RespuestaFormularioOrganica.objects.filter(paciente=paciente).order_by('fecha_respuesta')

        if not respuestas.exists():
            return JsonResponse({'status': 'error', 'message': 'Aún no hay suficientes datos para generar una tendencia.'})

        # Armamos un historial de texto para que Groq lo lea
        datos_historial = []
        for i, r in enumerate(respuestas):
            ipt = round(((r.puntaje - 10) / 40) * 100)
            fecha = r.fecha_respuesta.strftime('%d/%m/%Y')
            datos_historial.append(f"Registro {i+1} ({fecha}): {ipt}% de Bienestar")

        historial_texto = "\n".join(datos_historial)

        # Prompt hiper especializado
        prompt = (
            f"Eres un analista clínico experto. Estoy revisando la gráfica del Índice de Progreso Psicológico (IPP) semanal del paciente {paciente.first_name}.\n"
            f"A continuación tienes su historial de puntuaciones (del 0% al 100%, donde más alto significa mayor bienestar):\n\n"
            f"{historial_texto}\n\n"
            "Analiza la curva de estos datos y redacta un reporte de 3 párrafos cortos y directos:\n"
            "1. Tendencia general (¿Hay mejora progresiva, estancamiento o retroceso?).\n"
            "2. Análisis de fluctuaciones (picos altos o caídas si las hay).\n"
            "3. Conclusión/Recomendación para el psicólogo antes de su próxima sesión.\n\n"
            "IMPORTANTE: Devuelve tu respuesta EXCLUSIVAMENTE en código HTML usando <p> y <b>. No uses títulos gigantes, no uses markdown (```html)."
        )


        client = Groq(api_key=os.environ.get("GROQ_API_KEY"))
        
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=800,
            temperature=0.2 # Temperatura baja para que sea clínico y no invente
        )
        
        analisis_html = response.choices[0].message.content.strip().replace('```html', '').replace('```', '')
        return JsonResponse({'status': 'success', 'analisis': analisis_html})

    except Exception as e:
        return JsonResponse({'status': 'error', 'message': str(e)})


def guardar_historial(request):
    """
    Guarda una nueva entrada en HistorialClinico.
    Si el form incluye `cita_id`, vincula la bitácora a esa Cita
    a través del OneToOneField `cita` — así Meet y Bitácora quedan
    en la misma sesión de forma permanente.
    """
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'Método no permitido'}, status=405)

    if not request.user.is_authenticated or not hasattr(request.user, 'perfil_psicologo'):
        return JsonResponse({'status': 'error', 'message': 'No autorizado'}, status=403)

    psicologo = request.user.perfil_psicologo
    paciente_id = request.POST.get('paciente_id')
    cita_id     = request.POST.get('cita_id')  # Puede venir vacío → bitácora libre

    try:
        paciente = User.objects.get(id=paciente_id)
    except User.DoesNotExist:
        return JsonResponse({'status': 'error', 'message': 'Paciente no encontrado'})

    # Resolver la cita si viene id
    cita = None
    if cita_id:
        try:
            cita = Cita.objects.get(
                id=cita_id,
                paciente=paciente,
                psicologo=psicologo,
            )
            # Protección: si ya tiene bitácora, no sobreescribir
            if hasattr(cita, 'nota_clinica') and cita.nota_clinica is not None:
                return JsonResponse({
                    'status': 'error',
                    'message': 'Esta sesión ya tiene una bitácora vinculada.'
                })
        except Cita.DoesNotExist:
            # Si el id no existe o no pertenece al contexto, lo ignoramos
            # y guardamos como bitácora libre (no bloqueamos al doctor)
            cita = None

    # Crear el historial
    historial = HistorialClinico.objects.create(
        paciente     = paciente,
        psicologo    = psicologo,
        cita         = cita,             # None si es bitácora libre
        como_llega   = request.POST.get('como_llega', '').strip(),
        notas_sesion = request.POST.get('notas_sesion', '').strip(),
        aprendizaje_paciente = request.POST.get('aprendizaje_paciente', '').strip(),
        como_se_va   = request.POST.get('como_se_va', '').strip(),
        recomendaciones = request.POST.get('recomendaciones', '').strip(),
    )

    # Adjunto (archivo escaneado o foto)
    archivo = request.FILES.get('archivo_adjunto')
    if archivo:
        historial.archivo_adjunto = archivo
        historial.save()

    return JsonResponse({'status': 'success', 'historial_id': historial.id})
# =========================================================================
# 👑 CENTRO DE COMANDO (PANEL DE SUPER ADMIN / CEO)
# =========================================================================


# ================== API PARA SCROLL INFINITO ==================
def api_pacientes_paginados(request):
    if not request.user.has_perm('es_admin'): 
        return JsonResponse({'error': 'No autorizado'}, status=403)
    
    page = int(request.GET.get('page', 1))
    per_page = int(request.GET.get('per_page', 20))
    
    # 🚀 OPTIMIZACIÓN: select_related trae al psicólogo en 1 consulta en lugar de 20
    pacientes_qs = UsuarioPerfil.objects.filter(es_psicologo=False).select_related(
        'usuario', 'psicologo_asignado__usuario'
    ).prefetch_related(
        Prefetch('usuario__citas_como_paciente', queryset=Cita.objects.exclude(estado='Cancelada'), to_attr='citas_precargadas')
    ).order_by('-id')
    
    paginator = Paginator(pacientes_qs, per_page)
    
    if page > paginator.num_pages:
        return JsonResponse({'results': [], 'has_next': False})
    
    pacientes_page = paginator.page(page)
    resultados = []
    
    for pac in pacientes_page:
        animo = calcular_animo_promedio(pac)  # ya detecta citas_precargadas y no hace query extra
        total_sesiones = len(pac.usuario.citas_precargadas) if pac.usuario else 0
        doctor_nombre = pac.psicologo_asignado.usuario.first_name if pac.psicologo_asignado else 'Pendiente'
        resultados.append({
            'nombre': pac.nombre,
            'email': pac.usuario.email if pac.usuario else '',
            'doctor': doctor_nombre,
            'sesiones': total_sesiones,
            'animo': animo['texto'],
            'icono_animo': animo['icono'],
            'color_animo': animo['color'],
        })
    
    return JsonResponse({
        'results': resultados,
        'has_next': page < paginator.num_pages,
        'current_page': page,
    })


# ================== API PARA POLLING (estadísticas + citas) ==================
# 🚀 Ya NO se calculan focos_rojos/nuevos_semana/completadas_mes/canceladas_mes/talleres_activos:
# el HTML nunca los leía (ningún elemento ni línea de JS los usaba), solo consumían consultas
# caras cada 10 segundos por el polling. Además el calendario ahora solo trae la semana en curso.
def api_stats(request):
    hoy = timezone.now().date()

    total_pacientes = UsuarioPerfil.objects.filter(es_psicologo=False).count()
    total_doctores = PerfilPsicologo.objects.filter(esta_activo=True).count()
    citas_hoy = Cita.objects.filter(fecha=hoy).exclude(estado='Cancelada').count()
    citas_totales = Cita.objects.exclude(estado='Cancelada').count()

    # 🚀 Solo la semana en curso (lunes a domingo) en vez de -30/+60 días
    inicio_semana = hoy - timezone.timedelta(days=hoy.weekday())
    fin_semana = inicio_semana + timezone.timedelta(days=6)

    citas_calendario = Cita.objects.filter(
        fecha__gte=inicio_semana, fecha__lte=fin_semana
    ).select_related('psicologo__usuario', 'paciente')

    citas_json = []
    for c in citas_calendario:
        nombre_pac = f"{c.paciente.first_name} {c.paciente.last_name}" if c.paciente.first_name else c.paciente.username
        citas_json.append({
            'title': nombre_pac,
            'start': f"{c.fecha.isoformat()}T{c.hora.strftime('%H:%M:%S')}",
            'end': f"{c.fecha.isoformat()}T{(c.hora.hour+1):02d}:{c.hora.minute:02d}:00",
            'extendedProps': {
                'psicologo': c.psicologo.usuario.first_name if c.psicologo else 'Sin asignar',
                'estado': c.estado,
                'modalidad': c.modalidad
            }
        })

    return JsonResponse({
        'total_pacientes': total_pacientes,
        'total_doctores': total_doctores,
        'citas_hoy': citas_hoy,
        'citas_totales': citas_totales,
        'citas_json': citas_json,
    })


def calcular_animo_promedio(perfil_paciente):
    valores = {'Muy mal': 1, 'Triste': 2, 'Normal': 3, 'Bien': 4, 'Excelente': 5}
    suma = 0
    count = 0
    
    # 🚀 OPTIMIZACIÓN: Si las citas ya fueron pre-cargadas (Prefetch), úsalas desde la RAM
    if hasattr(perfil_paciente.usuario, 'citas_precargadas'):
        citas = perfil_paciente.usuario.citas_precargadas
    else:
        citas = Cita.objects.filter(paciente=perfil_paciente.usuario).exclude(estado='Cancelada')
        
    for c in citas:
        if c.estado_animo in valores:
            suma += valores[c.estado_animo]
            count += 1
            
    if count == 0:
        return {'texto': 'Sin registro', 'icono': 'fas fa-minus', 'color': '#cbd5e1'}
    promedio = round(suma / count)
    texto = {1:'Muy mal',2:'Triste',3:'Normal',4:'Bien',5:'Excelente'}[promedio]
    icono = {'Muy mal':'fas fa-sad-cry','Triste':'fas fa-frown','Normal':'fas fa-meh','Bien':'fas fa-smile','Excelente':'fas fa-grin-stars'}[texto]
    color = {'Muy mal':'#ef4444','Triste':'#f97316','Normal':'#64748b','Bien':'#10b981','Excelente':'#B5992D'}[texto]
    return {'texto': texto, 'icono': icono, 'color': color}








def es_admin(user):
    return user.is_superuser

@user_passes_test(es_admin, login_url='/')
def panel_admin(request):
    hoy = timezone.now().date()

    total_pacientes = UsuarioPerfil.objects.filter(es_psicologo=False).count()
    total_doctores = PerfilPsicologo.objects.filter(esta_activo=True).count()
    citas_hoy = Cita.objects.filter(fecha=hoy).exclude(estado='Cancelada').count()
    citas_totales = Cita.objects.exclude(estado='Cancelada').count()

    # 🚀 Se eliminaron por completo "Especialistas Activos" y "Frecuencia de Ingresos":
    # esos dos bloques armaban listas de TODOS los doctores y hasta 20 pacientes con sus
    # citas precargadas en cada carga del panel. Ya no se calculan ni se envían al template.

    # 🚀 El calendario ahora solo trae la semana en curso (lunes a domingo) en vez del
    # rango de -30/+60 días, así se recorta drásticamente el volumen de citas por request.
    inicio_semana = hoy - timezone.timedelta(days=hoy.weekday())
    fin_semana = inicio_semana + timezone.timedelta(days=6)

    citas_calendario = Cita.objects.filter(
        fecha__gte=inicio_semana, fecha__lte=fin_semana
    ).select_related('psicologo__usuario', 'paciente__perfil')

    citas_json = []
    for c in citas_calendario:
        nombre_pac = f"{c.paciente.first_name} {c.paciente.last_name}" if c.paciente.first_name else c.paciente.username
        telefono_pac = c.paciente.perfil.telefono if hasattr(c.paciente, 'perfil') and c.paciente.perfil.telefono else 'Sin registrar'

        citas_json.append({
            'title': nombre_pac,
            'start': f"{c.fecha.isoformat()}T{c.hora.strftime('%H:%M:%S')}",
            'end': f"{c.fecha.isoformat()}T{(c.hora.hour+1):02d}:{c.hora.minute:02d}:00",
            'extendedProps': {
                'psicologo': c.psicologo.usuario.first_name if c.psicologo else 'Sin asignar',
                'estado': c.estado,
                'modalidad': c.modalidad,
                'enlace_meet': c.enlace_meet or '',
                'email': c.paciente.email or 'Sin registrar',
                'telefono': telefono_pac
            }
        })

    context = {
        'total_pacientes': total_pacientes,
        'total_doctores': total_doctores,
        'citas_hoy': citas_hoy,
        'citas_totales': citas_totales,
        'hoy': hoy,
        'citas_json': json.dumps(citas_json),
    }

    return render(request, 'panel_admin.html', context)



# Etiquetas y emojis por mood
MOOD_META = {
    'triste':    {'label': 'Triste 😔',    'emoji': '😔'},
    'tranquilo': {'label': 'Tranquilo 😌', 'emoji': '😌'},
    'bien':      {'label': 'Bien 🙂',      'emoji': '🙂'},
    'genial':    {'label': '¡Genial! 🤩',  'emoji': '🤩'},
}
 
 
def enviar_mood_ajax(request):
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'detail': 'Método no permitido'}, status=405)
 
    mood      = request.POST.get('mood', '').strip()
    mensaje   = request.POST.get('mensaje', '').strip()
    instagram = request.POST.get('instagram', '').strip().lstrip('@')
 
    # Fallback si el mood no es reconocido
    meta = MOOD_META.get(mood, {'label': mood.capitalize(), 'emoji': '💜'})
 
    # Contexto para el template
    context = {
        'mood_emoji':  meta['emoji'],
        'mood_label':  meta['label'],
        'mensaje':     mensaje or '(La persona no dejó un mensaje escrito.)',
        'instagram':   instagram,                                    # vacío = no compartido
        'fecha':       localtime(now()).strftime('%d %b %Y, %H:%M'),
    }
 
    # Renderizar HTML
    html_content = render_to_string('email_mood_hope.html', context)
 
    # Asunto
    subject = f'💜 Alguien compartió cómo se siente hoy: {meta["label"]}'
    if instagram:
        subject += f'  ·  IG: @{instagram}'
 
    # Texto plano de fallback
    text_content = (
        f"Estado de ánimo: {meta['label']}\n\n"
        f"Nos contó:\n{mensaje}\n\n"
        f"Instagram: {'@' + instagram if instagram else 'No compartido'}\n"
        f"Fecha: {context['fecha']}"
    )
 
    # Enviar
    try:
        email = EmailMultiAlternatives(
            subject=subject,
            body=text_content,
            from_email='Espacio HOPE <no-reply@espaciohope.com>',                          # usa DEFAULT_FROM_EMAIL del settings
            to=['contacto@espaciohope.com'],
        )
        email.attach_alternative(html_content, 'text/html')
        email.send(fail_silently=False)
    except Exception as exc:
        # Logueamos pero no exponemos el error al cliente
        import logging
        logging.getLogger(__name__).error('Error enviando mood email: %s', exc)
 
    return JsonResponse({'status': 'success'})


def admindoc_login(request):
    """
    Login exclusivo para doctores/admin.
    - GET  → muestra la página de login
    - POST → autentica y redirige según rol (NO devuelve JSON)
    """
    # Si ya está autenticado, redirigir directo
    if request.user.is_authenticated:
        if request.user.is_superuser:
            return redirect('/panel-admin/')
        elif hasattr(request.user, 'perfil_psicologo'):
            return redirect('/panel-doctor/')
        else:
            return redirect('/panel/')

    error = None

    if request.method == 'POST':
        email    = request.POST.get('login_email', '').strip()
        password = request.POST.get('login_password', '')

        if not email or not password:
            error = 'Por favor ingresa tu correo y contraseña.'
        else:
            try:
                user = User.objects.get(username__iexact=email)
            except User.DoesNotExist:
                error = 'El correo o la contraseña son incorrectos.'
            except User.MultipleObjectsReturned:
                error = 'El correo o la contraseña son incorrectos.'
            else:
                if not user.check_password(password):
                    error = 'El correo o la contraseña son incorrectos.'
                elif not user.is_active:
                    error = 'Aún no verificas tu cuenta. Revisa tu bandeja de entrada.'
                else:
                    login(request, user)
                    if user.is_superuser:
                        return redirect('/panel-admin/')
                    elif hasattr(user, 'perfil_psicologo'):
                        return redirect('/panel-doctor/')
                    else:
                        return redirect('/panel/')

    return render(request, 'admindoc.html', {'error': error})


def _extraer_json_groq(texto):
    """ Función auxiliar para limpiar la respuesta de la IA y asegurar que sea JSON válido """
    try:
        import re
        match = re.search(r'\{.*\}', texto, re.DOTALL)
        if match:
            return json.loads(match.group(0))
        return json.loads(texto)
    except:
        return {"notas_sesion": texto} 

@user_passes_test(lambda u: hasattr(u, 'perfil_psicologo'))
def procesar_archivo_ia(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'Método no permitido'}, status=405)

    archivo = request.FILES.get('archivo')
    if not archivo:
        return JsonResponse({'error': 'No se recibió ningún archivo'}, status=400)

    extension = archivo.name.rsplit('.', 1)[-1].lower()

    # 🔥 SÚPER PROMPT CLÍNICO DEDUCTIVO 🔥
    prompt_instrucciones = (
        "Eres un analizador clínico experto. Lee el documento adjunto, analiza el contexto y distribuye la información "
        "en formato JSON. Los textos no siempre tendrán los títulos exactos, debes usar tu capacidad de comprensión "
        "lectora para deducir en qué categoría va cada párrafo.\n\n"
        "Usa ESTRICTAMENTE estas 5 claves:\n"
        "- \"como_llega\": Análisis del estado inicial, puntualidad, actitud al llegar.\n"
        "- \"notas_sesion\": El desarrollo de la consulta, temas hablados, dinámicas.\n"
        "- \"aprendizaje_paciente\": La respuesta a qué se llevó de la sesión (conclusiones del paciente).\n"
        "- \"como_se_va\": Notas de alta, estado emocional al finalizar o cierre de sesión.\n"
        "- \"recomendaciones\": Tareas, sugerencias o indicaciones futuras.\n\n"
        "Si no encuentras información para alguna categoría, déjala como cadena vacía (\"\").\n"
        "IMPORTANTE: Devuelve ÚNICAMENTE el JSON válido en texto plano, sin bloques de código markdown (```json), ni explicaciones."
    )

    try:
        from groq import Groq
        client = Groq(api_key=os.environ.get("GROQ_API_KEY"))

        # ── IMAGEN ──
        if extension in ['jpg', 'jpeg', 'png', 'webp']:
            imagen_bytes  = archivo.read()
            import base64
            imagen_base64 = base64.standard_b64encode(imagen_bytes).decode('utf-8')
            mime = 'image/jpeg' if extension in ('jpg', 'jpeg') else f'image/{extension}'

            response = client.chat.completions.create(
                model="llama-3.2-90b-vision-preview", # 🔥 Modelo de visión super potente
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt_instrucciones},
                            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{imagen_base64}"}}
                        ]
                    }
                ],
                max_tokens=2000,
                temperature=0.1
            )
            texto_respuesta = response.choices[0].message.content.strip()
            datos_distribuidos = _extraer_json_groq(texto_respuesta)

        # ── PDF O WORD ──
        else:
            texto_raw = ""
            if extension == 'pdf':
                import fitz
                doc_pdf = fitz.open(stream=archivo.read(), filetype="pdf")
                for pagina in doc_pdf: texto_raw += pagina.get_text()
                doc_pdf.close()
            elif extension in ['doc', 'docx']:
                import docx
                doc_word = docx.Document(archivo)
                texto_raw = "\n".join(p.text for p in doc_word.paragraphs if p.text.strip())

            if not texto_raw.strip():
                return JsonResponse({'error': 'El documento no contiene texto extraíble.'}, status=422)

            response = client.chat.completions.create(
                model="llama-3.3-70b-versatile", # 🔥 Modelo de texto líder en razonamiento
                messages=[
                    {"role": "system", "content": prompt_instrucciones},
                    {"role": "user", "content": texto_raw[:8000]}
                ],
                max_tokens=2000,
                temperature=0.1,
            )
            datos_distribuidos = _extraer_json_groq(response.choices[0].message.content.strip())

        return JsonResponse({'status': 'success', 'data': datos_distribuidos})

    except Exception as e:
        print(f">>> ERROR IA: {e}")
        return JsonResponse({'error': f'Error al procesar: {str(e)}'}, status=500)



def _limpiar_texto_con_groq(client: Groq, texto_raw: str) -> str:
    """
    Pasa el texto extraído de PDF/Word por Groq para limpiarlo,
    quitar encabezados de página, numeraciones y ruido de formato.
    Si el texto es muy largo, lo trunca a 8000 chars antes de enviarlo.
    """
    texto_truncado = texto_raw[:8000]
 
    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {
                "role": "system",
                "content": (
                    "Eres un asistente clínico. Tu tarea es limpiar y formatear "
                    "notas de sesión psicológica extraídas de un documento. "
                    "REGLAS: "
                    "1. Elimina encabezados de página, números de página, fechas repetidas y ruido de formato. "
                    "2. Conserva TODO el contenido clínico relevante sin resumirlo ni alterarlo. "
                    "3. Corrige ortografía y puntuación mínimamente. "
                    "4. Devuelve ÚNICAMENTE el texto limpio, sin comentarios ni markdown."
                )
            },
            {
                "role": "user",
                "content": texto_truncado
            }
        ],
        max_tokens=2000,
        temperature=0.1,
    )
    return response.choices[0].message.content.strip()
 


def check_meet_notes(request, cita_id):
    if not request.user.is_authenticated:
        return JsonResponse({'status': 'error', 'message': 'No autorizado'})

    try:
        cita = Cita.objects.get(id=cita_id)
        if not cita.id_evento_google:
            return JsonResponse({'status': 'error', 'message': 'Esta cita no tiene un enlace de Meet registrado.'})

        # Conectar a Google Calendar usando tu token
# DESPUÉS
        token_json_str = os.environ.get('GOOGLE_TOKEN_JSON')
        if not token_json_str:
            return JsonResponse({'status': 'error', 'message': 'No hay conexión con Google.'})

        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        creds = Credentials.from_authorized_user_info(json.loads(token_json_str), ['https://www.googleapis.com/auth/calendar.events'])
        service = build('calendar', 'v3', credentials=creds)

        # Buscamos el evento exacto con la huella digital
        event = service.events().get(calendarId='primary', eventId=cita.id_evento_google).execute()

        # Google guarda las transcripciones como "attachments" en el calendario
        attachments = event.get('attachments', [])

        if attachments:
            archivos = []
            for a in attachments:
                archivos.append({
                    'title': a.get('title', 'Documento de Google'),
                    'url': a.get('fileUrl', '#')
                })
            return JsonResponse({'status': 'success', 'attachments': archivos})
        else:
            return JsonResponse({'status': 'pending', 'message': 'esperando notas danos un minutito :)'})

    except Exception as e:
        return JsonResponse({'status': 'error', 'message': str(e)})


def obtener_bitacora(request, historial_id):
    if not request.user.is_authenticated:
        return JsonResponse({'status': 'error'})
    try:
        h = HistorialClinico.objects.get(id=historial_id, psicologo=request.user.perfil_psicologo)
        return JsonResponse({
            'status': 'success',
            'data': {
                'como_llega': h.como_llega or '',
                'notas_sesion': h.notas_sesion or '',
                'aprendizaje_paciente': h.aprendizaje_paciente or '',
                'como_se_va': h.como_se_va or '',
                'recomendaciones': h.recomendaciones or '',
            }
        })
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': str(e)})


@csrf_exempt
def generar_sintesis_ajax(request, paciente_id):
    if not request.user.is_authenticated or not hasattr(request.user, 'perfil_psicologo'):
        return JsonResponse({'status': 'error', 'message': 'No autorizado'})

    try:
        paciente = User.objects.get(id=paciente_id)
        psicologo = request.user.perfil_psicologo

        # 1. Obtener Cuestionario Inicial
        cuestionario_texto = "El paciente no tiene cuestionario inicial registrado."
        if hasattr(paciente, 'cuestionario_inicial'):
            respuestas = paciente.cuestionario_inicial.respuestas
            # Formatear el JSON del cuestionario a texto legible
            cuestionario_texto = "\n".join([f"- {k.replace('_', ' ').capitalize()}: {v}" for k, v in respuestas.items()])

        # 2. Obtener TODAS las bitácoras en orden cronológico
        historiales = HistorialClinico.objects.filter(paciente=paciente, psicologo=psicologo).order_by('fecha_registro')
        bitacoras_texto = ""
        
        for i, h in enumerate(historiales):
            bitacoras_texto += f"\n--- SESIÓN {i+1} ({h.fecha_registro.strftime('%d/%m/%Y')}) ---\n"
            bitacoras_texto += f"Estado inicial: {h.como_llega or 'N/A'}\n"
            bitacoras_texto += f"Desarrollo de la sesión: {h.notas_sesion or 'N/A'}\n"
            bitacoras_texto += f"Aprendizaje del paciente: {h.aprendizaje_paciente or 'N/A'}\n"
            bitacoras_texto += f"Cierre y alta: {h.como_se_va or 'N/A'}\n"
            bitacoras_texto += f"Recomendaciones dadas: {h.recomendaciones or 'N/A'}\n"

        if not historiales.exists():
            return JsonResponse({'status': 'error', 'message': 'No hay bitácoras suficientes para generar un resumen.'})

        # 3. El Prompt para Groq
        prompt = (
            f"Eres un supervisor clínico experto de altísimo nivel. A continuación te presento los datos de un paciente llamado {paciente.first_name}.\n\n"
            f"CUESTIONARIO INICIAL (Contexto):\n{cuestionario_texto}\n\n"
            f"BITÁCORAS DE SESIONES (Evolución cronológica):\n{bitacoras_texto}\n\n"
            "Analiza todo cruzando la información y genera una SÍNTESIS INTEGRAL MAESTRA profunda y estructurada con los siguientes 4 puntos:\n"
            "1. Situación actual del paciente.\n"
            "2. Contexto inicial y evolución a través de las sesiones.\n"
            "3. Puntos clínicos relevantes identificados (patrones, alertas, logros).\n"
            "4. Posibles medidas o líneas de acción a considerar para futuras sesiones.\n\n"
            "INSTRUCCIONES DE FORMATO: Devuelve tu respuesta EXCLUSIVAMENTE en código HTML. "
            "Usa <h3> para los títulos de cada sección (color púrpura sugerido style='color:#5A3FA3;'), "
            "usa <ul> y <li> para listas, y <p> para párrafos regulares. "
            "No incluyas etiquetas <html> ni <body>, solo el fragmento de contenido. No incluyas backticks (```html) en tu respuesta."
        )

        # 4. Llamada a la IA
        from groq import Groq
        client = Groq(api_key=os.environ.get("GROQ_API_KEY"))
        
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=3000,
            temperature=0.2 # Temperatura baja para que sea muy analítico y no invente nada
        )
        
        sintesis_html = response.choices[0].message.content.strip()
        # Limpieza por si Groq devuelve backticks de markdown a pesar de las instrucciones
        sintesis_html = sintesis_html.replace('```html', '').replace('```', '')

        return JsonResponse({'status': 'success', 'sintesis': sintesis_html})

    except Exception as e:
        return JsonResponse({'status': 'error', 'message': str(e)})



@csrf_exempt
def solicitar_recuperacion(request):
    if request.method == 'POST':
        email = request.POST.get('email', '').strip()
        try:
            user = User.objects.get(username__iexact=email) # Recuerda que manejas el email como username
            
            # Generamos los tokens de Django
            uid = urlsafe_base64_encode(force_bytes(user.pk))
            token = default_token_generator.make_token(user)
            
            # Creamos el link único que irá en el correo
            link = request.build_absolute_uri(
                reverse('resetear_password_form', kwargs={'uidb64': uid, 'token': token})
            )
            
            # Correo HTML bonito — HOPE Design
            asunto = '🔐 Recupera tu acceso a HOPE'
            nombre = user.first_name or 'amigo/a'

            html_correo = f"""
<!DOCTYPE html>
<html lang="es">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"></head>
<body style="margin:0;padding:0;background:#f3f0fa;font-family:'Helvetica Neue',Helvetica,Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f3f0fa;padding:40px 20px;">
    <tr><td align="center">
      <table width="100%" style="max-width:520px;background:#ffffff;border-radius:24px;overflow:hidden;box-shadow:0 8px 40px rgba(90,63,163,0.12);">
        <tr><td style="background:linear-gradient(90deg,#5a3fa3 0%,#e879a0 50%,#f27c21 100%);height:6px;"></td></tr>
        <tr><td style="padding:40px 40px 0;text-align:center;">
          <div style="width:68px;height:68px;border-radius:18px;background:linear-gradient(135deg,#f3e8ff,#fce7f3);display:inline-block;line-height:68px;margin-bottom:20px;text-align:center;">
            <span style="font-size:30px;">🔐</span>
          </div>
          <h1 style="margin:0 0 8px;font-size:26px;font-weight:800;color:#1a1035;letter-spacing:-0.5px;">Recupera tu acceso</h1>
          <p style="margin:0 0 0;font-size:15px;color:#7c6fa0;line-height:1.6;">Hola <strong style="color:#5a3fa3;">{nombre}</strong>, recibimos una solicitud para restablecer la contraseña de tu cuenta en HOPE.</p>
        </td></tr>
        <tr><td style="padding:32px 40px;">
          <table width="100%" style="background:#f8f5ff;border-radius:14px;margin-bottom:28px;" cellpadding="0" cellspacing="0">
            <tr><td style="padding:18px 20px;">
              <p style="margin:0 0 4px;font-size:12px;font-weight:700;color:#5a3fa3;text-transform:uppercase;letter-spacing:1px;">¿Fuiste tú?</p>
              <p style="margin:0;font-size:14px;color:#6b7280;line-height:1.5;">Si fue así, haz clic en el botón para crear tu nueva contraseña. Si <strong>no</strong> fuiste tú, ignora este correo — tu cuenta está segura.</p>
            </td></tr>
          </table>
          <table width="100%" cellpadding="0" cellspacing="0">
            <tr><td align="center" style="padding-bottom:28px;">
              <a href="{link}" style="display:inline-block;background:#5a3fa3;color:#ffffff !important;text-decoration:none;font-size:16px;font-weight:800;padding:18px 44px;border-radius:14px;letter-spacing:0.5px;text-transform:uppercase;">
                Crear nueva contraseña &rarr;
              </a>
            </td></tr>
          </table>
          <table width="100%" cellpadding="0" cellspacing="0" style="background:#fafafa;border:1px solid #e9e5f0;border-radius:12px;">
            <tr><td style="padding:14px 16px;">
              <p style="margin:0 0 6px;font-size:11px;font-weight:700;color:#9ca3af;text-transform:uppercase;letter-spacing:0.8px;">O copia este enlace:</p>
              <p style="margin:0;font-size:12px;color:#5a3fa3;word-break:break-all;line-height:1.5;">{link}</p>
            </td></tr>
          </table>
        </td></tr>
        <tr><td style="background:#f8f5ff;padding:24px 40px;text-align:center;border-top:1px solid #ede9f8;">
          <p style="margin:0 0 4px;font-size:13px;color:#9ca3af;">⏱ Este enlace <strong>expira en 15 minutos</strong> y es de un solo uso.</p>
          <p style="margin:8px 0 0;font-size:12px;color:#b0bec5;">Con cariño, el equipo de <strong style="color:#5a3fa3;">Espacio HOPE</strong> 💜</p>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>
"""

            texto_plano = f"Hola {nombre},\n\nRestablece tu contraseña aquí:\n{link}\n\nEste enlace expira en 15 minutos.\n\nEquipo HOPE"

            email_msg = EmailMultiAlternatives(
                subject=asunto,
                body=texto_plano,
                from_email='Espacio HOPE <no-reply@espaciohope.com>',
                to=[user.email]
            )
            email_msg.attach_alternative(html_correo, "text/html")
            email_msg.send(fail_silently=False)

            return JsonResponse({'status': 'success'})
        except User.DoesNotExist:
            # Por seguridad, respondemos success aunque no exista, así los atacantes no adivinan correos.
            return JsonResponse({'status': 'success'})
    return JsonResponse({'status': 'error', 'message': 'Método no válido'})

def resetear_password_form(request, uidb64, token):
    try:
        uid = force_str(urlsafe_base64_decode(uidb64))
        user = User.objects.get(pk=uid)
    except (TypeError, ValueError, OverflowError, User.DoesNotExist):
        user = None

    # Si el token no es válido o ya caducó
    if user is not None and default_token_generator.check_token(user, token):
        if request.method == 'POST':
            nueva_pass = request.POST.get('nueva_password')
            if nueva_pass:
                user.set_password(nueva_pass)
                user.save()
                messages.success(request, 'Tu contraseña ha sido actualizada. Ya puedes iniciar sesión.')
                return redirect('inicio') # O a tu landing page de inicio
            
        return render(request, 'reset_form.html', {'uid': uidb64, 'token': token})
    else:
        return render(request, 'verificacion_resultado.html', {'exito': False, 'mensaje': 'El enlace de recuperación ha expirado o ya fue utilizado.'})



def sesion_individual(request):
    now_local = timezone.localtime(timezone.now())
    hoy = now_local.date()
    fecha_limite = hoy + timedelta(days=90)

    # 🔥 MAGIA PURA: Ahora llamamos a la función que SÍ revisa turnos, comidas y días libres
    dias_json = obtener_slots_globales(hoy, fecha_limite, "")

    dias_html = {}
    for fecha_str, horas_str in dias_json.items():
        fecha_obj = datetime.strptime(fecha_str, '%Y-%m-%d').date()
        horas_obj = [datetime.strptime(h, '%I:%M %p').time() for h in horas_str]
        dias_html[fecha_obj] = horas_obj

    context = {
        'dias_disponibles_json': dias_json,
        'dias_disponibles': dias_html,
        'mostrar_completar_perfil': request.GET.get('completar_perfil') == '1',  
        'cuestionario_json': json.dumps(CUESTIONARIO_CLINICO),
        'paypal_client_id': settings.PAYPAL_CLIENT_ID,
    }
    return render(request, 'sesion_individual.html', context)


@csrf_exempt
def registro_rapido_ajax(request):
    if request.method == 'POST':
        from django.contrib.auth import authenticate, login
        from django.middleware.csrf import get_token
        from django.urls import reverse

        email = request.POST.get('email', '').strip()
        password = request.POST.get('password', '')

        # 1. VALIDADOR DE USUARIO EXISTENTE (Lógica de tu login_usuario)
        user_match = User.objects.filter(username__iexact=email).first()
        
        if user_match:
            user = authenticate(request, username=user_match.username, password=password)
            if user is not None:
                if not user.is_active:
                    return JsonResponse({
                        'status': 'error',
                        'message': 'Aún no verificas tu cuenta. Por favor, revisa tu bandeja de entrada.'
                    })

                login(request, user)
                
                # Asignamos la ruta dependiendo del rol
                if user.is_superuser:
                    redirect_url = reverse('panel_admin')
                elif hasattr(user, 'perfil_psicologo'):
                    redirect_url = reverse('panel_doctor')
                else:
                    redirect_url = reverse('panel_generico')
                    
                return JsonResponse({
                    'status': 'login_existente', 
                    'redirect_url': redirect_url,
                    'new_token': get_token(request)
                })
            else:
                return JsonResponse({
                    'status': 'error', 
                    'message': 'El correo o la contraseña son incorrectos.'
                })

        # 2. FLUJO NORMAL: CREACIÓN DE USUARIO NUEVO
        try:
            user = User.objects.create_user(
                username=email, 
                email=email, 
                password=password,
                first_name="Paciente" 
            )
            user.is_active = True
            user.save()
            UsuarioPerfil.objects.create(usuario=user, nombre="Paciente")
            
            login(request, user)
            return JsonResponse({'status': 'success', 'new_token': get_token(request)})

        except Exception as e:
            return JsonResponse({'status': 'error', 'message': f'Error en base de datos: {str(e)}'})
        
    return JsonResponse({'status': 'error', 'message': 'Método no permitido.'})





@login_required
def guardar_expediente_completo(request):
    if request.method == 'POST':
        import json
        respuestas_raw = request.POST.get('respuestas_json', '{}')
        respuestas = json.loads(respuestas_raw)

        # 1. Actualizar el modelo User de Django
        request.user.first_name = respuestas.get('nombre', 'Paciente')
        request.user.save()

        # 2. Actualizar el UsuarioPerfil (teléfonos y nombre)
        perfil, created = UsuarioPerfil.objects.get_or_create(usuario=request.user)
        perfil.nombre = respuestas.get('nombre', 'Paciente')
        perfil.telefono = respuestas.get('telefono', '')
        perfil.telefono_emergencia = respuestas.get('telefono_emergencia', '')
        perfil.save()

        # 3. Guardar todo el cuestionario en CuestionarioRegistro (Modelo Correcto)
        CuestionarioRegistro.objects.update_or_create(
            paciente=request.user,
            defaults={
                'flujo_elegido': 'individual',
                'respuestas': respuestas
            }
        )

        return JsonResponse({'status': 'success', 'redirect_url': '/panel/'})
    return JsonResponse({'status': 'error', 'message': 'Método no permitido'}, status=400)



# =========================================================================
# 💬 API DEL CHAT P2P (PACIENTE - DOCTOR)
# =========================================================================
from .models import MensajeChat

def enviar_mensaje_chat(request):
    """Guarda un mensaje nuevo en la base de datos"""
    if request.method == 'POST' and request.user.is_authenticated:
        destinatario_id = request.POST.get('destinatario_id')
        contenido = request.POST.get('contenido', '').strip()

        if not destinatario_id or not contenido:
            return JsonResponse({'status': 'error', 'message': 'Mensaje vacío.'})

        try:
            destinatario = User.objects.get(id=destinatario_id)
            mensaje = MensajeChat.objects.create(
                remitente=request.user,
                destinatario=destinatario,
                contenido=contenido
            )
            return JsonResponse({
                'status': 'success',
                'mensaje': {
                    'id': mensaje.id,
                    'contenido': mensaje.contenido,
                    'fecha': mensaje.fecha_envio.strftime('%H:%M'),
                    'es_mio': True
                }
            })
        except User.DoesNotExist:
            return JsonResponse({'status': 'error', 'message': 'Usuario no encontrado.'})
            
    return JsonResponse({'status': 'error'})

def obtener_mensajes_chat(request, usuario_id):
    """Trae el historial de chat entre el usuario logueado y otro usuario"""
    if not request.user.is_authenticated:
        return JsonResponse({'status': 'error'})

    # 1. Marcar como leídos los mensajes que me envió la otra persona
    MensajeChat.objects.filter(
        remitente_id=usuario_id,
        destinatario=request.user,
        leido=False
    ).update(leido=True)

    # 2. Buscar toda la conversación entre ambos
    mensajes_db = MensajeChat.objects.filter(
        Q(remitente=request.user, destinatario_id=usuario_id) |
        Q(remitente_id=usuario_id, destinatario=request.user)
    ).order_by('fecha_envio') # Cronológico: Viejos arriba, nuevos abajo

    data = []
    for m in mensajes_db:
        data.append({
            'id': m.id,
            'contenido': m.contenido,
            'fecha': m.fecha_envio.strftime('%H:%M'),
            'es_mio': m.remitente == request.user
        })

    return JsonResponse({'status': 'success', 'mensajes': data})

def obtener_contactos_chat(request):
    """Carga la lista de pacientes en el panel izquierdo del Doctor al estilo WhatsApp"""
    if not request.user.is_authenticated or not hasattr(request.user, 'perfil_psicologo'):
        return JsonResponse({'status': 'error', 'message': 'No autorizado'})

    psicologo = request.user.perfil_psicologo
    
    # Traemos a todos los pacientes asignados a este doctor
    pacientes = User.objects.filter(perfil__psicologo_asignado=psicologo)

    contactos = []
    for p in pacientes:
        # ¿Cuántos mensajes sin leer me mandó este paciente?
        no_leidos = MensajeChat.objects.filter(remitente=p, destinatario=request.user, leido=False).count()
        
        # ¿Cuál fue el último mensaje que nos mandamos para ponerlo como previsualización?
        ultimo_msg = MensajeChat.objects.filter(
            Q(remitente=request.user, destinatario=p) |
            Q(remitente=p, destinatario=request.user)
        ).order_by('-fecha_envio').first()

        contactos.append({
            'id': p.id,
            'nombre': f"{p.first_name} {p.last_name}".strip() or p.username,
            'inicial': p.first_name[0].upper() if p.first_name else 'P',
            'no_leidos': no_leidos,
            'ultimo_mensaje': ultimo_msg.contenido if ultimo_msg else 'Inicia la conversación',
            'fecha_orden': ultimo_msg.fecha_envio.isoformat() if ultimo_msg else '1970-01-01T00:00:00',
            'hora_ultimo': ultimo_msg.fecha_envio.strftime('%H:%M') if ultimo_msg else ''
        })

    # Ordenar contactos como en WhatsApp: El que mandó mensaje más reciente va hasta arriba
    contactos.sort(key=lambda x: x['fecha_orden'], reverse=True)

    return JsonResponse({'status': 'success', 'contactos': contactos})




# 📖 NUEVA VISTA: LECTURA DEL ARTÍCULO COMPLETO
def detalle_prensa(request, slug):
    articulo = get_object_or_404(ArticuloPrensa, slug=slug, publicado=True)
    return render(request, 'detalle_prensa.html', {'articulo': articulo}) 


@csrf_exempt
@transaction.atomic
def iniciar_pago_clip(request):
    if request.method == 'POST':
        try:
            tipo_servicio = request.POST.get('tipo_servicio', 'individual')
            
            # Si es un donativo, saltamos la creación de la cita y respetamos
            # el monto libre que decide la persona que dona.
            if tipo_servicio == 'donativo':
                monto = request.POST.get('monto')
                cita_id = "DONATIVO"
            else:
                if tipo_servicio not in TIPOS_SESION_VALIDOS:
                    tipo_servicio = 'individual'

                # 1. ES UNA CITA: Capturamos datos y pre-apartamos el lugar
                fecha_str = request.POST.get('fecha')
                hora_str = request.POST.get('hora')
                animo = request.POST.get('animo', 'No especificó')
                modalidad = request.POST.get('modalidad', 'En línea')

                # 🔥 NUNCA confiamos en el "monto" que manda el navegador: se
                # recalcula aquí con la misma función usada en el resumen.
                integrantes_familia = None
                if tipo_servicio == 'familiar':
                    precio_info = calcular_precio_sesion(tipo_servicio, request.POST.get('integrantes_familia'))
                    integrantes_familia = precio_info['integrantes_familia']
                else:
                    precio_info = calcular_precio_sesion(tipo_servicio)
                monto = precio_info['total']

                fecha_obj = datetime.strptime(fecha_str, '%Y-%m-%d').date()
                hora_obj = datetime.strptime(hora_str, '%H:%M').time()

                # Guardamos la cita en estado 'Pendiente' usando tus campos originales
                cita = Cita.objects.create(
                    paciente=request.user if request.user.is_authenticated else None,
                    fecha=fecha_obj,
                    hora=hora_obj,
                    estado='Pendiente',
                    tipo_sesion=tipo_servicio,
                    integrantes_familia=integrantes_familia,
                    estado_animo=animo,
                    modalidad=modalidad,
                    motivo='Primera Sesión' if not getattr(request.user.perfil, 'psicologo_asignado', None) else 'Sesión de Seguimiento'
                )
                cita_id = cita.id

            # 2. Configuración de Clip para Producción
            api_key = os.environ.get("CLIP_API_KEY_PROD")
            clave_secreta = os.environ.get("CLIP_CLAVE_SECRETA_PROD")
            
            credenciales_puras = f"{api_key}:{clave_secreta}"
            base64_token = base64.b64encode(credenciales_puras.encode('utf-8')).decode('utf-8')
            
            url = "https://api.payclip.com/v2/checkout"
            dominio = "https://espaciohope.com" # <--- Pon tu dominio real

            payload = json.dumps({
                "amount": float(monto),
                "currency": "MXN",
                "purchase_description": f"HOPE - {tipo_servicio.capitalize()}",
                "redirection_url": {
                    "success": f"{dominio}/pago-exitoso/{cita_id}/",
                    "error": f"{dominio}/pago-cancelado/{cita_id}/",
                    "default": f"{dominio}/panel/"
                },
                "custom_transaction_id": f"HOPE_{cita_id}_{timezone.now().strftime('%M%S')}"
            })

            headers = {
                'accept': 'application/vnd.clip.v2+json', 
                'content-type': 'application/json',
                'Authorization': f"Basic {base64_token}"
            }

            # 3. Lanzamos petición
            response = requests.post(url, headers=headers, data=payload)
            clip_data = response.json()

            if response.status_code == 200 and 'payment_request_url' in clip_data:
                return JsonResponse({'status': 'success', 'payment_url': clip_data['payment_request_url']})
            else:
                if tipo_servicio != 'donativo':
                    cita.delete() # Borramos la basura si Clip falla
                return JsonResponse({'status': 'error', 'message': 'Clip no generó el link.'})

        except Exception as e:
            return JsonResponse({'status': 'error', 'message': str(e)})
            
    return JsonResponse({'status': 'error', 'message': 'Método no permitido'})


@transaction.atomic
def pago_exitoso_clip(request, cita_id):
    # Caso 1: Fue un donativo
    if cita_id == "DONATIVO":
        messages.success(request, "¡Gracias por tu donativo! Eres un puente hacia la esperanza 🤍")
        return redirect('inicio')

    # Caso 2: Fue una Cita (Copiamos TU lógica original de PayPal)
    try:
        cita = Cita.objects.get(id=cita_id)
        
        if cita.estado == 'Pendiente de Pago':
            paciente = cita.paciente
            perfil = paciente.perfil
            psicologo = perfil.psicologo_asignado

            # --- ASIGNACIÓN DE PSICÓLOGO ---
            if not psicologo:
                preferencia = ""
                try:
                    cuestionario = paciente.cuestionario_inicial
                    preferencia = cuestionario.respuestas.get('preferencia_terapeuta', '')
                except:
                    pass

                doctores_con_slot_valido = []
                for psicologo_temp in PerfilPsicologo.objects.filter(esta_activo=True):
                    slots_del_dia = obtener_slots_psicologo_para_dia(psicologo_temp, cita.fecha)
                    if cita.hora.strftime('%I:%M %p') in slots_del_dia:
                        doctores_con_slot_valido.append(psicologo_temp.id)

                psicologos_libres = PerfilPsicologo.objects.filter(
                    id__in=doctores_con_slot_valido
                ).select_for_update().annotate(
                    carga_historica=Count('citas_agendadas', filter=~Q(citas_agendadas__estado='Cancelada'))
                )

                if not psicologos_libres.exists():
                    cita.delete()
                    messages.error(request, "Tu pago fue exitoso, pero el horario se ocupó. Contáctanos por WhatsApp para reagendar.")
                    return redirect('panel_generico')

                if 'Mujer' in preferencia:
                    psicologo = psicologos_libres.filter(genero='Mujer').order_by('carga_historica', '?').first()
                elif 'Hombre' in preferencia:
                    psicologo = psicologos_libres.filter(genero='Hombre').order_by('carga_historica', '?').first()

                if not psicologo:
                    psicologo = psicologos_libres.order_by('carga_historica', '?').first()

                perfil.psicologo_asignado = psicologo
                perfil.save()
            else:
                if Cita.objects.filter(psicologo=psicologo, fecha=cita.fecha, hora=cita.hora, estado='Confirmada').exists():
                    cita.delete()
                    messages.error(request, "Tu pago fue exitoso, pero tu terapeuta ya agendó a alguien en ese minuto. Contáctanos para reagendar.")
                    return redirect('panel_generico')

            # --- GOOGLE MEET ---
            link_final = None
            id_google = None
            if cita.modalidad == 'En línea':
                datos_meet = generar_link_meet(
                    fecha_obj=cita.fecha,
                    hora_obj=cita.hora,
                    paciente_nombre=paciente.first_name,
                    psicologo_nombre=psicologo.usuario.first_name,
                    paciente_email=paciente.email,             
                    psicologo_email=psicologo.usuario.email        
                )
                if datos_meet:
                    link_final = datos_meet['link']
                    id_google = datos_meet['id_evento']

            # --- GUARDADO FINAL ---
            cita.psicologo = psicologo
            cita.estado = 'Confirmada'
            cita.enlace_meet = link_final
            cita.id_evento_google = id_google
            cita.save()

            # --- CORREO ---
            asunto = 'Confirmación de tu sesión en HOPE'
            link_correo = link_final if link_final else "Cita Presencial (Revisa tu panel para ver la dirección)"
            contexto = {
                'nombre': paciente.first_name,
                'psicologo_nombre': psicologo.usuario.first_name,
                'fecha': cita.fecha.strftime('%d/%m/%Y'),
                'hora': cita.hora.strftime('%H:%M'),
                'link_meet': link_correo
            }
            mensaje_html = render_to_string('correo_cita.html', contexto)
            mensaje_plano = strip_tags(mensaje_html)
            send_mail(asunto, mensaje_plano, 'Espacio HOPE <no-reply@espaciohope.com>', [paciente.email], html_message=mensaje_html, fail_silently=True)

            messages.success(request, "¡Todo listo! Tu pago fue procesado con éxito y tu sesión ha sido agendada en tu panel.")
        
        return redirect('panel_generico')

    except Cita.DoesNotExist:
        messages.error(request, "Hubo un problema verificando tu cita, por favor contacta a soporte.")
        return redirect('panel_generico')


def pago_cancelado_clip(request, cita_id):
    if cita_id != "DONATIVO":
        try:
            cita = Cita.objects.get(id=cita_id)
            if cita.estado == 'Pendiente':
                cita.delete() # Liberamos el horario para que otro lo ocupe
        except Cita.DoesNotExist:
            pass
        messages.error(request, "El pago fue cancelado o la tarjeta fue rechazada. Puedes intentar agendar nuevamente.")
        
    return redirect('panel_generico')



def talleres_view(request):
    # Aquí podrías pasar variables desde la base de datos si después los haces dinámicos
     return render(request, 'talleres.html')

def procesar_registro_taller(request):
    if request.method == 'POST':
        nombre = request.POST.get('nombre')
        telefono = request.POST.get('telefono')
        correo = request.POST.get('correo')
        taller = request.POST.get('taller_seleccionado')

        # 1. Buscamos si este correo YA se registró en algún lado
        registro_previo = RegistroTallerPublico.objects.filter(correo=correo).first()

        if registro_previo:
            # 🔥 Si ya existe, le mandamos el mensaje exacto que pediste
            return JsonResponse({
                'status': 'error',
                'message': f'¡Ups! Ya estás registrado en el taller "{registro_previo.taller_seleccionado}". Solo puedes asistir a un taller para dar oportunidad a los demás.'
            })

        # 2. Si no tiene registros previos, lo guardamos felizmente
        RegistroTallerPublico.objects.create(
            nombre=nombre,
            telefono=telefono,
            correo=correo,
            taller_seleccionado=taller
        )

        return JsonResponse({'status': 'success'})
        
    return JsonResponse({'status': 'error', 'message': 'Método no permitido'})




# =========================================================================
# 👑 ADMIN: AGENDAR CITA A NOMBRE DE CUALQUIER PACIENTE
# =========================================================================
# Diferencia clave frente al flujo normal (agendar_cita / guardar_cita_ajax):
# aquí el "paciente" NO es request.user, sino el paciente_id que el admin
# eligió en el buscador. Todo lo demás -verificación real de horario,
# asignación automática de psicólogo, generación de Google Meet, envío de
# correo- es exactamente la misma lógica ya probada, así no se desincroniza
# nunca del resto del sistema. Y no se cobra nada, igual que ya pasaba
# antes en guardar_cita_ajax (el cobro solo vive en iniciar_pago_clip,
# que aquí ni se toca).

@user_passes_test(es_admin, login_url='/')
def panel_admin_agendar_cita(request):
    """Pantalla para que el admin busque un paciente y le agende la cita."""
    return render(request, 'admin_agendar_cita.html', {
        'precios_config_json': json.dumps({
            'base': PRECIO_BASE_SESION,
            'comision_pct': COMISION_PORCENTAJE_SESION,
            'incremento_integrante_familiar': INCREMENTO_POR_INTEGRANTE_FAMILIAR,
            'min_integrantes_familiar': MIN_INTEGRANTES_FAMILIAR,
        }),
    })


@user_passes_test(es_admin, login_url='/')
def admin_buscar_pacientes_ajax(request):
    """
    Autocompletar de pacientes para el buscador del admin.
    Busca por nombre, correo o teléfono (mínimo 2 caracteres).
    """
    q = request.GET.get('q', '').strip()
    if len(q) < 2:
        return JsonResponse({'status': 'success', 'resultados': []})

    pacientes = UsuarioPerfil.objects.filter(
        es_psicologo=False
    ).filter(
        Q(nombre__icontains=q) | Q(usuario__email__icontains=q) | Q(telefono__icontains=q)
    ).select_related('usuario', 'psicologo_asignado__usuario')[:10]

    resultados = [{
        'id': p.usuario.id,
        'nombre': p.nombre or (p.usuario.first_name if p.usuario else 'Sin nombre'),
        'email': p.usuario.email if p.usuario else '',
        'psicologo_asignado': p.psicologo_asignado.usuario.first_name if p.psicologo_asignado else None,
    } for p in pacientes if p.usuario]

    return JsonResponse({'status': 'success', 'resultados': resultados})


@user_passes_test(es_admin, login_url='/')
def admin_disponibilidad_ajax(request):
    """
    Igual que obtener_disponibilidad_por_tipo_ajax, pero para cuando el
    ADMIN consulta la disponibilidad del paciente que él eligió (en vez de
    usar request.user). Usa las mismas funciones de slots de siempre.
    """
    paciente_id = request.GET.get('paciente_id')
    tipo_sesion = request.GET.get('tipo_sesion', 'individual')
    if tipo_sesion not in TIPOS_SESION_VALIDOS:
        tipo_sesion = 'individual'

    if not paciente_id:
        return JsonResponse({'status': 'error', 'message': 'Selecciona primero un paciente.'}, status=400)

    try:
        paciente = User.objects.select_related('perfil__psicologo_asignado__usuario').get(id=paciente_id)
        perfil_paciente = paciente.perfil
    except Exception:
        return JsonResponse({'status': 'error', 'message': 'Paciente no encontrado o sin perfil.'}, status=404)

    preferencia = ""
    ultimo_cuestionario = CuestionarioRegistro.objects.filter(paciente=paciente).last()
    if ultimo_cuestionario and ultimo_cuestionario.respuestas:
        respuestas = ultimo_cuestionario.respuestas
        if isinstance(respuestas, str):
            try:
                respuestas = json.loads(respuestas)
            except Exception:
                respuestas = {}
        preferencia = respuestas.get("preferencia_terapeuta", "")

    hoy = timezone.localtime(timezone.now()).date()
    fecha_limite = hoy + timedelta(days=90)
    psicologo_asignado = perfil_paciente.psicologo_asignado

    if psicologo_asignado:
        dias_json = obtener_slots_psicologo(psicologo_asignado, hoy, fecha_limite, tipo_sesion=tipo_sesion)
        if not dias_json:
            return JsonResponse({
                'status': 'success',
                'dias': {},
                'psicologo_asignado': psicologo_asignado.usuario.first_name,
                'aviso': 'El terapeuta asignado a este paciente no atiende esta modalidad todavía.',
            })
    else:
        dias_json = obtener_slots_globales(hoy, fecha_limite, preferencia, tipo_sesion=tipo_sesion)

    return JsonResponse({
        'status': 'success',
        'dias': dias_json,
        'psicologo_asignado': psicologo_asignado.usuario.first_name if psicologo_asignado else None,
    })


@user_passes_test(es_admin, login_url='/')
@transaction.atomic
def admin_guardar_cita_ajax(request):
    """
    Versión admin de guardar_cita_ajax: crea la Cita para el paciente_id que
    el admin eligió (no para request.user). Mantiene íntegra la lógica de
    asignación automática de psicólogo, verificación anti-empalme y Meet.
    """
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'Método no permitido.'}, status=405)

    paciente_id = request.POST.get('paciente_id')
    if not paciente_id:
        return JsonResponse({'status': 'error', 'message': 'Selecciona un paciente antes de confirmar.'})

    try:
        paciente_user = User.objects.get(id=paciente_id)
        perfil = paciente_user.perfil
    except Exception:
        return JsonResponse({'status': 'error', 'message': 'Paciente no encontrado o sin perfil.'})

    fecha_str = request.POST.get('fecha')
    hora_str = request.POST.get('hora')
    animo = request.POST.get('animo', 'No especificó')
    modalidad_str = request.POST.get('modalidad', 'En línea')
    tipo_sesion_str = request.POST.get('tipo_servicio', 'individual')

    if tipo_sesion_str not in TIPOS_SESION_VALIDOS:
        tipo_sesion_str = 'individual'

    integrantes_familia = None
    if tipo_sesion_str == 'familiar':
        precio_info = calcular_precio_sesion(tipo_sesion_str, request.POST.get('integrantes_familia'))
        integrantes_familia = precio_info['integrantes_familia']

    campo_capacidad = CAPACIDAD_POR_TIPO_SESION[tipo_sesion_str]

    try:
        fecha_obj = datetime.strptime(fecha_str, '%Y-%m-%d').date()
        hora_obj = datetime.strptime(hora_str, '%H:%M').time()
        psicologo = perfil.psicologo_asignado

        if psicologo and not getattr(psicologo, campo_capacidad, False):
            return JsonResponse({'status': 'error', 'message': 'El terapeuta asignado a este paciente no atiende esta modalidad.'})

        # Asignación automática (misma lógica que guardar_cita_ajax)
        if not psicologo:
            preferencia = ""
            try:
                cuestionario = paciente_user.cuestionario_inicial
                preferencia = cuestionario.respuestas.get('preferencia_terapeuta', '')
            except Exception:
                pass

            esquemas_del_dia = EsquemaHorarioPsicologo.objects.filter(
                activo=True, fecha_inicio__lte=fecha_obj, fecha_fin__gte=fecha_obj
            )
            psicologos_candidatos = PerfilPsicologo.objects.filter(
                esta_activo=True, **{campo_capacidad: True}
            ).prefetch_related(
                Prefetch('esquemas_horarios', queryset=esquemas_del_dia, to_attr='_esquemas_rango')
            )

            doctores_con_slot_valido = []
            for psicologo_temp in psicologos_candidatos:
                slots_del_dia = obtener_slots_psicologo_para_dia(psicologo_temp, fecha_obj, tipo_sesion=tipo_sesion_str)
                if hora_obj.strftime('%I:%M %p') in slots_del_dia:
                    doctores_con_slot_valido.append(psicologo_temp.id)

            psicologos_libres = PerfilPsicologo.objects.filter(
                id__in=doctores_con_slot_valido
            ).select_for_update().annotate(
                carga_historica=Count('citas_agendadas', filter=~Q(citas_agendadas__estado='Cancelada'))
            )

            if not psicologos_libres.exists():
                return JsonResponse({'status': 'error', 'message': 'No hay especialistas disponibles para ese horario y modalidad.'})

            if 'Mujer' in preferencia:
                psicologo = psicologos_libres.filter(genero='Mujer').order_by('carga_historica', '?').first()
            elif 'Hombre' in preferencia:
                psicologo = psicologos_libres.filter(genero='Hombre').order_by('carga_historica', '?').first()

            if not psicologo:
                psicologo = psicologos_libres.order_by('carga_historica', '?').first()

            perfil.psicologo_asignado = psicologo
            perfil.save()

            if Cita.objects.filter(psicologo=psicologo, fecha=fecha_obj, hora=hora_obj, estado='Confirmada').exists():
                return JsonResponse({'status': 'error', 'message': 'Ese horario acaba de ser ocupado. Elige otro.'})
        else:
            if Cita.objects.filter(psicologo=psicologo, fecha=fecha_obj, hora=hora_obj, estado='Confirmada').exists():
                return JsonResponse({'status': 'error', 'message': 'El terapeuta ya tiene una cita en ese horario. Elige otro.'})

        # Google Meet (misma función que ya usa todo el sistema)
        link_final = None
        id_google = None
        if modalidad_str == 'En línea':
            datos_meet = generar_link_meet(
                fecha_obj=fecha_obj,
                hora_obj=hora_obj,
                paciente_nombre=paciente_user.first_name,
                psicologo_nombre=psicologo.usuario.first_name,
                paciente_email=paciente_user.email,
                psicologo_email=psicologo.usuario.email
            )
            if datos_meet:
                link_final = datos_meet['link']
                id_google = datos_meet['id_evento']

        Cita.objects.create(
            paciente=paciente_user,
            psicologo=psicologo,
            fecha=fecha_obj,
            hora=hora_obj,
            estado_animo=animo,
            modalidad=modalidad_str,
            tipo_sesion=tipo_sesion_str,
            integrantes_familia=integrantes_familia,
            motivo='Agendada por administración',
            estado='Confirmada',
            enlace_meet=link_final,
            id_evento_google=id_google
        )

        # Mismo correo de confirmación que ya usa el flujo normal
        asunto = 'Confirmación de tu sesión en HOPE'
        link_correo = link_final if link_final else "Cita Presencial (Revisa tu panel para ver la dirección)"
        contexto = {
            'nombre': paciente_user.first_name,
            'psicologo_nombre': psicologo.usuario.first_name,
            'fecha': fecha_obj.strftime('%d/%m/%Y'),
            'hora': hora_obj.strftime('%H:%M'),
            'link_meet': link_correo
        }
        mensaje_html = render_to_string('correo_cita.html', contexto)
        mensaje_plano = strip_tags(mensaje_html)
        send_mail(asunto, mensaje_plano, 'Espacio HOPE <no-reply@espaciohope.com>', [paciente_user.email], html_message=mensaje_html, fail_silently=True)

        return JsonResponse({'status': 'success'})
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': str(e)})


# =========================================================================
# 🇻🇪 MINI FORMULARIO DE CONTACTO — COMUNIDAD VENEZOLANA
# =========================================================================
# Formulario público (no requiere cuenta, no toca ningún modelo nuevo).
# Igual que ya hace enviar_mood_ajax con el buzón de ánimo: solo notifica
# por correo al equipo de HOPE con los datos de contacto. Al enviarse,
# regresa a la página de inicio con un mensaje de confirmación.
def formulario_venezuela(request):
    if request.method == 'POST':
        correo = request.POST.get('correo', '').strip()
        nombre = request.POST.get('nombre', '').strip()
        celular = request.POST.get('celular', '').strip()
        lugar_vivienda = request.POST.get('lugar_vivienda', '').strip()
        horario_preferencia = request.POST.get('horario_preferencia', '').strip()

        if not correo or not nombre or not celular:
            messages.error(request, 'Por favor completa al menos tu nombre, correo y celular.')
            return render(request, 'formulario_venezuela.html')

        # 1. Guardar en BD
        ContactoVenezuela.objects.create(
            nombre=nombre,
            correo=correo,
            celular=celular,
            lugar_vivienda=lugar_vivienda,
            horario_preferencia=horario_preferencia
        )

        # 2. Enviar correo
        fecha_str = localtime(now()).strftime('%d %b %Y, %H:%M')
        texto_plano = (
            "Nuevo contacto desde el formulario para la comunidad venezolana:\n\n"
            f"Nombre: {nombre}\n"
            f"Correo: {correo}\n"
            f"Celular: {celular}\n"
            f"Lugar de vivienda en Venezuela: {lugar_vivienda or 'No especificado'}\n"
            f"Horario de preferencia: {horario_preferencia or 'No especificado'}\n"
            f"Fecha: {fecha_str}"
        )

        try:
            send_mail(
                subject=f'🇻🇪 Nuevo contacto: {nombre}',
                message=texto_plano,
                from_email='Espacio HOPE <no-reply@espaciohope.com>',
                recipient_list=['contacto@espaciohope.com'],
                fail_silently=True,
            )
        except Exception as exc:
            logging.getLogger(__name__).error('Error enviando formulario Venezuela: %s', exc)

        # 3. ¡LA MAGIA! Retornamos el mismo HTML pero le pasamos exito=True
        return render(request, 'formulario_venezuela.html', {'exito': True})

    return render(request, 'formulario_venezuela.html')



# =========================================================================
# 🇻🇪 PÁGINA DE DONACIONES — COMUNIDAD VENEZOLANA
# =========================================================================
# Vista dedicada (antes vivía solo como modal en inicio.html). Reutiliza la
# MISMA lógica de cobro que ya existe: PayPal Smart Buttons + Clip, ambos
# apuntando al mismo endpoint iniciar_pago_clip con tipo_servicio='donativo',
# así que no se toca nada del flujo de pago ya probado.
def donaciones_venezuela(request):
    context = {
        'paypal_client_id': settings.PAYPAL_CLIENT_ID,
    }
    return render(request, 'donaciones_venezuela.html', context)



def _limpiar_numero_whatsapp(numero, codigo_pais_default='52'):
    """Deja solo dígitos y agrega código de país si hace falta (para wa.me)."""
    if not numero:
        return None
    limpio = re.sub(r'\D', '', numero)  # se queda solo con los dígitos
    if not limpio:
        return None
    if len(limpio) == 10:  # número local mexicano sin lada de país
        limpio = codigo_pais_default + limpio
    return limpio

# =========================================================================
# 📋 FORMULARIO ORGÁNICO PREVIO A LA SESIÓN (bloquea el acceso a Meet)
# =========================================================================
# Preguntas del formulario breve (≈2 min). Cada opción tiene un valor de
# puntaje; el total se guarda en RespuestaFormularioOrganica.puntaje.
# Si en el futuro se agregan/quitan preguntas, esta es la ÚNICA fuente de
# verdad tanto para renderizar el form como para validar/calcular el puntaje
# en el servidor (nunca confiamos en un puntaje mandado desde el navegador).
# =========================================================================
# 📋 ÍNDICE DE PROGRESO PSICOLÓGICO (IPP) - cuestionario semanal previo a Meet
# =========================================================================
# Mismos 12 reactivos cada semana (agrupados en 4 categorías: Bienestar
# emocional, Afrontamiento, Aplicación de herramientas y Esperanza y
# autoeficacia), escala Likert 1-5. Fuente única de verdad para render +
# cálculo del puntaje.



ESCALA_IPP = [
    {'valor': 'nunca', 'texto': 'Nunca', 'puntos': 1},
    {'valor': 'casi_nunca', 'texto': 'Casi nunca', 'puntos': 2},
    {'valor': 'algunas_veces', 'texto': 'Algunas veces', 'puntos': 3},
    {'valor': 'casi_siempre', 'texto': 'Casi siempre', 'puntos': 4},
    {'valor': 'siempre', 'texto': 'Siempre', 'puntos': 5},
]

FORMULARIO_ORGANICO_PREGUNTAS = [
    # --- Bienestar emocional ---
    {'id': 'ipp_1', 'categoria': 'Bienestar emocional',
     'texto': 'Durante las últimas dos semanas, he sentido que mi vida tiene un propósito o dirección'},
    {'id': 'ipp_2', 'categoria': 'Bienestar emocional',
     'texto': 'Durante las últimas dos semanas, me he sentido capaz de afrontar los desafíos que se presentan'},
    {'id': 'ipp_3', 'categoria': 'Bienestar emocional',
     'texto': 'Durante las últimas dos semanas, me he sentido de buen ánimo'},

    # --- Afrontamiento ---
    {'id': 'ipp_4', 'categoria': 'Afrontamiento',
     'texto': 'Cuando enfrento una situación que me genera malestar, soy capaz de identificar estrategias que me ayudan a manejarla de manera saludable'},
    {'id': 'ipp_5', 'categoria': 'Afrontamiento',
     'texto': 'En comparación con semanas anteriores, me resulta más fácil regular mis emociones cuando atravieso una situación difícil'},
    {'id': 'ipp_6', 'categoria': 'Afrontamiento',
     'texto': 'Ante los desafíos que enfrenté, siento que cuento con más herramientas personales para responder de manera efectiva que antes'},

    # --- Aplicación de herramientas ---
    {'id': 'ipp_7', 'categoria': 'Aplicación de herramientas',
     'texto': 'Desde mi última sesión, he puesto en práctica al menos una de las herramientas o estrategias trabajadas durante mi proceso psicológico'},
    {'id': 'ipp_8', 'categoria': 'Aplicación de herramientas',
     'texto': 'Las herramientas que he utilizado me han ayudado a manejar mejor las situaciones que me generan malestar'},
    {'id': 'ipp_9', 'categoria': 'Aplicación de herramientas',
     'texto': 'Cuando enfrento una situación difícil, recuerdo y aplico de manera intencional las estrategias aprendidas en terapia'},

    # --- Esperanza y autoeficacia ---
    {'id': 'ipp_10', 'categoria': 'Esperanza y autoeficacia',
     'texto': 'Siento que existen diferentes maneras de avanzar hacia las metas que son importantes para mí, incluso cuando encuentro obstáculos'},
    {'id': 'ipp_11', 'categoria': 'Esperanza y autoeficacia',
     'texto': 'Confío en mi capacidad para afrontar los desafíos que puedan presentarse en mi vida'},
    {'id': 'ipp_12', 'categoria': 'Esperanza y autoeficacia',
     'texto': 'Me siento con la motivación y la confianza necesarias para seguir trabajando en mi bienestar emocional'},
]

# Cada pregunta usa la MISMA escala (así lo indica el documento del IPP)
_OPCIONES_POR_PREGUNTA = {
    p['id']: {o['valor']: o['puntos'] for o in ESCALA_IPP}
    for p in FORMULARIO_ORGANICO_PREGUNTAS
}

IPP_PUNTAJE_MIN = 12   # 12 reactivos x mínimo 1 punto
IPP_PUNTAJE_MAX = 60   # 12 reactivos x máximo 5 puntos


def _calcular_puntaje_formulario_organico(respuestas_dict):
    """Suma bruta (10-50), recalculada SIEMPRE en el servidor (nunca se
    confía en un puntaje que pudiera venir del navegador)."""
    total = 0
    for pregunta_id, opciones_validas in _OPCIONES_POR_PREGUNTA.items():
        valor_elegido = respuestas_dict.get(pregunta_id)
        total += opciones_validas.get(valor_elegido, 0)
    return total


def calcular_ipt(puntaje_bruto):
    """IPT = ((Puntaje - min)/(max - min)) * 100, tal como indica el IPP."""
    return round(
        ((puntaje_bruto - IPP_PUNTAJE_MIN) / (IPP_PUNTAJE_MAX - IPP_PUNTAJE_MIN)) * 100
    )


def _mensaje_tierno_progreso(ipt_actual, ipt_anterior):
    """Mensaje siempre cálido y amoroso, nunca punitivo, ni cuando baja el índice."""
    if ipt_anterior is None:
        return (
            "🌱 ¡Este es tu primer registro de progreso! A partir de hoy iremos "
            "celebrando juntos cada pasito que des en tu proceso. 💜"
        )

    diferencia = ipt_actual - ipt_anterior

    if diferencia > 0:
        return (
            f"🌟 ¡Qué bonito avance! Subiste {diferencia} puntos respecto a la semana "
            "pasada. Sigue practicando lo que has aprendido, se nota tu esfuerzo. 💜"
        )
    elif diferencia == 0:
        return (
            "🌷 Te mantuviste estable esta semana, y eso también es un logro. "
            "Sostenerse también es avanzar. Sigamos construyendo juntos."
        )
    else:
        return (
            "💜 Esta semana se sintió un poco más pesada, y está bien sentirlo así. "
            "No hay retrocesos, solo procesos. Aquí seguimos contigo, paso a paso."
        )

def _enviar_correo_ipp_async(psicologo_email, paciente_nombre, ipt_actual, ipt_anterior, respuestas_legibles):
    """
    Función que se ejecuta en segundo plano para enviar el resultado
    del cuestionario al psicólogo sin bloquear la redirección del paciente.
    """
    asunto = f'Resultados del Formulario Semanal - {paciente_nombre}'
    contexto = {
        'paciente_nombre': paciente_nombre,
        'ipt_actual': ipt_actual,
        'ipt_anterior': ipt_anterior,
        'respuestas': respuestas_legibles,
    }
    
    try:
        mensaje_html = render_to_string('correo_resultado_ipp.html', contexto)
        mensaje_plano = strip_tags(mensaje_html)
        
        email = EmailMultiAlternatives(
            subject=asunto,
            body=mensaje_plano,
            from_email='Espacio HOPE <no-reply@espaciohope.com>',
            to=[psicologo_email]
        )
        email.attach_alternative(mensaje_html, "text/html")
        email.send(fail_silently=True)
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Error enviando correo IPP al doctor: {e}")

@login_required
def formulario_previo_meet(request, cita_id):

    cita = get_object_or_404(
        Cita.objects.select_related('psicologo__usuario'),
        id=cita_id, paciente=request.user, estado='Confirmada'
    )

    if not cita.enlace_meet:
        messages.error(request, 'Aún no se ha generado el enlace de tu sesión. Contáctanos por WhatsApp.')
        return redirect('panel_generico')

    # Si ya contestó el formulario para ESTA cita, saltamos directo a Meet.
    ya_respondido = RespuestaFormularioOrganica.objects.filter(paciente=request.user, cita=cita).exists()
    if ya_respondido:
        return redirect(cita.enlace_meet)

    if request.method == 'POST':
        respuestas_dict = {}
        for pregunta in FORMULARIO_ORGANICO_PREGUNTAS:
            valor = request.POST.get(pregunta['id'], '')
            respuestas_dict[pregunta['id']] = valor

        puntaje = _calcular_puntaje_formulario_organico(respuestas_dict)

        try:
            with transaction.atomic():
                Cita.objects.select_for_update().get(id=cita.id)

                respuesta_anterior = RespuestaFormularioOrganica.objects.filter(
                    paciente=request.user
                ).exclude(cita=cita).order_by('-fecha_respuesta').first()

                RespuestaFormularioOrganica.objects.get_or_create(
                    paciente=request.user,
                    cita=cita,
                    defaults={'respuestas': respuestas_dict, 'puntaje': puntaje}
                )
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': str(e)}, status=500)

        ipt_actual = calcular_ipt(puntaje)
        ipt_anterior = calcular_ipt(respuesta_anterior.puntaje) if respuesta_anterior else None
        mensaje = _mensaje_tierno_progreso(ipt_actual, ipt_anterior)

        # =====================================================================
        # 🔥 NUEVO: PREPARAR Y ENVIAR CORREO AL DOCTOR EN SEGUNDO PLANO
        # =====================================================================
        # 1. Cruzar las IDs de las respuestas con los textos legibles
        respuestas_legibles = []
        for p in FORMULARIO_ORGANICO_PREGUNTAS:
            val_id = respuestas_dict.get(p['id'])
            # Buscamos el texto exacto ('Nunca', 'Siempre', etc.)
            texto_val = next((o['texto'] for o in ESCALA_IPP if o['valor'] == val_id), val_id)
            respuestas_legibles.append({'pregunta': p['texto'], 'respuesta': texto_val})

        # 2. Extraer datos del doctor y paciente
        psicologo_email = cita.psicologo.usuario.email
        paciente_nombre = request.user.first_name or request.user.username

        # 3. Lanzar el envío de correo en un Hilo (Thread) separado. 
        # Esto no bloquea la petición actual; el paciente recibe su JSON de inmediato.
        if psicologo_email:
            hilo_correo = threading.Thread(
                target=_enviar_correo_ipp_async,
                args=(psicologo_email, paciente_nombre, ipt_actual, ipt_anterior, respuestas_legibles)
            )
            hilo_correo.start()
        # =====================================================================

        return JsonResponse({
            'status': 'success',
            'redirect_url': cita.enlace_meet,
            'ipt': ipt_actual,
            'mensaje': mensaje,
        })

    return render(request, 'formulario_organico.html', {
        'cita': cita,
        'preguntas': FORMULARIO_ORGANICO_PREGUNTAS,
        'escala': ESCALA_IPP,
    })
    

def citas_hoy_view(request):
    """Vista simple: todas las citas de hoy con el match paciente-psicólogo."""
    hoy = timezone.localdate()

    citas = Cita.objects.filter(
        fecha=hoy
    ).exclude(
        estado='Cancelada'
    ).select_related(
        'psicologo__usuario', 'paciente__perfil'
    ).order_by('hora')

    for cita in citas:
        nombre_paciente = cita.paciente.first_name or cita.paciente.username
        if cita.psicologo:
            nombre_doctor = cita.psicologo.usuario.first_name or cita.psicologo.usuario.username
        else:
            nombre_doctor = 'Sin asignar'

        cita.nombre_paciente = nombre_paciente
        cita.nombre_doctor = nombre_doctor

        # 🔥 Ahora el teléfono que usamos es el del DOCTOR, no el del paciente
        telefono_doctor = cita.psicologo.telefono if cita.psicologo else None
        numero_limpio = _limpiar_numero_whatsapp(telefono_doctor)

        if numero_limpio:
            mensaje = (
                f"Hola Psic. {nombre_doctor} 👋, tienes sesión con {nombre_paciente} "
                f"hoy a las {cita.hora.strftime('%H:%M')} hrs. "
                f"En 10 minutos comienza tu sesión. "
                f"Aquí está tu link de Google Meet: {cita.enlace_meet or 'Se te compartirá en breve.'}"
            )
            cita.wa_link = f"https://wa.me/{numero_limpio}?text={quote(mensaje)}"
        else:
            cita.wa_link = None

    return render(request, 'citas_hoy.html', {
        'citas': citas,
        'hoy': hoy,
    })


def api_citas_hoy(request):
    hoy = timezone.localdate()
    # Traemos las citas de hoy que no estén canceladas
    citas = Cita.objects.filter(fecha=hoy).exclude(estado='Cancelada').order_by('hora')
    
    lista_json = []
    for cita in citas:
        # 1. Teléfono del DOCTOR
        telefono_doctor = cita.psicologo.telefono if cita.psicologo else None
        numero_doc_limpio = _limpiar_numero_whatsapp(telefono_doctor)
        
        # 2. Teléfono del PACIENTE (🔥 NUEVO)
        # Verificamos si el paciente tiene perfil antes de sacar el teléfono
        telefono_paciente = cita.paciente.perfil.telefono if hasattr(cita.paciente, 'perfil') else None
        numero_pac_limpio = _limpiar_numero_whatsapp(telefono_paciente)
        
        # Sacamos el nombre del consultante
        nombre_paciente = cita.paciente.first_name or cita.paciente.username
        
        lista_json.append({
            "hora": cita.hora.strftime('%H:%M'),
            "telefono_doctor": numero_doc_limpio,       # Cambié el nombre para distinguirlo
            "telefono_paciente": numero_pac_limpio,     # Agregamos al paciente al JSON
            "nombre_paciente": nombre_paciente,
            "link_meet": cita.enlace_meet or "No asignado"
        })
            
    return JsonResponse(lista_json, safe=False)


def renderizar_imagen(request):
    # Diccionario de contexto con los datos que le pasaremos al template
    return render(request, 'pruebas/claude.html')