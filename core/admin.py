from django.contrib import admin
from import_export.admin import ImportExportModelAdmin
from .models import *
# ==========================================
# 1. PERFIL DEL PSICÓLOGO
# ==========================================
@admin.register(PerfilPsicologo)
class PerfilPsicologoAdmin(ImportExportModelAdmin):
    list_display = ('usuario', 'cedula_profesional', 'genero', 'esta_activo')
    search_fields = ('usuario__first_name', 'usuario__email', 'cedula_profesional')
    list_filter = ('genero', 'esta_activo')

# ==========================================
# 2. PERFIL DEL PACIENTE
# ==========================================
@admin.register(UsuarioPerfil)
class UsuarioPerfilAdmin(ImportExportModelAdmin):
    list_display = ('nombre', 'usuario', 'telefono', 'es_psicologo', 'psicologo_asignado')
    search_fields = ('nombre', 'usuario__email', 'telefono')
    list_filter = ('es_psicologo',)

# ==========================================
# 3. CITAS
# ==========================================
# ==========================================
# 3. CITAS
# ==========================================
@admin.register(Cita)
class CitaAdmin(ImportExportModelAdmin):
    # 🔥 AHORA SÍ, TODOS LOS CAMPOS EN LA TABLA 🔥
    list_display = (
        'id', 
        'paciente', 
        'psicologo', 
        'fecha', 
        'hora', 
        'estado', 
        'motivo', 
        'estado_animo', 
        'enlace_meet', 
        'id_evento_google'
    )
    search_fields = ('paciente__first_name', 'paciente__email', 'psicologo__usuario__first_name', 'id_evento_google')
    list_filter = ('estado', 'fecha', 'psicologo')
    
    # Para que también puedas ver la fecha de creación cuando entres a editar una cita específica
    readonly_fields = ('fecha_creacion',)
# ==========================================
# 4. HISTORIAL CLÍNICO (EXPEDIENTE)
# ==========================================
@admin.register(HistorialClinico)
class HistorialClinicoAdmin(ImportExportModelAdmin):
    list_display = ('paciente', 'psicologo', 'fecha_registro')
    search_fields = ('paciente__first_name', 'psicologo__usuario__first_name')
    list_filter = ('fecha_registro', 'psicologo')

# ==========================================
# 5. CUESTIONARIO INICIAL
# ==========================================
@admin.register(CuestionarioRegistro)
class CuestionarioRegistroAdmin(ImportExportModelAdmin):
    list_display = ('paciente', 'flujo_elegido', 'fecha_completado')
    search_fields = ('paciente__first_name', 'paciente__email')
    list_filter = ('flujo_elegido', 'fecha_completado')

# ==========================================
# 6. DÍAS FESTIVOS (BLOQUEOS DE CALENDARIO)
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

@admin.register(InscripcionTaller)
class InscripcionTallerAdmin(ImportExportModelAdmin):
    list_display = ('paciente', 'taller', 'fecha_inscripcion')
    list_filter = ('taller__tipo', 'taller__fecha')