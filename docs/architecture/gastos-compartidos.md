# Gastos compartidos de un plan

## Arquitectura encontrada y alcance

El dominio vive en Supabase: Django consulta Data API con publishable key y el
JWT del usuario mediante clientes efímeros (`users.services.clients`). El ORM
local se reserva para sesiones. Planes y grupos tienen UUID y las migraciones
SQL son incrementales. `group_members` registra al owner como integrante real.
RLS permite leer planes a miembros actuales; el owner administra los planes.
Las RPC de grupos/invitaciones usan `auth.uid()`, `SECURITY DEFINER`, nombres
cualificados, `search_path=''` y grants explícitos. La lectura directa de
membresías sólo expone la propia fila; las RPC pueden devolver las identidades
del grupo tras verificar acceso. El detalle ya usa `<dialog>` para propuesta
ideal; conserva calendario, propuestas, clima, decisiones y notificaciones.

El primer bloque agrega crear, listar, calcular balances y proponer transferencias
dentro de un popup. No agrega pantallas, edición, eliminación de gastos, estados
del plan ni pagos. El segundo bloque agrega [notificaciones de deuda](notificaciones-deudas.md).

## Modelo y migración

Migración nueva: `supabase/migrations/202610300002_shared_expenses.sql`.

| Tabla | Columnas principales |
| --- | --- |
| `plan_expenses` | `id uuid`, `plan_id uuid`, `description text` (1–160, trim), `amount numeric(14,2)`, `paid_by uuid`, `created_by uuid`, `created_at timestamptz` |
| `plan_expense_participants` | PK `(expense_id uuid, user_id uuid)`, `share_amount numeric(14,2)` |

Monto mayor que cero, máximo $999.999.999.999,99. Partes no negativas: al
dividir un centavo entre tres personas, dos partes valen cero. Los importes
de entrada se validan **antes** de insertarse en numeric(14,2), para evitar
redondear silenciosamente un monto con fracciones de centavo. No se usa float.

Gastos → plan y partes → gasto usan `ON DELETE CASCADE`. Las identidades
referencian `profiles` con `ON DELETE RESTRICT`: salir del grupo no elimina su
participación ni su deuda, y no se permite borrar una identidad referenciada
accidentalmente. Una futura eliminación de cuenta con historial necesitará una
política explícita de anonimización. El nombre visible se resuelve del perfil
actual; se conserva su UUID, no una copia congelada del username.

No se referencian filas de `group_members` desde las partes históricas. No se
persisten balances, transferencias ni un participante ficticio de grupo completo.

## Escritura, TODO EL GRUPO y centavos

`create_plan_expense(plan, description, amount, paid_by, participants, all_group)`
es la única puerta de escritura para usuarios autenticados:

1. Obtiene al actor de `auth.uid()`; no acepta `created_by` del cliente.
2. Verifica acceso al plan por membresía actual, sin condiciones de estado.
3. Bloquea grupo, plan y membresías para impedir cambios mientras valida y
   guarda. Usa el orden de bloqueo grupo → dependientes de las altas existentes.
4. Revalida que pagador y participantes sean integrantes actuales; rechaza
   duplicados, NULL, reparto vacío, importes inválidos y selección ambigua.
5. Con `p_all_group=true` exige participantes vacíos y expande los integrantes
   actuales, incluido el owner. Django envía el flag, nunca un usuario especial.
6. Ordena los UUID de participantes de menor a mayor. Convierte el monto a
   centavos enteros, calcula cociente y resto de la división por cantidad.
   Todos reciben el cociente; los primeros `resto` UUID reciben un centavo más.
7. Inserta gasto y partes en la misma transacción de la RPC. Cualquier error
   revierte ambas escrituras.

Ejemplo: 100,00 / 3 = 10.000 centavos; cociente 3.333, resto 1. Resultado:
33,34 para el primer UUID, 33,33 y 33,33. El nombre, orden del formulario y
pagador no afectan el reparto. El pagador puede estar fuera de los participantes.

Cada parte queda congelada al guardar. Si luego entra alguien, no se agrega a
gastos previos. Si alguien se va, sus partes y pagos siguen en los cálculos;
aparece como exintegrante y pierde todo acceso y selección para gastos nuevos.

## Lectura y cálculos

`get_plan_expenses(plan)` produce una instantánea con integrantes actuales,
identidades necesarias (actuales e históricas), gastos y partes. Usa consultas
por conjuntos, con agregaciones ordenadas, sin una consulta por gasto/persona.
No usa la secret key ni amplía RLS de perfiles. Los montos JSON se devuelven
como **texto decimal**, evitando que el SDK los convierta a punto flotante.

El servicio Django verifica forma, UUID, tipos, importes, duplicados y que las
partes de cada gasto sumen exactamente su monto. Rechaza respuestas incompletas;
no presenta un fallo como si no hubiera gastos. Acumula `Decimal`:

- Total gastado = suma de los montos del plan.
- Pagado por persona = suma de los gastos cuyo pagador es esa persona.
- Parte consumida = suma de `share_amount` persistido para esa persona.
- Balance = pagado − parte consumida. Positivo: tiene a favor; negativo: debe.
- La suma de balances debe ser exactamente 0,00; si no, rechaza el resumen.

