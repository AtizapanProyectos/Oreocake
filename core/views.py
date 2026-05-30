from django.shortcuts import render, redirect
from django.contrib.auth.models import User
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
from django.utils import timezone
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

from django.utils import timezone as tz



from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.http import JsonResponse
from django.utils.timezone import localtime, now

import re

import base64
import os


import fitz          # PyMuPDF  — para leer PDFs
import docx          # python-docx — para leer .doc/.docx
from groq import Groq
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.decorators import login_required
 
 
TIPOS_IMAGEN  = {'jpg', 'jpeg', 'png', 'webp'}
TIPOS_PDF     = {'pdf'}
TIPOS_DOC     = {'doc', 'docx'}
TAMANO_MAX_MB = 10  # límite de seguridad




from django.contrib.auth import logout

def logout_usuario(request):
    logout(request)
    return redirect('inicio')

# =========================================================================
# 🧠 FUNCIÓN MAESTRA: CREAR ENLACE DE GOOGLE MEET
# =========================================================================
def generar_link_meet(fecha_obj, hora_obj, paciente_nombre, psicologo_nombre, paciente_email, psicologo_email):
    SCOPES = ['https://www.googleapis.com/auth/calendar.events']

    # ✅ NUEVO: leer desde variable de entorno en lugar de archivo
    token_json_str = os.environ.get('GOOGLE_TOKEN_JSON')
    if not token_json_str:
        print("ERROR: No existe la variable de entorno GOOGLE_TOKEN_JSON.")
        return None

    try:
        token_data = json.loads(token_json_str)
        creds = Credentials.from_authorized_user_info(token_data, SCOPES)

        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            # ✅ NUEVO: actualizar la variable en memoria (no escribir archivo)
            # Nota: en Railway no podemos persistir el refresh automáticamente,
            # así que actualiza GOOGLE_TOKEN_JSON manualmente si expira.
            print("⚠️ Token refrescado. Actualiza GOOGLE_TOKEN_JSON en Railway con:", creds.to_json())

        service = build('calendar', 'v3', credentials=creds)

        inicio_datetime = datetime.combine(fecha_obj, hora_obj)
        fin_datetime = inicio_datetime + timedelta(minutes=50)

        start_format = inicio_datetime.isoformat() + '-06:00'
        end_format = fin_datetime.isoformat() + '-06:00'

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
            'attendees': [
                {'email': paciente_email},
                {'email': psicologo_email},
            ],
            'conferenceData': {
                'createRequest': {
                    'requestId': f"hope_meet_{uuid.uuid4().hex[:10]}",
                    'conferenceSolutionKey': {'type': 'hangoutsMeet'}
                }
            }
        }

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
    context = {
        'cuestionario_json': json.dumps(CUESTIONARIO_CLINICO),
        'paypal_client_id': settings.PAYPAL_CLIENT_ID,
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

    # 1. Primero obtenemos el perfil (¡Esto debe ir antes de todo!)
    try:
        perfil_usuario = request.user.perfil
    except Exception:
        logout(request)
        return redirect('modulo_informativo')

    # 2. Ahora sí, extraemos qué vino a buscar el paciente (ya conocemos perfil_usuario)
# 2. 🔥 CORRECCIÓN: Leer desde tu tabla CuestionarioRegistro 🔥
    tipo_servicio = "individual" 
    
    # Buscamos el último cuestionario que haya llenado este paciente
    ultimo_cuestionario = CuestionarioRegistro.objects.filter(paciente=request.user).last()

    if ultimo_cuestionario and ultimo_cuestionario.respuestas:
        respuestas = ultimo_cuestionario.respuestas
        if isinstance(respuestas, str):
            try:
                respuestas = json.loads(respuestas)
            except:
                respuestas = {}
        
        # Extraemos la llave exacta que guardaste (Ej: 'terapia_pareja' o 'Criar con Conciencia')
        tipo_servicio = respuestas.get("servicio_solicitado", "individual")

    # 3. Configuración de tiempos y citas
    now_local = timezone.localtime(timezone.now())
    hoy = now_local.date()
    hora_actual = now_local.time().replace(second=0, microsecond=0)

    psicologo_asignado = perfil_usuario.psicologo_asignado

    cita_proxima = Cita.objects.filter(
        Q(fecha__gt=hoy) | Q(fecha=hoy, hora__gte=hora_actual),
        paciente=request.user,
        estado='Confirmada'
    ).order_by('fecha', 'hora').first()

    # 4. Lógica de disponibilidad del calendario
    festivos = set(DiaFestivo.objects.filter(fecha__gte=hoy).values_list('fecha', flat=True))
    horas_base = [time(h, 0) for h in range(9, 21)]
    total_psicologos_activos = PerfilPsicologo.objects.filter(esta_activo=True).count()

    dias_json = {}
    dias_html = {}

    if total_psicologos_activos > 0 or psicologo_asignado:
        fecha_limite = hoy + timedelta(days=90)
        if psicologo_asignado:
            citas_ocupadas = set(
                (c['fecha'], c['hora'].replace(second=0, microsecond=0))
                for c in Cita.objects.filter(
                    psicologo=psicologo_asignado,
                    fecha__gte=hoy,
                    fecha__lte=fecha_limite,
                    estado='Confirmada'
                ).values('fecha', 'hora')
            )
        else:
            citas_agrupadas = Cita.objects.filter(
                fecha__gte=hoy,
                fecha__lte=fecha_limite,
                estado='Confirmada'
            ).values('fecha', 'hora').annotate(total=Count('id'))
            citas_ocupadas = {
                (c['fecha'], c['hora'].replace(second=0, microsecond=0)): c['total']
                for c in citas_agrupadas
            }

        dias_agregados = 0
        dia_actual = hoy
        dias_iterados = 0
        while dias_agregados < 30 and dias_iterados < 120:
            dias_iterados += 1
            if dia_actual not in festivos:
                horas_del_dia_str = []
                horas_del_dia_obj = []
                for h in horas_base:
                    if dia_actual == hoy and h < hora_actual:
                        continue
                    if psicologo_asignado:
                        if (dia_actual, h) not in citas_ocupadas:
                            horas_del_dia_str.append(h.strftime('%I:%M %p'))
                            horas_del_dia_obj.append(h)
                    else:
                        ocupadas = citas_ocupadas.get((dia_actual, h), 0)
                        if ocupadas < total_psicologos_activos:
                            horas_del_dia_str.append(h.strftime('%I:%M %p'))
                            horas_del_dia_obj.append(h)
                if horas_del_dia_str:
                    dias_json[dia_actual.strftime('%Y-%m-%d')] = horas_del_dia_str
                    dias_html[dia_actual] = horas_del_dia_obj
                    dias_agregados += 1
            dia_actual += timedelta(days=1)

    # 5. Talleres e Inscripciones
    talleres_futuros = Taller.objects.filter(fecha__gte=hoy).order_by('fecha', 'hora')
    mis_inscripciones_ids = InscripcionTaller.objects.filter(paciente=request.user).values_list('taller_id', flat=True)
    mis_talleres = InscripcionTaller.objects.filter(
        paciente=request.user,
        taller__fecha__gte=hoy
    ).order_by('taller__fecha')

    # 🔥 NUEVO: Buscamos el taller exacto si el usuario viene por uno
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

def guardar_cita_ajax(request):
    if request.method == 'POST':
        if not request.user.is_authenticated:
            return JsonResponse({'status': 'error', 'message': 'Debes iniciar sesión.'})

        # Atrapamos los datos del formulario
        fecha_str = request.POST.get('fecha')
        hora_str = request.POST.get('hora')
        animo = request.POST.get('animo', 'No especificó')
        modalidad_str = request.POST.get('modalidad', 'En línea')
        # 🔥 Tipo de sesión elegido por el usuario en el panel (individual o pareja)
        tipo_sesion_str = request.POST.get('tipo_servicio', 'individual')
        if tipo_sesion_str not in ['individual', 'pareja']:
            tipo_sesion_str = 'individual'

        try:
            fecha_obj = datetime.strptime(fecha_str, '%Y-%m-%d').date()
            hora_obj = datetime.strptime(hora_str, '%H:%M').time()
            perfil = request.user.perfil
            psicologo = perfil.psicologo_asignado

            # Lógica de asignación automática de psicólogo (si no tiene uno)
            if not psicologo:
                preferencia = ""
                try:
                    cuestionario = request.user.cuestionario_inicial
                    preferencia = cuestionario.respuestas.get('preferencia_terapeuta', '')
                except:
                    pass

                psicologos_ocupados_ids = Cita.objects.filter(
                    fecha=fecha_obj, hora=hora_obj, estado='Confirmada').values_list('psicologo_id', flat=True)
                
                psicologos_libres = PerfilPsicologo.objects.filter(esta_activo=True).exclude(
                    id__in=psicologos_ocupados_ids).annotate(carga_pacientes=Count('pacientes_asignados'))

                if not psicologos_libres.exists():
                    return JsonResponse({'status': 'error', 'message': 'Lo sentimos, este horario acaba de ser ocupado. Elige otro.'})

                if 'Mujer' in preferencia:
                    psicologo = psicologos_libres.filter(genero='Mujer').order_by('carga_pacientes').first()
                elif 'Hombre' in preferencia:
                    psicologo = psicologos_libres.filter(genero='Hombre').order_by('carga_pacientes').first()

                if not psicologo:
                    psicologo = psicologos_libres.order_by('carga_pacientes').first()

                perfil.psicologo_asignado = psicologo
                perfil.save()
            else:
                # Validar disponibilidad si ya tiene psicólogo
                if Cita.objects.filter(psicologo=psicologo, fecha=fecha_obj, hora=hora_obj, estado='Confirmada').exists():
                    return JsonResponse({'status': 'error', 'message': 'Tu terapeuta ya tiene una cita en ese horario. Elige otro.'})

            # 🔥 LÓGICA DE MODALIDAD: ¿Creamos Meet o no? 🔥
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

            # Creamos la cita con el nuevo campo modalidad
            Cita.objects.create(
                paciente=request.user,
                psicologo=psicologo,
                fecha=fecha_obj,
                hora=hora_obj,
                estado_animo=animo,
                modalidad=modalidad_str,
                tipo_sesion=tipo_sesion_str,  # 🔥 Guardamos si fue individual o de pareja
                motivo='Primera Sesión' if not perfil.psicologo_asignado else 'Sesión de Seguimiento',
                estado='Confirmada',
                enlace_meet=link_final,
                id_evento_google=id_google  
            )

            # --- Enviar correo de confirmación ---
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
            return JsonResponse({'status': 'error', 'message': str(e)})

    return JsonResponse({'status': 'error'})


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
    citas_todas = Cita.objects.filter(psicologo=psicologo)
    eventos_calendario = [{
        'title': f"{c.paciente.first_name} ({c.hora.strftime('%H:%M')})",
        'start': f"{c.fecha.isoformat()}T{c.hora.strftime('%H:%M:%S')}",
        'backgroundColor': '#297E7E' if c.fecha >= hoy else '#D1D5DB',
        'borderColor': '#297E7E' if c.fecha >= hoy else '#D1D5DB'
    } for c in citas_todas]

    mis_pacientes_db = User.objects.filter(perfil__psicologo_asignado=psicologo).distinct()
    pacientes_data = [
        {
            'usuario': p,
            'total_citas': Cita.objects.filter(paciente=p, psicologo=psicologo).count()
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
    
    historiales = HistorialClinico.objects.filter(paciente=paciente, psicologo=request.user.perfil_psicologo).order_by('-fecha_registro')
    hist_data = [{'fecha': h.fecha_registro.strftime('%d/%m/%Y'), 'notas': h.notas_sesion, 'aprendizaje': h.aprendizaje_paciente} for h in historiales]
    
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
    #
    # La lógica es simple:
    #   1. Tomamos TODAS las citas pasadas del paciente con este psicólogo.
    #   2. Para cada cita buscamos si ya existe un HistorialClinico vinculado
    #      (usando la relación OneToOne cita.nota_clinica).
    #   3. Si existe historial SIN cita (fue creado manualmente sin agendar),
    #      también lo incluimos al final.
    #   4. Le pasamos al template una lista de dicts con estructura clara.
    # =========================================================================
    now_local = timezone.localtime(timezone.now())
 
    # Todas las citas pasadas, con o sin evento Google (para unificar todo)
    citas_pasadas = Cita.objects.filter(
        paciente=paciente,
        psicologo=psicologo,
        fecha__lte=now_local.date(),
    ).order_by('-fecha', '-hora').select_related('nota_clinica')
 
    # IDs de historiales que YA están vinculados a una cita (para no duplicarlos)
    historiales_vinculados_ids = set()
 
    sesiones = []  # ← Lista final que le pasamos al template
 
    for cita in citas_pasadas:
        historial = None
        # El related_name del OneToOne en HistorialClinico.cita es 'nota_clinica'
        try:
            historial = cita.nota_clinica  # puede lanzar RelatedObjectDoesNotExist
            historiales_vinculados_ids.add(historial.id)
        except Exception:
            historial = None
 
        # Solo incluir citas que tienen Meet O que tienen bitácora
        # (omitir citas vacías sin ningún contenido)
        tiene_meet = bool(cita.id_evento_google)
        tiene_historial = historial is not None
 
        if tiene_meet or tiene_historial:
            sesiones.append({
                'tipo': 'completa',          # tiene cita base
                'cita': cita,
                'historial': historial,
                'tiene_meet': tiene_meet,
                'tiene_historial': tiene_historial,
                # Fecha canónica para ordenar: usamos la de la cita
                'fecha_orden': cita.fecha,
                'hora_orden': cita.hora,
                'slug': f"c-{cita.id}", # <--- 1. AGREGA ESTA LÍNEA AQUÍ
            })
 
    # Historiales huérfanos: creados manualmente sin asociar a ninguna cita
    historiales_huerfanos = HistorialClinico.objects.filter(
        paciente=paciente,
        psicologo=psicologo,
        cita__isnull=True,   # sin cita vinculada
    ).exclude(
        id__in=historiales_vinculados_ids
    ).order_by('-fecha_registro')
 
    for h in historiales_huerfanos:
        sesiones.append({
            'tipo': 'solo_historial',    # no tiene cita asociada
            'cita': None,
            'historial': h,
            'tiene_meet': False,
            'tiene_historial': True,
            'fecha_orden': h.fecha_registro.date(),
            'hora_orden': h.fecha_registro.time(),
            'slug': f"h-{h.id}", # <--- 2. AGREGA ESTA LÍNEA AQUÍ TAMBIÉN
        })
 
    # Ordenamos todo junto por fecha descendente
    sesiones.sort(key=lambda s: (s['fecha_orden'], s['hora_orden']), reverse=True)
 
    total_sesiones = Cita.objects.filter(paciente=paciente, psicologo=psicologo).count()
 
    return render(request, 'detalle_paciente.html', {
        'paciente': paciente,
        'sesiones': sesiones,           # ← ÚNICA LISTA, reemplaza historiales + citas_pasadas
        'total_sesiones': total_sesiones,
        'cuestionario': cuestionario,
        'respuestas_formateadas': respuestas_formateadas,
    })


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

def es_admin(user):
    return user.is_superuser

@user_passes_test(es_admin, login_url='/')
def panel_admin(request):
    hoy = timezone.now().date()
    
    # 1. Estadísticas Generales (Métricas de Impacto)
    total_pacientes = UsuarioPerfil.objects.filter(es_psicologo=False).count()
    total_doctores = PerfilPsicologo.objects.filter(esta_activo=True).count()
    citas_hoy = Cita.objects.filter(fecha=hoy).exclude(estado='Cancelada').count()
    citas_totales = Cita.objects.exclude(estado='Cancelada').count()

    # 2. Rendimiento por Doctor (Capacidad del Equipo Médico y Barra de Energía)
    doctores_data = []
    doctores = PerfilPsicologo.objects.all()
    for doc in doctores:
        pacientes_activos = doc.pacientes_asignados.count()
        citas_doc_hoy = Cita.objects.filter(psicologo=doc, fecha=hoy).exclude(estado='Cancelada').count()
        citas_doc_total = Cita.objects.filter(psicologo=doc).exclude(estado='Cancelada').count()
        
        # 🧠 Lógica de la Batería (Barra de Capacidad)
        capacidad_maxima = 20
        porcentaje_carga = min(int((pacientes_activos / capacidad_maxima) * 100), 100) if pacientes_activos > 0 else 0

        estado_carga = "Equilibrada"
        color_carga = "#10b981" # Verde
        
        if pacientes_activos >= 16: 
            estado_carga = "Sobrecarga"
            color_carga = "#ef4444" # Rojo
        elif pacientes_activos >= 12:
            estado_carga = "Carga Alta"
            color_carga = "#f59e0b" # Naranja
        elif pacientes_activos == 0: 
            estado_carga = "Disponible"
            color_carga = "#94a3b8" # Gris

        doctores_data.append({
            'nombre': doc.usuario.first_name,
            'especialidad': doc.especialidad or 'Psicología Clínica',
            'pacientes': pacientes_activos,
            'citas_hoy': citas_doc_hoy,
            'citas_historicas': citas_doc_total,
            'carga': estado_carga,
            'color_carga': color_carga,
            'porcentaje_carga': porcentaje_carga
        })

    # 3. Listado de Pacientes (Monitoreo de Estado de Ánimo PROMEDIO)
    pacientes_recientes = UsuarioPerfil.objects.filter(es_psicologo=False).order_by('-id')[:20]
    pacientes_data = []
    for pac in pacientes_recientes:
        if pac.usuario:
            citas_paciente = Cita.objects.filter(paciente=pac.usuario).exclude(estado='Cancelada')
            total_sesiones = citas_paciente.count()
            
            # 🧠 LÓGICA DE PROMEDIO DE ESTADO DE ÁNIMO
            valores_animo = {'Muy mal': 1, 'Triste': 2, 'Normal': 3, 'Bien': 4, 'Excelente': 5}
            textos_animo = {1: 'Muy mal', 2: 'Triste', 3: 'Normal', 4: 'Bien', 5: 'Excelente'}
            
            suma_animo = 0
            sesiones_validas = 0
            
            for cita in citas_paciente:
                if cita.estado_animo in valores_animo:
                    suma_animo += valores_animo[cita.estado_animo]
                    sesiones_validas += 1
            
            # Calculamos el promedio si hay sesiones válidas
            if sesiones_validas > 0:
                promedio_num = round(suma_animo / sesiones_validas)
                animo_actual = textos_animo.get(promedio_num, "Normal")
            else:
                animo_actual = "Sin registro"
        else:
            total_sesiones = 0
            animo_actual = "Sin registro"
            
        doctor_nombre = pac.psicologo_asignado.usuario.first_name if pac.psicologo_asignado else 'Pendiente'
        
        # Inteligencia: Asignar icono y color según el estado de ánimo PROMEDIO
        if animo_actual == "Muy mal":
            icono_animo = "fas fa-sad-cry"
            color_animo = "#ef4444" # Rojo alerta
        elif animo_actual == "Triste":
            icono_animo = "fas fa-frown"
            color_animo = "#f97316" # Naranja
        elif animo_actual == "Normal":
            icono_animo = "fas fa-meh"
            color_animo = "#64748b" # Gris azulado
        elif animo_actual == "Bien":
            icono_animo = "fas fa-smile"
            color_animo = "#10b981" # Verde
        elif animo_actual == "Excelente":
            icono_animo = "fas fa-grin-stars"
            color_animo = "#B5992D" # Dorado
        else:
            animo_actual = "Sin registro"
            icono_animo = "fas fa-minus"
            color_animo = "#cbd5e1" # Gris claro

        pacientes_data.append({
            'nombre': pac.nombre,
            'email': pac.usuario.email if pac.usuario else 'Sin email registrado',
            'doctor': doctor_nombre,
            'sesiones': total_sesiones,
            'animo': animo_actual,
            'icono_animo': icono_animo,
            'color_animo': color_animo
        })

    context = {
        'total_pacientes': total_pacientes,
        'total_doctores': total_doctores,
        'citas_hoy': citas_hoy,
        'citas_totales': citas_totales,
        'doctores': doctores_data,
        'pacientes': pacientes_data,
        'hoy': hoy
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
    # Lógica de tiempos y citas libres (Igualita a la del panel_generico)
    now_local = timezone.localtime(timezone.now())
    hoy = now_local.date()
    hora_actual = now_local.time().replace(second=0, microsecond=0)
    festivos = set(DiaFestivo.objects.filter(fecha__gte=hoy).values_list('fecha', flat=True))
    horas_base = [time(h, 0) for h in range(9, 21)]
    total_psicologos_activos = PerfilPsicologo.objects.filter(esta_activo=True).count()

    dias_json = {}
    dias_html = {}

    
    # Calculamos espacios globales (ya que es un paciente nuevo sin psicólogo asignado)
    if total_psicologos_activos > 0:
        fecha_limite = hoy + timedelta(days=90)
        citas_agrupadas = Cita.objects.filter(fecha__gte=hoy, fecha__lte=fecha_limite, estado='Confirmada').values('fecha', 'hora').annotate(total=Count('id'))
        citas_ocupadas = {(c['fecha'], c['hora'].replace(second=0, microsecond=0)): c['total'] for c in citas_agrupadas}

        dias_agregados = 0
        dia_actual = hoy
        dias_iterados = 0
        while dias_agregados < 30 and dias_iterados < 120:
            dias_iterados += 1
            if dia_actual not in festivos:
                horas_del_dia_str = []
                horas_del_dia_obj = []
                for h in horas_base:
                    if dia_actual == hoy and h < hora_actual: continue
                    ocupadas = citas_ocupadas.get((dia_actual, h), 0)
                    if ocupadas < total_psicologos_activos:
                        horas_del_dia_str.append(h.strftime('%I:%M %p'))
                        horas_del_dia_obj.append(h)
                if horas_del_dia_str:
                    dias_json[dia_actual.strftime('%Y-%m-%d')] = horas_del_dia_str
                    dias_html[dia_actual] = horas_del_dia_obj
                    dias_agregados += 1
            dia_actual += timedelta(days=1)

    context = {
        'dias_disponibles_json': dias_json,
        'dias_disponibles': dias_html,
        'paypal_client_id': settings.PAYPAL_CLIENT_ID,
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