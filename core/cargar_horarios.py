import datetime
from django.db import transaction
from core.models import PerfilPsicologo, HorarioPsicologo

print("🚀 ¡MODO EXTREMO ACTIVADO! Rastreando psicólogos sin usar IDs...")

BLOQUES = {'A': [4, 5], 'B': [6, 0], 'C': [1, 2], 'D': [3, 4]}

# Ampliamos la red: busca por primer nombre, segundo nombre o apellidos.
IDENTIFICADORES = {
    'ABRAHAM':   {'turno': 'matutino',   'aliases': ['ABRAHAM', 'LEVI', 'GÓMEZ', 'GOMEZ']},
    'ANDREA':    {'turno': 'vespertino', 'aliases': ['ANDREA', 'GWEYNETH', 'URIBE']},
    'CLAUDIA':   {'turno': 'matutino',   'aliases': ['CLAUDIA', 'ANGELICA', 'ANGÉLICA', 'BENÍTEZ']},
    'MIGUEL':    {'turno': 'vespertino', 'aliases': ['MIGUEL', 'CEBALLOS']},
    'YAMASHIRO': {'turno': 'vespertino', 'aliases': ['YAMASHIRO', 'YAMASHYRO', 'CHRISTOPHER', 'CEJA']},
    'MICHELLE':  {'turno': 'vespertino', 'aliases': ['MICHELLE', 'MARLENE', 'SÁNCHEZ']},
    'GONZALO':   {'turno': 'matutino',   'aliases': ['GONZALO', 'IBRAHIM', 'ABARCA']},
    'SARAHI':    {'turno': 'matutino',   'aliases': ['SARAHI', 'SARAHÍ', 'TORRES']}
}

TRIMESTRES = [
    {"inicio": datetime.date(2026, 6, 30), "fin": datetime.date(2026, 9, 30), "mapeo": {'ABRAHAM': 'A', 'ANDREA': 'A', 'CLAUDIA': 'B', 'MIGUEL': 'B', 'YAMASHIRO': 'C', 'MICHELLE': 'C', 'GONZALO': 'D', 'SARAHI': 'D'}},
    {"inicio": datetime.date(2026, 10, 1), "fin": datetime.date(2026, 12, 31), "mapeo": {'GONZALO': 'A', 'SARAHI': 'A', 'ABRAHAM': 'B', 'ANDREA': 'B', 'CLAUDIA': 'C', 'MIGUEL': 'C', 'YAMASHIRO': 'D', 'MICHELLE': 'D'}},
    {"inicio": datetime.date(2027, 1, 1), "fin": datetime.date(2027, 3, 31), "mapeo": {'YAMASHIRO': 'A', 'MICHELLE': 'A', 'GONZALO': 'B', 'SARAHI': 'B', 'ABRAHAM': 'C', 'ANDREA': 'C', 'CLAUDIA': 'D', 'MIGUEL': 'D'}},
    {"inicio": datetime.date(2027, 4, 1), "fin": datetime.date(2027, 6, 30), "mapeo": {'CLAUDIA': 'A', 'MIGUEL': 'A', 'YAMASHIRO': 'B', 'MICHELLE': 'B', 'GONZALO': 'C', 'SARAHI': 'C', 'ABRAHAM': 'D', 'ANDREA': 'D'}}
]

def obtener_numero_semana(fecha_actual):
    return (fecha_actual.day - 1) // 7 + 1

def cargar_horarios_extremo():
    print("🧹 Limpiando los horarios que hayan chocado...")
    HorarioPsicologo.objects.all().delete()
    
    psicologos_db = PerfilPsicologo.objects.filter(esta_activo=True)
    registros_creados = 0
    dia_delta = datetime.timedelta(days=1)
    fecha_inicio = datetime.date(2026, 6, 30)
    fecha_fin = datetime.date(2027, 6, 30)
    
    agendados = set()
    no_agendados = []

    print("⚡ Cruzando Nombres, Apellidos y Correos de la base de datos...")
    
    with transaction.atomic():
        mapeo_psicologos = {}
        
        # Primero identificamos a cada quien como detectives
        for p in psicologos_db:
            if not p.usuario:
                continue
                
            # Juntamos TODO lo que haya del usuario en un solo texto gigante
            datos_busqueda = f"{p.usuario.first_name} {p.usuario.last_name} {p.usuario.username} {p.usuario.email}".upper()
            
            encontrado = False
            for clave, config in IDENTIFICADORES.items():
                for alias in config['aliases']:
                    if alias in datos_busqueda:
                        mapeo_psicologos[p.id] = clave
                        agendados.add(f"✅ {clave} (Encontrado como: {p.usuario.first_name} {p.usuario.last_name} | {p.usuario.email})")
                        encontrado = True
                        break
                if encontrado:
                    break
            
            if not encontrado:
                no_agendados.append(f"❌ ID Oculto: {p.id} | Nombre BD: {p.usuario.first_name} {p.usuario.last_name} | Correo: {p.usuario.email}")

        # Inyectamos todos los horarios usando nuestro mapeo perfecto
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
                    if p.id in mapeo_psicologos:
                        clave_psicologo = mapeo_psicologos[p.id]
                        bloque_asignado = trim_actual["mapeo"].get(clave_psicologo)
                        turno_asignado = IDENTIFICADORES[clave_psicologo]['turno']
                        
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
    print(f"¡ÉXITO TOTAL! 🚀 Se crearon {registros_creados} registros perfectos.")
    print("\n🩺 ASÍ QUEDÓ EL EQUIPO (Misión Cumplida):")
    for doc in sorted(list(agendados)):
        print(doc)
        
    if no_agendados:
        print("\n⚠️ FANTASMAS EN LA BASE DE DATOS (No empataron con nadie del PDF):")
        for doc in no_agendados:
            print(doc)
    else:
        print("\n🎉 ¡TODOS LOS PSICÓLOGOS FUERON CAPTURADOS Y AGENDADOS!")
    print("="*50 + "\n")

cargar_horarios_extremo()