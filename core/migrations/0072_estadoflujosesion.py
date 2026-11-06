from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('core', '0071_estado_correo_confirmacion')]

    operations = [migrations.CreateModel(
        name='EstadoFlujoSesion',
        fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('adjuntos', models.JSONField(blank=True, default=list)),
            ('texto_notas', models.TextField(blank=True)),
            ('datos_clinicos', models.JSONField(blank=True, default=dict)),
            ('fuente', models.CharField(blank=True, max_length=30)),
            ('fechas', models.JSONField(blank=True, default=dict)),
            ('actualizado', models.DateTimeField(auto_now=True)),
            ('cita', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='flujo_manual', to='core.cita')),
        ],
    ), migrations.AlterField(
        model_name='logautomatizacionnotas', name='etapa',
        field=models.CharField(max_length=20, db_index=True, verbose_name='Etapa del flujo', choices=[
            ('meet', 'Verificación de Google Meet'),
            ('lectura', 'Lectura de notas (Google Meet)'),
            ('ia', 'Procesamiento IA (Groq)'),
            ('escritura', 'Escritura de nota clínica'),
            ('ipp', 'Verificación del IPP de la sesión'),
            ('reporte', 'Generación de reporte'),
            ('correo', 'Envío de correo'),
            ('completo', 'Sincronización completa'),
        ]),
    )]
