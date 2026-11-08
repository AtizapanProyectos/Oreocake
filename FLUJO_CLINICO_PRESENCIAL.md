# Flujo clínico de citas presenciales

## Alternativa: transcripción en vivo en el navegador

La grabadora compartida ofrece reconocimiento local (`SpeechRecognition.processLocally`) cuando el navegador tiene soporte y el paquete español instalado. El botón de preparación solicita al navegador instalar ese paquete. No se cambia automáticamente al proveedor remoto. La alternativa «Usar servicio de reconocimiento del navegador» requiere selección explícita y avisa que el audio puede enviarse a ese proveedor. Si no hay soporte o se deniega el micrófono, el mensaje lo indica y la grabación de audio conserva su flujo existente.

El texto confirmado se acumula en memoria mientras se graba; los resultados provisionales se muestran aparte. Hay reconexión limitada por errores, espera del resultado final al detener, descarga TXT y aviso al salir. No se promete transcripción completa: interrupciones, suspensión de pestañas, ruido y errores de reconocimiento requieren revisión. No se guarda texto clínico en localStorage. Antes de cerrar, descargarlo o completar el análisis.

Después de guardar el audio, el terapeuta revisa/corrige el texto y solicita «Generar notas con este texto». Cada petición procesa hasta 10.000 caracteres con Groq; luego se consolida la evidencia. La API autentica al terapeuta asignado o administrador, exige CSRF y verifica la versión de audio antes y después del análisis. Guarda la transcripción en `GrabacionSesion` y un borrador en `AnalisisGrabacionClinica`, sin modificar la bitácora revisada ni enviar reportes. La revisión y guardado clínico siguen en el expediente.

Este camino no necesita Whisper, FFmpeg ni un proceso de fondo en Railway; sí requiere el servidor web, `GROQ_API_KEY`, la migración 0074 ya aplicada y el almacenamiento de audio existente. Los audios capturados con modo de texto quedan archivados hasta terminar el análisis para evitar que el consumidor de audio compita con este flujo. Los archivos cargados y el modo «Solo grabar audio» conservan su cola anterior. No transcribe retroactivamente archivos existentes. No modifica la configuración del volumen.

Validación: `node tests/transcripcion_en_vivo.test.cjs` y tests Django aislados `core.tests.SesionPresencialTests`. Es necesaria una prueba de micrófono y sesión prolongada en el navegador de destino; las pruebas automatizadas simulan reconocimiento y Groq y no garantizan su precisión.

## Trazabilidad

| Entrada | Vista y proceso | Datos y salida |
|---|---|---|
| Reserva presencial por el paciente, administración o confirmación de Clip cuando aplica | `guardar_cita_ajax`, `admin_guardar_cita_ajax` y `pago_exitoso_clip` en `core/views.py` | `Cita.modalidad`, terapeuta compatible y pase de acceso. `correo_pase_admision.html` incluye un enlace al IPP cuando hay una URL de solicitud disponible. |
| Sesión en línea en `detalle_paciente.html` | `generar-bitacora-meet-ia/<id>/` → `generar_bitacora_automatica_meet` → adjuntos de Calendar y texto de Google Docs → `_generar_bitacora_con_ia_de_texto` con Groq | Cinco campos de `HistorialClinico`, cita completada y telemetría de la automatización. El backend de HOPE usa Groq para estructurar las notas que obtiene de Google. |
| `panel_doctor.html` y `grabacion_sesion.html` | Ambos incluyen `snippets/grabadora_sesion.html`. MediaRecorder o selección de archivo → POST con CSRF a `api/guardar-grabacion-sesion/<id>/` | Audio en almacenamiento privado, un registro `GrabacionSesion` por cita y trabajo en cola. El psicólogo asignado y el superusuario pueden consultar el estado y abrir el audio. |
| Grabación cargada | `procesar_grabaciones_presenciales` → `core/grabaciones.py`. FFmpeg decodifica audio mono de 16 kHz. Whisper local transcribe en español. Reglas extraen pasajes para las cinco secciones clínicas. | La transcripción y la extracción quedan en `GrabacionSesion`. La bitácora se crea o actualiza si sigue pendiente de revisión. Se conserva una bitácora clínica previa y también el último resultado exitoso si falla un reemplazo. |
| Expediente del paciente | `detalle_paciente` consulta las citas de ese psicólogo y vincula `nota_clinica` y `grabacion_sesion`. | El expediente muestra bitácora, transcripción y notas extraídas. El psicólogo puede editar y guardar la bitácora desde el mismo expediente. |
| Generación y envío de Check-In | `enviar_reportes_citas` → `procesar_citas_pendientes_de_reporte` → `_procesar_reporte_de_una_cita_detallado` | Espera a que el psicólogo revise y guarde la bitácora generada desde audio. El flujo de Check-In posterior puede usar Groq, el motor PDF y Zoho. |
| Foto, PDF o DOCX en `detalle_paciente.html` | `procesar-archivo-ia/` → Groq Vision para imágenes, PyMuPDF para PDF y python-docx para DOCX. Las páginas de PDF sin texto pasan por OCR local en español. | Devuelve cinco campos al formulario. El paso **Guardar** envía los campos y adjunto a `guardar_historial_ajax`. |
| IPP presencial en HOPE, TECTUM o Beneficios | `sesion-previa/<id>/` → `formulario_previo_meet` → `formulario_organico.html`. El paciente titular inicia sesión. También puede registrarlo el terapeuta asignado. | Guarda IPP y satisfacción en `RespuestaFormularioOrganica` y `EvaluacionSesionPaciente` dentro de una transacción. Confirma en la misma página y ofrece volver al panel. Un reenvío conserva la primera respuesta. |

## Activación

1. Aplicar la migración `0068_procesamiento_grabaciones` en el entorno autorizado.
2. Instalar FFmpeg, `whisper-cli` y un modelo **multilingüe** GGML. Definir `HOPE_WHISPER_MODEL` con la ruta del modelo. Si los binarios están fuera del PATH, definir `HOPE_FFMPEG` y `HOPE_WHISPER_CLI` con sus rutas.
3. Configurar `HOPE_GRABACIONES_ROOT` con una ruta privada persistente que compartan el proceso web y el consumidor. Si ambos procesos se ejecutan en contenedores distintos, necesitan acceso al mismo almacenamiento de archivos.
4. Iniciar `python manage.py procesar_grabaciones_presenciales` como proceso supervisado y separado del servidor HTTP. El parámetro `--una-vez` consume como máximo un trabajo. Los audios existentes quedan archivados. Las cargas nuevas se encolan automáticamente.
5. Para PDF escaneado, instalar los datos de Tesseract en español (`spa.traineddata`) y, si es necesario, apuntar `TESSDATA_PREFIX` al directorio correspondiente. La clasificación de documentos sigue utilizando la configuración existente de Groq.

Las grabaciones anteriores al cambio se leen desde el almacén anterior como compatibilidad. Si el servidor publica ese directorio como contenido multimedia, trasladar esos archivos al almacén privado conservando sus rutas relativas para retirar las URL antiguas.

El audio se procesa localmente. Las reglas de notas conservan texto de la transcripción, dejan sin completar las secciones que no identifican y no hacen inferencias clínicas. Revisar el resultado con un audio de prueba en español y con el modelo que se utilizará en el servidor antes de activar la función.
