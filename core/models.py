import calendar
from datetime import date

from django.db import models
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.utils import timezone


def _sumar_meses(fecha, meses):
    """Suma 'meses' meses a una fecha, sin depender de librerías externas
    (dateutil). Ajusta automáticamente al último día del mes si hace falta
    (ej. 31 de enero + 1 mes -> 28/29 de febrero)."""
    mes_index = fecha.month - 1 + meses
    anio = fecha.year + mes_index // 12
    mes = mes_index % 12 + 1
    dia = min(fecha.day, calendar.monthrange(anio, mes)[1])
    return date(anio, mes, dia)

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

    # Modalidades de atención habilitadas por psicólogo
    atiende_individual = models.BooleanField(default=True, verbose_name="Atiende Terapia Individual", db_index=True) # 🔥 OPTIMIZADO
    atiende_pareja = models.BooleanField(default=False, verbose_name="Atiende Terapia de Pareja", db_index=True) # 🔥 OPTIMIZADO
    atiende_familiar = models.BooleanField(default=False, verbose_name="Atiende Terapia Familiar", db_index=True) # 🔥 OPTIMIZADO

    class Meta:
        indexes = [
            # Filtro combinado crucial para la búsqueda global de disponibilidad por estado, género y modalidad
            models.Index(fields=['esta_activo', 'genero']),
            models.Index(fields=['esta_activo', 'atiende_individual']),
            models.Index(fields=['esta_activo', 'atiende_pareja']),
            models.Index(fields=['esta_activo', 'atiende_familiar']),
        ]

# En models.py -> Class PerfilPsicologo
    def __str__(self):
        try:
            # Usamos hasattr para evitar el crash del OneToOneField
            if hasattr(self, 'usuario') and self.usuario:
                nombre = self.usuario.first_name if self.usuario.first_name else self.usuario.username
            else:
                nombre = "Sin Usuario"
        except Exception:
            nombre = f"ID: {self.pk}"
        return f"Psicólogo/a: {nombre} ({self.genero})"

    def esquema_vigente(self, fecha=None):
        """
        Devuelve el EsquemaHorarioPsicologo vigente para este psicólogo en la
        fecha indicada (hoy por defecto), o None si no tiene uno configurado.
        """
        from django.utils import timezone
        fecha = fecha or timezone.localdate()
        
        # Busca si ya precargamos los esquemas (para evitar doble consulta)
        esquemas = getattr(self, '_esquemas_rango', None)
        if esquemas is not None:
            for esquema in esquemas:
                if esquema.fecha_inicio <= fecha <= esquema.fecha_fin:
                    return esquema
            return None
            
        # Si no están precargados, hace la consulta a la base de datos
        return self.esquemas_horarios.filter(
            activo=True, fecha_inicio__lte=fecha, fecha_fin__gte=fecha
        ).order_by('-fecha_inicio').first()





class EsquemaHorarioPsicologo(models.Model):
    psicologo = models.ForeignKey(PerfilPsicologo, on_delete=models.CASCADE, related_name='esquemas_horarios')
    fecha_inicio = models.DateField(default=timezone.localdate, db_index=True)
    fecha_fin = models.DateField(db_index=True)
    
    # Jornada base
    hora_inicio = models.TimeField(verbose_name="Inicio Jornada")
    hora_fin = models.TimeField(verbose_name="Fin Jornada")
    
    # 🔥 HORA DE COMIDA (Opcional pero completamente integrada)
    hora_comida_inicio = models.TimeField(null=True, blank=True, verbose_name="Inicio Comida")
    hora_comida_fin = models.TimeField(null=True, blank=True, verbose_name="Fin Comida")
    
    dias_descanso = models.JSONField(default=list, help_text="Ej: [0, 1] para Lunes y Martes")
    activo = models.BooleanField(default=True, db_index=True)

    class Meta:
        verbose_name = "Esquema de Horario"
        verbose_name_plural = "Esquemas de Horarios"

    def clean(self):
        # Validación extra: Si pones inicio de comida, debes poner fin de comida
        if bool(self.hora_comida_inicio) != bool(self.hora_comida_fin):
            raise ValidationError("Si defines una hora de comida, debes rellenar tanto el inicio como el fin.")
            
        if self.hora_comida_inicio and self.hora_comida_fin:
            if self.hora_comida_inicio >= self.hora_comida_fin:
                raise ValidationError({"hora_comida_fin": "El fin de la comida debe ser posterior al inicio."})
            if self.hora_comida_inicio < self.hora_inicio or self.hora_comida_fin > self.hora_fin:
                raise ValidationError("La hora de comida debe estar dentro del rango de la jornada laboral.")

