# cuestionario_data.py

CUESTIONARIO_CLINICO = {
    "solo_talleres": {
        "titulo": "Inscripción a Talleres",
        "preguntas": [

        ]
    },
    
    # --- NUEVO FLUJO CLÍNICO INTEGRADO ---
    "individual": {
        "titulo": "Evaluación Clínica Inicial",
        "preguntas": [
            {"id": "edad", "tipo": "numero", "pregunta": "¿Cuál es tu edad?"},
            {"id": "residencia", "tipo": "texto", "pregunta": "¿Cuál es tu lugar de residencia?"},
            {"id": "motivo_consulta", "tipo": "multiple", "pregunta": "¿Por qué quieres iniciar terapia en este momento? (Puedes elegir más de una opción)",
             "opciones": ["Ansiedad", "Estrés", "Tristeza o desánimo", "Problemas de autoestima", "Problemas en relaciones (familia, pareja, amistades)", "Estrés escolar o laboral", "Problemas de sueño", "Dificultades para concentrarte", "Manejo de emociones", "Otro"]},
            {"id": "meta_terapia", "tipo": "texto", "pregunta": "¿Qué te gustaría lograr con la terapia? (Ejemplo: sentirme más tranquilo/a, mejorar mis relaciones, controlar mis pensamientos, etc.)"},
            {"id": "estado_semana", "tipo": "single", "pregunta": "En la última semana, ¿cómo te has sentido principalmente?",
             "opciones": ["Bien / estable", "Con estrés o ansiedad", "Triste o desmotivado/a", "Cambios constantes de ánimo"]},
            {"id": "intensidad_malestar", "tipo": "numero", "pregunta": "En una escala del 1 al 10, ¿qué tan intenso ha sido tu malestar en la última semana?", "min": 1, "max": 10},
            {"id": "riesgo", "tipo": "single", "pregunta": "En la última semana, ¿has tenido pensamientos de hacerte daño o de no querer vivir?",
             "opciones": ["Sí", "No"]},
            {"id": "terapia_previa", "tipo": "single", "pregunta": "¿Has tomado terapia anteriormente?",
             "opciones": ["Sí", "No"]},
            {"id": "exp_previa", "tipo": "texto", "pregunta": "¿Qué te gustó o no te gustó de tu experiencia previa?",
             "mostrar_si": {"id": "terapia_previa", "valores": ["Sí"]}},
            {"id": "preferencia_terapeuta", "tipo": "single", "pregunta": "Para tu comodidad, ¿tienes alguna preferencia de terapeuta?",
             "opciones": ["Mujer", "Hombre", "Indistinto"]},
            {"id": "horario", "tipo": "multiple", "pregunta": "¿Cuál es tu disponibilidad de horario? (Puedes elegir más de una opción)",
             "opciones": ["Mañana (9:00 a 12:00)", "Tarde (12:00 a 17:00)", "Noche (17:00 a 20:00)", "Fines de semana"]}
        ]
    },
    
    # Replicamos el flujo maestro para los demás casos para que siempre se evalúe igual
    "pareja": {
        "titulo": "Evaluación Clínica (Pareja)",
        "preguntas": [
            {"id": "edad", "tipo": "numero", "pregunta": "¿Cuál es tu edad?"},
            {"id": "residencia", "tipo": "texto", "pregunta": "¿Cuál es tu lugar de residencia?"},
            {"id": "motivo_consulta", "tipo": "multiple", "pregunta": "¿Por qué buscan iniciar terapia en este momento?",
             "opciones": ["Problemas de comunicación", "Infidelidad o desconfianza", "Distanciamiento emocional", "Problemas de crianza", "Otro"]},
            {"id": "meta_terapia", "tipo": "texto", "pregunta": "¿Qué les gustaría lograr con la terapia?"},
            {"id": "terapia_previa", "tipo": "single", "pregunta": "¿Han tomado terapia de pareja anteriormente?",
             "opciones": ["Sí", "No"]},
            {"id": "modalidad", "tipo": "single", "pregunta": "¿Qué modalidad prefieren?",
             "opciones": ["Presencial", "En línea", "Indistinto"]},
            {"id": "horario", "tipo": "multiple", "pregunta": "¿Cuál es su disponibilidad de horario conjunta?",
             "opciones": ["Mañana (9:00 a 12:00)", "Tarde (12:00 a 17:00)", "Noche (17:00 a 20:00)", "Fines de semana"]}
        ]
    },
    "familiar": {
        "titulo": "Evaluación Clínica (Familiar)",
        "preguntas": [
            {"id": "edad", "tipo": "numero", "pregunta": "¿Cuál es tu edad (titular de la solicitud)?"},
            {"id": "residencia", "tipo": "texto", "pregunta": "¿Cuál es su lugar de residencia familiar?"},
            {"id": "integrantes_familia", "tipo": "numero", "pregunta": "¿Cuántos integrantes de la familia participarán en las sesiones?", "min": 2, "max": 10},
            {"id": "motivo_consulta", "tipo": "multiple", "pregunta": "¿Cuál es el motivo principal por el que buscan terapia familiar?",
             "opciones": ["Problemas de comunicación", "Conflictos entre padres e hijos", "Manejo de límites y reglas", "Duelo o pérdida familiar", "Transiciones difíciles (divorcio, mudanza)", "Problemas de conducta en hijos", "Otro"]},
            {"id": "meta_terapia", "tipo": "texto", "pregunta": "¿Qué les gustaría lograr como familia con la terapia?"},
            {"id": "terapia_previa", "tipo": "single", "pregunta": "¿Han asistido a terapia familiar previamente?",
             "opciones": ["Sí", "No"]},
            {"id": "modalidad", "tipo": "single", "pregunta": "¿Qué modalidad prefieren?",
             "opciones": ["Presencial", "En línea", "Indistinto"]},
            {"id": "horario", "tipo": "multiple", "pregunta": "¿Cuál es su disponibilidad de horario familiar?",
             "opciones": ["Mañana (9:00 a 12:00)", "Tarde (12:00 a 17:00)", "Noche (17:00 a 20:00)", "Fines de semana"]}
        ]
    },
    "tercero": {
        "titulo": "Ayuda para un Tercero",
        "preguntas": [
            {"id": "edad_tercero", "tipo": "numero", "pregunta": "¿Qué edad tiene la persona que recibirá la terapia?"},
            {"id": "relacion", "tipo": "single", "pregunta": "¿Cuál es tu relación con esta persona?",
             "opciones": ["Soy su padre/madre", "Soy su pareja", "Soy su amigo/a", "Otro familiar"]},
            {"id": "motivo_consulta", "tipo": "texto", "pregunta": "¿Cuál es el motivo principal por el que buscas ayuda para él/ella?"},
            {"id": "horario", "tipo": "multiple", "pregunta": "¿Cuál es la disponibilidad de horario de la persona?",
             "opciones": ["Mañana (9:00 a 12:00)", "Tarde (12:00 a 17:00)", "Noche (17:00 a 20:00)", "Fines de semana"]}
        ]
    }
}