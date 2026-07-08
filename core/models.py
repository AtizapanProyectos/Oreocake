from django.db import models
from django.contrib.auth.models import User

# ==========================================
# 1. PERFIL DEL PSICÓLOGO (DOCTORES)
# ==========================================
class PerfilPsicologo(models.Model):
    usuario = models.OneToOneField(User, on_delete=models.CASCADE, related_name='perfil_psicologo')
    cedula_profesional = models.CharField(max_length=50, unique=True, verbose_name="Cédula Profesional")
    genero = models.CharField(max_length=20, choices=[('Hombre', 'Hombre'), ('Mujer', 'Mujer')], verbose_name="Género", db_index=True)
    especialidad = models.CharField(max_length=150, blank=True, null=True, verbose_name="Especialidad")
    esta_activo = models.BooleanField(default=True, verbose_name="Aceptando nuevos pacientes", db_index=True)
    foto = models.ImageField(upload_to='fotos_doctores/', blank=True, null=True, verbose_name="Foto de Perfil")
    cv_breve = models.TextField(blank=True, null=True, verbose_name="Breve CV o Enfoque Clínico")

    # 🔥 NUEVO: Modalidades de atención habilitadas por psicólogo.
    # Permiten filtrar la búsqueda de disponibilidad según el tipo de sesión
    # solicitado por el paciente (individual / pareja / familiar).
    atiende_individual = models.BooleanField(default=True, verbose_name="Atiende Terapia Individual")
    atiende_pareja = models.BooleanField(default=False, verbose_name="Atiende Terapia de Pareja")
    atiende_familiar = models.BooleanField(default=False, verbose_name="Atiende Terapia Familiar")

    class Meta:
        indexes = [
            # 🔥 OPTIMIZACIÓN: acelera el filtro combinado usado en la búsqueda
            # global de disponibilidad (activos + género + modalidad).
            models.Index(fields=['esta_activo', 'genero']),
        ]

    def __str__(self):
        # 🔥 OPTIMIZACIÓN: Protección por si el usuario pierde el first_name
        nombre = self.usuario.first_name if self.usuario and self.usuario.first_name else self.usuario.username
        return f"Psicólogo/a: {nombre} ({self.genero})"

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
        return str(self.nombre) if self.nombre else "Usuario sin nombre"

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
    tipo_sesion = models.CharField(max_length=50, default='individual', choices=[
        ('individual', 'Individual'),
        ('pareja', 'En Pareja'),
        ('familiar', 'Terapia Familiar'),
    ])
    # 🔥 NUEVO: solo aplica cuando tipo_sesion == 'familiar'. Usado para el
    # cálculo de precio (base + $100 MXN por integrante adicional).
    integrantes_familia = models.PositiveSmallIntegerField(blank=True, null=True, verbose_name="Número de integrantes (Terapia Familiar)")

    class Meta:
        unique_together = [['psicologo', 'fecha', 'hora']]
        indexes = [
            # 🔥 OPTIMIZACIÓN: acelera las búsquedas de disponibilidad y de
            # "próxima cita" que filtran por fecha + estado constantemente.
            models.Index(fields=['fecha', 'estado']),
            models.Index(fields=['psicologo', 'fecha', 'estado']),
        ]

    def __str__(self):
        # 🔥 OPTIMIZACIÓN: Protegido contra atributos nulos
        nombre_psicologo = self.psicologo.usuario.first_name if (self.psicologo and self.psicologo.usuario) else "Sin asignar"
        nombre_paciente = self.paciente.first_name if self.paciente else "Desconocido"
        fecha_str = self.fecha.strftime('%d/%m') if self.fecha else "Sin fecha"
        return f"Cita: {nombre_paciente} con {nombre_psicologo} el {fecha_str}"

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
        # 🔥 AQUÍ ESTABA UN POSIBLE ERROR str(obj). Si no hay fecha aún, strftime explotaba.
        fecha_str = self.fecha_registro.strftime('%d/%m/%Y') if self.fecha_registro else "Borrador"
        paciente_str = self.paciente.first_name if self.paciente else "Sin paciente"
        return f"Sesión de {paciente_str} - {fecha_str}"

# ==========================================
# 5. CUESTIONARIO Y EXTRAS
# ==========================================
class CuestionarioRegistro(models.Model):
    paciente = models.OneToOneField(User, on_delete=models.CASCADE, related_name='cuestionario_inicial')
    flujo_elegido = models.CharField(max_length=50, verbose_name="Tipo de Terapia")
    respuestas = models.JSONField(verbose_name="Respuestas", default=dict)
    fecha_completado = models.DateTimeField(auto_now_add=True)

    # 🔥 NUEVO: Faltaba método str
    def __str__(self):
        paciente_str = self.paciente.first_name if self.paciente else "Desconocido"
        return f"Cuestionario: {paciente_str} ({self.flujo_elegido})"

