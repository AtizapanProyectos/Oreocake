from django.urls import path
from . import views


urlpatterns = [
    path('', views.inicio, name='inicio'),
    path('informativo/', views.modulo_informativo, name='modulo_informativo'),
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

    path('panel-doctor/', views.panel_doctor, name='panel_doctor'),
    path('guardar-historial/', views.guardar_historial_ajax, name='guardar_historial'),
    path('obtener-expediente/', views.obtener_expediente_ajax, name='obtener_expediente'), # NUEVO
    path('guardar-expediente-global/', views.guardar_expediente_global_ajax, name='guardar_expediente_global'), # NUEVO
]