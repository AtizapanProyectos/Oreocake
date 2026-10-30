# ===================================================================
# INTELLIGENCE CENTER (30/10/2026)
# Índice opcional sobre auth_user.date_joined para el feed en tiempo
# real (búsquedas > timestamp). Es la única pieza que no se puede
# declarar en unde Meta de modelo (tabla de auth). El SQL corre
# protegido: si MySQL lo rechaza, la migración termina igual y el
# módulo funciona con los índices ya creados en 0062.
# ===================================================================
from django.db import migrations


def crear_indice(apps, schema_editor):
    db_alias = schema_editor.connection.alias
    with schema_editor.connection.cursor() as cursor:
        try:
            cursor.execute('CREATE INDEX intelligence_user_date_joined_idx ON auth_user (date_joined)')
        except Exception:
            # El motor no soporta IF NOT EXISTS para índices: si ya existe
            # o rechaza la pieza, quedamos tal cual (operación no crítica).
            pass


def borrar_indice(apps, schema_editor):
    with schema_editor.connection.cursor() as cursor:
        try:
            cursor.execute('DROP INDEX intelligence_user_date_joined_idx ON auth_user')
        except Exception:
            pass


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0062_intelligence_indexes'),
    ]

    operations = [
        migrations.RunPython(crear_indice, borrar_indice),
    ]
