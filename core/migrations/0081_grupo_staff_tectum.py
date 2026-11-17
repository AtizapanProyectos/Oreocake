from django.db import migrations


def crear_grupo_staff_tectum(apps, schema_editor):
    Grupo = apps.get_model('auth', 'Group')
    Grupo.objects.using(schema_editor.connection.alias).get_or_create(name='tectum_staff')


class Migration(migrations.Migration):
    dependencies = [
        ('auth', '0012_alter_user_first_name_max_length'),
        ('core', '0080_unir_adjuntos_y_codigos_qr'),
    ]

    operations = [migrations.RunPython(crear_grupo_staff_tectum, migrations.RunPython.noop)]
