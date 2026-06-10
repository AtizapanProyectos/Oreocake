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
    foto = models.ImageField(upload_to='fotos_doctores/', blank=True, null=True, verbose_name="Foto de Perfil")
    cv_breve = models.TextField(blank=True, null=True, verbose_name="Breve CV o Enfoque Clínico")
    
    def __str__(self):
        return f"Psicólogo/a: {self.usuario.first_name} ({self.genero})"

# ==========================================
# 2. PERFIL DEL PACIENTE
# ==========================================
class UsuarioPerfil(models.Model):
    usuario = models.OneToOneField(User, on_delete=models.CASCADE, related_name='perfil', null=True, blank=True)
    nombre = models.CharField(max_length=100)
    es_psicologo = models.BooleanField(default=False)
    telefono = models.CharField(max_length=20, blank=True, null=True)
    telefono_emergencia = models.CharField(max_length=20, blank=True, null=True, verbose_name="Teléfono de Emergencia")
    es_padre = models.BooleanField(default=False, verbose_name="¿Es padre/madre de familia?")
    psicologo_asignado = models.ForeignKey(PerfilPsicologo, on_delete=models.SET_NULL, null=True, blank=True, related_name='pacientes_asignados', verbose_name="Psicólogo Asignado")

    historia_clinica = models.TextField(blank=True, null=True, verbose_name="1. Cómo llega el paciente (Historia Clínica)")
    focos_rojos = models.TextField(blank=True, null=True, verbose_name="🚨 Focos Rojos / Alertas")
    recommendaciones_generales = models.TextField(blank=True, null=True, verbose_name="4. Recomendaciones Generales")
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

    class Meta:
        unique_together = [['psicologo', 'fecha', 'hora']]

