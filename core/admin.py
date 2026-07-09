from django.contrib import admin
from import_export.admin import ImportExportModelAdmin
from .models import *
from django.db.models import Count

# ==========================================
# INLINES PARA EL PERFIL DEL PSICÓLOGO
# ==========================================

# 1. El nuevo inline para la configuración de Horario Fijo
class HorarioFijoInline(admin.StackedInline):
    model = HorarioFijoPsicologo
    can_delete = False
    verbose_name_plural = 'Configuración de Horario Fijo'

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('psicologo__usuario')

# 2. El inline de días libres (se queda igual)
class DiaLibreInline(admin.TabularInline):
    model = DiaLibrePsicologo
    fk_name = 'psicologo'
    extra = 1

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('psicologo__usuario')

# ==========================================
# 1. PERFIL DEL PSICÓLOGO
# ==========================================
@admin.register(PerfilPsicologo)
class PerfilPsicologoAdmin(ImportExportModelAdmin):
    # 🔥 AQUÍ QUITAMOS EL HORARIO INLINE VIEJO Y PONEMOS EL NUEVO FIJO
    inlines = [HorarioFijoInline, DiaLibreInline] 
    list_display = ('usuario', 'cedula_profesional', 'especialidad', 'genero', 'esta_activo')
    search_fields = ('usuario__first_name', 'usuario__last_name', 'cedula_profesional')
    list_filter = ('genero', 'esta_activo')
    list_per_page = 20

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
    autocomplete_fields = ('usuario', 'psicologo_asignado')
    list_per_page = 50
    show_full_result_count = False

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
    autocomplete_fields = ('paciente', 'psicologo')
    list_per_page = 50
    show_full_result_count = False

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
    autocomplete_fields = ('paciente', 'psicologo')
    list_per_page = 50
    show_full_result_count = False

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
    autocomplete_fields = ('paciente',)
    list_per_page = 50
    show_full_result_count = False

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
    # Usamos 'cupos_disponibles_annotated' en lugar de la propiedad del modelo
    list_display = ('nombre', 'tipo', 'fecha', 'hora', 'cupo_maximo', 'cupos_disponibles_annotated')
    list_filter = ('tipo', 'fecha')
    search_fields = ('nombre',)
    list_select_related = ('psicologo',)
    autocomplete_fields = ('psicologo',)
    list_per_page = 50
    show_full_result_count = False

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        # Hacemos que la base de datos cuente todo en un solo viaje limpio
        return queryset.annotate(total_inscritos=Count('inscripciones'))

    @admin.display(description='Cupos Disponibles')
    def cupos_disponibles_annotated(self, obj):
        # Evita consultas extra usando el valor precalculado por la anotación
        return max(0, obj.cupo_maximo - obj.total_inscritos)

@admin.register(InscripcionTaller)
class InscripcionTallerAdmin(ImportExportModelAdmin):
    list_display = ('paciente', 'taller', 'fecha_inscripcion')
    list_filter = ('taller__tipo', 'taller__fecha')
    # 🔥 MAGIA DE VELOCIDAD
    list_select_related = ('paciente', 'taller')
    autocomplete_fields = ('paciente', 'taller')
    list_per_page = 50
    show_full_result_count = False

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
    autocomplete_fields = ('remitente', 'destinatario')
    list_per_page = 50
    show_full_result_count = False

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


@admin.register(DiaLibrePsicologo)
class DiaLibrePsicologoAdmin(ImportExportModelAdmin):
    list_display = ('psicologo', 'fecha', 'motivo')
    list_filter = ('fecha', 'psicologo')
    # 🔥 MAGIA DE VELOCIDAD
    list_select_related = ('psicologo', 'psicologo__usuario')
    autocomplete_fields = ('psicologo',)
    list_per_page = 50
    show_full_result_count = False

@admin.register(EventoAuditoria)
class EventoAuditoriaAdmin(ImportExportModelAdmin):
    list_display = ('timestamp', 'usuario', 'accion')
    list_filter = ('accion', 'timestamp')
    search_fields = ('usuario__username', 'accion')
    # 🔥 MAGIA DE VELOCIDAD
    list_select_related = ('usuario',)
    autocomplete_fields = ('usuario',)
    list_per_page = 50
    show_full_result_count = False

@admin.register(PreferenciasUsuario)
class PreferenciasUsuarioAdmin(ImportExportModelAdmin):
    list_display = ('user', 'notificaciones_activadas', 'ultima_conexion')
    list_filter = ('notificaciones_activadas',)
    # 🔥 MAGIA DE VELOCIDAD
    list_select_related = ('user',)
    autocomplete_fields = ('user',)
    list_per_page = 50
    show_full_result_count = False

# 🔥 Aquí cambiamos admin.ModelAdmin por ImportExportModelAdmin
@admin.register(RegistroTallerPublico)
class RegistroTallerPublicoAdmin(ImportExportModelAdmin):
    list_display = ('nombre', 'correo', 'telefono', 'taller_seleccionado', 'fecha_registro')
    search_fields = ('nombre', 'correo', 'telefono', 'taller_seleccionado')
    list_filter = ('taller_seleccionado', 'fecha_registro')
    readonly_fields = ('fecha_registro',)
    list_per_page = 50
    show_full_result_count = False