from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('core', '0076_almacen_privado_transcripcion')]

    operations = [
        migrations.AddField(
            model_name='cita',
            name='metodo_pago_presencial',
            field=models.CharField(
                max_length=16, default='sitio_web',
                choices=[
                    ('sitio_web', 'Sitio web'),
                    ('efectivo', 'Pago con efectivo'),
                    ('clip_presencial', 'Pago con Clip (presencial)'),
                ],
            ),
        ),
    ]
