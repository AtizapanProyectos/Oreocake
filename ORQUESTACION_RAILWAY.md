# Centro de Orquestación HOPE

## Preparación y límites

El motor permanece desactivado hasta `ORQUESTADOR_ACTIVO=1`. No se ha desplegado,
no se han aplicado migraciones en Railway ni se han autorizado nuevas credenciales.
El panel conserva `/panel-admin/automatizacion-notas/`; el indicador usa hora CDMX.
`orquestar_procesos` sustituye a los dos comandos anteriores, eliminados del código.
Retira sus programaciones durante el corte y activa únicamente el cron maestro.

El selector del panel comparte el modo con el servicio cron. En Manual, el modo
predeterminado, el maestro atiende solicitudes individuales y lotes filtrados. En Automático
también busca citas elegibles desde dos horas después de su inicio y procesa pendientes
históricos. Las solicitudes individuales conservan prioridad y no tienen espera por fecha.
Si ya tienen bitácora válida e IPP, pasan a la preparación del reporte.
El motor no modifica su estado. Inasistencias presenciales se muestran pero no se procesan.
Una etapa manual ejecuta sus prerrequisitos y deja detenidas las etapas posteriores:
seleccionar **Flujo completo** permite continuar. Nunca se repite una entrega acreditada.
Entregas inciertas, notas modificadas y marcas históricas ambiguas necesitan revisión
operativa; el botón no autoriza un reenvío de un correo incierto.

## Corte único

1. Identificar en Railway los dos servicios por su Start Command: `enviar_reportes_citas`
   y `generar_bitacoras_historicas_meet`.
2. Desactivar ambas programaciones y esperar a que terminen las ejecuciones activas.
   Retirar los dos servicios dedicados al cron una vez confirmada su identidad.
3. Respaldar la base y los PDF locales actuales antes de reemplazar el contenedor web.
   Los archivos locales del contenedor anterior no aparecen automáticamente en un cron nuevo.
4. Desplegar el código con `ORQUESTADOR_ACTIVO=0` en web y cron; aplicar una sola vez
   `python manage.py migrate`. Revisar previamente las migraciones pendientes: esta entrega
   incluye `0086_centro_orquestacion` y `0087_solicitud_lote_sesion`.
5. Si se conservaron los archivos locales o se restauraron desde el respaldo en `MEDIA_ROOT`,
   ejecutar en ese servicio `python manage.py preservar_pdf_reportes --limite 100`.
   Repetir con `--despues-id ULTIMO_ID` hasta terminar. No borra originales ni envía correos.
   Los archivos que ya se perdieron se reportan como ausentes; no se inventa su recuperación.
6. Ejecutar `python manage.py orquestar_procesos --reconciliar --limite 100`.
   Repetir con `--despues-id ULTIMO_ID` hasta obtener cero reconciliadas.
   Este modo escribe estados persistentes, pero no contacta servicios ni envía.
7. Preparar **un solo servicio continuo** del mismo repositorio y versión que la web:
   - Start Command: `python manage.py orquestar_procesos --continuo --limite 25 --segundos 240`
   - Cron Schedule vacío para mantener el maestro disponible.
   - Misma BD y credenciales de Google, Groq y SMTP que la web.
   - Chromium instalado por `railpack.json`; conservar el comando Gunicorn de la web.
8. Configurar `ORQUESTADOR_ACTIVO=1` en ambos servicios. Activar solamente el cron nuevo.
   La misma bandera activa el nuevo panel y desvía las acciones antiguas al coordinador.
9. Verificar una cita lista, una bloqueada y una entrega parcial. Confirmar que cerrar el panel
   no elimina solicitudes, que no se altera `Cita.estado` y que ambos destinatarios se registran
   por separado. Verificar descargas autenticadas de los PDF nuevos.

El límite temporal se comprueba **entre etapas**: una llamada en curso puede superar los
240 segundos totales. Google tiene timeout por solicitud; Groq 45 s y sin reintentos SDK;
SMTP 30 s. La reserva por etapa vence a los diez minutos. Una ejecución que encuentra
la reserva global ocupada termina sin procesar. Railway omite el siguiente cron si la
ejecución anterior aún no termina: https://docs.railway.com/cron-jobs

## Google privado

`GOOGLE_TOKEN_JSON` debe corresponder a la cuenta organizadora HOPE, tener refresh token
y autorización de Calendar y lectura de Drive (`drive.readonly`). Cambiar los scopes en
un JSON no otorga permisos: reautorizar con Google cuando falten. Evitar OAuth externo
en Testing para operación permanente. La aplicación no crea permisos públicos.

Validar con un documento **nuevo y privado**, no solo uno que ya se hizo público.
El lector exporta Google Docs mediante Drive API y rechaza HTML. Archivos Word binarios
no son transcripciones exportables por esta ruta; requieren conversión o revisión manual.
La interfaz de conexión OAuth completa no se añade en esta entrega: se conserva la
configuración por secreto existente.

## Almacenamiento y operación

Los PDF nuevos se guardan como BLOB privado en MySQL mediante `ArchivoProceso`.
Así comparten persistencia entre web y cron sin exponer URLs de media. Dimensionar
almacenamiento y backups, y confirmar `max_allowed_packet` para el tamaño real de PDF.
Las referencias locales antiguas siguen siendo legibles donde exista el archivo.

El panel muestra las incidencias y los intentos por cita. Las etapas transitorias de
una cita solicitada pueden reintentarse con su límite habitual. Cambiar a Manual evita
reclamar nuevas etapas automáticas. Una etapa externa que ya arrancó puede terminar.
Los cron previos no deben reactivarse en paralelo. Para detener, apagar primero el cron,
esperar su terminación y cambiar la bandera; no borrar trabajos ni volver a reenviar históricos.

## Validación aislada

```
venv/Scripts/python.exe manage.py test core.test_orquestador core.test_flujo_clinico --settings=hope_project.settings_flujo_test --noinput
```

No usar settings de producción para tests. SQLite no verifica las garantías de bloqueo de
MySQL. Antes de activar, ejecutar las pruebas de concurrencia en MySQL desechable, separado
de Railway; Docker no estuvo disponible durante esta implementación. También falta la
verificación con OAuth, SMTP y cuotas reales en el despliegue controlado.

El lanzador `scripts/test_orquestador_mysql.py` importa exclusivamente
`settings_flujo_test`, fija el host a `127.0.0.1` y utiliza la base de prueba
`test_hope_orquestador_aislado`. Requiere variables `HOPE_MYSQL_TEST_USER`,
`HOPE_MYSQL_TEST_PASSWORD`, `HOPE_MYSQL_TEST_PORT` (por defecto 3307) y
`HOPE_MYSQL_TEST_CONFIRM=BASE_LOCAL_DESECHABLE`. Solo usar credenciales de una
instancia local desechable; el runner crea y elimina su base de pruebas.

```
venv/Scripts/python.exe scripts/test_orquestador_mysql.py
```