class DiaFestivo(models.Model):
    fecha = models.DateField(unique=True, verbose_name="Día bloqueado")
    motivo = models.CharField(max_length=150, blank=True, null=True)

    def __str__(self):
        fecha_str = self.fecha.strftime('%d/%m/%Y') if self.fecha else "Sin fecha"
        return f"{fecha_str} - {self.motivo}"

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

    # 🔥 NUEVO: Faltaba método str
    def __str__(self):
        return f"{self.nombre} ({self.get_tipo_display()})"

class InscripcionTaller(models.Model):
    paciente = models.ForeignKey(User, on_delete=models.CASCADE, related_name='talleres_inscritos')
    taller = models.ForeignKey(Taller, on_delete=models.CASCADE, related_name='inscripciones')
    fecha_inscripcion = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        unique_together = ('paciente', 'taller')

    # 🔥 NUEVO: Faltaba método str
    def __str__(self):
        paciente_str = self.paciente.first_name if self.paciente else "Desconocido"
        taller_str = self.taller.nombre if self.taller else "Taller vacío"
        return f"Inscripción: {paciente_str} a {taller_str}"

# ==========================================
# 7. HORARIOS Y DÍAS LIBRES
# ==========================================
class HorarioPsicologo(models.Model):
    DIAS_SEMANA = [(0, 'Lunes'), (1, 'Martes'), (2, 'Miércoles'), (3, 'Jueves'), (4, 'Viernes'), (5, 'Sábado'), (6, 'Domingo')]
    SEMANAS_MES = [(1, 'Semana 1'), (2, 'Semana 2'), (3, 'Semana 3'), (4, 'Semana 4'), (5, 'Semana 5')]
    TURNOS = [('matutino', 'Turno Matutino (8:00 am - 4:00 pm)'), ('vespertino', 'Turno Vespertino (1:00 pm - 9:00 pm)')]

    psicologo = models.ForeignKey(PerfilPsicologo, on_delete=models.CASCADE, related_name='horarios')
    mes = models.DateField(null=True, blank=True, help_text="Usa el día 1 del mes")
    semana = models.IntegerField(choices=SEMANAS_MES, null=True, blank=True)
    dia_semana = models.IntegerField(choices=DIAS_SEMANA)
    es_descanso = models.BooleanField(default=False, verbose_name="¿Es día de Descanso?")
    turno = models.CharField(max_length=20, choices=TURNOS, blank=True, null=True)

    hora_inicio = models.TimeField(blank=True, null=True, editable=False)
    hora_fin = models.TimeField(blank=True, null=True, editable=False)
    hora_comida_inicio = models.TimeField(blank=True, null=True, editable=False)
    hora_comida_fin = models.TimeField(blank=True, null=True, editable=False)

    class Meta:
        unique_together = [['psicologo', 'mes', 'semana', 'dia_semana']]

    def save(self, *args, **kwargs):
        from datetime import time
        if self.es_descanso:
            self.turno = self.hora_inicio = self.hora_fin = self.hora_comida_inicio = self.hora_comida_fin = None
        elif self.turno and self.psicologo and self.psicologo.usuario:
            nombre_doc = self.psicologo.usuario.first_name.upper() if self.psicologo.usuario.first_name else ""
            if self.turno == 'matutino':
                self.hora_inicio, self.hora_fin = time(8, 0), time(16, 0)
                if "ABRAHAM" in nombre_doc or "CLAUDIA" in nombre_doc:
                    self.hora_comida_inicio, self.hora_comida_fin = time(13, 0), time(14, 0)
                else: 
                    self.hora_comida_inicio, self.hora_comida_fin = time(14, 0), time(15, 0)
            elif self.turno == 'vespertino':
                self.hora_inicio, self.hora_fin = time(13, 0), time(21, 0)
                if "GWEYNETH" in nombre_doc or "MIGUEL" in nombre_doc:
                    self.hora_comida_inicio, self.hora_comida_fin = time(14, 0), time(15, 0)
                else:
                    self.hora_comida_inicio, self.hora_comida_fin = time(15, 0), time(16, 0)
        super().save(*args, **kwargs)

    def __str__(self):
        mes_str = self.mes.strftime("%B %Y") if self.mes else "Sin Mes"
        estado = "DESCANSO" if self.es_descanso else f"Turno {self.turno}"
        doc_str = self.psicologo.usuario.first_name if (self.psicologo and self.psicologo.usuario) else "Psicólogo"
        return f"[{mes_str} - Sem {self.semana}] {doc_str} - {self.get_dia_semana_display()}: {estado}"

