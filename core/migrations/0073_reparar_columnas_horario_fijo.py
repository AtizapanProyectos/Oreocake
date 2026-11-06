from django.db import migrations


def reparar_columnas(apps, schema_editor):
    modelo = apps.get_model('core', 'HorarioFijoPsicologo')
    connection = schema_editor.connection
    with connection.cursor() as cursor:
        columnas = {c.name for c in connection.introspection.get_table_description(cursor, modelo._meta.db_table)}
    faltantes = [campo for campo in modelo._meta.local_fields if campo.column not in columnas]
    if faltantes and modelo.objects.using(connection.alias).exists():
        raise RuntimeError('La reparación requiere revisar los horarios existentes antes de añadir columnas.')
    for campo in faltantes:
        schema_editor.add_field(modelo, campo)


class Migration(migrations.Migration):
    dependencies = [('core', '0072_estadoflujosesion')]
    operations = [migrations.RunPython(reparar_columnas, migrations.RunPython.noop, atomic=False)]

