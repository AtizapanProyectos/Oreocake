"""Calendario acordado de Co-Visión. No crea citas ni envía notificaciones."""
from datetime import date, time, timedelta

from django.core.management.base import CommandError
from django.db.models import Q

from .models import BloqueoAgendaPsicologo, Cita, PerfilPsicologo, ReglaCoVision


MOTIVO = 'Co-Visión · noviembre–diciembre 2026'
# Identidades verificadas; usar username admite los correos de acceso históricos.
GRUPOS = (
    (date(2026, 11, 24), 8, ('psi.alevi@espaciohope.com', 'psi.clopez@espaciohope.com', 'psi.ecastañeda@espaciohope.com')),
    (date(2026, 11, 24), 9, ('psi.storres@espaciohope.com', 'psi.glopez@espaciohope.com')),
    (date(2026, 11, 25), 19, ('psi.guribe@espaciohope.com', 'psi.mceballos@espaciohope.com')),
    (date(2026, 11, 25), 20, ('psi.cceja@espaciohope.com',)),
    (date(2026, 11, 26), 18, ('psi.aorozco@espaciohope.com', 'psi.jromero@espaciohope.com', 'psi.msanchez@espaciohope.com')),
)
# Karla (psi.kecheverria): pertenece al grupo del jueves 18–19.
# PENDIENTE: entra una hora más tarde ese día. No cargar bloqueo hasta resolverlo.


def calendario_acordado():
    """Resuelve todas las cuentas antes de permitir cualquier escritura."""
    calendario = []
    for primera_fecha, hora, cuentas in GRUPOS:
        for cuenta in cuentas:
            perfiles = list(PerfilPsicologo.objects.filter(
                Q(usuario__username__iexact=cuenta) | Q(usuario__email__iexact=cuenta),
            ).select_related('usuario'))
            if len(perfiles) != 1:
                raise CommandError(f'La cuenta {cuenta} debe identificar exactamente un psicólogo; encontrados: {len(perfiles)}.')
            for dias in (0, 14, 28):
                calendario.append(BloqueoAgendaPsicologo(
                    psicologo=perfiles[0], fecha=primera_fecha + timedelta(days=dias),
                    hora_inicio=time(hora), hora_fin=time(hora + 1), motivo=MOTIVO,
                ))
    return calendario


def reglas_globales_anteriores():
    """Solo las dos reglas originales, nunca otras reglas del administrador."""
    return ReglaCoVision.objects.filter(
        Q(dia_semana=1, hora_inicio=time(8), fecha_ancla=date(2026, 10, 27))
        | Q(dia_semana=2, hora_inicio=time(19), fecha_ancla=date(2026, 10, 28)),
        nombre='Co-Visión', duracion_minutos=90, cada_n_dias=15,
    )


def citas_en_conflicto(bloqueo):
    """Citas de una hora que se solapan; conserva también reservas pendientes."""
    desde = (bloqueo.hora_inicio.hour * 60 + bloqueo.hora_inicio.minute) - 60
    candidatas = Cita.objects.filter(
        psicologo_id=bloqueo.psicologo_id, fecha=bloqueo.fecha, hora__lt=bloqueo.hora_fin,
    ).exclude(estado__in=['Cancelada', 'No asistió'])
    return [c for c in candidatas if c.hora.hour * 60 + c.hora.minute > desde]
