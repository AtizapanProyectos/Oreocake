from django.db import models
from django.contrib.auth.models import User

# ==========================================
# 1. PERFIL DEL PSICÓLOGO (DOCTORES)
# ==========================================

class PerfilPsicologo(models.Model):
    usuario = models.OneToOneField(User, on_delete=models.CASCADE, related_name='perfil_psicologo')
    cedula_profesional = models.CharField(max_length=50, unique=True, verbose_name="Cédula Profesional")
    genero = models.CharField(max_length=20, choices=[('Hombre', 'Hombre'), ('Mujer', 'Mujer')], verbose_name="Género")
    especialidad = models.CharField(max_length=150, blank=True, null=True, verbose_name="Especialidad (Ej. Terapia Cognitiva)")
    esta_activo = models.BooleanField(default=True, verbose_name="Aceptando nuevos pacientes")
    
    # 🔥 Ahora sí, para subir tu propia imagen al servidor 🔥
    foto = models.ImageField(upload_to='fotos_doctores/', blank=True, null=True, verbose_name="Foto de Perfil")
    cv_breve = models.TextField(blank=True, null=True, verbose_name="Breve CV o Enfoque Clínico")
    
    def __str__(self):
        return f"Psicólogo/a: {self.usuario.first_name} ({self.genero})"
# ==========================================
# 2. PERFIL DEL PACIENTE (CON EXPEDIENTE MAESTRO)
# ==========================================
class UsuarioPerfil(models.Model):
    usuario = models.OneToOneField(User, on_delete=models.CASCADE, related_name='perfil', null=True, blank=True)
    nombre = models.CharField(max_length=100)
    es_psicologo = models.BooleanField(default=False)
    telefono = models.CharField(max_length=20, blank=True, null=True)
    telefono_emergencia = models.CharField(max_length=20, blank=True, null=True, verbose_name="Teléfono de Emergencia")
    es_padre = models.BooleanField(default=False, verbose_name="¿Es padre/madre de familia?")
    
    psicologo_asignado = models.ForeignKey(PerfilPsicologo, on_delete=models.SET_NULL, null=True, blank=True, related_name='pacientes_asignados', verbose_name="Psicólogo Asignado")

    # 🔥 NUEVOS CAMPOS DEL EXPEDIENTE CLÍNICO GLOBAL 🔥
    historia_clinica = models.TextField(blank=True, null=True, verbose_name="1. Cómo llega el paciente (Historia Clínica)")
    focos_rojos = models.TextField(blank=True, null=True, verbose_name="🚨 Focos Rojos / Alertas")
    recomendaciones_generales = models.TextField(blank=True, null=True, verbose_name="4. Recomendaciones Generales")
    notas_alta = models.TextField(blank=True, null=True, verbose_name="3. Cómo se va (El Alta)")

    def __str__(self):
        return self.nombre


# ==========================================
# 3. CITAS
# ==========================================
class Cita(models.Model):
    paciente = models.ForeignKey(User, on_delete=models.CASCADE, related_name='citas_como_paciente')
    psicologo = models.ForeignKey(PerfilPsicologo, on_delete=models.CASCADE, related_name='citas_agendadas', null=True)
    fecha = models.DateField(verbose_name='Fecha de la sesión')
    hora = models.TimeField(verbose_name='Hora de la sesión')
    motivo = models.CharField(max_length=150, verbose_name='Motivo de consulta', default='Primera sesión')
    estado_animo = models.CharField(max_length=50, blank=True, null=True, verbose_name='Estado de ánimo')
    estado = models.CharField(max_length=50, default='Confirmada', choices=[
        ('Pendiente', 'Pendiente'), ('Confirmada', 'Confirmada'), ('Completada', 'Completada'), ('Cancelada', 'Cancelada'),
    ])
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    enlace_meet = models.URLField(blank=True, null=True)
    id_evento_google = models.CharField(max_length=255, blank=True, null=True)
    modalidad = models.CharField(max_length=50, default='En línea', choices=[('En línea', 'En línea'), ('Presencial', 'Presencial')])
    tipo_sesion = models.CharField(
        max_length=50,
        default='individual',
        choices=[('individual', 'Individual'), ('pareja', 'En Pareja')],
        verbose_name='Tipo de Sesión'
    )

    def __str__(self):
        return f"Cita: {self.paciente.first_name} con {self.psicologo.usuario.first_name} el {self.fecha.strftime('%d/%m')}"
