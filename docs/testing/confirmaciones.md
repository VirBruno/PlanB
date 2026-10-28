# Notificación inmediata de plan confirmado

## Flujo y atomicidad

La vista POST `plans.views.confirm_plan_proposal` usa el servicio
`plan_confirmation_service.confirm`, que envía el JWT de sesión a la RPC existente
`public.confirm_plan_proposal(plan_id, proposal_id)`. No se agrega otro flujo ni
una escritura de notificaciones desde Django.

La migración incremental `202610260002_plan_confirmed_notifications.sql` reemplaza
el cuerpo de esa RPC. Conserva la firma, retorno, validaciones de owner vigente,
plan activo, propuesta del propio plan con fecha/ubicación y ganador único positivo
para votos/combate. Después de fijar `selected_proposal_id`, copiar `date_pick` a
`scheduled_at`, fijar `confirmed_at` y limpiar la ubicación tentativa, inserta
`plan_confirmed` para todos los miembros actuales del grupo, owner incluido.

Todo sucede en la transacción de la RPC: si falla cualquier INSERT, también se
revierte la confirmación y los avisos insertados para otros destinatarios. El aviso
es inmediato al confirmar, sin esperar el comando de recordatorios ni un render.
No se filtra por la ventana de 24h; se respetan las fechas aceptadas por la RPC
existente. No se agregan email, push, dependencias ni variables de entorno.

Se bloquea primero el grupo y después el plan, en el mismo orden que los
recordatorios y la eliminación del grupo, para evitar invertir esos locks.
Se revalida el grupo del plan después de bloquearlo. La membresía owner y las
membresías destinatarias se bloquean con `FOR SHARE` durante la operación.
No se notifica a antiguos miembros, outsiders o invitados pendientes.

## Schema, privacidad y convivencia

Aplicar la nueva migración después de `202610260001_plan_reminders.sql`.
No se editan migraciones anteriores ni se recrean tablas. Los cambios son:

- El CHECK `notification_plan_required` admite `plan_id` para `plan_reminder` y
  `plan_confirmed`, y lo prohíbe para los demás tipos.
- `notification_invitation_required` continúa exigiendo `invitation_id` únicamente
  para `group_invitation`; por tanto no se pueden mezclar ambas referencias.
- El índice único parcial `notifications_one_plan_confirmed_idx` protege
  `(user_id, plan_id, type) WHERE type = 'plan_confirmed'`.
- La RPC inserta mediante `ON CONFLICT DO NOTHING` usando ese índice. El índice
  de recordatorios se conserva separado: el usuario puede tener un aviso de cada
  tipo para el mismo plan, con lectura independiente.

`CREATE OR REPLACE FUNCTION` conserva los grants existentes de confirmación.
No se conceden INSERT/UPDATE directos sobre `notifications`, ni nuevos permisos
de lectura. Se mantienen RLS, JWT, `mark_notification_read` y el contador HEAD
que cuenta todas las notificaciones propias sin leer.

`list_notifications` **ya soportaba** cualquier fila con `plan_id`: obtiene el
plan por FK, comprueba membresía actual, devuelve grupo/disponibilidad y proyecta
el snapshot. No requiere reemplazar su función ni ampliar el retorno o los grants.
La paginación sigue siendo 12 filas más una para detectar la página siguiente.
El servicio Django comparte validación/normalización de ambos tipos de plan.

El payload conserva `plan_name` y `scheduled_at` históricos. No decide identidad,
grupo ni autorización. Si el usuario pierde membresía, sigue viendo su aviso
histórico pero no el nombre vigente del grupo ni el botón **Ver plan**. El detalle
vuelve a autorizar con JWT/RLS y membresía actual, también frente a enlaces viejos.
La FK existente elimina ambos tipos de aviso al borrar el plan.

## Reintentos y reconfirmación

El modelo permite `Confirmado -> En organización -> Confirmado` mediante
`reopen_plan_decision`: limpia propuesta, fecha definitiva y fecha de confirmación.
Sólo después se pueden cambiar las propuestas o seleccionar otra. Repetir una
confirmación sobre un plan ya confirmado conserva el error `22023` existente.
No se convierte ese rechazo en un éxito silencioso ni se agrega un aviso nuevo.

La unidad de idempotencia solicitada es **usuario + plan**, sin versión de fecha.
Al reconfirmar, cada miembro actual que nunca recibió `plan_confirmed` recibe uno;
los demás conservan el original, su snapshot y su estado de lectura. No se envía
una actualización de fecha como segunda confirmación. Un nuevo miembro puede
recibir su primer aviso al reconfirmar; incorporarse sin reconfirmación no lo crea.
Esta decisión no cambia la idempotencia o ventana de `plan_reminder`.

