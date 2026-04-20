from django.urls import path
from . import views


urlpatterns = [
    path('', views.inicio, name='inicio'),
    path('login/', views.modulo_informativo, name='modulo_informativo'),
    path('registro-ajax/', views.registrar_usuario, name='registro_usuario'),
    path('activar/<uidb64>/<token>/', views.activar_cuenta, name='activar_cuenta'),
    path('login-ajax/', views.login_usuario, name='login_usuario'),
    path('panel/', views.panel_generico, name='panel_generico'),
    path('guardar-cita/', views.guardar_cita_ajax, name='guardar_cita'), # <-- AGREGA ESTA LÍNEA
    path('panel-doctor/', views.panel_doctor, name='panel_doctor'),
    path('guardar-historial/', views.guardar_historial_ajax, name='guardar_historial'),
    path('paciente/<int:paciente_id>/', views.detalle_paciente, name='detalle_paciente'),
    path('panel-admin/', views.panel_admin, name='panel_admin'),
    path('inscribir-taller/', views.inscribir_taller_ajax, name='inscribir_taller'),
    path('enviar-mood/', views.enviar_mood_ajax, name='enviar_mood'),
    path('panel-doctor/', views.panel_doctor, name='panel_doctor'),
    path('guardar-historial/', views.guardar_historial_ajax, name='guardar_historial'),
    path('obtener-expediente/', views.obtener_expediente_ajax, name='obtener_expediente'), # NUEVO
    path('guardar-expediente-global/', views.guardar_expediente_global_ajax, name='guardar_expediente_global'), # NUEVO
    path('procesar-archivo-ia/', views.procesar_archivo_ia, name='procesar_archivo_ia'),
    path('check-meet-notes/<int:cita_id>/', views.check_meet_notes, name='check_meet_notes'),
    path('admindoc/', views.admindoc_login, name='admindoc_login'),
    path('obtener-bitacora/<int:historial_id>/', views.obtener_bitacora, name='obtener_bitacora'),
    path('generar-sintesis/<int:paciente_id>/', views.generar_sintesis_ajax, name='generar_sintesis'),
    path('logout/', views.logout_usuario, name='logout_usuario'),
]