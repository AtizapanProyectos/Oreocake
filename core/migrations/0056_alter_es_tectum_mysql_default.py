from django.db import migrations

class Migration(migrations.Migration):

    dependencies = [
        ('core', '0055_cita_es_tectum_usuarioperfil_es_tectum'),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
            ALTER TABLE `core_cita` ALTER COLUMN `es_tectum` SET DEFAULT 0;
            ALTER TABLE `core_usuarioperfil` ALTER COLUMN `es_tectum` SET DEFAULT 0;
            UPDATE `core_cita` SET `es_tectum` = 0 WHERE `es_tectum` IS NULL;
            UPDATE `core_usuarioperfil` SET `es_tectum` = 0 WHERE `es_tectum` IS NULL;
            """,
            reverse_sql="""
            ALTER TABLE `core_cita` ALTER COLUMN `es_tectum` DROP DEFAULT;
            ALTER TABLE `core_usuarioperfil` ALTER COLUMN `es_tectum` DROP DEFAULT;
            """
        ),
    ]
