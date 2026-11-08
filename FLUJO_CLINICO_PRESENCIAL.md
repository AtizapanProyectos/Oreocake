# Flujo clínico de citas presenciales

## Trazabilidad

| Entrada | Vista y proceso | Datos y salida |
|---|---|---|
| Reserva presencial por el paciente, administración o confirmación de Clip cuando aplica | `guardar_cita_ajax`, `admin_guardar_cita_ajax` y `pago_exitoso_clip` en `core/views.py` | `Cita.modalidad`, terapeuta compatible y pase de acceso. `correo_pase_admision.html` incluye un enlace al IPP cuando hay una URL de solicitud disponible. |
| Sesión en línea en `detalle_paciente.html` | `generar-bitacora-meet-ia/<id>/` → `generar_bitacora_automatica_meet` → adjuntos de Calendar y texto de Google Docs → `_generar_bitacora_con_ia_de_texto` con Groq | Cinco campos de `HistorialClinico`, cita completada y telemetría de la automatización. El backend de HOPE usa Groq para estructurar las notas que obtiene de Google. |
| `panel_doctor.html` y `grabacion_sesion.html` | Ambos incluyen `snippets/grabadora_sesion.html`. MediaRecorder o selección de archivo → POST con CSRF a `api/guardar-grabacion-sesion/<id>/` | Audio en almacenamiento privado, un registro `GrabacionSesion` por cita y procesamiento local automático en segundo plano. El psicólogo asignado y el superusuario pueden consultar el estado, reintentar la transcripción y abrir el audio. |
| Grabación cargada | `core/grabaciones.py` con **faster-whisper local** (PyAV decodifica el audio; no usa FFmpeg/`whisper-cli` externos ni Groq). Modelo `tiny` por defecto. Reglas extraen pasajes para las cinco secciones clínicas. | La transcripción completa y la extracción quedan en `GrabacionSesion`. La bitácora se crea o actualiza si sigue pendiente de revisión. Se conserva una bitácora clínica previa y también el último resultado exitoso si falla un reemplazo. La primera transcripción descarga el modelo (~75 MB) en `GRABACIONES_ROOT/_modelos_whisper` o en `HOPE_WHISPER_DIR`. |
| Expediente del paciente | `detalle_paciente` consulta las citas de ese psicólogo y vincula `nota_clinica` y `grabacion_sesion`. | El expediente muestra bitácora, transcripción y notas extraídas. El psicólogo puede editar y guardar la bitácora desde el mismo expediente. |
| Generación y envío de Check-In | `enviar_reportes_citas` → `procesar_citas_pendientes_de_reporte` → `_procesar_reporte_de_una_cita_detallado` | Espera a que el psicólogo revise y guarde la bitácora generada desde audio. El flujo de Check-In posterior puede usar Groq, el motor PDF y Zoho. |
| Foto, PDF o DOCX en `detalle_paciente.html` | `procesar-archivo-ia/` → Groq Vision para imágenes, PyMuPDF para PDF y python-docx para DOCX. Las páginas de PDF sin texto pasan por OCR local en español. | Devuelve cinco campos al formulario. El paso **Guardar** envía los campos y adjunto a `guardar_historial_ajax`. |
| IPP presencial en HOPE, TECTUM o Beneficios | `sesion-previa/<id>/` → `formulario_previo_meet` → `formulario_organico.html`. El paciente titular inicia sesión. También puede registrarlo el terapeuta asignado. | Guarda IPP y satisfacción en `RespuestaFormularioOrganica` y `EvaluacionSesionPaciente` dentro de una transacción. Confirma en la misma página y ofrece volver al panel. Un reenvío conserva la primera respuesta. |

## Activación

1. Aplicar la migración `0068_procesamiento_grabaciones` en el entorno autorizado.
2. Instalar `requirements.txt` (incluye `faster-whisper==1.2.1`). **No hace falta instalar FFmpeg ni `whisper-cli` del sistema**: PyAV viene empaquetado con faster-whisper. No se usa Groq para transcribir.
3. Montar un volumen persistente en el **servicio web** de Railway (el mismo que ejecuta gunicorn) y definir `HOPE_GRABACIONES_ROOT` a una subcarpeta del punto de montaje. Ejemplo: si el volumen está montado en `/data`, usar `HOPE_GRABACIONES_ROOT=/data/grabaciones`. Sin esta variable la app usa `BASE_DIR/grabaciones_privadas`, que en Railway es efímero y se pierde en cada deploy.
4. No se necesita un worker separado: al subir el audio (o al abrir/consultar la pantalla de grabación) se lanza un hilo daemon que vacía la cola con `core/grabaciones.py`. El comando `python manage.py procesar_grabaciones_presenciales` sigue disponible para reprocesar manualmente; `--una-vez` consume como máximo un trabajo.
5. Para PDF escaneado, instalar los datos de Tesseract en español (`spa.traineddata`) y, si es necesario, apuntar `TESSDATA_PREFIX` al directorio correspondiente. La clasificación de documentos sigue utilizando la configuración existente de Groq.

Variables opcionales del motor local:

| Variable | Valor por defecto | Uso |
|---|---|---|
| `HOPE_WHISPER_MODELO` | `tiny` | `tiny`, `base`, `small` o una ruta local de modelo CTranslate2. |
| `HOPE_WHISPER_DIR` | `GRABACIONES_ROOT/_modelos_whisper` | Caché del modelo. Si está en el volumen, no se vuelve a descargar. |
| `HOPE_WHISPER_HILOS` | `1` | Hilos de CPU para CTranslate2. |
| `HOPE_WHISPER_DISPOSITIVO` | `cpu` | `cpu` o `cuda` (si el host tiene GPU). |
| `HOPE_WHISPER_PRECISION` | `int8` | `int8` para CPU; `float16` en GPU. |
| `HOPE_PROCESAR_GRABACIONES` | `1` | `0` desactiva el procesamiento automático (pruebas/mantenimiento). |

Para comprobar dónde está guardando y si el volumen está bien montado:

```powershell
$env:PYTHONIOENCODING="utf-8"
.\venv\Scripts\python.exe manage.py verificar_grabaciones
```

El comando (solo lectura) imprime la ruta efectiva, si está en un punto de montaje, los audios en disco, las filas de `GrabacionSesion` con audio OK/FALTA y la caché del modelo. En Railway se puede ejecutar desde la consola/shell del servicio.

Las grabaciones anteriores al cambio se leen desde el almacén anterior como compatibilidad. Si el servidor publica ese directorio como contenido multimedia, trasladar esos archivos al almacén privado conservando sus rutas relativas para retirar las URL antiguas.

El audio se procesa localmente. Las reglas de notas conservan texto de la transcripción, dejan sin completar las secciones que no identifican y no hacen inferencias clínicas. Revisar el resultado con un audio de prueba en español y con el modelo que se utilizará en el servidor antes de activar la función.