class DiaLibrePsicologo(models.Model):
    psicologo = models.ForeignKey(PerfilPsicologo, on_delete=models.CASCADE, related_name='dias_libres')
    fecha = models.DateField()
    motivo = models.CharField(max_length=100, blank=True, null=True)
    class Meta:
        unique_together = [['psicologo', 'fecha']]
    def __str__(self):
        doc = self.psicologo.usuario.first_name if (self.psicologo and self.psicologo.usuario) else "Doc"
        return f"{doc} - {self.fecha}"

# ==========================================
# OTROS MODELOS
# ==========================================
class NotificacionSistema(models.Model):
    TIPO_CHOICES = [('nueva_cita', 'Nueva cita'), ('cancelacion', 'Cita cancelada'), ('foco_rojo', 'Foco rojo'), ('recordatorio', 'Recordatorio')]
    tipo = models.CharField(max_length=50, choices=TIPO_CHOICES)
    titulo = models.CharField(max_length=200)
    mensaje = models.TextField()
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    leida = models.BooleanField(default=False)
    destinatarios = models.ManyToManyField(User, related_name='notificaciones_recibidas', blank=True)

    def __str__(self):
        # 🔥 AQUÍ HABÍA OTRO POSIBLE ERROR str(obj) con fechas auto_now_add
        fecha = self.fecha_creacion.strftime('%d/%m %H:%M') if self.fecha_creacion else "Ahora"
        return f"{self.tipo} - {fecha}"

class MetricaDiaria(models.Model):
    fecha = models.DateField(unique=True)
    nuevos_pacientes_semana = models.PositiveIntegerField(default=0)
    citas_completadas_mes = models.PositiveIntegerField(default=0)
    citas_canceladas_mes = models.PositiveIntegerField(default=0)
    talleres_activos = models.PositiveIntegerField(default=0)
    focos_rojos_activos = models.PositiveIntegerField(default=0)
    def __str__(self): return f"Métrica {self.fecha}"

class EventoAuditoria(models.Model):
    usuario = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    accion = models.CharField(max_length=100)
    detalles = models.JSONField(default=dict)
    timestamp = models.DateTimeField(auto_now_add=True)
    def __str__(self): 
        usr = self.usuario.username if self.usuario else "Sistema"
        return f"{self.timestamp} - {usr} - {self.accion}"

class PreferenciasUsuario(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='preferencias_notif')
    notificaciones_activadas = models.BooleanField(default=False)
    ultima_conexion = models.DateTimeField(auto_now=True)
    def __str__(self): return f"{self.user.username} - Notif: {self.notificaciones_activadas}"

class MensajeChat(models.Model):
    remitente = models.ForeignKey(User, on_delete=models.CASCADE, related_name='mensajes_enviados')
    destinatario = models.ForeignKey(User, on_delete=models.CASCADE, related_name='mensajes_recibidos')
    contenido = models.TextField(verbose_name="Contenido del mensaje")
    fecha_envio = models.DateTimeField(auto_now_add=True, verbose_name="Fecha y hora de envío")
    leido = models.BooleanField(default=False, verbose_name="¿Fue leído?")

    class Meta:
        ordering = ['fecha_envio']
        indexes = [models.Index(fields=['remitente', 'destinatario']), models.Index(fields=['leido'])]

    def __str__(self):
        rem = self.remitente.first_name if self.remitente else "Desconocido"
        dest = self.destinatario.first_name if self.destinatario else "Desconocido"
        estado = "Leído" if self.leido else "No leído"
        return f"De {rem} para {dest} ({estado})"

class ArticuloPrensa(models.Model):
    titulo = models.CharField(max_length=250, verbose_name="Título del Artículo")
    slug = models.SlugField(unique=True, max_length=250)
    resumen = models.TextField()
    contenido = models.TextField()
    imagen = models.ImageField(upload_to='prensa/', blank=True, null=True)
    imagen_url_externa = models.URLField(blank=True, null=True)
    fecha_publicacion = models.DateField(auto_now_add=True)
    publicado = models.BooleanField(default=True)
    class Meta: ordering = ['-fecha_publicacion']
    def __str__(self): return self.titulo

class RegistroTallerPublico(models.Model):
    nombre = models.CharField(max_length=150)
    telefono = models.CharField(max_length=20)
    correo = models.EmailField(unique=True)
    taller_seleccionado = models.CharField(max_length=200)
    fecha_registro = models.DateTimeField(auto_now_add=True)
    class Meta:
        unique_together = ['correo', 'taller_seleccionado']
        verbose_name = "Registro de Taller"
        verbose_name_plural = "Registros de Talleres"
    def __str__(self): return f"{self.nombre} - {self.taller_seleccionado}"