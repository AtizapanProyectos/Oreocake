import os
import shutil
import subprocess
import tempfile
import logging

logger = logging.getLogger(__name__)

def obtener_ejecutable_chromium():
    """
    Localiza el ejecutable de Chromium / Chrome / Edge disponible en el sistema.
    Funciona tanto en Windows (entorno local) como en Linux (Docker / Servidor de producción).
    """
    candidatos = [
        # Rutas comunes en Windows
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        # Comandos en PATH (Linux / Docker / Mac)
        shutil.which("google-chrome"),
        shutil.which("google-chrome-stable"),
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
        shutil.which("msedge"),
        shutil.which("chrome"),
    ]

    for ruta in candidatos:
        if ruta and os.path.exists(ruta):
            return ruta

    return None


def convertir_html_a_pdf(html_content):
    """
    Convierte una cadena de HTML con CSS moderno, tipografías y SVGs a un binario PDF
    utilizando el motor nativo de Chromium headless.
    Si no encuentra Chromium, utiliza PyMuPDF como fallback de seguridad.
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

            cmd = [
                browser_bin,
                '--headless=new',
                '--disable-gpu',
                '--no-sandbox',
                '--disable-dev-shm-usage',
                '--no-pdf-header-footer',
                f'--print-to-pdf={temp_pdf}',
                temp_html
            ]

            res = subprocess.run(cmd, capture_output=True, timeout=30)
            if res.returncode == 0 and os.path.exists(temp_pdf):
                with open(temp_pdf, 'rb') as f_pdf:
                    return f_pdf.read()
            else:
                stderr_msg = res.stderr.decode('utf-8', errors='ignore')
                logger.warning(f"Chromium PDF falló (código {res.returncode}): {stderr_msg}. Usando fallback...")
        except Exception as e:
            logger.error(f"Error ejecutando Chromium headless: {e}. Usando fallback...")
        finally:
            if temp_html and os.path.exists(temp_html):
                try: os.remove(temp_html)
                except Exception: pass
            if temp_pdf and os.path.exists(temp_pdf):
                try: os.remove(temp_pdf)
                except Exception: pass

    # Fallback de seguridad usando PyMuPDF (fitz)
    try:
        import fitz
        doc = fitz.open('html', html_content.encode('utf-8'))
        return doc.convert_to_pdf()
    except Exception as e:
        logger.error(f"Fallback PyMuPDF también falló: {e}")
        raise RuntimeError(f"No se pudo generar el PDF: {e}")