# ==========================================
# 2. PERFIL DEL PACIENTE
# ==========================================
class UsuarioPerfil(models.Model):
    usuario = models.OneToOneField(User, on_delete=models.CASCADE, related_name='perfil', null=True, blank=True)
    nombre = models.CharField(max_length=100, db_index=True) # 🔥 OPTIMIZADO: Para búsquedas por nombre en el admin
    es_psicologo = models.BooleanField(default=False, db_index=True) # 🔥 OPTIMIZADO: Filtro frecuente en list_filter
    telefono = models.CharField(max_length=20, blank=True, null=True, db_index=True) # 🔥 OPTIMIZADO: search_fields frecuente
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
    fecha = models.DateField(verbose_name='Fecha de la sesión', db_index=True) # 🔥 OPTIMIZADO
    hora = models.TimeField(verbose_name='Hora de la sesión')
    motivo = models.CharField(max_length=150, verbose_name='Motivo de consulta', default='Primera sesión')
    estado_animo = models.CharField(max_length=50, blank=True, null=True, verbose_name='Estado de ánimo')
    estado = models.CharField(max_length=50, default='Confirmada', choices=[
        ('Pendiente', 'Pendiente'), ('Confirmada', 'Confirmada'), ('Completada', 'Completada'), ('Cancelada', 'Cancelada'),
    ], db_index=True) # 🔥 OPTIMIZADO
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    enlace_meet = models.URLField(blank=True, null=True)
    # En models.py -> class Cita
    id_evento_google = models.CharField(max_length=200, blank=True, null=True, db_index=True) # 200 * 4 = 800 bytes (¡Seguro!) 🔥 OPTIMIZADO: Búsquedas API Google Calendar
    modalidad = models.CharField(max_length=50, default='En línea', choices=[('En línea', 'En línea'), ('Presencial', 'Presencial')])
    tipo_sesion = models.CharField(max_length=50, default='individual', choices=[
        ('individual', 'Individual'),
        ('pareja', 'En Pareja'),
        ('familiar', 'Terapia Familiar'),
    ])
    integrantes_familia = models.PositiveSmallIntegerField(blank=True, null=True, verbose_name="Número de integrantes (Terapia Familiar)")

    class Meta:
        unique_together = [['psicologo', 'fecha', 'hora']]
        indexes = [
            models.Index(fields=['fecha', 'estado']),
            models.Index(fields=['psicologo', 'fecha', 'estado']),
            models.Index(fields=['paciente', 'estado']), # 🔥 OPTIMIZADO: Historial del paciente rápido
        ]

    def __str__(self):
        try:
            # Verificamos de forma segura que exista el psicólogo y su usuario
            if self.psicologo and hasattr(self.psicologo, 'usuario') and self.psicologo.usuario:
                nombre_psicologo = self.psicologo.usuario.first_name if self.psicologo.usuario.first_name else self.psicologo.usuario.username
            else:
                nombre_psicologo = "Sin asignar"
        except Exception:
            nombre_psicologo = "Sin asignar"
            
        try:
            nombre_paciente = self.paciente.first_name if self.paciente else "Desconocido"
        except Exception:
            nombre_paciente = "Desconocido"
            
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
    fecha_registro = models.DateTimeField(auto_now_add=True, db_index=True) # 🔥 OPTIMIZADO: list_filter rápido

    def __str__(self):
        fecha_str = self.fecha_registro.strftime('%d/%m/%Y') if self.fecha_registro else "Borrador"
        try:
            paciente_str = self.paciente.first_name if self.paciente else "Sin paciente"
        except Exception:
            paciente_str = "Sin paciente"
        return f"Sesión de {paciente_str} - {fecha_str}"

