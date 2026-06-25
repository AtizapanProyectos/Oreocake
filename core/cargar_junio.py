import datetime
from django.db import transaction
from core.models import PerfilPsicologo, HorarioPsicologo

print("🚀 Rellenando los últimos días de Junio 2026 (25 al 29)...")

# Configuración EXACTA de la Semana 4 del PDF de Junio
DESCANSOS_SEMANA_4 = {
    'ABRAHAM': [3, 4],  # Descansa Jueves, Viernes
    'ANDREA': [3, 4],   # Descansa Jueves, Viernes
    'CLAUDIA': [4, 5],  # Descansa Viernes, Sábado
    'MIGUEL': [4, 5],   # Descansa Viernes, Sábado
    'SARAHI': [0, 6],   # Descansa Lunes, Domingo
    'MICHELLE': [0, 6], # Descansa Lunes, Domingo
    'GONZALO': [1, 2],  # Descansa Martes, Miércoles
    'YAMASHIRO': [1, 2] # Descansa Martes, Miércoles
}

TURNOS = {
    'ABRAHAM': 'matutino', 'CLAUDIA': 'matutino', 'GONZALO': 'matutino', 'SARAHI': 'matutino',
    'ANDREA': 'vespertino', 'MIGUEL': 'vespertino', 'YAMASHIRO': 'vespertino', 'MICHELLE': 'vespertino'
}

def cargar_hueco_junio():
    psicologos_db = PerfilPsicologo.objects.filter(esta_activo=True)
    registros_creados = 0
    
    # Los 5 días exactos que nos faltan
    fechas_faltantes = [
        datetime.date(2026, 6, 25), # Jueves
        datetime.date(2026, 6, 26), # Viernes
        datetime.date(2026, 6, 27), # Sábado
        datetime.date(2026, 6, 28), # Domingo
        datetime.date(2026, 6, 29)  # Lunes
    ]
    
    with transaction.atomic():
        for fecha_actual in fechas_faltantes:
            primer_dia_mes = fecha_actual.replace(day=1)
            dia_semana_int = fecha_actual.weekday()
            
            # La misma matemática a prueba de balas para la semana
            num_semana = (fecha_actual.day - 1) // 7 + 1
            
            for p in psicologos_db:
                nombre_doc = p.usuario.first_name.upper() if p.usuario.first_name else ""
                
                descansos_doc = []
                turno_doc = None
                
                for clave in TURNOS.keys():
                    if clave in nombre_doc:
                        descansos_doc = DESCANSOS_SEMANA_4.get(clave, [])
                        turno_doc = TURNOS.get(clave)
                        break
                        
                if turno_doc:
                    es_descanso_hoy = dia_semana_int in descansos_doc
                    
                    # update_or_create para que sea seguro correrlo
                    HorarioPsicologo.objects.update_or_create(
                        psicologo=p,
                        mes=primer_dia_mes,
                        semana=num_semana,
                        dia_semana=dia_semana_int,
                        defaults={
                            'es_descanso': es_descanso_hoy,
                            'turno': None if es_descanso_hoy else turno_doc
                        }
                    )
                    registros_creados += 1
                    
    print("\n" + "="*50)
    print(f"¡LISTO! 🎉 Se agregaron los {registros_creados} horarios faltantes de Junio.")
    print("="*50 + "\n")

cargar_hueco_junio()