from django.shortcuts import render, redirect
from django.contrib.auth.models import User
from django.core.mail import send_mail
from django.contrib.auth.tokens import default_token_generator
from django.utils.http import urlsafe_base64_encode, urlsafe_base64_decode
from django.utils.encoding import force_bytes, force_str
from django.contrib import messages
from django.urls import reverse
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

from django.template import Template, RequestContext
from django.utils.safestring import mark_safe
# --- LIBRERÍAS DE GOOGLE PARA MEET ---
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from django.conf import settings

from .models import *
from .cuestionario_data import CUESTIONARIO_CLINICO

# =========================================================================
# 🧠 FUNCIÓN MAESTRA: CREAR ENLACE DE GOOGLE MEET
# =========================================================================
def generar_link_meet(fecha_obj, hora_obj, paciente_nombre, psicologo_nombre, paciente_email, psicologo_email):
    SCOPES = ['https://www.googleapis.com/auth/calendar.events']

    token_path = os.path.join(settings.BASE_DIR, 'token.json')

    if not os.path.exists(token_path):
        print("ERROR: No existe token.json.")
        return None

    try:
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            with open(token_path, 'w') as token:
                token.write(creds.to_json())

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
            sendUpdates='all'  
        ).execute()

        return event_result.get('hangoutLink')

    except Exception as e:
        print(f"Error generando Meet: {e}")
        return None

# =========================================================================
# 🏠 NUEVA VISTA: PÁGINA DE INICIO (LANDING PAGE)
# =========================================================================
def inicio(request):
    pagina = PaginaInicioHTML.objects.first()
    
    if pagina and pagina.codigo_html:
        try:
            # Procesamos el HTML por si el sub usó etiquetas de Django como {% url %}
            plantilla_dinamica = Template(pagina.codigo_html)
            contexto = RequestContext(request, {})
            html_listo = plantilla_dinamica.render(contexto)
            
            return render(request, 'inicio.html', {'html_dinamico': mark_safe(html_listo)})
        except Exception as e:
            error_msg = f"<h1 style='color:red;'>Error de código: {e}</h1><p>Revisa la sintaxis de tu HTML.</p>"
            return render(request, 'inicio.html', {'html_dinamico': mark_safe(error_msg)})
            
    # Si no hay nada guardado aún
    mensaje_vacio = "<h1 style='text-align:center; margin-top:50px; font-family: sans-serif;'>Aún no hay código. Ve al Admin y pega tu HTML.</h1>"
    return render(request, 'inicio.html', {'html_dinamico': mark_safe(mensaje_vacio)})


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
            username=email, email=email, password=password)
        user.first_name = nombre
        user.is_active = False
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
        return JsonResponse({'status': 'success', 'message': '¡Registro exitoso! Revisa tu correo para activar tu cuenta.'})


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
    if request.method == 'POST':
        email = request.POST.get('login_email')
        password = request.POST.get('login_password')

        try:
            user = User.objects.get(username=email)
        except User.DoesNotExist:
            return JsonResponse({'status': 'error', 'error_type': 'invalid', 'message': 'El correo o la contraseña son incorrectos.'})

        if not user.check_password(password):
            return JsonResponse({'status': 'error', 'error_type': 'invalid', 'message': 'El correo o la contraseña son incorrectos.'})

        if not user.is_active:
            return JsonResponse({'status': 'error', 'error_type': 'unverified', 'message': 'Aún no verificas tu cuenta. Por favor, revisa tu bandeja de entrada.'})

        login(request, user)

        if user.is_superuser:
            return JsonResponse({'status': 'success', 'redirect_url': '/panel-admin/'})
        elif hasattr(user, 'perfil_psicologo'):
            return JsonResponse({'status': 'success', 'redirect_url': '/panel-doctor/'})
        else:
            return JsonResponse({'status': 'success', 'redirect_url': '/panel/'})

    return JsonResponse({'status': 'error', 'message': 'Método no permitido.'})


