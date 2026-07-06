from django.contrib import admin
from import_export.admin import ImportExportModelAdmin
from .models import * # ==========================================
# INLINES PARA EL PERFIL DEL PSICÓLOGO
# ==========================================
class HorarioInline(admin.TabularInline):
    model = HorarioPsicologo
    extra = 1

class DiaLibreInline(admin.TabularInline):
    model = DiaLibrePsicologo
    extra = 1

# ==========================================
# 1. PERFIL DEL PSICÓLOGO
# ==========================================
@admin.register(PerfilPsicologo)
class PerfilPsicologoAdmin(ImportExportModelAdmin):
    inlines = [HorarioInline, DiaLibreInline] 
    list_display = ('usuario', 'cedula_profesional', 'especialidad', 'genero', 'esta_activo')
    search_fields = ('usuario__first_name', 'usuario__email', 'cedula_profesional')
    list_filter = ('genero', 'esta_activo')
    # 🔥 MAGIA DE VELOCIDAD: Evita múltiples consultas a la tabla User
    list_select_related = ('usuario',)

# ==========================================
# 2. PERFIL DEL PACIENTE
# ==========================================
@admin.register(UsuarioPerfil)
class UsuarioPerfilAdmin(ImportExportModelAdmin):
    list_display = ('nombre', 'usuario', 'telefono', 'es_psicologo', 'psicologo_asignado')
    search_fields = ('nombre', 'usuario__email', 'telefono')
    list_filter = ('es_psicologo',)
    # 🔥 MAGIA DE VELOCIDAD
    list_select_related = ('usuario', 'psicologo_asignado', 'psicologo_asignado__usuario')

# ==========================================
# 3. CITAS
# ==========================================
@admin.register(Cita)
class CitaAdmin(ImportExportModelAdmin):
    list_display = (
        'id', 'paciente', 'psicologo', 'fecha', 'hora', 
        'estado', 'motivo', 'estado_animo', 'enlace_meet', 'id_evento_google'
    )
    search_fields = ('paciente__first_name', 'paciente__email', 'psicologo__usuario__first_name', 'id_evento_google')
    list_filter = ('estado', 'fecha', 'psicologo')
    readonly_fields = ('fecha_creacion',)
    # 🔥 MAGIA DE VELOCIDAD: Esta era la tabla que seguro más se trababa
    list_select_related = ('paciente', 'psicologo', 'psicologo__usuario')

# ==========================================
# 4. HISTORIAL CLÍNICO (EXPEDIENTE)
# ==========================================
@admin.register(HistorialClinico)
class HistorialClinicoAdmin(ImportExportModelAdmin):
    list_display = ('paciente', 'psicologo', 'fecha_registro')
    search_fields = ('paciente__first_name', 'psicologo__usuario__first_name')
    list_filter = ('fecha_registro', 'psicologo')
    # 🔥 MAGIA DE VELOCIDAD
    list_select_related = ('paciente', 'psicologo', 'psicologo__usuario')

# ==========================================
# 5. CUESTIONARIO INICIAL
# ==========================================
@admin.register(CuestionarioRegistro)
class CuestionarioRegistroAdmin(ImportExportModelAdmin):
    list_display = ('paciente', 'flujo_elegido', 'fecha_completado')
    search_fields = ('paciente__first_name', 'paciente__email')
    list_filter = ('flujo_elegido', 'fecha_completado')
    # 🔥 MAGIA DE VELOCIDAD
    list_select_related = ('paciente',)

# ==========================================
# 6. DÍAS FESTIVOS
# ==========================================
@admin.register(DiaFestivo)
class DiaFestivoAdmin(ImportExportModelAdmin):
    list_display = ('fecha', 'motivo')
    search_fields = ('motivo',)
    list_filter = ('fecha',)

# ==========================================
# 7. TALLERES E INSCRIPCIONES
# ==========================================
@admin.register(Taller)
class TallerAdmin(ImportExportModelAdmin):
    list_display = ('nombre', 'tipo', 'fecha', 'hora', 'cupo_maximo', 'cupos_disponibles')
    list_filter = ('tipo', 'fecha')
    search_fields = ('nombre',)
    list_select_related = ('psicologo',)

