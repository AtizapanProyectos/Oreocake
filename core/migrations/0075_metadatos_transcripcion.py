from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('core', '0074_analisis_grabacion_clinica')]

    operations = [
        migrations.AddField(
            model_name='grabacionsesion',
            name='transcripcion_audio',
            field=models.CharField(blank=True, default='', max_length=100),
        ),
        migrations.AddField(
            model_name='grabacionsesion',
            name='transcripcion_fecha',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
