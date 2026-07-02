from django.db.models import Count
from core.models import Cita, UsuarioPerfil

def reporte_retencion():
    print("\n" + "="*60)
    print("📊 REPORTE DE RETENCIÓN Y EMBUDO DE PACIENTES")
    print("="*60)
    
    # ---------------------------------------------------------
    # 1. ANÁLISIS DE FRECUENCIA (FIDELIDAD)
    # ---------------------------------------------------------
    # Agrupamos las citas por paciente y contamos cuántas tiene cada uno
    pacientes_citas = Cita.objects.values('paciente').annotate(total_citas=Count('id'))
    
    una_cita = pacientes_citas.filter(total_citas=1).count()
    multiples_citas = pacientes_citas.filter(total_citas__gte=2).count()
    
    print("\n📈 1. FRECUENCIA DE TERAPIA (RETENCIÓN):")
    print(f" - Pacientes que tomaron 1 sola cita: {una_cita}")
    print(f" - Pacientes con 2 o más citas (Retención exitosa): {multiples_citas}")
    
    # ---------------------------------------------------------
    # 2. EMBUDO DE CONVERSIÓN (REGISTROS VS CONSULTAS)
    # ---------------------------------------------------------
    # Filtramos solo a los usuarios que son pacientes (es_psicologo=False)
    pacientes_base = UsuarioPerfil.objects.filter(es_psicologo=False)
    total_registrados = pacientes_base.count()
    
    # Si tienen doctor asignado, es que ya entraron al flujo de terapia
    con_doctor = pacientes_base.filter(psicologo_asignado__isnull=False).count()
    
    # Si no tienen doctor, se registraron pero se quedaron en el limbo
    sin_doctor = pacientes_base.filter(psicologo_asignado__isnull=True).count()
    
    print("\n funnel 2. EMBUDO DE CONVERSIÓN (REGISTRO -> TERAPIA):")
    print(f" - Total de pacientes registrados en la plataforma: {total_registrados}")
    print(f" - Pacientes ACTIVOS (Ya tienen psicólogo asignado): {con_doctor}")
    print(f" - Pacientes INACTIVOS (Se registraron pero no han agendado): {sin_doctor}")
    
    if total_registrados > 0:
        conversion = (con_doctor / total_registrados) * 100
        print(f"\n💡 Tasa de conversión: El {conversion:.1f}% de las personas que crean cuenta, realmente inician terapia.")
    
    print("="*60 + "\n")

reporte_retencion()