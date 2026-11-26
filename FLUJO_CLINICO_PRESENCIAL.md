# Flujo clínico de citas presenciales

## Transcripción progresiva mediante Django y Groq

La grabadora conserva un MediaRecorder para el audio completo y otro para fragmentos independientes de aproximadamente 20 segundos. Django autoriza cada petición con sesión y CSRF, limita cada fragmento a 8 MB y llama a `whisper-large-v3-turbo` con la clave del servidor. No usa SpeechRecognition, paquetes de idiomas, FFmpeg ni un consumidor de transcripción local.

Los fragmentos se envían secuencialmente; un fallo conserva el fragmento y bloquea los posteriores hasta reintentar. El audio completo sigue grabándose. El medidor usa Web Audio. El texto es revisable antes de generar las notas con el endpoint existente. No se sobrescriben las notas manuales. Mantén la página abierta hasta ver «Transcripción guardada en el expediente». Si falla el guardado, puedes reintentar o descargar el texto. Los límites del proveedor y la conexión pueden retrasar el texto. Los cortes entre fragmentos pueden afectar palabras en los límites; la revisión clínica sigue siendo necesaria.

Al detener la grabación, el navegador guarda el texto disponible en GrabacionSesion una vez que el servidor confirma el audio. Los últimos fragmentos se guardan al llegar, y el botón **Guardar transcripción** persiste las correcciones sin generar notas ni llamar a Groq. Django verifica el permiso y la versión del audio en cada guardado. Al reemplazar un audio, el último texto queda identificado como anterior hasta que haya texto para la grabación nueva.

El expediente muestra el reproductor, un diálogo con el texto completo y la fecha del primer guardado, y la descarga. Las grabaciones antiguas pueden no tener una fecha de transcripción registrada.

Requiere GROQ_API_KEY para transcribir y almacenamiento privado de audio. Aplica **0075_metadatos_transcripcion** antes de servir esta versión. La validación automatizada usa datos ficticios y simula Groq.

## Trazabilidad

| Entrada | Vista y proceso | Datos y salida |
|---|---|---|
| Reserva presencial por el paciente, administración o confirmación de Clip cuando aplica | `guardar_cita_ajax`, `admin_guardar_cita_ajax` y `pago_exitoso_clip` en `core/views.py` | `Cita.modalidad`, terapeuta compatible y pase de acceso. `correo_pase_admision.html` incluye un enlace al IPP cuando hay una URL de solicitud disponible. |
| Sesión en línea en `detalle_paciente.html` | `generar-bitacora-meet-ia/<id>/` → `generar_bitacora_automatica_meet` → adjuntos de Calendar y texto de Google Docs → `_generar_bitacora_con_ia_de_texto` con Groq | Cinco campos de `HistorialClinico`, cita completada y telemetría de la automatización. El backend de HOPE usa Groq para estructurar las notas que obtiene de Google. |
| `panel_doctor.html` y `grabacion_sesion.html` | Ambos incluyen `snippets/grabadora_sesion.html`. MediaRecorder o selección de archivo → POST con CSRF a `api/guardar-grabacion-sesion/<id>/` | Audio en almacenamiento privado, un registro `GrabacionSesion` por cita y transcripción revisable. El psicólogo asignado y el superusuario pueden consultar el estado y abrir el audio. |
| Texto revisado | Agenda o expediente → Guardar transcripción → Generar notas con este texto. | El expediente consulta el texto de la cita y, cuando falta, el archivo vinculado a su bitácora. El borrador de Groq queda listo para revisión clínica. |
| Expediente del paciente | `detalle_paciente` consulta las citas de ese psicólogo y vincula `nota_clinica` y `grabacion_sesion`. | El expediente muestra bitácora, transcripción y notas extraídas. El psicólogo puede editar y guardar la bitácora desde el mismo expediente. |
| Generación y envío de Check-In | `orquestar_procesos` | Espera a que el psicólogo revise y guarde la bitácora generada desde audio. El flujo de Check-In posterior puede usar Groq, el motor PDF y Zoho. |
| Foto, PDF o DOCX en `detalle_paciente.html` | `procesar-archivo-ia/` → Groq Vision para imágenes, PyMuPDF para PDF y python-docx para DOCX. Las páginas de PDF sin texto pasan por OCR local en español. | Devuelve cinco campos al formulario. El paso **Guardar** envía los campos y adjunto a `guardar_historial_ajax`. |
| IPP presencial en HOPE, TECTUM o Beneficios | `sesion-previa/<id>/` → `formulario_previo_meet` → `formulario_organico.html`. El paciente titular inicia sesión. También puede registrarlo el terapeuta asignado. | Guarda IPP y satisfacción en `RespuestaFormularioOrganica` y `EvaluacionSesionPaciente` dentro de una transacción. Confirma en la misma página y ofrece volver al panel. Un reenvío conserva la primera respuesta. |

## Activación

1. Aplicar las migraciones pendientes en el entorno autorizado antes de arrancar la nueva versión.
2. Configurar GROQ_API_KEY para transcribir fragmentos y generar borradores desde texto revisado.
3. Configurar HOPE_GRABACIONES_ROOT con una ruta privada persistente. En Railway, los adjuntos y transcripciones de bitácora se guardan en notas_clinicas dentro del volumen montado.
4. Para PDF escaneado, instalar los datos de Tesseract en español y configurar TESSDATA_PREFIX si es necesario.

Los archivos históricos se consultan con compatibilidad para sus rutas anteriores. El comando verificar_grabaciones permite revisar el almacenamiento y el acceso a los archivos sin escribir en la base de datos.
