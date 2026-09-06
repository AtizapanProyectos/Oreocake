from django.core.management.base import BaseCommand
from django.db import transaction
from django.contrib.auth.models import User
from core.models import UsuarioPerfil, Cita, TratamientoPaciente

class Command(BaseCommand):
    help = 'Pobla la tabla TratamientoPaciente a partir de los datos históricos existentes sin borrar nada.'

    def handle(self, *args, **options):
        self.stdout.write(self.style.NOTICE("Iniciando migración de datos a TratamientoPaciente..."))
        
        perfiles_con_doctor = UsuarioPerfil.objects.filter(
            psicologo_asignado__isnull=False,
            usuario__isnull=False
        ).select_related('usuario', 'psicologo_asignado')

        creados = 0
        actualizados = 0

        with transaction.atomic():
            for perfil in perfiles_con_doctor:
                user = perfil.usuario
                doctor_base = perfil.psicologo_asignado

                # 1. Revisamos qué tipos de citas ha tenido este paciente
                citas_paciente = Cita.objects.filter(paciente=user).select_related('psicologo')
                modalidades_encontradas = set(citas_paciente.values_list('tipo_sesion', flat=True))

                # Si no tiene citas registradas, por defecto creamos su tratamiento 'individual'
                if not modalidades_encontradas:
                    modalidades_encontradas = {'individual'}

                for modalidad in modalidades_encontradas:
                    if modalidad not in ['individual', 'pareja', 'familiar']:
                        continue

                    # Buscamos el psicólogo de esa modalidad específica (de su última cita) o el del perfil
                    ultima_cita_modalidad = citas_paciente.filter(
                        tipo_sesion=modalidad, psicologo__isnull=False
                    ).order_by('-fecha', '-hora').first()

                    psicologo_modalidad = ultima_cita_modalidad.psicologo if ultima_cita_modalidad else doctor_base

                    # Solo copiamos las notas clínicas del perfil al tratamiento individual para no mezclarlas
                    es_individual = (modalidad == 'individual')
                    historia = perfil.historia_clinica if es_individual else ""
                    focos = perfil.focos_rojos if es_individual else ""
                    recom = perfil.recommendaciones_generales if es_individual else ""
                    alta = perfil.notas_alta if es_individual else ""

                    tratamiento, creado = TratamientoPaciente.objects.get_or_create(
                        paciente=user,
                        tipo_servicio=modalidad,
                        defaults={
                            'psicologo_asignado': psicologo_modalidad,
                            'historia_clinica': historia,
                            'focos_rojos': focos,
                            'recommendaciones_generales': recom,
                            'notas_alta': alta,
                            'activo': True,
                        }
                    )

                    if creado:
                        creados += 1
                    else:
                        # Si ya existía, aseguramos que tenga psicólogo asignado si estaba vacío
                        if not tratamiento.psicologo_asignado and psicologo_modalidad:
                            tratamiento.psicologo_asignado = psicologo_modalidad
                            tratamiento.save(update_fields=['psicologo_asignado'])
                            actualizados += 1

        self.stdout.write(self.style.SUCCESS(
            f"¡Migración completada exitosamente! Tratamientos creados: {creados}, actualizados: {actualizados}."
        ))
        total = TratamientoPaciente.objects.count()
        self.stdout.write(self.style.SUCCESS(f"Total de tratamientos en base de datos: {total}."))