# ==========================================
# 4. HISTORIAL CLÍNICO
# ==========================================
class HistorialClinico(models.Model):
    paciente = models.ForeignKey(User, on_delete=models.CASCADE, related_name='historiales_clinicos')
    psicologo = models.ForeignKey(PerfilPsicologo, on_delete=models.SET_NULL, null=True, related_name='notas_creadas')
    cita = models.OneToOneField(Cita, on_delete=models.SET_NULL, null=True, blank=True, related_name='nota_clinica')
    como_llega = models.TextField(blank=True, null=True, verbose_name="1. ¿Cómo llega el paciente?")
    notas_sesion = models.TextField(verbose_name="2. Notas privadas del desarrollo")
    aprendizaje_paciente = models.TextField(blank=True, null=True, verbose_name="3. ¿Qué te llevas de esta sesión?")
    como_se_va = models.TextField(blank=True, null=True, verbose_name="4. Cierre y Alta de sesión")
    recomendaciones = models.TextField(blank=True, null=True, verbose_name="5. Recomendaciones generales")
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
# ==========================================
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
    psicologo = models.ForeignKey(PerfilPsicologo, on_delete=models.SET_NULL, null=True, blank=True, related_name='talleres_impartidos')
    enlace_meet = models.URLField(blank=True, null=True, verbose_name="Enlace de Google Meet")
    
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
# 7. NUEVOS MODELOS: HORARIOS POR MES/SEMANA (JUNIO)
# ==========================================
class HorarioPsicologo(models.Model):
    DIAS_SEMANA = [
        (0, 'Lunes'), (1, 'Martes'), (2, 'Miércoles'),
        (3, 'Jueves'), (4, 'Viernes'), (5, 'Sábado'), (6, 'Domingo')
    ]
    SEMANAS_MES = [
        (1, 'Semana 1'), (2, 'Semana 2'), (3, 'Semana 3'),
        (4, 'Semana 4'), (5, 'Semana 5')
    ]
    TURNOS = [
        ('matutino', 'Turno Matutino (8:00 am - 4:00 pm)'),
        ('vespertino', 'Turno Vespertino (1:00 pm - 9:00 pm)'),
    ]

    psicologo = models.ForeignKey(PerfilPsicologo, on_delete=models.CASCADE, related_name='horarios')
    mes = models.DateField(null=True, blank=True, help_text="Usa el día 1 del mes")
    semana = models.IntegerField(choices=SEMANAS_MES, null=True, blank=True)
    dia_semana = models.IntegerField(choices=DIAS_SEMANA)
    
    es_descanso = models.BooleanField(default=False, verbose_name="¿Es día de Descanso?")
    
    # Nuevo selector para no escribir mamadas a mano
    turno = models.CharField(max_length=20, choices=TURNOS, blank=True, null=True, help_text="Selecciona el turno si trabaja este día")

    # Estos quedan ocultos o automáticos, se llenan solos al guardar
    hora_inicio = models.TimeField(blank=True, null=True, editable=False)
    hora_fin = models.TimeField(blank=True, null=True, editable=False)
    hora_comida_inicio = models.TimeField(blank=True, null=True, editable=False)
    hora_comida_fin = models.TimeField(blank=True, null=True, editable=False)

    class Meta:
        unique_together = [['psicologo', 'mes', 'semana', 'dia_semana']]

    def save(self, *args, **kwargs):
        from datetime import time
        
        # Si es descanso, limpiamos todo el horario automáticamente
        if self.es_descanso:
            self.turno = None
            self.hora_inicio = None
            self.hora_fin = None
            self.hora_comida_inicio = None
            self.hora_comida_fin = None
        elif self.turno:
            # Diccionario con las reglas exactas de comida de tu PDF de Junio
            # Buscamos coincidencias por nombre en mayúsculas/minúsculas
            nombre_doc = self.psicologo.usuario.first_name.upper() if self.psicologo.usuario.first_name else ""
            
            if self.turno == 'matutino':
                self.hora_inicio = time(8, 0)
                self.hora_fin = time(16, 0)
                
                # Asignación de comida según el PDF
                if "ABRAHAM" in nombre_doc or "CLAUDIA" in nombre_doc:
                    self.hora_comida_inicio = time(13, 0)
                    self.hora_comida_fin = time(14, 0)
                else: # Sarahi o Gonzalo
                    self.hora_comida_inicio = time(14, 0)
                    self.hora_comida_fin = time(15, 0)
                    
            elif self.turno == 'vespertino':
                self.hora_inicio = time(13, 0)
                self.hora_fin = time(21, 0)
                
                # Asignación de comida según el PDF
                if "GWEYNETH" in nombre_doc or "MIGUEL" in nombre_doc:
                    self.hora_comida_inicio = time(14, 0)
                    self.hora_comida_fin = time(15, 0)
                else: # Michelle o Christopher
                    self.hora_comida_inicio = time(15, 0)
                    self.hora_comida_fin = time(16, 0)

        super().save(*args, **kwargs)

    def __str__(self):
        mes_str = self.mes.strftime("%B %Y") if self.mes else "Sin Mes"
        estado = "DESCANSO" if self.es_descanso else f"Turno {self.turno}"
        return f"[{mes_str} - Sem {self.semana}] {self.psicologo.usuario.first_name} - {self.get_dia_semana_display()}: {estado}"



        
class DiaLibrePsicologo(models.Model):
    psicologo = models.ForeignKey(PerfilPsicologo, on_delete=models.CASCADE, related_name='dias_libres')
    fecha = models.DateField()
    motivo = models.CharField(max_length=100, blank=True, null=True)

    class Meta:
        unique_together = [['psicologo', 'fecha']]

    def __str__(self):
        return f"{self.psicologo.usuario.first_name} - {self.fecha}"


# ============================================================
# NUEVOS MODELOS PARA PANEL ADMIN (Inteligencia Operativa)
# ============================================================