# ==========================================
# 5. CUESTIONARIO Y EXTRAS
# ==========================================
class CuestionarioRegistro(models.Model):
    paciente = models.OneToOneField(User, on_delete=models.CASCADE, related_name='cuestionario_inicial')
    flujo_elegido = models.CharField(max_length=50, verbose_name="Tipo de Terapia", db_index=True) # 🔥 OPTIMIZADO
    respuestas = models.JSONField(verbose_name="Respuestas", default=dict)
    fecha_completado = models.DateTimeField(auto_now_add=True, db_index=True) # 🔥 OPTIMIZADO

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
    nombre = models.CharField(max_length=200, verbose_name="Nombre del Programa", db_index=True) # 🔥 OPTIMIZADO
    descripcion = models.TextField(verbose_name="Descripción Breve")
    tipo = models.CharField(max_length=50, choices=TIPO_CHOICES, verbose_name="Categoría", db_index=True) # 🔥 OPTIMIZADO
    fecha = models.DateField(verbose_name="Fecha", db_index=True) # 🔥 OPTIMIZADO
    hora = models.TimeField(verbose_name="Hora")
    cupo_maximo = models.PositiveIntegerField(default=20, verbose_name="Cupo Máximo")
    psicologo = models.ForeignKey(PerfilPsicologo, on_delete=models.SET_NULL, null=True, blank=True, related_name='talleres_impartidos')
    enlace_meet = models.URLField(blank=True, null=True, verbose_name="Enlace de Google Meet")
    
    @property
    def cupos_disponibles(self):
        return max(0, self.cupo_maximo - self.inscripciones.count())

    def __str__(self):
        return f"{self.nombre} ({self.get_tipo_display()})"

class InscripcionTaller(models.Model):
    paciente = models.ForeignKey(User, on_delete=models.CASCADE, related_name='talleres_inscritos')
    taller = models.ForeignKey(Taller, on_delete=models.CASCADE, related_name='inscripciones')
    fecha_inscripcion = models.DateTimeField(auto_now_add=True, db_index=True) # 🔥 OPTIMIZADO
    
    class Meta:
        unique_together = ('paciente', 'taller')

    def __str__(self):
        try:
            paciente_str = self.paciente.first_name if self.paciente else "Desconocido"
        except Exception:
            paciente_str = "Desconocido"
        try:
            taller_str = self.taller.nombre if self.taller else "Taller vacío"
        except Exception:
            taller_str = "Taller vacío"
        return f"Inscripción: {paciente_str} a {taller_str}"

# ==========================================
# 7. HORARIOS Y DÍAS LIBRES
# ==========================================
# Elimina el modelo viejo HorarioPsicologo y crea este:
class DiaLibrePsicologo(models.Model):
    psicologo = models.ForeignKey(PerfilPsicologo, on_delete=models.CASCADE, related_name='dias_libres')
    fecha = models.DateField(verbose_name="Día libre")
    motivo = models.CharField(max_length=200, blank=True, null=True, verbose_name="Motivo (Opcional)")

    class Meta:
        unique_together = ['psicologo', 'fecha']
        verbose_name = "Día Libre"
        verbose_name_plural = "Días Libres"

    def __str__(self):
        return f"{self.psicologo} - {self.fecha.strftime('%d/%m/%Y')}"

