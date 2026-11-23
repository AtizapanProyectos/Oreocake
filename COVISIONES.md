# Co-Visión: bloqueos individuales, noviembre–diciembre de 2026

Modelo editable: `BloqueoAgendaPsicologo`. Administración:
`https://espaciohope.com/error-59/core/bloqueoagendapsicologo/`.
Permite cambiar psicólogo, fecha, intervalo, motivo y estado activo. Los intervalos
son del mismo día y se interpretan en hora local de Ciudad de México.

El cálculo común de disponibilidad excluye cada sesión de una hora que se solape
con un bloqueo activo del psicólogo. Aplica a todos los servicios y modalidades,
reservas de paciente, recepción, administración, transferencia, reagendamientos
y plan mensual. Las citas ya existentes no se modifican.

## Calendario aprobado

| Fechas de 2026 | Horario | Usuarios de acceso |
| --- | --- | --- |
| 24/11, 08/12, 22/12 | 08:00–09:00 | psi.alevi, psi.clopez, psi.ecastañeda |
| 24/11, 08/12, 22/12 | 09:00–10:00 | psi.storres, psi.glopez |
| 25/11, 09/12, 23/12 | 19:00–20:00 | psi.guribe, psi.mceballos |
| 25/11, 09/12, 23/12 | 20:00–21:00 | psi.cceja |
| 26/11, 10/12, 24/12 | 18:00–19:00 | psi.aorozco, psi.jromero, psi.msanchez |

Todos los usuarios llevan `@espaciohope.com`. En producción, el usuario
`psi.ecastañeda` tiene email `psi.ecastaneda@espaciohope.com` y `psi.jromero`
tiene email `jesus.romero@espaciohope.com`; se resuelve por usuario o email,
exigiendo una única coincidencia. Prevalecen las cuentas proporcionadas:
`psi.glopez` y `psi.guribe` figuran como Gonzalo y Gweyneth en la base.

**Karla (`psi.kecheverria`): sin bloqueo, pendiente.** Participa con el grupo del
jueves 18:00–19:00; se indicó que ese día entraría una hora más tarde. El usuario
pidió dejar pendiente su configuración. No bloquearle ni 10:00 ni 18:00.

33 bloqueos fechados, ninguno fuera de estas fechas. Participar en otro grupo
voluntariamente no genera bloqueos adicionales. No se crean citas, Meet ni correos.

## Activación

La base por defecto es producción. La migración 0084 agrega solamente la tabla
y sus restricciones; no carga datos ni desactiva reglas.

1. Aplicar `python manage.py migrate core 0084` después de revisar el plan de
   migraciones y confirmar que las anteriores están aplicadas.
2. Desplegar el código nuevo antes de cambiar las reglas de agenda.
3. Previsualizar: `python manage.py cargar_covisiones_2026`.
4. Aplicar: `python manage.py cargar_covisiones_2026 --aplicar`.

La carga resuelve todas las identidades antes de escribir. En una transacción,
crea solo registros faltantes y desactiva las dos reglas globales originales
(martes 08:00 / miércoles 19:00, anclas 27–28/10/2026, duración 90 minutos,
ciclo 15 días, nombre Co-Visión). No toca otras reglas ni cambia citas.
Repetirla con el calendario intacto no duplica registros; no reactiva los bloqueos
desactivados manualmente. Después de editar fechas u horas, administrar desde el
modelo; no volver a cargar el calendario original, pues recrearía espacios movidos.

Auditar después: `python manage.py reportar_conflictos_covisiones --dias 90`.
Incluye bloqueos individuales y citas pendientes, además de las reglas globales.
La consulta inicial detectó las citas #1986 (Jesús) y #2045 (Angélica), ambas
el 26/11/2026 a las 18:00. Se conservan; coordinar cualquier reprogramación por separado.

## Reversión

Desactivar en el admin los bloqueos del motivo
`Co-Visión · noviembre–diciembre 2026`. Si se necesita restaurar el esquema
anterior, reactivar únicamente las dos reglas originales previamente activas
(en la auditoría, IDs 1 y 2). Hacer ambos cambios en una transacción de gestión
si se requiere una transición sin estado intermedio. No borrar citas ni revertir
la migración mientras esté desplegado código que consulte esta tabla.

## Verificación local aislada

`python manage.py test core.test_covisiones core.test_planes_mensuales core.test_pagos_recepcion --settings=hope_project.settings_flujo_test --noinput`

Este entorno usa SQLite en memoria y correo local; no conecta a producción.