# ==========================================
# 4. HISTORIAL CLÍNICO (BITÁCORA DE CADA SESIÓN)
# ==========================================

class HistorialClinico(models.Model):
    paciente = models.ForeignKey(User, on_delete=models.CASCADE, related_name='historiales_clinicos')
    psicologo = models.ForeignKey(PerfilPsicologo, on_delete=models.SET_NULL, null=True, related_name='notas_creadas')
    cita = models.OneToOneField(Cita, on_delete=models.SET_NULL, null=True, blank=True, related_name='nota_clinica')
    
    # 🔥 LA LÍNEA DEL TIEMPO DE LA SESIÓN 🔥
    como_llega = models.TextField(blank=True, null=True, verbose_name="1. ¿Cómo llega el paciente?")
    notas_sesion = models.TextField(verbose_name="2. Notas privadas del desarrollo")
    aprendizaje_paciente = models.TextField(blank=True, null=True, verbose_name="3. ¿Qué te llevas de esta sesión?")
    como_se_va = models.TextField(blank=True, null=True, verbose_name="4. Cierre y Alta de sesión")
    recomendaciones = models.TextField(blank=True, null=True, verbose_name="5. Recomendaciones generales")
    
    # 🔥 LOS ARCHIVOS DE EVIDENCIA 🔥
    archivo_adjunto = models.FileField(upload_to='bitacoras_adjuntos/', blank=True, null=True, verbose_name="Documento Escaneado o Foto")
    transcripcion_meet = models.FileField(upload_to='transcripciones_meet/', blank=True, null=True, verbose_name="Transcripción de Meet")
    
    diagnostico_temporal = models.CharField(max_length=250, blank=True, null=True)
    fecha_registro = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Sesión de {self.paciente.first_name} - {self.fecha_registro.strftime('%d/%m/%Y')}"
# ==========================================
# 5. CUESTIONARIO Y EXTRAS
# ==========================================
class CuestionarioRegistro(models.Model):
    paciente = models.OneToOneField(User, on_delete=models.CASCADE, related_name='cuestionario_inicial')
    flujo_elegido = models.CharField(max_length=50, verbose_name="Tipo de Terapia")
    respuestas = models.JSONField(verbose_name="Respuestas", default=dict)
    fecha_completado = models.DateTimeField(auto_now_add=True)

class DiaFestivo(models.Model):
    fecha = models.DateField(unique=True, verbose_name="Día bloqueado")
    motivo = models.CharField(max_length=150, blank=True, null=True)

    def __str__(self):
        return f"{self.fecha.strftime('%d/%m/%Y')} - {self.motivo}"



# ==========================================
# 6. TALLERES Y GRUPOS

class Taller(models.Model):

    TIPO_CHOICES = [
    ('padres', 'Taller para Padres de Familia'),
    ('pareja', 'Taller para Parejas'),
    ('grupal', 'Taller Grupal'),
    ('autoestima', 'Taller de Autoestima'),
    ('profesional', 'Eco-visión'),
    ]
    nombre = models.CharField(max_length=200, verbose_name="Nombre del Programa")
    descripcion = models.TextField(verbose_name="Descripción Breve")
    tipo = models.CharField(max_length=50, choices=TIPO_CHOICES, verbose_name="Categoría")
    fecha = models.DateField(verbose_name="Fecha")
    hora = models.TimeField(verbose_name="Hora")
    cupo_maximo = models.PositiveIntegerField(default=20, verbose_name="Cupo Máximo")
    
    # 🔥 NUEVO: Asignar al doctor y poner enlace Meet 🔥
    psicologo = models.ForeignKey(PerfilPsicologo, on_delete=models.SET_NULL, null=True, blank=True, related_name='talleres_impartidos')
    enlace_meet = models.URLField(blank=True, null=True, verbose_name="Enlace de Google Meet para la sala grupal")
    
    @property
    def cupos_disponibles(self):
        return max(0, self.cupo_maximo - self.inscripciones.count())

class InscripcionTaller(models.Model):
    paciente = models.ForeignKey(User, on_delete=models.CASCADE, related_name='talleres_inscritos')
    taller = models.ForeignKey(Taller, on_delete=models.CASCADE, related_name='inscripciones')
    fecha_inscripcion = models.DateTimeField(auto_now_add=True)
    class Meta:
        unique_together = ('paciente', 'taller')

# ==========================================