class HorarioFijoPsicologo(models.Model):
    """
    Horario de trabajo de un psicólogo, vigente durante un periodo de tiempo
    (por defecto 3 meses desde que se crea — su "caducidad"). Cuando el
    periodo termina, deja de generar citas disponibles automáticamente hasta
    que se le cargue (o se renueve) un horario nuevo. Los registros viejos
    NO se borran: quedan como historial de horarios pasados.

    Nada aquí se calcula a partir del nombre del psicólogo: las horas de
    inicio/fin/comida y los días de descanso se capturan explícitamente al
    dar de alta el registro (en el admin).
    """
    DIAS_SEMANA = [
        (0, 'Lunes'), (1, 'Martes'), (2, 'Miércoles'),
        (3, 'Jueves'), (4, 'Viernes'), (5, 'Sábado'), (6, 'Domingo'),
    ]

    # 🔥 "Caducidad" del horario: si no se especifica fecha_fin, se calcula
    # automáticamente a partir de fecha_inicio + DURACION_DEFAULT_MESES.
    DURACION_DEFAULT_MESES = 3

    psicologo = models.ForeignKey(
        PerfilPsicologo, on_delete=models.CASCADE, related_name='horarios_fijos',
        verbose_name="Psicólogo/a"
    )

    # 👇 AQUÍ ESTÁ LA CORRECCIÓN: Agregamos null=True, blank=True 👇
    hora_inicio = models.TimeField(verbose_name="Hora de inicio de jornada", null=True, blank=True)
    hora_fin = models.TimeField(verbose_name="Hora de fin de jornada", null=True, blank=True)
    hora_comida_inicio = models.TimeField(verbose_name="Inicio de comida", null=True, blank=True)
    hora_comida_fin = models.TimeField(verbose_name="Fin de comida", null=True, blank=True)

    # Lista de enteros 0-6 (ver DIAS_SEMANA). JSONField en vez de dos campos
    # fijos para permitir 1, 2 o más días de descanso según cada psicólogo.
    dias_descanso = models.JSONField(
        default=list, verbose_name="Días de descanso fijos",
        help_text="Días de la semana en que el psicólogo NO atiende (ej. Lunes y Martes)."
    )

    fecha_inicio = models.DateField(default=timezone.localdate, db_index=True, verbose_name="Vigente desde")
    fecha_fin = models.DateField(
        blank=True, null=True, db_index=True, verbose_name="Vigente hasta (caducidad)",
        help_text="Si se deja en blanco, se calcula automáticamente a 3 meses de la fecha de inicio."
    )
    activo = models.BooleanField(default=True, db_index=True, verbose_name="Activo")

    class Meta:
        verbose_name = "Horario Fijo"
        verbose_name_plural = "Horarios Fijos"
        ordering = ['-fecha_inicio']
        indexes = [
            models.Index(fields=['psicologo', 'activo', 'fecha_inicio', 'fecha_fin']),
        ]

    def _calcular_fecha_fin_default(self):
        base = self.fecha_inicio or timezone.localdate()
        return _sumar_meses(base, self.DURACION_DEFAULT_MESES)

    def clean(self):
        errores = {}

        if self.hora_inicio and self.hora_fin and self.hora_inicio >= self.hora_fin:
            errores['hora_fin'] = "La hora de fin debe ser posterior a la hora de inicio."

        if self.hora_comida_inicio and self.hora_comida_fin and self.hora_comida_inicio >= self.hora_comida_fin:
            errores['hora_comida_fin'] = "La hora de fin de comida debe ser posterior a la de inicio."

        if not isinstance(self.dias_descanso, list) or not all(
            isinstance(d, int) and 0 <= d <= 6 for d in self.dias_descanso
        ):
            errores['dias_descanso'] = "Debe ser una lista de días 0 (Lunes) a 6 (Domingo)."

        fecha_fin_efectiva = self.fecha_fin or self._calcular_fecha_fin_default()
        if self.fecha_inicio and fecha_fin_efectiva <= self.fecha_inicio:
            errores['fecha_fin'] = "La fecha de fin (caducidad) debe ser posterior a la fecha de inicio."

        # Evita traslapes con otro horario activo del mismo psicólogo.
        if self.psicologo_id and self.fecha_inicio:
            otros = HorarioFijoPsicologo.objects.filter(
                psicologo_id=self.psicologo_id, activo=True
            ).exclude(pk=self.pk)
            for otro in otros:
                otro_fin = otro.fecha_fin or otro._calcular_fecha_fin_default()
                if self.fecha_inicio <= otro_fin and otro.fecha_inicio <= fecha_fin_efectiva:
                    errores['fecha_inicio'] = (
                        f"Se traslapa con un horario ya existente de este psicólogo "
                        f"({otro.fecha_inicio} a {otro_fin})."
                    )
                    break

        if errores:
            raise ValidationError(errores)

    def save(self, *args, **kwargs):
        if not self.fecha_inicio:
            self.fecha_inicio = timezone.localdate()
        if not self.fecha_fin:
            self.fecha_fin = self._calcular_fecha_fin_default()
        super().save(*args, **kwargs)

    def esta_vigente(self, fecha=None):
        fecha = fecha or timezone.localdate()
        return bool(self.activo and self.fecha_inicio <= fecha <= (self.fecha_fin or fecha))

    def dias_descanso_display(self):
        nombres = dict(self.DIAS_SEMANA)
        return ", ".join(nombres.get(d, "?") for d in sorted(self.dias_descanso or []))
    dias_descanso_display.short_description = "Días de descanso"

    def __str__(self):
        return f"Horario de {self.psicologo} ({self.fecha_inicio} a {self.fecha_fin})"

# ==========================================
# OTROS MODELOS
# ==========================================
class NotificacionSistema(models.Model):
    TIPO_CHOICES = [('nueva_cita', 'Nueva cita'), ('cancelacion', 'Cita cancelada'), ('foco_rojo', 'Foco rojo'), ('recordatorio', 'Recordatorio')]
    tipo = models.CharField(max_length=50, choices=TIPO_CHOICES, db_index=True) # 🔥 OPTIMIZADO
    titulo = models.CharField(max_length=200)
    mensaje = models.TextField()
    fecha_creacion = models.DateTimeField(auto_now_add=True, db_index=True) # 🔥 OPTIMIZADO: Para ordenar por más reciente
    leida = models.BooleanField(default=False, db_index=True) # 🔥 OPTIMIZADO: Panel de pendientes rápido
    destinatarios = models.ManyToManyField(User, related_name='notificaciones_recibidas', blank=True)

    class Meta:
        ordering = ['-fecha_creacion']
        indexes = [
            models.Index(fields=['leida', 'fecha_creacion']), # 🔥 Combo letal para contadores de no leídas
        ]

    def __str__(self):
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
    accion = models.CharField(max_length=100, db_index=True) # 🔥 OPTIMIZADO
    detalles = models.JSONField(default=dict)
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True) # 🔥 OPTIMIZADO

    class Meta:
        ordering = ['-timestamp']

    def __str__(self): 
        usr = self.usuario.username if self.usuario else "Sistema"
        return f"{self.timestamp} - {usr} - {self.accion}"

