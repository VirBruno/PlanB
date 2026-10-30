# Notificaciones internas de deudas pendientes

## Infraestructura reutilizada

Se inspeccionaron las migraciones de invitaciones, `plan_reminder`,
`plan_confirmed` y gastos compartidos, las RPC de lectura/escritura, RLS,
servicios Django, badge, bandeja, paginación y popup existentes antes de editar.
El bloque nuevo usa la misma tabla `notifications`, JWT del usuario, clientes
efímeros con publishable key, `list_notifications`, contador de no leídas y
`mark_notification_read`. No instala dependencias ni un scheduler.

La migración incremental es
`supabase/migrations/202610300003_expense_debt_notifications.sql`.
Las migraciones previas, incluida la de gastos ya aplicada, no se modifican.
El backend nuevo requiere esta migración: sin las transferencias de la fuente
SQL común falla la consulta de gastos de forma controlada; no inventa un fallback
con otro cálculo. Coordinar aplicación de migración y despliegue del código.

## Autoridad, destinatario y monto

La RPC `notify_expense_debt(p_plan_id uuid, p_debtor_id uuid)` no admite monto,
acreedor, recipient alternativo, payload ni ID de notificación. Obtiene al actor
con `auth.uid()` y comprueba:

1. Actor miembro actual del grupo del plan.
2. Destinatario distinto del actor y también integrante actual.
3. Existencia de una transferencia positiva desde el destinatario al actor en
   las cuentas actuales del plan.

La interfaz sólo muestra el botón cuando el actor es acreedor de esa
transferencia y el deudor sigue en el grupo. Django revalida esa condición y
rechaza campos extra/repetidos; la RPC la comprueba nuevamente bajo locks.
Ser owner no permite notificar transferencias de otros acreedores.

## Una sola fuente de transferencias

Se movió el cálculo productivo de transferencias desde Python a
`planb_private.plan_expense_transfers`. No se cambian los gastos, participantes,
partes, reglas de centavos, balances ni orden determinístico previo por UUID.

El auxiliar suma pagos menos partes persistidas por persona. Ordena acreedores
y deudores por UUID ascendente y crea intervalos acumulados de sus saldos. La
intersección entre ambos intervalos representa el importe a transferir. Es la
misma semántica que avanzar por las dos listas consumiendo el menor saldo.
No utiliza float ni redondeos, ni busca el mínimo absoluto de transferencias.

La RPC pública `get_plan_expenses` conserva su firma, valida acceso mediante la
lectura anterior (movida al esquema privado) y añade `transfers` como JSON con
montos de texto decimal. `notify_expense_debt` consulta ese mismo auxiliar para
autorizar la pareja y obtener el monto. Django conserva los balances Decimal y
valida que las transferencias recibidas salden exactamente los balances, sin
calcular una segunda propuesta. El algoritmo anterior se conserva sólo como
oráculo de fixtures de tests offline, nunca como fallback productivo.

## Modelo, idempotencia y concurrencia

Se agrega `notifications.creditor_id uuid`, FK explícita a `profiles` con
`ON DELETE RESTRICT`. El CHECK exige acreedor distinto del destinatario para
`expense_debt_reminder` y NULL para otros tipos. Se amplía el CHECK de plan para
incluir el tipo nuevo; invitaciones, confirmaciones y recordatorios conservan
sus referencias e índices previos.

El índice único parcial abarca `(user_id, creditor_id, plan_id, type)` sólo para
`expense_debt_reminder` con `read_at IS NULL`. Garantiza como máximo una fila
pendiente por combinación. Personas, acreedores o planes diferentes tienen
pendientes independientes.

La RPC bloquea grupo → plan → membresías, en el mismo orden que la creación de
gastos. Así el cálculo autoritativo se realiza después de adquirir los locks y
se serializa con gastos nuevos y solicitudes repetidas. Bloquea también la fila
pendiente para coordinar con `mark_notification_read`.

- Primer aviso: inserta una notificación para el deudor, devuelve `created=true`.
- Repetir sin leer: actualiza el payload de la misma fila y devuelve
  `created=false`, aunque el monto no haya cambiado. Conserva ID, `created_at`
  y posición original de paginación; no vuelve a incrementar el badge.
- Si cambia el monto: otro click actualiza el snapshot pendiente desde los
  gastos reales. El payload incluye `notified_at`; no modifica la autorización.
- Después de leer: si sigue la transferencia, otro click crea una nueva fila.
  Peticiones simultáneas sin lectura intermedia comparten un único pendiente.
- Un fallo de red no dispara reintentos automáticos. Se puede repetir la acción:
  mientras el destinatario no haya leído entre intentos, no genera otra fila.

La notificación tiene `user_id` del deudor, `creditor_id` del actor, FK `plan_id`
y payload histórico: `plan_name`, `creditor_username`, `amount` como texto y
`notified_at`. El payload es contenido, nunca una fuente de autorización.
No se crea ninguna tabla de deudas ni registro de pagos.

