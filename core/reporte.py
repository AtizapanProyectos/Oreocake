from django.db.models import Count, Q
from core.models import (
    Cita, UsuarioPerfil, Taller, InscripcionTaller, 
    RegistroTallerPublico, MensajeChat, PerfilPsicologo
)

def generar_macro_reporte():
    print("\n" + "="*70)
    print("🚀 MACRO REPORTE DE IMPACTO Y PREVENCIÓN: ESPACIO HOPE")
    print("="*70)

    # ---------------------------------------------------------
    # 1. DEMOGRAFÍA Y PERFIL DEL PACIENTE
    # ---------------------------------------------------------
    print("\n👥 1. DEMOGRAFÍA Y PACIENTES:")
    total_pacientes = UsuarioPerfil.objects.filter(es_psicologo=False).count()
    padres = UsuarioPerfil.objects.filter(es_padre=True).count()
    
    # Focos rojos (pacientes en riesgo que Hope está ayudando)
    focos_rojos = UsuarioPerfil.objects.exclude(focos_rojos__exact='').exclude(focos_rojos__isnull=True).count()
    
    print(f" - Total de pacientes registrados en la plataforma: {total_pacientes}")
    if total_pacientes > 0:
        print(f" - Padres/Madres de familia atendidos: {padres} ({(padres/total_pacientes)*100:.1f}%)")
        print(f" - Pacientes con alertas o Focos Rojos detectados: {focos_rojos} ({(focos_rojos/total_pacientes)*100:.1f}%)")

    # ---------------------------------------------------------
    # 2. PREVENCIÓN COMUNITARIA (TALLERES) - ¡Clave para Gobierno!
    # ---------------------------------------------------------
    print("\n🏫 2. ALCANCE COMUNITARIO (TALLERES Y GRUPOS):")
    total_talleres = Taller.objects.count()
    inscritos_app = InscripcionTaller.objects.count()
    inscritos_publicos = RegistroTallerPublico.objects.count()
    impacto_total_talleres = inscritos_app + inscritos_publicos
    
    print(f" - Talleres creados/impartidos: {total_talleres}")
    print(f" - Personas impactadas en talleres (Total): {impacto_total_talleres}")
    print(f"   > Inscritos desde la App: {inscritos_app}")
    print(f"   > Inscritos del público general (Landing Page): {inscritos_publicos}")
    
    if total_talleres > 0:
        print(" - Demanda por tipo de taller:")
        tipos_taller = Taller.objects.values('tipo').annotate(total=Count('id')).order_by('-total')
        for t in tipos_taller:
            print(f"   * {t['tipo'].capitalize()}: {t['total']} talleres impartidos")

    # ---------------------------------------------------------
    # 3. MODALIDAD Y TIPO DE TERAPIA
    # ---------------------------------------------------------
    print("\n💻 3. MODALIDAD DE ATENCIÓN (BARRERAS DE ACCESO):")
    total_citas = Cita.objects.count()
    if total_citas > 0:
        modalidades = Cita.objects.values('modalidad').annotate(total=Count('id')).order_by('-total')
        for m in modalidades:
            print(f" - {m['modalidad']}: {(m['total']/total_citas)*100:.1f}% ({m['total']} citas)")
            
        print("\n👥 4. TIPO DE SESIÓN:")
        tipos = Cita.objects.values('tipo_sesion').annotate(total=Count('id')).order_by('-total')
        for t in tipos:
            print(f" - {t['tipo_sesion'].capitalize()}: {(t['total']/total_citas)*100:.1f}% ({t['total']} citas)")

    # ---------------------------------------------------------
    # 4. ESTADOS DE ÁNIMO REPORTADOS
    # ---------------------------------------------------------
    print("\n❤️‍🩹 5. TERMÓMETRO EMOCIONAL (ESTADOS DE ÁNIMO REPORTADOS):")
    animos = Cita.objects.exclude(estado_animo__isnull=True).exclude(estado_animo__exact='').values('estado_animo').annotate(total=Count('id')).order_by('-total')
    if animos:
        for a in animos:
            print(f" - {a['estado_animo']}: {a['total']} registros")
    else:
        print(" - Aún no hay suficientes registros de estado de ánimo.")

    # ---------------------------------------------------------
    # 5. ENGAGEMENT Y SEGUIMIENTO CONTINUO
    # ---------------------------------------------------------
    print("\n💬 6. ACOMPAÑAMIENTO CONTINUO (CHAT P2P):")
    total_mensajes = MensajeChat.objects.count()
    mensajes_no_leidos = MensajeChat.objects.filter(leido=False).count()
    print(f" - Total de mensajes intercambiados (Doctor-Paciente): {total_mensajes}")
    if total_mensajes > 0:
        print(f" - Tasa de mensajes pendientes de lectura: {(mensajes_no_leidos/total_mensajes)*100:.1f}%")

    print("\n" + "="*70)

generar_macro_reporte()