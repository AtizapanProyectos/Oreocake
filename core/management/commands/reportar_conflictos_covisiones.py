from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import Cita, ReglaCoVision


class Command(BaseCommand):
    help = (
        'Lista las próximas sesiones de Co-Visión (quincenal: martes 8:00 am y '
        'miércoles 7:00 pm, duran 1.5 h) y las citas ya agendadas que se solapan '
        'con ellas. SOLO REPORTA: no modifica ni cancela ninguna cita.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dias',
            type=int,
            default=90,
            help='Horizonte de búsqueda en días a partir de hoy (default: 90).',
        )
        parser.add_argument(
            '--ocurrencias',
            type=int,
            default=None,
            help='Máximo de ocurrencias a listar POR regla (default: sin límite dentro del horizonte).',
        )

    def _log(self, msg, style_func=None):
        out = style_func(msg) if style_func else msg
        try:
            self.stdout.write(out)
        except UnicodeEncodeError:
            try:
                self.stdout.write(out.encode('ascii', errors='replace').decode('ascii'))
            except Exception:
                pass

    def handle(self, *args, **options):
        if hasattr(self.stdout, 'reconfigure'):
            pass

        dias = options.get('dias', 90)
        max_ocurrencias = options.get('ocurrencias')

        hoy = timezone.localdate()
        fecha_limite = hoy + timedelta(days=dias)

        self._log("=" * 80)
        self._log("🔄 CO-VISIÓN: Próximas sesiones quincenales y conflictos con citas agendadas")
        self._log(f"   Ventana analizada: {hoy.strftime('%d/%m/%Y')} -> {fecha_limite.strftime('%d/%m/%Y')}")
        self._log("=" * 80)

        reglas = ReglaCoVision.objects.filter(activo=True)
        if not reglas:
            self._log("⚠️ No hay reglas de Co-Visión activas. Actívalas desde el admin Django.", self.style.WARNING)
            return

        # 1. Calculamos las ocurrencias de cada regla dentro de la ventana
        eventos = []  # (fecha, hora_inicio, regla)
        for regla in reglas:
            paso = max(1, regla.cada_n_dias)
            k = 0
            encontradas = 0
            while True:
                fecha_ciclo = regla.fecha_ancla + timedelta(days=paso * k)
                if fecha_ciclo > fecha_limite:
                    break
                fecha_oc = regla._ajustar_a_dia_semana(fecha_ciclo)
                if hoy <= fecha_oc <= fecha_limite:
                    eventos.append((fecha_oc, regla))
                    encontradas += 1
                    if max_ocurrencias and encontradas >= max_ocurrencias:
                        break
                k += 1
                if k > 400:  # guarda de seguridad
                    break

        if not eventos:
            self._log("✨ No hay sesiones de Co-Visión en la ventana analizada.")
            return

        eventos.sort(key=lambda par: (par[0], par[1].hora_inicio))

        DIAS_ES = ['Lunes', 'Martes', 'Miércoles', 'Jueves', 'Viernes', 'Sábado', 'Domingo']

        # 2. Para cada sesión, reportamos las citas que se solapan
        total_conflictos = 0
        for fecha_oc, regla in eventos:
            slots = regla.slots_bloqueados(fecha_oc)
            self._log("")
            self._log(
                f"🗓️  Co-Visión: {DIAS_ES[fecha_oc.weekday()]} {fecha_oc.strftime('%d/%m/%Y')} "
                f"{regla.hora_inicio.strftime('%H:%M')} ({regla.duracion_minutos} min)"
            )

            citas_solapadas = Cita.objects.filter(
                fecha=fecha_oc,
                hora__in=slots,
                estado__in=['Confirmada', 'Completada'],
            ).select_related('paciente', 'psicologo__usuario').order_by('hora')

            if not citas_solapadas.exists():
                self._log("   ✅ Sin citas agendadas en este horario. Slots ya bloqueados para nuevas reservas.")
                continue

            for cita in citas_solapadas:
                total_conflictos += 1
                pac = cita.paciente.get_full_name() or cita.paciente.username if cita.paciente else 'Desconocido'
                psi = cita.psicologo.usuario.get_full_name() if cita.psicologo and cita.psicologo.usuario else 'Sin psicólogo'
                self._log(
                    f"   ⚠️ SOLAPE — Cita #{cita.id} @ {cita.hora.strftime('%H:%M')} | "
                    f"Paciente: {pac} | Psicólogo: {psi} | Estado: {cita.estado}",
                    self.style.WARNING,
                )

        self._log("")
        self._log("=" * 80)
        if total_conflictos:
            self._log(
                f"📊 RESUMEN: {len(eventos)} sesiones de Co-Visión próximas | "
                f"{total_conflictos} cita(s) ya agendadas se solapan (reprogramar manualmente).",
                self.style.WARNING,
            )
        else:
            self._log(f"📊 RESUMEN: {len(eventos)} sesiones de Co-Visión próximas | 0 conflictos.")
        self._log("ℹ️ Este comando SOLO reporta: no modifica ni cancela citas.")
        self._log("=" * 80)