## Cambios o desaparición de la transferencia

Una nueva solicitud se rechaza si esa pareja ya no tiene transferencia positiva,
incluso cuando ambos tengan saldos pero el algoritmo los empareje con otras
personas. Los avisos anteriores, leídos o no, quedan como snapshots históricos.
La bandeja aclara «Saldo al momento del aviso. Consultá los gastos para ver las
cuentas actuales». No se cancela ni actualiza automáticamente una notificación
al cargar gastos: no se agrega automatización fuera de alcance.

Exintegrantes conservan su historia de gastos y avisos recibidos, pero no pueden
consultar el plan ni recibir nuevos avisos mediante esta acción. Las referencias
de `notifications.plan_id` conservan el CASCADE existente: borrar el plan elimina
sus notificaciones, como ocurre con confirmaciones y recordatorios.

## Bandeja, privacidad y deep-link

`list_notifications` agrega al retorno `creditor_id`, `creditor_username` y
`expense_amount`. Conserva destinatario desde JWT, orden fecha/UUID, 12 elementos
más una fila para detectar siguiente página y los campos de los tres tipos
anteriores. El servicio valida los montos sin float y descarta campos extra.
Los timestamps siguen usando la zona horaria activa y los templates existentes.

El mensaje muestra «Saldo pendiente · nombre del plan», importe, acreedor y
«Ver gastos». Django genera el enlace al detalle con `?open=expenses`. El modal
recibe una marca HTML y JavaScript lo abre mediante su mecanismo normal; no
contiene rutas hardcodeadas. Al cerrar consume sólo ese parámetro y conserva
otros parámetros y el hash.

Si el destinatario perdió membresía, `plan_available=false` oculta el enlace
según el patrón previo de recordatorios/confirmaciones. Un enlace viejo vuelve
a comprobar acceso al detalle y a los gastos. Conocer un UUID no da acceso.

RLS de notificaciones sigue restringiendo SELECT a `user_id=auth.uid()`.
No se concede INSERT, UPDATE ni DELETE directo. Las RPC públicas son
`SECURITY DEFINER`, usan `search_path=''` y grants explícitos sólo para
`authenticated`. Los auxiliares privados no se exponen ni reciben EXECUTE para
roles API. La secret key no participa en el envío desde el navegador.

## Interfaz y errores

El botón incluye sólo CSRF y `debtor_id`; el plan está en la ruta de Django.
Durante el envío se deshabilita y se evita un segundo submit. El feedback usa
texto, sin HTML arbitrario. El aviso no cierra el modal, no recarga el detalle
ni modifica sus formularios. Un rechazo por cambio de deuda pide actualizar
los gastos; un resultado incierto permite reintentar con la garantía pendiente.

## Archivos del segundo bloque

Modificados:

- `backend/apps/notifications/services.py`
- `backend/apps/notifications/templates/notifications/index.html`
- `backend/apps/plans/services/expense_service.py`
- `backend/apps/plans/expense_views.py`, `backend/apps/plans/urls.py`
- `backend/apps/plans/templates/plans/detail.html`
- `backend/apps/plans/templates/plans/expenses/modal.html`, `panel.html`
- `backend/apps/plans/tests/test_expenses.py`, `browser_expenses.py`
- `backend/apps/users/static/users/js/plan-expenses.js`
- `backend/apps/users/static/users/css/plan-expenses.css`
- `docs/architecture/README.md`, `gastos-compartidos.md`

Creados:

- `backend/apps/notifications/debts.py`
- `backend/apps/notifications/tests/test_expense_debts.py`
- `supabase/migrations/202610300003_expense_debt_notifications.sql`
- `supabase/tests/expense_debt_notifications.sql`
- `supabase/tests/expense_debt_concurrency.py`
- `docs/architecture/notificaciones-deudas.md`
- `docs/testing/notificaciones-deudas.md`

## Cobertura y límites

34 tests nuevos de Django cubren permisos de botón/acción, contratos SDK,
validación de datos, bandeja, badge, lectura, paginación, timezone y deep-link.
La suite de gastos mantiene sus 47 casos; los tests de transferencias validan
ahora la fuente SQL usando fixtures esperadas. La prueba de navegador agrega
14 verificaciones por ancho (320, 390 y 1280 px) para deep-link, botones, doble
envío, feedback, errores, temas y cierre.

La prueba SQL reversible cubre autorización real, actualización del monto,
retry, lectura, privacidad, planes/deudores/acreedores independientes,
desaparición de deuda, permisos y equivalencia de un vector con varios saldos.
La prueba de concurrencia usa dos conexiones PostgreSQL reales y sólo admite
loopback; crea fixtures exclusivas y las elimina al terminar.
Las pruebas Django/navegador simulan Supabase: no demuestran RLS ni bloqueos.
Las pruebas SQL y de concurrencia se entregan para ejecución local autorizada;
no se ejecuta SQL remoto durante esta tarea.

Guía manual: [notificaciones de deudas](../testing/notificaciones-deudas.md).
