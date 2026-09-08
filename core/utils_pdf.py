import os
import shutil
import subprocess
import tempfile
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

def obtener_ejecutable_chromium():
    """
    Localiza el ejecutable de Chromium / Chrome / Edge disponible en el sistema.
    Funciona en Windows (entorno local), Linux (Docker / Heroku / Servidores de producción) y Mac.
    """
    import glob

    candidatos = [
        # Variables de entorno comunes (Heroku buildpack, Docker, CI/CD, etc.)
        os.environ.get("GOOGLE_CHROME_BIN"),
        os.environ.get("GOOGLE_CHROME_SHIM"),
        os.environ.get("CHROME_BIN"),
        os.environ.get("CHROMIUM_PATH"),
        os.environ.get("PUPPETEER_EXECUTABLE_PATH"),
        os.environ.get("EDGE_BIN"),
        # Comandos en PATH (Linux / Docker / Mac / Windows)
        shutil.which("google-chrome"),
        shutil.which("google-chrome-stable"),
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
        shutil.which("msedge"),
        shutil.which("chrome"),
        # Rutas directas comunes en Linux / Docker / Heroku / VPS
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/usr/local/bin/google-chrome",
        "/usr/local/bin/chromium",
        "/snap/bin/chromium",
        "/snap/bin/chromium-browser",
        "/usr/lib/chromium/chromium",
        "/usr/lib/chromium-browser/chromium-browser",
        "/app/.apt/usr/bin/google-chrome",
        "/app/.apt/usr/bin/google-chrome-stable",
        # Rutas comunes de Nixpacks en Railway
        "/root/.nix-profile/bin/chromium",
        "/root/.nix-profile/bin/chromium-browser",
        "/nix/var/nix/profiles/default/bin/chromium",
        os.path.expanduser("~/.nix-profile/bin/chromium"),
        os.path.expanduser("~/.nix-profile/bin/chromium-browser"),
        # Rutas comunes en Windows
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%PROGRAMFILES%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%PROGRAMFILES(X86)%\Microsoft\Edge\Application\msedge.exe"),
    ]

    # Búsqueda en store de Nix (Railway) o caches de Puppeteer
    patrones_glob = [
        "/root/.cache/puppeteer/chrome/*/*/chrome",
        os.path.expanduser("~/.cache/puppeteer/chrome/*/*/chrome"),
        "/nix/store/*-chromium-*/bin/chromium",
        "/nix/store/*-chromium-*/bin/chromium-browser",
    ]
    for patron in patrones_glob:
        candidatos.extend(glob.glob(patron))

    for ruta in candidatos:
        if ruta and os.path.exists(ruta):
            return ruta

    return None


def obtener_diagnostico_motor_pdf():
    """
    Retorna el estado detallado del motor PDF activo en el servidor para diagnósticos en producción.
    """
    browser_bin = obtener_ejecutable_chromium()
    if browser_bin:
        return {
            'disponible': True,
            'es_optimo': True,
            'motor': 'Chromium Headless (Vectorial de Alta Fidelidad)',
            'ruta': browser_bin,
            'color': 'success',
            'mensaje': 'Motor óptimo detectado. Los PDFs se generarán con estilos CSS, colores y tipografías idénticas a la web.',
            'comando_instalacion': None,
        }

    try:
        import weasyprint
        return {
            'disponible': True,
            'es_optimo': True,
            'motor': 'WeasyPrint (HTML a PDF)',
            'ruta': 'Python WeasyPrint',
            'color': 'info',
            'mensaje': 'Motor WeasyPrint disponible.',
            'comando_instalacion': None,
        }
    except ImportError:
        pass

    return {
        'disponible': False,
        'es_optimo': False,
        'motor': 'PyMuPDF (Modo Emergencia Básico)',
        'ruta': 'Librería fitz (Python)',
        'color': 'danger',
        'mensaje': (
            'Chromium/Chrome NO está instalado o detectado en este servidor. '
            'El motor de emergencia PyMuPDF no soporta CSS moderno, flexbox ni colores avanzados. '
            'Para que tus PDFs se vean exactamente como en la web, instala Chromium en tu servidor.'
        ),
        'comando_instalacion': 'sudo apt update && sudo apt install -y chromium-browser (o google-chrome-stable)',
    }


def convertir_html_a_pdf(html_content):
    """
    Convierte una cadena de HTML con CSS moderno, tipografías, gradientes y SVGs a un binario PDF
    utilizando el motor nativo de Chromium headless para máxima fidelidad gráfica y vectorial.
    """
    browser_bin = obtener_ejecutable_chromium()

    if browser_bin:
        temp_html = None
        temp_pdf = None
        try:
            with tempfile.NamedTemporaryFile(suffix='.html', delete=False, mode='w', encoding='utf-8') as f:
                f.write(html_content)
                temp_html = f.name

            temp_pdf = temp_html.replace('.html', '.pdf')
            temp_html_url = Path(temp_html).resolve().as_uri()

            # Intentar primero con el nuevo motor headless (--headless=new)
            flags_headless = ['--headless=new', '--headless']
            pdf_generado = False

            for h_flag in flags_headless:
                for target_input in [temp_html_url, temp_html]:
                    cmd = [
                        browser_bin,
                        h_flag,
                        '--disable-gpu',
                        '--no-sandbox',
                        '--disable-setuid-sandbox',
                        '--disable-dev-shm-usage',
                        '--no-pdf-header-footer',
                        '--allow-file-access-from-files',
                        '--enable-local-file-accesses',
                        '--run-all-compositor-stages-before-draw',
                        '--virtual-time-budget=2500',
                        f'--print-to-pdf={temp_pdf}',
                        target_input
                    ]

                    res = subprocess.run(cmd, capture_output=True, timeout=40)
                    if res.returncode == 0 and os.path.exists(temp_pdf) and os.path.getsize(temp_pdf) > 1000:
                        pdf_generado = True
                        break
                    else:
                        stderr_msg = res.stderr.decode('utf-8', errors='ignore')
                        logger.warning(f"Chromium ({h_flag} / {target_input}) retorno código {res.returncode}: {stderr_msg}")
                if pdf_generado:
                    break

            if pdf_generado:
                with open(temp_pdf, 'rb') as f_pdf:
                    return f_pdf.read()

        except Exception as e:
            logger.error(f"Error ejecutando Chromium headless: {e}")
        finally:
            if temp_html and os.path.exists(temp_html):
                try: os.remove(temp_html)
                except Exception: pass
            if temp_pdf and os.path.exists(temp_pdf):
                try: os.remove(temp_pdf)
                except Exception: pass

    # Fallback secundario: WeasyPrint si está instalado
    try:
        import weasyprint
        logger.info("Generando PDF mediante WeasyPrint...")
        return weasyprint.HTML(string=html_content).write_pdf()
    except ImportError:
        pass
    except Exception as e:
        logger.warning(f"WeasyPrint falló: {e}")

    # Fallback terciario: PyMuPDF (fitz)
    try:
        import fitz
        logger.warning("Generando PDF mediante PyMuPDF (precaución: no soporta CSS avanzado).")
        doc = fitz.open('html', html_content.encode('utf-8'))
        return doc.convert_to_pdf()
    except Exception as e:
        logger.error(f"Fallback PyMuPDF también falló: {e}")
        raise RuntimeError(
            f"No se encontró un motor para generar el PDF (Chromium/Chrome/Edge o WeasyPrint). Error: {e}"
        )