class NotificacionSistema(models.Model):
    TIPO_CHOICES = [
        ('nueva_cita', 'Nueva cita agendada'),
        ('cancelacion', 'Cita cancelada'),
        ('foco_rojo', 'Foco rojo detectado'),
        ('recordatorio', 'Recordatorio'),
    ]
    tipo = models.CharField(max_length=50, choices=TIPO_CHOICES)
    titulo = models.CharField(max_length=200)
    mensaje = models.TextField()
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    leida = models.BooleanField(default=False)
    destinatarios = models.ManyToManyField(User, related_name='notificaciones_recibidas', blank=True)

    def __str__(self):
        return f"{self.tipo} - {self.fecha_creacion.strftime('%d/%m %H:%M')}"


class MetricaDiaria(models.Model):
    fecha = models.DateField(unique=True)
    nuevos_pacientes_semana = models.PositiveIntegerField(default=0)
    citas_completadas_mes = models.PositiveIntegerField(default=0)
    citas_canceladas_mes = models.PositiveIntegerField(default=0)
    talleres_activos = models.PositiveIntegerField(default=0)
    focos_rojos_activos = models.PositiveIntegerField(default=0)

    def __str__(self):
        return f"Métrica {self.fecha}"


class EventoAuditoria(models.Model):
    usuario = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    accion = models.CharField(max_length=100)
    detalles = models.JSONField(default=dict)
    timestamp = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.timestamp} - {self.usuario} - {self.accion}"


class PreferenciasUsuario(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='preferencias_notif')
    notificaciones_activadas = models.BooleanField(default=False)
    ultima_conexion = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user.username} - Notif: {self.notificaciones_activadas}"



# ==========================================
# 8. CHAT P2P (PACIENTE - DOCTOR)
# ==========================================
class MensajeChat(models.Model):
    remitente = models.ForeignKey(User, on_delete=models.CASCADE, related_name='mensajes_enviados')
    destinatario = models.ForeignKey(User, on_delete=models.CASCADE, related_name='mensajes_recibidos')
    contenido = models.TextField(verbose_name="Contenido del mensaje")
    fecha_envio = models.DateTimeField(auto_now_add=True, verbose_name="Fecha y hora de envío")
    leido = models.BooleanField(default=False, verbose_name="¿Fue leído?")

    class Meta:
        # Ordenamos por fecha de envío para que el chat se vea cronológico (los más viejos arriba, los nuevos abajo)
        ordering = ['fecha_envio']
        indexes = [
            # Estos índices hacen que las consultas del chat sean ultra rápidas en la base de datos
            models.Index(fields=['remitente', 'destinatario']),
            models.Index(fields=['leido']),
        ]

    def __str__(self):
        estado = "Leído" if self.leido else "No leído"
        return f"De {self.remitente.first_name} para {self.destinatario.first_name} ({estado})"



# ==========================================
# 9. PRENSA Y BLOG HOPE
# ==========================================
class ArticuloPrensa(models.Model):
    titulo = models.CharField(max_length=250, verbose_name="Título del Artículo")
    slug = models.SlugField(unique=True, max_length=250, help_text="URL amigable (ej: la-salud-mental-en-mexico)")
    resumen = models.TextField(help_text="Texto breve para la tarjeta de inicio")
    contenido = models.TextField(help_text="Contenido completo del artículo (Soporta etiquetas HTML como <h3>, <p>, <ul>)")
    imagen = models.ImageField(upload_to='prensa/', blank=True, null=True, verbose_name="Imagen de Portada")
    imagen_url_externa = models.URLField(blank=True, null=True, help_text="O usa un enlace directo de Unsplash/Google")
    fecha_publicacion = models.DateField(auto_now_add=True)
    publicado = models.BooleanField(default=True, verbose_name="¿Mostrar en la página?")

    class Meta:
        ordering = ['-fecha_publicacion']

    def __str__(self):
        return self.titulo
    
    @property
    def get_imagen(self):
        if self.imagen:
            return self.imagen.url
        elif self.imagen_url_externa:
            return self.imagen_url_externa
        return '/static/img/default_blog.jpg' # Imagen por defecto si olvidas poner una