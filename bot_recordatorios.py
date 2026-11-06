import os
import subprocess
import requests
import time
import webbrowser
import pyautogui
import pygetwindow as gw
from datetime import datetime, timedelta
import urllib.parse

# DESACTIVAR EL FAIL-SAFE DE SEGURIDAD
pyautogui.FAILSAFE = False

API_URL = "https://espaciohope.com/api/citas-hoy/"
MINUTOS_ESPERA_MEET = 10  # Minutos a permanecer dentro de la sesión de Meet antes de cerrar
URL_CAMUFLAJE = "https://atizapan.gob.mx"
citas_ya_avisadas = []
citas_ya_unidas_meet = []

# ==========================================
# UTILIDADES PARA FORZAR MICROSOFT EDGE
# ==========================================
EDGE_PATHS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]
EDGE_EXE = next((p for p in EDGE_PATHS if os.path.exists(p)), None)


def abrir_en_edge(url):
    """Abre la URL siempre en Edge (nueva pestaña)."""
    if EDGE_EXE:
        subprocess.Popen([EDGE_EXE, "--new-tab", url])
    else:
        print("⚠️ No encontré msedge.exe, uso el navegador predeterminado.")
        webbrowser.open(url)


def enfocar_edge(texto=None, usar_alt=True):
    """Trae Edge al frente. Si se da 'texto' (ej. 'Meet'), busca primero la ventana de Edge con ese texto."""
    ventanas = [w for w in gw.getAllWindows() if "Edge" in w.title]
    if texto:
        ventanas.sort(key=lambda w: texto not in w.title)  # las que contienen el texto primero
    for w in ventanas:
        try:
            if w.isMinimized:
                w.restore()
            if usar_alt:
                pyautogui.press('alt')  # truco para cambiar foco (abre la barra de herramientas de Edge)
            w.activate()
            time.sleep(1)
            return True
        except Exception as e:
            print(f"No pude enfocar Edge: {e}")
    print("⚠️ No encontré ninguna ventana de Edge.")
    return False


def minimizar_whatsapp_desktop():
    """Minimiza la app de WhatsApp Desktop para que no se quede al frente tapando Edge."""
    for w in gw.getAllWindows():
        if w.title.strip() == "WhatsApp" or (w.title.startswith("WhatsApp") and "Edge" not in w.title):
            try:
                w.minimize()
            except Exception as e:
                print(f"No pude minimizar WhatsApp: {e}")


# ==========================================
# WHATSAPP (como antes: el link abre WhatsApp Desktop solo)
# ==========================================
def enviar_whatsapp(telefono, mensaje):
    """Abre el link wa.me (se abre WhatsApp Desktop), envía con doble Enter espaciado y cierra pestaña."""
    if not telefono:
        return

    mensaje_codificado = urllib.parse.quote(mensaje)
    url_wa = f"https://wa.me/{telefono}?text={mensaje_codificado}"

    webbrowser.open(url_wa)
    time.sleep(20)

    pyautogui.press('enter')
    time.sleep(28)

    pyautogui.press('enter')
    time.sleep(2)
    pyautogui.hotkey('ctrl', 'w')
    time.sleep(1)

    # NUEVO: quitar WhatsApp Desktop del frente para que no robe el foco después
    minimizar_whatsapp_desktop()
    time.sleep(1)


# ==========================================
# GOOGLE MEET
# ==========================================
def unirse_a_meet(link_meet, nombre_paciente=""):
    """Abre Meet en Edge, enfoca con Tab + Enter y permanece conectado."""
    if not link_meet or link_meet == "No asignado":
        print(f"No hay link de Meet asignado para la sesión de {nombre_paciente}.")
        return False

    print(f"Abriendo Meet: {link_meet}")
    try:
        abrir_en_edge(link_meet)
        time.sleep(10)  # espera a que cargue la sala de Meet

        minimizar_whatsapp_desktop()
        enfocar_edge("Meet", usar_alt=False)  # sin Alt para no activar la barra de Edge
        pyautogui.press('enter')  # solo Enter, sin Tab
        time.sleep(2)
        print(f"¡Unido al Meet de {nombre_paciente} exitosamente!")
        return True
    except Exception as e:
        print(f"Error al abrir o unirse al Meet de {nombre_paciente}: {e}")
        return False


