import uuid

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('core', '0077_metodo_pago_presencial')]

    operations = [
        migrations.CreateModel(
            name='CodigoQRHope',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('codigo', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('nombre', models.CharField(max_length=120)),
                ('destino', models.URLField(max_length=2048)),
                ('color', models.CharField(
                    choices=[('morado', 'Morado HOPE'), ('petroleo', 'Verde petróleo'), ('negro', 'Negro')],
                    default='morado', max_length=12,
                )),
                ('escaneos', models.PositiveBigIntegerField(default=0)),
                ('visitas_directas', models.PositiveBigIntegerField(default=0)),
                ('activo', models.BooleanField(default=True)),
                ('creado', models.DateTimeField(auto_now_add=True)),
            ],
            options={'ordering': ['-creado', '-pk']},
        ),
    ]
