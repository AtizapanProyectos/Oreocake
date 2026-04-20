# cuestionario_data.py

CUESTIONARIO_CLINICO = {
    # --- NUEVO FLUJO: SOLO TALLERES ---
    "solo_talleres": {
        "titulo": "Inscripción a Programas de Vida",
        "preguntas": [
            {"id": "interes_talleres", "tipo": "multiple", "pregunta": "¿A qué programas te gustaría inscribirte?",
             "opciones": ["Yoga Clínico", "Meditación y Mindfulness", "Gestión Emocional", "Arquitectura del Ser", "Terapia de Grupo"]},
            {"id": "es_padre", "tipo": "single", "pregunta": "¿Eres padre o madre de familia?",
             "opciones": ["Sí", "No"]},
            {"id": "interes_escuela_padres", "tipo": "single", "pregunta": "Contamos con una 'Escuela para Padres'. ¿Te interesaría recibir información?",
             "opciones": ["Sí, me interesa mucho", "Por ahora no"],
             "mostrar_si": {"id": "es_padre", "valores": ["Sí"]}}
        ]
    },
    
    # --- FLUJOS DE TERAPIA (LOS QUE YA TENÍAMOS) ---
    "individual": {
        "titulo": "Terapia Individual (Para mí)",
        "preguntas": [
            {"id": "edad", "tipo": "numero", "pregunta": "¿Cuál es tu edad actual?"},
            {"id": "es_padre", "tipo": "single", "pregunta": "¿Eres padre o madre de familia?",
             "opciones": ["Sí", "No"]},
            {"id": "interes_escuela_padres", "tipo": "single", "pregunta": "¿Te interesaría participar en nuestra Escuela para Padres?",
             "opciones": ["Sí, me interesa mucho", "Por ahora no"],
             "mostrar_si": {"id": "es_padre", "valores": ["Sí"]}},
            {"id": "interes_talleres", "tipo": "multiple", "pregunta": "¿Te gustaría complementar tu terapia con algún taller?",
             "opciones": ["Yoga Clínico", "Meditación", "Gestión Emocional", "Arquitectura del Ser", "Prefiero solo terapia por ahora"]},
            {"id": "estado_emocional", "tipo": "multiple", "pregunta": "¿Qué buscas alcanzar?",
             "opciones": ["Paz mental", "Motivación", "Manejo emocional", "Autoconocimiento"]},
            {"id": "preferencia_terapeuta", "tipo": "single", "pregunta": "¿Preferencia de género para tu terapeuta?",
             "opciones": ["Hombre", "Mujer", "Indiferente"]},
        ]
    },
    "pareja": {
        "titulo": "Terapia de Pareja",
        "preguntas": [
            {"id": "edad", "tipo": "numero", "pregunta": "¿Cuál es tu edad?"},
            {"id": "son_padres", "tipo": "single", "pregunta": "¿Son padres de familia?",
             "opciones": ["Sí", "No"]},
            {"id": "interes_escuela_padres_pareja", "tipo": "single", "pregunta": "¿Les interesaría participar en la Escuela para Padres?",
             "opciones": ["Sí, nos interesa", "Por ahora no"],
             "mostrar_si": {"id": "son_padres", "valores": ["Sí"]}},
            {"id": "expectativas", "tipo": "multiple", "pregunta": "¿Qué metas buscan con este proceso?",
             "opciones": ["Mejorar comunicación", "Recuperar confianza", "Espacio seguro de crecimiento", "Herramientas prácticas", "Conexión íntima"]},
        ]
    },
    "tercero": {
        "titulo": "Para un Familiar o Amigo",
        "preguntas": [
            {"id": "edad_tuya", "tipo": "numero", "pregunta": "¿Cuál es tu edad?"},
            {"id": "edad_tercero", "tipo": "numero", "pregunta": "¿Qué edad tiene la persona?"},
            {"id": "rol_respecto_a_tercero", "tipo": "single", "pregunta": "¿Cuál es tu relación con esta persona?",
             "opciones": ["Soy su padre/madre", "Soy su pareja", "Soy su amigo/a", "Otro familiar"]},
            {"id": "motivo_tercero", "tipo": "multiple", "pregunta": "¿Qué te motiva a buscar apoyo para él/ella?",
             "opciones": ["Que encuentre paz", "Mejores hábitos", "Mejor entorno social/escolar", "Acompañamiento en cambios", "Espacio seguro para hablar"]},
        ]
    }
}