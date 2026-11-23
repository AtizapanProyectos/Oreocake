# Registro de donaciones PayPal

Todas las donaciones nuevas de los botones de HOPE se registran en `Donacion`,
separadas de `Cita`, `PlanMensual` y los formularios de contacto. No se crean sesiones
clínicas para el destinatario de una terapia donada.

## Consulta en administración

En Django admin: `/error-59/core/donacion/` → **Donaciones**.

- Búsqueda por folio, nombre/correo de PayPal, usuario HOPE, orden o captura.
- Filtros por origen, estado, medio, fecha de inicio y fecha de confirmación.
- Cantidad y monto bruto total de **Completadas**, en MXN y respetando los filtros actuales; no descuenta comisiones de PayPal.
- Exportación CSV de los registros filtrados, incluidos sus estados y referencias.
- Consulta de solo lectura. No permite crear, editar ni borrar donaciones manualmente.
- Acceso para superusuarios y personal con permiso `core.view_donacion`.

El nombre/correo comunicado por PayPal se conserva separado del usuario de HOPE.
Si no hay nombre, cuenta vinculada ni correo disponible, se muestra **Donante sin
identificar**. No se crean cuentas ni se vinculan usuarios automáticamente por correo.

## Criterio de confirmación

Por decisión de esta versión, la confirmación se basa en `COMPLETED` comunicado por
el SDK en el navegador. **No se consulta la API de PayPal desde el servidor.**
El admin y el CSV identifican expresamente este criterio. Las comprobaciones del
backend aseguran pertenencia, consistencia e idempotencia de los datos recibidos;
no constituyen una verificación independiente de que se haya realizado el cargo.

El registro guarda importe, moneda, medio PayPal, origen, estado, orden, captura,
fechas y datos disponibles del donante. El folio se envía a PayPal como
`custom_id=HOPE-DON-<UUID>`, además de la descripción actual de cada botón.

Orígenes: inicio, página de donaciones/Legado de Esperanza, Venezuela, Colombia e
invitación después de agendar. Esta última conserva la cita de origen, valida que
pertenezca al usuario autenticado y mantiene el importe fijo de $100 MXN.

## Flujo y recuperación

1. Se crea una donación **Pendiente** antes de crear la orden PayPal.
2. Se guarda la orden; los reintentos retoman la misma referencia.
3. Antes de capturar se marca `captura_iniciada`. Un resultado incierto conserva
   Pendiente y bloquea un nuevo intento de cobro en ese flujo.
4. Tras la respuesta COMPLETED se guarda la captura y el estado **Completada**.
   Orden y captura son únicas; un callback repetido no suma otra donación.
5. Cancelar antes de capturar marca **Cancelada**; pendientes y canceladas no suman.

Si se pierde la respuesta al guardar después del cobro, el navegador conserva los
datos mínimos de confirmación y ofrece **Reintentar guardado**, sin volver a capturar.
También intenta recuperarlos al recargar. Si storage está bloqueado, existe respaldo
en memoria mientras siga abierta la pestaña.

Si se cierra antes de recibir o conservar la respuesta de captura, no hay una API
que permita resolver automáticamente el cargo en esta versión: se conserva el
pendiente y se indica contactar a HOPE con su referencia. Borrar la sesión del
visitante impide operar su registro anterior; el personal sigue pudiendo consultarlo.

## API interna

| Ruta | Método | Uso |
| --- | --- | --- |
| `/api/donaciones/iniciar/` | POST | Folio UUID idempotente, monto, origen y cita opcional |
| `/api/donaciones/estado/` | GET | Estado por folio o pendiente del origen/cita |
| `/api/donaciones/actualizar/` | POST | Registrar orden, iniciar captura, completar, cancelar o anotar incidencia |

POST requiere CSRF. Los visitantes se autorizan mediante un propietario aleatorio
conservado en su sesión; el folio por sí solo no da acceso. Un usuario autenticado
también puede recuperar donaciones vinculadas a su cuenta. Los endpoints no exponen
nombre ni correo del donante. El registro limita nuevas solicitudes a 20 por hora
y consultas/actualizaciones a 120 por minuto y sesión/cuenta usando la caché existente;
estos límites son por proceso cuando la caché es locmem.

## Validación y despliegue

```powershell
.\venv\Scripts\python.exe manage.py test core.test_donaciones core.test_planes_mensuales core.test_pagos_recepcion core.test_flujo_clinico --settings=hope_project.settings_flujo_test --noinput
node --test tests/donaciones_registro.test.cjs
```

Las pruebas usan SQLite y PayPal simulado, sin cobros ni datos clínicos reales.
CSS y JavaScript usan `versioned_static` para evitar recursos antiguos en caché.

La migración aditiva **0083_donacion** crea solo la tabla nueva y sus relaciones
opcionales. Revisar y aplicar a Railway antes de publicar/activar el código nuevo
en `main`. Verificar el estado del despliegue y abrir el listado del admin.

Esta versión no incluye transferencias, conciliación mediante API, webhooks,
reembolsos ni clasificación automática de donaciones anteriores a su publicación.