No hay backfill al aplicar la migración. Los planes ya confirmados no generan un
aviso por abrir una página; lo harán para destinatarios pendientes si se reabren
y se confirman mediante el flujo existente.

## Bandeja

Mismo estilo y acciones que los recordatorios:

```text
Plan confirmado · Mates a la plaza
El encuentro quedó confirmado para el 7 de noviembre de 2026 a las 16:22.
Ver plan
```

La fecha se convierte a la zona activa de Django (por defecto Argentina), se
localiza el mes y se incluye el año para el historial. Suma al badge; **Marcar
como leída** usa POST/CSRF y sólo afecta esa fila. Abrir el plan no lo marca
automáticamente como leído, según la convención existente de la bandeja.

## Pruebas y ejecución

Desde `backend/`, con el virtualenv activo:

```powershell
python manage.py check
python manage.py check --settings=config.settings_test
python manage.py test apps.notifications apps.plans.tests.test_plan_decisions
python manage.py test
```

Desde la raíz: `git diff --check` y `git status`.

Los 16 tests nuevos de `apps.notifications.tests.test_plan_confirmed` usan SDK
real con transporte HTTP en memoria y cubren contrato de RPC única, JWT, retry,
error sin escrituras sueltas, bandeja de tres tipos, badge, lectura con CSRF,
enlaces, membresía perdida, conversión de fecha/hora incluso cruzando medianoche,
zona activa, validación de datos, escape HTML, paginación y ausencia de escrituras
al renderizar. Se mantienen los tests de invitaciones, recordatorios y decisiones.

`supabase/tests/plan_confirmed_notifications.sql` prueba en PostgreSQL la transición,
miembros vigentes, owner, outsiders/exmiembros, unicidad, reintentos, reconfirmación,
snapshot, independencia de recordatorios, RLS/grants, lectura, invitaciones,
CASCADE y atomicidad con fallo inducido después de insertar otros destinatarios.
También comprueba que siguen rechazándose propuestas ajenas y elecciones sin
resultado positivo. Todo termina con `ROLLBACK`.

Se ajusta `supabase/tests/plan_reminders.sql` para filtrar explícitamente
`plan_reminder`: sus fixtures ahora también generan confirmaciones. Sus conteos y
selección del aviso a leer deben distinguir los dos tipos; las búsquedas de la
bandeja contemplan las páginas adicionales. No cambia el generador ni scheduler.

Los scripts SQL deben ejecutarse únicamente en una instancia local/de pruebas
aislada con todas las migraciones. Ejemplo local desde la raíz, con `psql`:

```powershell
psql -h 127.0.0.1 -p 54322 -U postgres -d postgres -v ON_ERROR_STOP=1 -f supabase/tests/plan_confirmed_notifications.sql
psql -h 127.0.0.1 -p 54322 -U postgres -d postgres -v ON_ERROR_STOP=1 -f supabase/tests/plan_reminders.sql
```

La suite Django bloquea red/DNS y no demuestra ejecución de PL/pgSQL ni RLS real.
La prueba SQL de unicidad valida la garantía que arbitra INSERTs concurrentes;
no es una prueba de carga con dos sesiones simultáneas. No ejecutar estos scripts
en Supabase compartido. El despliegue requiere aplicar la migración antes de usar
la nueva confirmación, sin reconfigurar scheduler ni calendario.

Resultado local: **491 tests correctos** (475 existentes + 16 nuevos); el bloque
de notificaciones/decisiones ejecutó **98 tests correctos**. Ambos `manage.py check`
pasaron y `git diff --check` no informó errores de whitespace. No se ejecutaron
los scripts SQL: no había `psql`/PostgreSQL local ni motor Docker disponible.
La atomicidad, los locks y RLS están implementados y cubiertos por el script,
pero su ejecución real queda pendiente en un entorno de pruebas. No se hizo
commit, push, SQL remoto ni cambios en `.env`.

## Archivos

Creados:

```text
supabase/migrations/202610260002_plan_confirmed_notifications.sql
supabase/tests/plan_confirmed_notifications.sql
backend/apps/notifications/tests/test_plan_confirmed.py
docs/testing/confirmaciones.md
```

Modificados:

```text
backend/apps/notifications/services.py
backend/apps/notifications/templates/notifications/index.html
supabase/tests/plan_reminders.sql
README.md
docs/architecture/esquema-datos.md
```