Desde el segundo bloque, Para saldar usa la fuente común de PostgreSQL
`planb_private.plan_expense_transfers`, también usada para autorizar avisos.
Python valida conservación de saldos sin producir otra solución. Se ordenan
deudores y acreedores por UUID ascendente. Se transfiere
el mínimo del saldo pendiente del deudor y del acreedor; se resta de ambos y se
avanza cuando alguno queda en cero. Nadie paga ni recibe de más. Se conservan
centavos y se llega exactamente a cero. Es determinístico; no busca el mínimo
absoluto de transferencias y puede compensar gastos entre personas que no
compartieron el mismo gasto. Las transferencias son una propuesta derivada, no
un registro de pagos realizados.

## Permisos

RLS habilitado en ambas tablas; `authenticated` recibe únicamente SELECT
filtrado por pertenencia actual al grupo del plan. Sin INSERT/UPDATE/DELETE
directos, ni acceso de `anon`, `service_role` o `planb_django_runtime` a estas
tablas/RPC. Las dos RPC tienen EXECUTE sólo para `authenticated`,
`SECURITY DEFINER`, `search_path=''` y verificaciones explícitas de identidad y
membresía antes de exponer información. No cambian policies ni RPC existentes.

Django verifica plan/grupo usando JWT, limita las opciones del formulario a la
instantánea de miembros actuales y la RPC valida nuevamente al guardar. Una
baja entre carga y envío es rechazada. La ruta de fragmentos requiere sesión,
CSRF para POST y respuestas privadas sin caché. IDs válidos no conceden acceso.

## Popup y limitaciones

La tarjeta aparece en el detalle para todos los estados del plan. La consulta
de gastos se realiza al abrir, evitando demorar el detalle si falta la migración
o el servicio está caído. La tarjeta muestra total/cantidad después de cargar.
`/planes/<uuid>/gastos/` es un endpoint de fragmentos GET/POST para el popup, no
una página de administración.

`<dialog>` nativo ofrece foco contenido y Escape. X devuelve el foco a la
tarjeta; Cancelar regresa al listado. Desktop: centrado y grande; móvil: ancho
adaptado con scroll interno. La tabla puede desplazarse horizontalmente en su
contenedor. Colores basados en variables existentes para light/dark.

Guardar usa fetch con CSRF. Éxito devuelve 201 y se hace otra lectura para
actualizar el popup y la tarjeta, sin recargar ni cambiar la URL, scroll o DOM
del detalle. Si falla la lectura posterior, se indica que el gasto se guardó.
Si el resultado del POST es incierto, se retira el formulario y se exige
actualizar el listado antes de otro intento. No hay reintentos automáticos ni
idempotencia entre pestañas: una futura ampliación puede agregar claves de
operación. El formulario requiere JavaScript y un navegador con `<dialog>`.

La lectura devuelve todo el historial del plan, sin truncado por el límite de
filas de Data API al usar un único JSON. Para volúmenes grandes convendrá
paginar el listado separando los agregados. El segundo bloque permite avisos
manuales sólo al acreedor; no hay edición, eliminación ni registro de saldos pagados.

## Archivos de implementación y pruebas

- `backend/apps/plans/services/expense_service.py`: RPC, validaciones, Decimal,
  resumen y transferencias.
- `backend/apps/plans/expense_forms.py`, `expense_views.py`, `urls.py`:
  formularios, fragmentos y ruta.
- `backend/apps/plans/templatetags/expense_money.py`, `__init__.py`:
  presentación de dinero sin float.
- `backend/apps/plans/templates/plans/detail.html` y
  `templates/plans/expenses/{modal,panel,form}.html`: tarjeta y popup.
- `backend/apps/users/static/users/{css/plan-expenses.css,js/plan-expenses.js}`:
  estilos y comportamiento localizado.
- `backend/apps/plans/tests/test_expenses.py`: 47 pruebas de cálculos,
  formularios, SDK con transporte simulado, acceso, CSRF e integración de vistas.
- `backend/apps/plans/tests/browser_expenses.py`: navegador real en 320, 390 y
  1280 px; dos gastos, temas, errores, cancelación, foco, Escape y conservación
  del detalle, con servicios simulados y sin conexiones externas de negocio.
- `supabase/tests/shared_expenses.sql`: integración reversible (ROLLBACK),
  membresías/RLS reales, reparto, límites, grants e historial.
- Este documento, `docs/architecture/README.md` y
  `docs/testing/gastos-compartidos.md`: índice, decisiones y prueba manual.

La prueba SQL necesita una base PostgreSQL/Supabase de pruebas preparada. La
suite Django usa SQLite, servicios simulados y red deshabilitada: no demuestra
la ejecución de RLS ni bloqueos PostgreSQL. La migración debe revisarse y
aplicarse explícitamente en un entorno autorizado antes de la prueba funcional
con datos reales. No se ejecutó SQL remoto durante esta implementación.
