# Panel manual del flujo clínico

Ruta conservada: `/panel-admin/automatizacion-notas/`.
Implementación: `core/flujo_clinico.py`, con plantilla y estáticos propios.
Solo superusuarios. Lecturas sin efectos secundarios; ejecuciones mediante POST y CSRF.

## Lo verificado en el código existente

- `enviar_reportes_citas` llama al pipeline de `core/views.py`. La ventana se calcula
  desde **el inicio** de la cita: 60 minutos de sesión + 60 minutos de espera.
- Calendar entrega referencias a documentos. La aplicación recupera el texto; Groq
  estructura los cinco campos de la bitácora. Groq no consulta Google Docs por sí mismo.
- `_construir_datos_checkin_paciente` y `_construir_datos_checkin_psicologo` calculan
  métricas y usan Groq para redacción. Ambos tienen respaldo de texto cuando falta
  `GROQ_API_KEY` o falla la llamada. El reporte puede generarse sin Groq; la extracción
  automática de una bitácora nueva sí depende de IA.
- `reporte_enviado=True` también puede significar PDF generado sin enviar correo o
  envío parcial. El panel usa `ReporteClinicoPDF.correo_enviado` por destinatario.

## Operación

1. Aplicar fecha o rango, organización, paciente, psicólogo y estado.
2. Revisar el mapa agregado y las tarjetas. Agenda agrupa por fecha y Kanban por
   estado; ambos muestran la página actual. Los totales y lotes incluyen todas las
   páginas filtradas, sin el antiguo corte de 1,200 citas.
3. Elegir una etapa o flujo completo. Seleccionar 1, 2, 5, 10, todos los pendientes
   o únicamente fallidos. Revisar el resumen antes de ejecutar.
4. El navegador solicita las etapas secuencialmente y conserva el progreso visible.
   Un fallo detiene esa sesión; puede continuar con otras. Un error de transporte
   detiene el lote: actualizar el panel antes de reintentar por resultado incierto.
5. Detener evita iniciar el siguiente paso. No interrumpe una petición ya enviada.
   Cerrar la pestaña detiene el encadenamiento, no crea trabajo en segundo plano.
6. Actualizar al terminar para recalcular todos los contadores y los filtros.

Etapas independientes:

| Etapa | Acción |
| --- | --- |
| Meet | Consulta el evento y guarda referencias a sus adjuntos. |
| Notas | Exporta texto autenticado y lo conserva en la BD para la siguiente etapa. |
| Groq | Estructura los cinco campos y guarda el resultado intermedio. |
| Expediente | Completa campos vacíos del historial vinculado a esa cita. |
| IPP | Comprueba formulario con respuestas de esa cita y paciente. |
| Reportes PDF | Reutiliza PDFs existentes o ejecuta el pipeline con `solo_generar=True`. |
| Correo | Usa los PDF archivados; envía únicamente a destinatarios sin evidencia de envío. |

Si existe una nota clínica, se extraen directamente sus campos. Meet y Groq no
son necesarios y no se sobreescribe el contenido guardado. Una nota de audio con
revisión pendiente impide avanzar. El modelo histórico no identifica inequívocamente
la autoría manual: el panel dice «nota existente» cuando no hay evidencia suficiente,
en lugar de atribuir automáticamente al psicólogo todo historial sin logs.

## Orden y protección de los envíos

Los lotes se ordenan por fecha, hora e ID. Antes de generar PDF o enviar correo,
el servidor busca citas previas confirmadas/completadas del paciente, incluso fuera
del filtro, organización o página. Si alguna carece de envío a ambos destinatarios,
se muestra su ID y se bloquea la cita posterior. No se omite silenciosamente esa cita.
Falta de IPP bloquea reportes y correo en este panel, sin sustituirlo por otro histórico.

Los pasos del panel bloquean la fila de paciente y cita durante cada petición para
serializar ejecuciones entre workers. Esta protección y el orden cronológico se
aplican a este panel; **los comandos, el gestor de reportes y el monitor IPP original
no se modificaron**. No ejecutar en paralelo reenvíos desde esas otras herramientas.

El SMTP y la BD no forman una transacción distribuida: una caída después de la
aceptación SMTP y antes del commit puede dejar un resultado incierto. Los éxitos
parciales normales sí quedan persistidos y se omiten al reintentar.

## Acceso a Google

El panel exporta únicamente URLs de documentos de `docs.google.com` y
`drive.google.com` mediante `files.export` autenticado. No extrae contenido de una
página HTML de login ni solicita URLs arbitrarias. Un 401/403/404 se presenta como
acceso insuficiente o documento no disponible; no prueba por sí solo que sea privado.

La cuenta del token debe tener acceso al documento y un permiso OAuth admitido
para exportar, por ejemplo `drive.readonly` o `drive.meet.readonly`; un permiso de
Calendar no concede lectura de documentos. No se cambiaron tokens ni permisos.
Referencia: [Google Drive files.export](https://developers.google.com/workspace/drive/api/reference/rest/v3/files/export).

## Activación y validación

Se requiere la migración **0072_estadoflujosesion** antes de servir el nuevo panel.
Agrega la tabla de resultados intermedios y las opciones Meet/IPP de las bitácoras.
No modifica notas ni reportes históricos. No se aplicó a Railway durante este trabajo.
Los resultados intermedios contienen información clínica privada, con el mismo
acceso administrativo del módulo; no se incluyen en el JSON de la pantalla ni en logs.

La generación PDF conserva deliberadamente la semántica original de
`reporte_enviado`: incluso en modo solo generar puede quedar en True. Para completar
esos envíos se usa la etapa Correo de este panel, que consulta evidencia por destinatario.

Pruebas aisladas (SQLite en memoria, correo en memoria, sin importar settings de producción):

```powershell
venv/Scripts/python.exe -m django test core.test_flujo_clinico --settings=hope_project.settings_flujo_test --noinput
node --check static/js/flujo_clinico.js
```

La configuración aislada omite las migraciones históricas específicas de MySQL en
tests; crea el esquema desde modelos. La consistencia de la nueva migración se
verificó aparte con el autodetector de Django y SQLite, sin conexión a producción.
Google, Groq, SMTP y el motor PDF real no se invocaron. La revisión visual en navegador
no pudo completarse porque el navegador integrado no alcanza el servidor local y
el acceso al navegador de Windows expiró sin autorización.
