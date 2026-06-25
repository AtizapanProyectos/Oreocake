import datetime
from django.contrib.auth.models import User
from django.db import transaction
from core.models import PerfilPsicologo, HorarioPsicologo

print("🚀 Iniciando la limpieza y carga súper rápida...")

BLOQUES = {'A': [4, 5], 'B': [6, 0], 'C': [1, 2], 'D': [3, 4]}

TURNOS = {
    'ABRAHAM': 'matutino', 'CLAUDIA': 'matutino', 'GONZALO': 'matutino', 'SARAHI': 'matutino',
    'ANDREA': 'vespertino', 'MIGUEL': 'vespertino', 'YAMASHIRO': 'vespertino', 'MICHELLE': 'vespertino'
}

TRIMESTRES = [
    {"inicio": datetime.date(2026, 6, 30), "fin": datetime.date(2026, 9, 30), "mapeo": {'ABRAHAM': 'A', 'ANDREA': 'A', 'CLAUDIA': 'B', 'MIGUEL': 'B', 'YAMASHIRO': 'C', 'MICHELLE': 'C', 'GONZALO': 'D', 'SARAHI': 'D'}},
    {"inicio": datetime.date(2026, 10, 1), "fin": datetime.date(2026, 12, 31), "mapeo": {'GONZALO': 'A', 'SARAHI': 'A', 'ABRAHAM': 'B', 'ANDREA': 'B', 'CLAUDIA': 'C', 'MIGUEL': 'C', 'YAMASHIRO': 'D', 'MICHELLE': 'D'}},
    {"inicio": datetime.date(2027, 1, 1), "fin": datetime.date(2027, 3, 31), "mapeo": {'YAMASHIRO': 'A', 'MICHELLE': 'A', 'GONZALO': 'B', 'SARAHI': 'B', 'ABRAHAM': 'C', 'ANDREA': 'C', 'CLAUDIA': 'D', 'MIGUEL': 'D'}},
    {"inicio": datetime.date(2027, 4, 1), "fin": datetime.date(2027, 6, 30), "mapeo": {'CLAUDIA': 'A', 'MIGUEL': 'A', 'YAMASHIRO': 'B', 'MICHELLE': 'B', 'GONZALO': 'C', 'SARAHI': 'C', 'ABRAHAM': 'D', 'ANDREA': 'D'}}
]

def obtener_numero_semana(fecha_actual):
    # 🔥 EL FIX MÁGICO: Esto calcula matemáticamente si es el 1er, 2do, 3er, 4to o 5to [Lunes/Martes] del mes.
    # Garantiza 100% que nunca haya clones en tu base de datos.
    return (fecha_actual.day - 1) // 7 + 1

def cargar_horarios_turbo():
    print("🧹 Borrando registros previos para evitar basura...")
    HorarioPsicologo.objects.all().delete()
    print("✅ Base de datos limpia.")

    psicologos_db = PerfilPsicologo.objects.filter(esta_activo=True)
    registros_creados = 0
    dia_delta = datetime.timedelta(days=1)
    fecha_inicio = datetime.date(2026, 6, 30)
    fecha_fin = datetime.date(2027, 6, 30)
    
    print("⚡ Generando todos los horarios en un solo bloque (Turbo mode)...")
    
    with transaction.atomic():
        fecha_actual = fecha_inicio
        while fecha_actual <= fecha_fin:
            primer_dia_mes = fecha_actual.replace(day=1)
            dia_semana_int = fecha_actual.weekday() 
            num_semana = obtener_numero_semana(fecha_actual)
            
            trim_actual = None
            for trim in TRIMESTRES:
                if trim["inicio"] <= fecha_actual <= trim["fin"]:
                    trim_actual = trim
                    break
                    
            if trim_actual:
                for p in psicologos_db:
                    nombre_doc = p.usuario.first_name.upper() if p.usuario.first_name else ""
                    bloque_asignado = None
                    turno_asignado = None
                    
                    for clave in TURNOS.keys():
                        if clave in nombre_doc:
                            bloque_asignado = trim_actual["mapeo"].get(clave)
                            turno_asignado = TURNOS.get(clave)
                            break
                            
                    if bloque_asignado and turno_asignado:
                        es_descanso_hoy = dia_semana_int in BLOQUES[bloque_asignado]
                        
                        HorarioPsicologo.objects.create(
                            psicologo=p,
                            mes=primer_dia_mes,
                            semana=num_semana,
                            dia_semana=dia_semana_int,
                            es_descanso=es_descanso_hoy,
                            turno=None if es_descanso_hoy else turno_asignado
                        )
                        registros_creados += 1
                            
            fecha_actual += dia_delta
            
    print("\n" + "="*50)
    print(f"¡ÉXITO TOTAL EN TIEMPO RÉCORD! 🚀")
    print(f"Se crearon {registros_creados} registros limpiecitos.")
    print("="*50 + "\n")

cargar_horarios_turbo()