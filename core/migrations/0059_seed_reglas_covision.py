from django.db import migrations


def sembrar_reglas_covision(apps, schema_editor):
    """
    🔥 NUEVO: Siembra las dos reglas quincenales de Co-Visión:
      - Martes 8:00 am  (ancla: martes 27/10/2026)
      - Miércoles 7:00 pm (ancla: miércoles 28/10/2026)
    Idempotente: solo crea las reglas si aún no existen.
    """
    ReglaCoVision = apps.get_model('core', 'ReglaCoVision')

    import datetime
    if not ReglaCoVision.objects.filter(dia_semana=1, hora_inicio=datetime.time(8, 0)).exists():
        ReglaCoVision.objects.create(
            nombre='Co-Visión',
            dia_semana=1,  # Martes
            hora_inicio=datetime.time(8, 0),
            duracion_minutos=90,
            fecha_ancla=datetime.date(2026, 10, 27),
            cada_n_dias=15,
            activo=True,
        )

    if not ReglaCoVision.objects.filter(dia_semana=2, hora_inicio=datetime.time(19, 0)).exists():
        ReglaCoVision.objects.create(
            nombre='Co-Visión',
            dia_semana=2,  # Miércoles
            hora_inicio=datetime.time(19, 0),
            duracion_minutos=90,
            fecha_ancla=datetime.date(2026, 10, 28),
            cada_n_dias=15,
            activo=True,
        )


def deshacer_siembra(apps, schema_editor):
    ReglaCoVision = apps.get_model('core', 'ReglaCoVision')
    ReglaCoVision.objects.filter(nombre='Co-Visión').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0058_regla_covision'),
    ]

    operations = [
        migrations.RunPython(sembrar_reglas_covision, deshacer_siembra),
    ]