print("Iniciando el bot de automatización (WhatsApp + Meet)... 👀")
print(f"Configuración de Meet: Permanencia de {MINUTOS_ESPERA_MEET} minutos antes del cierre automático.")
print(f"Edge detectado en: {EDGE_EXE}")

while True:
    try:
        respuesta = requests.get(API_URL)
        citas = respuesta.json()
        ahora = datetime.now()

        # ==========================================
        # 1. PROGRAMACIÓN DE RECORDATORIOS
        # ==========================================
        citas_para_recordar = list(citas)
        # Una cita de madrugada puede requerir el aviso la noche anterior.
        # Esta consulta adicional no agrega citas a la lista que usa Meet.
        if ahora.hour >= 22:
            try:
                respuesta_manana = requests.get(
                    API_URL, params={'fecha_recordatorios': 'manana'}, timeout=5
                )
                respuesta_manana.raise_for_status()
                manana = (ahora.date() + timedelta(days=1)).isoformat()
                datos_manana = respuesta_manana.json()
                if isinstance(datos_manana, list):
                    citas_para_recordar.extend(
                        cita for cita in datos_manana
                        if isinstance(cita, dict) and cita.get('fecha') == manana
                    )
            except (requests.RequestException, ValueError):
                print("No se pudo consultar la agenda de mañana para recordatorios.")

        for cita in citas_para_recordar:
            fecha = cita.get('fecha') or ahora.date().isoformat()
            inicio = datetime.strptime(f"{fecha} {cita['hora']}", "%Y-%m-%d %H:%M")
            minutos_faltantes = (inicio - ahora).total_seconds() / 60
            presencial = cita.get('modalidad') == 'Presencial'
            minutos_en_linea = 20 if cita.get('modalidad') else 10
            plazo_doctor = 10 if presencial else minutos_en_linea
            plazo_paciente = 120 if presencial else minutos_en_linea
            id_doctor = (fecha, cita['cita_id'], 'doctor')
            id_paciente = (fecha, cita['cita_id'], 'paciente')
            fecha_texto = inicio.strftime('%d/%m/%Y')

            if 0 < minutos_faltantes <= plazo_doctor and id_doctor not in citas_ya_avisadas:
                if cita.get('telefono_doctor'):
                    nombre_doc = cita.get('nombre_doctor', 'Doc')
                    if presencial:
                        msg_doctor = (
                            f"¡Hola, {nombre_doc}! 👋 Te recordamos tu cita presencial con "
                            f"{cita['nombre_paciente']} el {fecha_texto} a las {cita['hora']} hrs.\n\n"
                            "¡Mucho éxito en la consulta!"
                        )
                    else:
                        msg_doctor = (
                            f"¡Hola, {nombre_doc}! 👋 Espero que estés teniendo un excelente día.\n\n"
                            f"Te recuerdo que tu sesión con {cita['nombre_paciente']} está "
                            f"programada a las {cita['hora']} hrs.\n\n"
                            f"Aquí tienes tu acceso directo a Meet: {cita.get('link_meet')}\n\n"
                            "¡Mucho éxito en la consulta!"
                        )
                    enviar_whatsapp(cita['telefono_doctor'], msg_doctor)
                    citas_ya_avisadas.append(id_doctor)

            if 0 < minutos_faltantes <= plazo_paciente and id_paciente not in citas_ya_avisadas:
                if cita.get('telefono_paciente'):
                    if presencial:
                        msg_paciente = (
                            f"¡Hola, {cita['nombre_paciente']}! ✨ Te recordamos tu cita presencial "
                            f"el {fecha_texto} a las {cita['hora']} hrs. 🌿\n\n"
                            "Consulta el pase y la dirección que recibiste al confirmar tu cita. "
                            "¡Te esperamos! 🤍"
                        )
                    else:
                        msg_paciente = (
                            f"¡Hola, {cita['nombre_paciente']}! ✨ Esperamos que estés teniendo un lindo día.\n\n"
                            f"Te recordamos con gusto que tu sesión está programada para las {cita['hora']} hrs. 🌿\n\n"
                            "Por favor, ingresa unos 5 minutitos antes desde tu enlace personal "
                            "para responder el cuestionario previo:\n"
                            f"👉 {cita.get('link_sesion')}\n\n"
                            "¡Nos vemos en un momento! 🤍"
                        )
                    enviar_whatsapp(cita['telefono_paciente'], msg_paciente)
                    citas_ya_avisadas.append(id_paciente)

        # ==========================================
        # 2. BLOQUE DE GOOGLE MEET (A la hora exacta)
        # ==========================================
        citas_para_meet = []
        for cita in citas:
            id_unica = f"{cita['hora']}-{cita['telefono_doctor']}-{cita['telefono_paciente']}"

            hora_cita_obj = datetime.strptime(cita['hora'], "%H:%M")
            fecha_hora_cita = ahora.replace(
                hour=hora_cita_obj.hour, minute=hora_cita_obj.minute, second=0, microsecond=0
            )

            diferencia = fecha_hora_cita - ahora
            minutos_faltantes = diferencia.total_seconds() / 60

            if -12 <= minutos_faltantes <= 0 and id_unica not in citas_ya_unidas_meet:
                citas_para_meet.append((cita, id_unica))

        meets_abiertos = 0
        if citas_para_meet:
            print(f"\nDetectadas {len(citas_para_meet)} sesión(es) de Meet listas para entrar...")

            for cita, id_unica in citas_para_meet:
                link = cita.get('link_meet')
                paciente = cita.get('nombre_paciente', 'Paciente')

                if not link or link == "No asignado":
                    print(f"Cita a las {cita['hora']} ({paciente}) no tiene link de Meet asignado.")
                    citas_ya_unidas_meet.append(id_unica)
                    continue

                print(f"\nProcesando ingreso a Meet de {paciente} ({cita['hora']} hrs)...")
                exito = unirse_a_meet(link, paciente)
                if exito:
                    meets_abiertos += 1

                citas_ya_unidas_meet.append(id_unica)
                time.sleep(2)

            # Espera activa y cierre de pestañas
            if meets_abiertos > 0:
                # Abrir página de camuflaje para tapar la videollamada
                print(f"\n🌐 Abriendo pestaña de camuflaje ({URL_CAMUFLAJE}) para cubrir la videollamada...")
                abrir_en_edge(URL_CAMUFLAJE)
                time.sleep(3)

                print(f"\n🔥 Se unió a {meets_abiertos} llamada(s). Esperando {MINUTOS_ESPERA_MEET} minutos...")
                for m in range(MINUTOS_ESPERA_MEET, 0, -1):
                    print(f"⏳ Quedan {m} minuto(s) en la(s) sala(s)...")
                    time.sleep(60)

                total_pestanas_cerrar = meets_abiertos + 1
                print(f"\n⏰ Cerrando {total_pestanas_cerrar} pestaña(s) ({meets_abiertos} Meet + 1 camuflaje)...")
                enfocar_edge()
                for _ in range(total_pestanas_cerrar):
                    pyautogui.hotkey('ctrl', 'w')
                    time.sleep(1)
                print("✅ ¡Sesiones de Meet y pestaña de camuflaje cerradas con éxito!")

    except Exception as e:
        print(f"Error en el ciclo principal: {e}")

    time.sleep(60)