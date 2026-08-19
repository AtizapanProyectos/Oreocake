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
    path('disponibilidad-por-tipo/', views.obtener_disponibilidad_por_tipo_ajax, name='disponibilidad_por_tipo'), # NUEVO: filtra por individual/pareja/familiar
    path('calcular-precio-sesion/', views.calcular_precio_sesion_ajax, name='calcular_precio_sesion'), # NUEVO: precio en tiempo real
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
    path('solicitar-recuperacion/', views.solicitar_recuperacion, name='solicitar_recuperacion'),
    path('reset/<uidb64>/<token>/', views.resetear_password_form, name='resetear_password_form'),
    path('sesion-individual/', views.sesion_individual, name='sesion_individual'),
    path('registro-rapido/', views.registro_rapido_ajax, name='registro_rapido'),
    path('guardar_cita_ajax/', views.guardar_cita_ajax, name='guardar_cita_ajax'),
    path('guardar-expediente-completo/', views.guardar_expediente_completo, name='guardar_expediente_completo'),
    path('api/admin/pacientes/', views.api_pacientes_paginados, name='api_pacientes'),
    path('api/admin/stats/', views.api_stats, name='api_stats'),
    path('api/chat/enviar/', views.enviar_mensaje_chat, name='enviar_mensaje_chat'),
    path('api/chat/historial/<int:usuario_id>/', views.obtener_mensajes_chat, name='obtener_mensajes_chat'),
    path('api/chat/contactos/', views.obtener_contactos_chat, name='obtener_contactos_chat'),
    path('blog/<slug:slug>/', views.detalle_prensa, name='detalle_prensa'),
    path('iniciar_pago_clip/', views.iniciar_pago_clip, name='iniciar_pago_clip'),
    path('pago-exitoso/<int:cita_id>/', views.pago_exitoso_clip, name='pago_exitoso_clip'),
    path('pago-cancelado/<int:cita_id>/', views.pago_cancelado_clip, name='pago_cancelado_clip'),
    path('talleres/', views.talleres_view, name='talleres'),
    path('api/registrar-taller/', views.procesar_registro_taller, name='registrar_taller_ajax'),
    path('reagendar-cita/', views.reagendar_cita_ajax, name='reagendar_cita'),
    path('sesion-previa/<int:cita_id>/', views.formulario_previo_meet, name='formulario_previo_meet'),

#Pruebas y produccion
# Agrega estas líneas dentro de tu `urlpatterns` en urls.py
# (junto a las demás rutas de panel-admin / admin):

    path('panel-admin/agendar-cita/', views.panel_admin_agendar_cita, name='panel_admin_agendar_cita'),
    path('admin/buscar-pacientes/', views.admin_buscar_pacientes_ajax, name='admin_buscar_pacientes'),
    path('admin/disponibilidad-paciente/', views.admin_disponibilidad_ajax, name='admin_disponibilidad_paciente'),
    path('admin/guardar-cita-paciente/', views.admin_guardar_cita_ajax, name='admin_guardar_cita_paciente'),
#Conatcto venezuela
    path('venezuela/', views.formulario_venezuela, name='formulario_venezuela'),
    path('donaciones-venezuela/', views.donaciones_venezuela, name='donaciones_venezuela'),

#Contacto colombia
    path('colombia/', views.landing_colombia, name='landing_colombia'),
    path('colombia/formulario/', views.formulario_colombia, name='formulario_colombia'),
    path('citas-hoy/', views.citas_hoy_view, name='citas_hoy'),
    path('api/citas-hoy/', views.api_citas_hoy, name='api_citas_hoy'),


    path('analizar-grafica-ipp/<int:paciente_id>/', views.analizar_grafica_ipp_ajax, name='analizar_grafica_ipp'),
    path('report/', views.renderizar_imagen, name='ver_imagen'),
    path('report2/', views.renderizar_pasciente, name='ver_imagen'),


    path('buscar-mis-pacientes/', views.buscar_mis_pacientes_ajax, name='buscar_mis_pacientes'),
    path('generar-reporte-checkin/', views.generar_reporte_checkin_ajax, name='generar_reporte_checkin'),
    path('generar-reporte-checkin-psicologo/', views.generar_reporte_checkin_psicologo_ajax, name='generar_reporte_checkin_psicologo'),
    path('talleres/mejorando-relacion-hijo-adolescente/', views.taller_detalle_adolescente, name='taller_detalle_adolescente'),


]