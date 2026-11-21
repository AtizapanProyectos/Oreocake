# Plan Mensual HOPE

Permite elegir cuatro fechas distintas con el mismo terapeuta, servicio, modalidad
y número de integrantes. La última fecha puede estar hasta 29 días después de la
primera, dentro de los 90 días ofrecidos por la agenda y con dos horas de anticipación.

El precio usa `calcular_precio_sesion` multiplicado por cuatro, con comisión incluida:
individual $440; pareja $860; familiar desde $860 según integrantes. No hay descuento,
renovación automática ni saldo de sesiones para elegir después.

## Flujos

- **Paciente / PayPal:** selecciona cuatro fechas, firma el consentimiento existente,
  reserva cuatro citas pendientes durante 15 minutos y paga una vez. Antes de capturar
  se marca el plan como `procesando`; tras `COMPLETED` del SDK se confirman las cuatro.
- **Transferencia:** el botón arma una solicitud de WhatsApp con las cuatro fechas y
  el importe. El usuario envía su comprobante al equipo. Este paso no guarda ni bloquea
  citas; el personal revisa el pago y agenda el plan desde admindoc.
- **Admindoc:** elegir paciente HOPE, abrir «Agendar cuatro citas sin cobro», seleccionar
  fechas y confirmar. El importe es referencial; no se inicia ningún cobro. El botón
  para una cita suelta queda al pie del formulario, fuera de las columnas.

Cada sesión sigue siendo una `Cita` normal, ligada a `PlanMensual`. Meet, pases,
expediente y reportes siguen usando las integraciones existentes. Las notificaciones
se ejecutan después del commit; un fallo externo no deshace las cuatro citas.

## Recuperación y revisión

`reservado → procesando → confirmado`, o `reservado → cancelado / expirado`.

Las reservas vencidas se liberan al consultar agendas o planes. Una captura incierta
permanece en `procesando` y conserva sus horarios: no se libera automáticamente ni
permite iniciar otro plan para ese paciente. La referencia se conserva en el navegador
para reintentar la confirmación; también existe respaldo en memoria si storage está
bloqueado. Tras cerrar la pestaña sin una referencia recuperable, el personal debe revisar.

Para resolverlo desde admindoc: seleccionar al paciente, abrir el plan pendiente,
revisar el pago externamente, marcar la casilla de revisión y confirmar con su referencia
o liberar la reserva. Las decisiones quedan registradas en `EventoAuditoria`.
El registro de planes en Django admin es de solo lectura.

### Calendario y continuidad de atención

El modal comparte la identidad del banner: encabezado con franja turquesa, superficies
blancas y lilas, cuatro tarjetas numeradas y un calendario mensual con navegación,
fechas disponibles y botones de hora. Al elegir una hora se incorpora la sesión al
resumen. Las fechas siguientes se filtran para conservar un terapeuta común y el periodo
de 30 días. Cambiar servicio, modalidad o integrantes limpia la selección anterior.

La modalidad inicial procede de la última sesión atendida del servicio seleccionado;
si no hay historial de ese servicio, usa la última sesión atendida general y, en su
ausencia, En línea. Se consideran Completada y Confirmada con llegada Vino; no se usan
reservas, cancelaciones ni citas futuras solo confirmadas. En historial mixto se permite
continuar presencial cuando ya consta asistencia presencial.

La asignación por TratamientoPaciente tiene prioridad. Para individual también se
consulta la asignación heredada del perfil. Cuando existe una asignación, disponibilidad
y reserva se limitan a ese profesional; si es incompatible o no tiene horarios, no se
ofrecen otros terapeutas automáticamente. El pool general se usa cuando no hay asignación
para el servicio. Estos ajustes del modal no requieren una nueva migración.

La captura PayPal continúa en el navegador, igual que en el panel HOPE actual.
**No se añadió validación definitiva del cargo mediante la API/ID dinámico de PayPal.**
La referencia guardada y su unicidad aportan trazabilidad y evitan reutilizarla entre
planes; no prueban por sí mismas que hubo un cargo real.

## API interna

Todas las rutas parten de `/api/planes-mensuales/`, requieren sesión y conservan CSRF
en POST. `paciente_id` solo está permitido para superusuarios. Los convenios TECTUM
quedan fuera de este flujo.

| Ruta | Método | Entrada principal | Resultado |
| --- | --- | --- | --- |
| `disponibilidad/` | GET | `tipo_servicio`, `modalidad`, `integrantes_familia` | Mapas por terapeuta, `monto` y `por_sesion` |
| `reservar/` | POST | UUID `clave`, configuración, `citas` como JSON con cuatro objetos `{fecha, hora}` | Plan con cuatro citas; admin lo confirma sin cobro |
| `estado/` | GET | `plan_id` opcional | Plan solicitado o lista de planes activos del paciente |
| `actualizar/` | POST | `plan_id`, `accion` | Estado actualizado del plan |

Acciones: `capturar`, `confirmar` y `cancelar`. La confirmación del navegador manda
`resultado=COMPLETED` y `referencia`. La revisión administrativa requiere `revisado=1`.
Las fechas usan `YYYY-MM-DD`; se aceptan las horas flexibles que ya utiliza HOPE.

Reserva y confirmación son atómicas e idempotentes. Los terapeutas se bloquean en orden
de ID antes de comprobar las cuatro fechas; la selección respeta terapeuta asignado,
preferencias y carga. Se conserva la restricción histórica única de horario.

## Validación y despliegue

Pruebas aisladas, sin importar settings de producción:

```powershell
.\venv\Scripts\python.exe manage.py test core.test_planes_mensuales core.test_pagos_recepcion core.test_flujo_clinico --settings=hope_project.settings_flujo_test --noinput
node --check static/js/plan_mensual.js
```

Las pruebas usan SQLite en memoria y servicios externos simulados. Verifican precios,
disponibilidad conjunta, fechas, rollback, idempotencia, vencimientos, permisos, CSRF,
recuperación, revisión administrativa y regresiones. Los bloqueos concurrentes reales
de MySQL requieren un entorno MySQL aislado para validación adicional.

La migración **0082_plan_mensual** crea la tabla del plan y añade una FK nullable a
`Cita`, sin transformar registros históricos. Aplicarla en la fase de despliegue antes
de activar el nuevo servidor y publicar los estáticos habituales. Revisar el SQL en
el entorno de despliegue y seguir el procedimiento operativo de respaldo.

No se ejecutó esta migración contra Railway ni se realizaron cobros, envíos de correo
o creación de eventos reales durante la implementación.