@admin.register(InscripcionTaller)
class InscripcionTallerAdmin(ImportExportModelAdmin):
    list_display = ('paciente', 'taller', 'fecha_inscripcion')
    list_filter = ('taller__tipo', 'taller__fecha')
    # 🔥 MAGIA DE VELOCIDAD
    list_select_related = ('paciente', 'taller')

# ==========================================
# 8. CHAT P2P (PACIENTE - DOCTOR)
# ==========================================
@admin.register(MensajeChat)
class MensajeChatAdmin(ImportExportModelAdmin):
    list_display = ('remitente', 'destinatario', 'fecha_envio', 'leido')
    search_fields = ('remitente__first_name', 'destinatario__first_name', 'contenido')
    list_filter = ('leido', 'fecha_envio')
    # 🔥 MAGIA DE VELOCIDAD
    list_select_related = ('remitente', 'destinatario')

# ==========================================
# 9. PRENSA Y BLOG HOPE
# ==========================================
@admin.register(ArticuloPrensa)
class ArticuloPrensaAdmin(ImportExportModelAdmin):
    list_display = ('titulo', 'fecha_publicacion', 'publicado')
    search_fields = ('titulo', 'resumen')
    list_filter = ('publicado', 'fecha_publicacion')
    prepopulated_fields = {'slug': ('titulo',)} 

# ==========================================
# 10. INTELIGENCIA OPERATIVA (KPIs y Alertas)
# ==========================================
@admin.register(MetricaDiaria)
class MetricaDiariaAdmin(ImportExportModelAdmin):
    list_display = ('fecha', 'nuevos_pacientes_semana', 'citas_completadas_mes', 'focos_rojos_activos')

@admin.register(NotificacionSistema)
class NotificacionSistemaAdmin(ImportExportModelAdmin):
    list_display = ('titulo', 'tipo', 'fecha_creacion', 'leida')
    list_filter = ('tipo', 'leida')

# ==========================================
# REGISTROS EXTRA
# ==========================================
@admin.register(HorarioPsicologo)
class HorarioPsicologoAdmin(ImportExportModelAdmin):
    list_display = ('psicologo', 'mes', 'semana', 'dia_semana', 'turno', 'es_descanso')
    list_filter = ('psicologo', 'es_descanso', 'turno')
    # 🔥 MAGIA DE VELOCIDAD
    list_select_related = ('psicologo', 'psicologo__usuario')

@admin.register(DiaLibrePsicologo)
class DiaLibrePsicologoAdmin(ImportExportModelAdmin):
    list_display = ('psicologo', 'fecha', 'motivo')
    list_filter = ('fecha', 'psicologo')
    # 🔥 MAGIA DE VELOCIDAD
    list_select_related = ('psicologo', 'psicologo__usuario')

@admin.register(EventoAuditoria)
class EventoAuditoriaAdmin(ImportExportModelAdmin):
    list_display = ('timestamp', 'usuario', 'accion')
    list_filter = ('accion', 'timestamp')
    search_fields = ('usuario__username', 'accion')
    # 🔥 MAGIA DE VELOCIDAD
    list_select_related = ('usuario',)

@admin.register(PreferenciasUsuario)
class PreferenciasUsuarioAdmin(ImportExportModelAdmin):
    list_display = ('user', 'notificaciones_activadas', 'ultima_conexion')
    list_filter = ('notificaciones_activadas',)
    # 🔥 MAGIA DE VELOCIDAD
    list_select_related = ('user',)

# 🔥 Aquí cambiamos admin.ModelAdmin por ImportExportModelAdmin
@admin.register(RegistroTallerPublico)
class RegistroTallerPublicoAdmin(ImportExportModelAdmin):
    list_display = ('nombre', 'correo', 'telefono', 'taller_seleccionado', 'fecha_registro')
    search_fields = ('nombre', 'correo', 'telefono', 'taller_seleccionado')
    list_filter = ('taller_seleccionado', 'fecha_registro')
    readonly_fields = ('fecha_registro',)