class PreferenciasUsuario(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='preferencias_notif')
    notificaciones_activadas = models.BooleanField(default=False, db_index=True) # 🔥 OPTIMIZADO
    ultima_conexion = models.DateTimeField(auto_now=True)
    def __str__(self): return f"{self.user.username} - Notif: {self.notificaciones_activadas}"

class MensajeChat(models.Model):
    remitente = models.ForeignKey(User, on_delete=models.CASCADE, related_name='mensajes_enviados')
    destinatario = models.ForeignKey(User, on_delete=models.CASCADE, related_name='mensajes_recibidos')
    contenido = models.TextField(verbose_name="Contenido del mensaje")
    fecha_envio = models.DateTimeField(auto_now_add=True, verbose_name="Fecha y hora de envío", db_index=True) # 🔥 OPTIMIZADO
    leido = models.BooleanField(default=False, verbose_name="¿Fue leído?", db_index=True) # 🔥 OPTIMIZADO

    class Meta:
        ordering = ['fecha_envio']
        indexes = [
            models.Index(fields=['remitente', 'destinatario']),
            models.Index(fields=['destinatario', 'leido']), # 🔥 Crucial para cargar alertas de "mensajes nuevos" por usuario
            models.Index(fields=['destinatario', 'fecha_envio']), # 🔥 Para ordenar y recuperar hilos rápido
        ]

    def __str__(self):
        try:
            rem = self.remitente.first_name if self.remitente else "Desconocido"
        except Exception:
            rem = "Desconocido"
        try:
            dest = self.destinatario.first_name if self.destinatario else "Desconocido"
        except Exception:
            dest = "Desconocido"
        estado = "Leído" if self.leido else "No leído"
        return f"De {rem} para {dest} ({estado})"

class ArticuloPrensa(models.Model):
    titulo = models.CharField(max_length=150, verbose_name="Título del Artículo", db_index=True) #
    slug = models.SlugField(unique=True, max_length=150) #[cite: 1]
    resumen = models.TextField() #[cite: 1]
    contenido = models.TextField() #[cite: 1]
    imagen = models.ImageField(upload_to='prensa/', blank=True, null=True) #[cite: 1]
    imagen_url_externa = models.URLField(blank=True, null=True) #[cite: 1]
    fecha_publicacion = models.DateField(auto_now_add=True, db_index=True) #[cite: 1]
    publicado = models.BooleanField(default=True, db_index=True) #[cite: 1]

    class Meta: 
        ordering = ['-fecha_publicacion'] #[cite: 1]
        indexes = [
            models.Index(fields=['publicado', 'fecha_publicacion']), #[cite: 1]
        ]
        
    def __str__(self): return self.titulo #[cite: 1]

    # 🔥 AGREGAR ESTO: Método para renderizar la ruta correcta
    def get_imagen(self):
        # 1. Prioriza la imagen subida localmente (tu volumen en Docker)
        if self.imagen:
            return self.imagen.url
        # 2. Si no hay archivo físico, usa la URL externa
        elif self.imagen_url_externa:
            return self.imagen_url_externa
        # 3. Fallback: Si no tiene ninguna, muestra una imagen por defecto para no romper el diseño
        return '/static/img/default-blog.jpg'

class RegistroTallerPublico(models.Model):
    nombre = models.CharField(max_length=100, db_index=True)
    telefono = models.CharField(max_length=20, db_index=True)
    correo = models.EmailField(max_length=120) # 120 * 4 = 480 bytes
    taller_seleccionado = models.CharField(max_length=120) # 120 * 4 = 480 bytes
    fecha_registro = models.DateTimeField(auto_now_add=True, db_index=True)
    
    class Meta:
        unique_together = ['correo', 'taller_seleccionado'] # 👈 Ahora la suma de bytes es totalmente segura
        verbose_name = "Registro de Taller"
        verbose_name_plural = "Registros de Talleres"
        indexes = [
            models.Index(fields=['taller_seleccionado', 'fecha_registro']),
        ]
        
    def __str__(self): return f"{self.nombre} - {self.taller_seleccionado}"