def panel_generico(request):
    if not request.user.is_authenticated:
        return redirect('modulo_informativo')

    hoy = timezone.now().date()
    hora_actual = timezone.now().time()
    perfil_usuario = request.user.perfil
    psicologo_asignado = perfil_usuario.psicologo_asignado

    cita_proxima = Cita.objects.filter(
        Q(fecha__gt=hoy) | Q(fecha=hoy, hora__gte=hora_actual),
        paciente=request.user,
        estado='Confirmada'
    ).order_by('fecha', 'hora').first()

    festivos = set(DiaFestivo.objects.filter(fecha__gte=hoy).values_list('fecha', flat=True))
    horas_base = [time(h, 0) for h in range(9, 19)]
    total_psicologos_activos = PerfilPsicologo.objects.filter(esta_activo=True).count()

    dias_json = {}
    dias_html = {}
    dias_agregados = 0
    dia_actual = hoy
    dias_iterados = 0

    while dias_agregados < 365 and dias_iterados < 400:
        dias_iterados += 1
        if dia_actual.weekday() <= 6 and dia_actual not in festivos:
            horas_del_dia_str = []
            horas_del_dia_obj = []

            for h in horas_base:
                if dia_actual == hoy and h <= hora_actual: continue

                if psicologo_asignado:
                    if not Cita.objects.filter(psicologo=psicologo_asignado, fecha=dia_actual, hora=h, estado='Confirmada').exists():
                        horas_del_dia_str.append(h.strftime('%I:%M %p'))
                        horas_del_dia_obj.append(h)
                else:
                    if Cita.objects.filter(fecha=dia_actual, hora=h, estado='Confirmada').count() < total_psicologos_activos:
                        horas_del_dia_str.append(h.strftime('%I:%M %p'))
                        horas_del_dia_obj.append(h)

            if horas_del_dia_str:
                dias_json[dia_actual.strftime('%Y-%m-%d')] = horas_del_dia_str
                dias_html[dia_actual] = horas_del_dia_obj
                dias_agregados += 1

        dia_actual += timedelta(days=1)

    talleres_futuros = Taller.objects.filter(fecha__gte=hoy).order_by('fecha', 'hora')
    mis_inscripciones_ids = InscripcionTaller.objects.filter(paciente=request.user).values_list('taller_id', flat=True)
    mis_talleres = InscripcionTaller.objects.filter(paciente=request.user, taller__fecha__gte=hoy).order_by('taller__fecha')

    return render(request, 'panel_generico.html', {
        'dias_disponibles_json': dias_json,
        'dias_disponibles': dias_html,
        'cita_proxima': cita_proxima,
        'perfil': perfil_usuario,
        'talleres': talleres_futuros.filter(tipo='Taller'),
        'grupales': talleres_futuros.filter(tipo='Grupal'),
        'escuela_padres': talleres_futuros.filter(tipo='Padres'),
        'mis_inscripciones_ids': list(mis_inscripciones_ids),
        'mis_talleres': mis_talleres,
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
                    send_mail(asunto, mensaje_plano, None, [request.user.email], html_message=mensaje_html, fail_silently=True)
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

        fecha_str = request.POST.get('fecha')
        hora_str = request.POST.get('hora')
        animo = request.POST.get('animo', 'No especificó')

        try:
            fecha_obj = datetime.strptime(fecha_str, '%Y-%m-%d').date()
            hora_obj = datetime.strptime(hora_str, '%H:%M').time()
            perfil = request.user.perfil

            psicologo = perfil.psicologo_asignado

            if not psicologo:
                preferencia = ""
                try:
                    cuestionario = request.user.cuestionario_inicial
                    preferencia = cuestionario.respuestas.get(
                        'preferencia_terapeuta', '')
                except:
                    pass

                psicologos_ocupados_ids = Cita.objects.filter(
                    fecha=fecha_obj, hora=hora_obj, estado='Confirmada').values_list('psicologo_id', flat=True)
                psicologos_libres = PerfilPsicologo.objects.filter(esta_activo=True).exclude(
                    id__in=psicologos_ocupados_ids).annotate(carga_pacientes=Count('pacientes_asignados'))

                if not psicologos_libres.exists():
                    return JsonResponse({'status': 'error', 'message': 'Lo sentimos, este horario acaba de ser ocupado por alguien más. Por favor elige otro.'})

                if 'Mujer' in preferencia:
                    psicologo = psicologos_libres.filter(
                        genero='Mujer').order_by('carga_pacientes').first()
                elif 'Hombre' in preferencia:
                    psicologo = psicologos_libres.filter(
                        genero='Hombre').order_by('carga_pacientes').first()

                if not psicologo:
                    psicologo = psicologos_libres.order_by(
                        'carga_pacientes').first()

                perfil.psicologo_asignado = psicologo
                perfil.save()

            else:
                if Cita.objects.filter(psicologo=psicologo, fecha=fecha_obj, hora=hora_obj, estado='Confirmada').exists():
                    return JsonResponse({'status': 'error', 'message': 'Lo sentimos, tu terapeuta acaba de ocupar este horario. Elige otro por favor.'})

            enlace_generado = generar_link_meet(
                fecha_obj=fecha_obj,
                hora_obj=hora_obj,
                paciente_nombre=request.user.first_name,
                psicologo_nombre=psicologo.usuario.first_name,
                paciente_email=request.user.email,             
                psicologo_email=psicologo.usuario.email        
            )

            Cita.objects.create(
                paciente=request.user,
                psicologo=psicologo,
                fecha=fecha_obj,
                hora=hora_obj,
                estado_animo=animo,
                motivo='Primera Sesión' if not perfil.psicologo_asignado else 'Sesión de Seguimiento',
                estado='Confirmada',
                enlace_meet=enlace_generado
            )

            # =========================================================
            # 📧 ENVIAR CORREO HTML DE CONFIRMACIÓN DE CITA CLÍNICA
            # =========================================================
            asunto = 'Confirmación de tu sesión en HOPE'
            link_final = enlace_generado if enlace_generado else "Se generará pronto y podrás verlo en tu panel."
            
            contexto = {
                'nombre': request.user.first_name,
                'psicologo_nombre': psicologo.usuario.first_name,
                'fecha': fecha_obj.strftime('%d/%m/%Y'),
                'hora': hora_obj.strftime('%H:%M'),
                'link_meet': link_final
            }
            
            mensaje_html = render_to_string('correo_cita.html', contexto)
            mensaje_plano = strip_tags(mensaje_html)
            
            try:
                send_mail(asunto, mensaje_plano, None, [request.user.email], html_message=mensaje_html, fail_silently=True)
            except Exception as e:
                print(f"Error al enviar correo de la cita: {e}")
            # =========================================================

            return JsonResponse({'status': 'success'})
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': str(e)})

    return JsonResponse({'status': 'error'})


# =========================================================================
# 🩺 PANEL DEL DOCTOR Y EXPEDIENTE MAESTRO
# =========================================================================
def panel_doctor(request):
    if not request.user.is_authenticated or not hasattr(request.user, 'perfil_psicologo'):
        return redirect('modulo_informativo')

    psicologo = request.user.perfil_psicologo
    hoy = timezone.now().date()
    
    citas_hoy = Cita.objects.filter(psicologo=psicologo, fecha=hoy, estado='Confirmada').order_by('hora')
    
    # 1. Agregamos las Citas Normales (Color Verde Teal: #297E7E)
    eventos_calendario = [{
        'title': f"{c.paciente.first_name} ({c.hora.strftime('%H:%M')})", 
        'start': f"{c.fecha.isoformat()}T{c.hora.strftime('%H:%M:%S')}", 
        'backgroundColor': '#297E7E' if c.fecha >= hoy else '#D1D5DB', 
        'borderColor': '#297E7E' if c.fecha >= hoy else '#D1D5DB'
    } for c in Cita.objects.filter(psicologo=psicologo)]

    mis_pacientes_db = User.objects.filter(perfil__psicologo_asignado=psicologo).distinct()
    pacientes_data = [{'usuario': p, 'total_citas': Cita.objects.filter(paciente=p, psicologo=psicologo).count()} for p in mis_pacientes_db]

    mis_talleres_impartidos = Taller.objects.filter(psicologo=psicologo).order_by('fecha', 'hora')

    # 2. Agregamos los Talleres/Grupos al mismo calendario (Color Dorado/Oliva: #B5992D)
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
        'hoy': hoy
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
        
        como_llega = request.POST.get('como_llega', '')
        notas = request.POST.get('notas_sesion', '')
        aprendizaje = request.POST.get('aprendizaje_paciente', '')
        como_se_va = request.POST.get('como_se_va', '')
        recomendaciones = request.POST.get('recomendaciones', '')

        try:
            paciente = User.objects.get(id=paciente_id)
            psicologo = request.user.perfil_psicologo
            
            historial = HistorialClinico(
                paciente=paciente,
                psicologo=psicologo,
                como_llega=como_llega,
                notas_sesion=notas,
                aprendizaje_paciente=aprendizaje,
                como_se_va=como_se_va,
                recomendaciones=recomendaciones
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

    historiales = HistorialClinico.objects.filter(
        paciente=paciente, psicologo=psicologo).order_by('-fecha_registro')
    total_sesiones = Cita.objects.filter(
        paciente=paciente, psicologo=psicologo).count()

    return render(request, 'detalle_paciente.html', {
        'paciente': paciente,
        'historiales': historiales,
        'total_sesiones': total_sesiones
    })

# =========================================================================
# 👑 CENTRO DE COMANDO (PANEL DE SUPER ADMIN / CEO)
# =========================================================================

# =========================================================================
# 👑 CENTRO DE COMANDO (PANEL DE SUPER ADMIN / CEO)
# =========================================================================
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