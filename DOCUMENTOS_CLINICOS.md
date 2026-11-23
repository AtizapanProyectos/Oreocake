# Archivos y guardado de bitácoras

## Comportamiento corregido

- `panel_doctor.html` y el expediente usan solicitudes con el token CSRF vigente.
  Una respuesta HTML 403/413/500, un vencimiento de sesión o un conflicto de
  bitácora se distinguen de un fallo de conexión. El contenido del formulario
  solo se limpia después de guardar con éxito.
- El panel envía `aprendizaje_paciente`, como espera el servidor. Se mantiene
  compatibilidad con formularios antiguos que envían `aprendizaje`.
- Guardar devuelve JSON y códigos HTTP adecuados. La escritura de la nota y el
  cambio de estado de su cita son atómicos; un fallo de almacenamiento conserva
  la nota anterior. Una cita ajena o inexistente no genera una nota huérfana.
- Repetir un guardado idéntico de una cita no duplica su bitácora. Si la cita ya
  tiene contenido distinto, devuelve 409 e indica abrir Editar, sin sobrescribir.
- Al editar, se carga el ID de la cita de esa bitácora para evitar arrastrar el
  de otra sesión abierta previamente.

## PDF, transcripción e IA

En el expediente, «Guardar documento como» permite elegir reporte adjunto o
transcripción. Los PDF se aceptan en ambos destinos. El archivo original queda
en el almacén clínico privado y conserva las descargas autorizadas existentes.

La IA acepta PDF de texto y escaneados, hasta 10 MB y 20 páginas. Extrae el texto
local y, si la página es escaneada y no funciona el OCR español del servidor,
renderiza la página como imagen y pide una transcripción a visión de Groq. El
texto se distribuye en los cinco campos clínicos para revisión del psicólogo.
No se requieren Tesseract ni un nuevo servicio externo para el fallback.

El modelo de visión se configura con `GROQ_VISION_MODEL`; por defecto se usa
`qwen/qwen3.8-27b`, documentado en [Groq Vision](https://console.groq.com/docs/vision).
El análisis de texto sigue usando `openai/gpt-oss-120b`. Se utiliza la variable
existente `GROQ_API_KEY`. No se cambian otros pipelines de IA.

Un PDF dañado, con contraseña, vacío o fuera de los límites muestra un error
legible. No se recortan silenciosamente documentos extensos: se pide dividirlos
si superan 60 000 caracteres. La lectura de escaneos tiene presupuesto de tiempo
y peticiones sin reintentos automáticos; Gunicorn permite hasta 120 segundos
para evitar que mate el worker durante la lectura de varias páginas.

La recuperación de una transcripción PDF vinculada también admite escaneos y
reutiliza el texto extraído en caché de servidor durante diez minutos. Se mantiene
la comprobación de versiones antes de guardar las notas derivadas.

## Verificación

```powershell
.\venv\Scripts\python.exe manage.py test core.test_documentos_clinicos core.tests.SesionPresencialTests core.test_sintesis_clinica core.test_covisiones --settings=hope_project.settings_flujo_test --noinput
node tests/solicitudes_clinicas.test.cjs
```

Pruebas aisladas con PDF sintéticos y Groq simulado; sin datos clínicos reales,
correo ni escrituras en producción. La clave Groq no está configurada localmente,
por lo que la llamada real al proveedor no forma parte de estas pruebas.

La batería antigua `core.tests.AgendamientoSeguroTests` tiene cuatro fallos
reproducibles también cargando `core/views.py` del commit previo: tres por
configuración PayPal del entorno aislado y uno por una expectativa antigua del
flujo de pago. No se modifican pagos como parte de esta corrección.

No hay cambios de modelo ni migraciones. El almacenamiento clínico sigue
requiriendo el volumen Railway ya definido; si falta, se informa el problema sin
guardar archivos en almacenamiento efímero ni marcar la cita como completada.
