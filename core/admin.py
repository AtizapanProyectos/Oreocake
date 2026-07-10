from django import forms
from django.contrib import admin
from django.utils import timezone
from import_export.admin import ImportExportModelAdmin
from django.db.models import Count
from .models import *

# =================================================================
# 1. FORMULARIOS (Python lo lee primero y ya sabe qué es)
# =================================================================
class EsquemaHorarioForm(forms.ModelForm):
    DIAS_SEMANA_CHOICES = [
        (0, 'Lunes'), (1, 'Martes'), (2, 'Miércoles'),
        (3, 'Jueves'), (4, 'Viernes'), (5, 'Sábado'), (6, 'Domingo'),
    ]
    
    dias_descanso_checkboxes = forms.MultipleChoiceField(
        choices=DIAS_SEMANA_CHOICES,
        widget=forms.CheckboxSelectMultiple,
        required=False,
        label="Días de descanso fijos",
        help_text="Selecciona los días en que el psicólogo NO dará consultas."
    )

    class Meta:
        model = EsquemaHorarioPsicologo
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk and self.instance.dias_descanso:
            self.fields['dias_descanso_checkboxes'].initial = [str(d) for d in self.instance.dias_descanso]

    def save(self, commit=True):
        instance = super().save(commit=False)
        dias_seleccionados = self.cleaned_data.get('dias_descanso_checkboxes', [])
        instance.dias_descanso = [int(d) for d in dias_seleccionados]
        if commit:
            instance.save()
        return instance


# =================================================================
# 2. INLINES (Usan los formularios que ya se cargaron arriba)
# =================================================================
class EsquemaHorarioInline(admin.StackedInline):
    model = EsquemaHorarioPsicologo
    form = EsquemaHorarioForm
    extra = 0
    
    fieldsets = (
        (None, {
            'fields': (('fecha_inicio', 'fecha_fin'), 'activo')
        }),
        ('Jornada y Comida', {
            'fields': (('hora_inicio', 'hora_fin'), ('hora_comida_inicio', 'hora_comida_fin'))
        }),
        ('Descansos', {
            'fields': ('dias_descanso_checkboxes',)
        }),
    )

class DiaLibreInline(admin.TabularInline):
    model = DiaLibrePsicologo
    extra = 0


# =================================================================
# 3. MODEL ADMINS (El registro final del panel)
# =================================================================
@admin.register(PerfilPsicologo)
class PerfilPsicologoAdmin(ImportExportModelAdmin):
    inlines = [EsquemaHorarioInline, DiaLibreInline] 
    list_display = ('usuario', 'cedula_profesional', 'especialidad', 'genero', 'esta_activo')
    search_fields = ('usuario__first_name', 'usuario__last_name', 'cedula_profesional')
    list_filter = ('genero', 'esta_activo')
    list_per_page = 20

# =================================================================
# 4. REGISTRO DEL RESTO DE TUS MODELOS
# =================================================================
# Si tenías clases de configuración personalizadas para estos modelos, 
# puedes pegarlas aquí abajo sin problema. Si no, con esto quedan registrados:
admin.site.register(UsuarioPerfil)
admin.site.register(Cita)
admin.site.register(HistorialClinico)
admin.site.register(CuestionarioRegistro)
admin.site.register(DiaFestivo)
admin.site.register(Taller)
admin.site.register(InscripcionTaller)
admin.site.register(NotificacionSistema)
admin.site.register(MetricaDiaria)
admin.site.register(EventoAuditoria)
admin.site.register(PreferenciasUsuario)
admin.site.register(MensajeChat)
admin.site.register(ArticuloPrensa)
admin.site.register(RegistroTallerPublico)