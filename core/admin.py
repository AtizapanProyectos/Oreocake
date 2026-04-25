from django.contrib import admin
from import_export.admin import ImportExportModelAdmin
from .models import *
from django.db import models
from django.forms import Textarea
from django.utils.html import format_html
from .models import PaginaInicioHTML, CatalogoImagen
from django_ace import AceWidget
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
@admin.register(Cita)
class CitaAdmin(ImportExportModelAdmin):
    list_display = ('paciente', 'psicologo', 'fecha', 'hora', 'estado')
    search_fields = ('paciente__first_name', 'paciente__email', 'psicologo__usuario__first_name')
    list_filter = ('estado', 'fecha', 'psicologo')

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



# ==========================================
# 8. CMS LIBRE (ADMIN)
# ==========================================
# ==========================================
# 8. CMS LIBRE (ADMIN)
# ==========================================
# ==========================================
# 8. CMS LIBRE (ADMIN)
# ==========================================
@admin.register(PaginaInicioHTML)
class PaginaInicioHTMLAdmin(admin.ModelAdmin):
    list_display = ('__str__',)
    
    # 🔥 EL TRUCO PRO: Integramos el editor de código real 🔥
    formfield_overrides = {
        models.TextField: {'widget': AceWidget(
            mode='html',          # Le decimos que es HTML para que coloree las etiquetas
            theme='monokai',      # Tema oscuro clásico (estilo VS Code)
            width="100%",         # Ancho completo
            height="70vh",        # Altura gigante (70% de tu pantalla)
            wordwrap=True,        # Ajusta el texto para que no tengas que hacer scroll horizontal
            showprintmargin=False # Quita la línea vertical de margen
        )},
    }

@admin.register(CatalogoImagen)
class CatalogoImagenAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'miniatura_visual', 'ruta_para_copiar', 'fecha_subida')
    readonly_fields = ('ruta_para_copiar',)
    
    # Esto dibuja una miniatura chiquita en la tabla para que vea qué foto es
    def miniatura_visual(self, obj):
        if obj.imagen:
            return format_html('<img src="{}" width="60" style="border-radius: 8px; box-shadow: 0 2px 5px rgba(0,0,0,0.2);" />', obj.imagen.url)
        return "Sin imagen"
    miniatura_visual.short_description = "Vista Previa"

    # Esto le da la ruta exacta que debe copiar
    def ruta_para_copiar(self, obj):
        if obj.imagen:
            # Le mostramos la ruta en texto seleccionable
            return format_html('<input type="text" readonly value="{}" style="width: 250px; padding: 5px; border-radius: 5px; border: 1px solid #ccc;">', obj.imagen.url)
        return "Sube la imagen y guarda para ver la ruta"
    ruta_para_copiar.short_description = "Ruta para tu HTML (Cópiala)"