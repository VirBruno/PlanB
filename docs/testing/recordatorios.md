# Recordatorios internos de encuentros confirmados

## Inspección y arquitectura

El repositorio no contiene scheduler: no hay Celery, APScheduler, cron, pg_cron,
management commands programados, workflows de GitHub Actions ni manifiestos de
hosting que programen trabajos. Esto describe lo versionado; no permite auditar
una configuración externa del hosting. No se instala ni activa infraestructura
de scheduling como parte de esta entrega.

Las notificaciones existentes usan `public.notifications`, RLS por destinatario,
la RPC `list_notifications`, un contador HEAD de no leídas y la RPC
`mark_notification_read`. `group_invitation` conserva su relación con la invitación,
estados y acciones. No existen modelos de dominio en el ORM Django.

Se agrega un servicio en `apps.notifications.reminders` y el management command
`generate_plan_reminders`. El servicio usa el cliente administrativo existente
únicamente para invocar una RPC atómica y obtener la cantidad insertada. La
selección, revalidación y escritura se ejecutan juntas en PostgreSQL. Separarlas
en consultas HTTP dejaría carreras entre lectura del estado/membresía e INSERT.
No hay llamadas de generación desde views, context processors, middleware,
login, calendario o templates.

## Ventana exacta y destinatarios

El reloj de referencia es `statement_timestamp()` de PostgreSQL al iniciar la RPC:

```text
status IS TRUE
AND selected_proposal_id IS NOT NULL
AND ahora < scheduled_at <= ahora + interval '24 hours'
```

La ventana es **(0, 24 horas]** de anticipación, con límite inferior abierto y
superior cerrado. NULL, fechas pasadas y encuentros que empiezan exactamente en
el corte quedan excluidos. La fecha tentativa sin propuesta definitiva no basta.
Finalizados e inactivos quedan excluidos aunque tengan fecha futura.

Programado cada hora, un plan confirmado con anticipación normal recibe el aviso
en la primera ejecución después de entrar a 24 horas: normalmente faltarán entre
23 y 24 horas. Si se confirma con menos de 24 horas, se recupera en la siguiente
ejecución mientras todavía sea futuro, incluso si faltan pocos minutos. Si el
proceso vuelve después de una interrupción, recupera los futuros dentro de la
ventana. No se envía un aviso retroactivo por un encuentro que ya comenzó.

Se recorren los miembros actuales de `group_members`, incluido el owner; no se
usa `created_by`, una lista congelada al confirmar ni usuarios de invitaciones
pendientes. Un integrante nuevo puede recibir su primer recordatorio en una
ejecución posterior, siempre dentro de la ventana. Quien salió antes del proceso
no recibe ninguno.

El reloj y `scheduled_at` son `timestamptz`: se comparan instantes, usando una
duración de 24 horas. El servidor SQL puede usar otro timezone. La UI convierte
la fecha a la zona activa de Django (por defecto `America/Argentina/Buenos_Aires`).

## Idempotencia, transacciones y permisos

La única migración incremental es
`supabase/migrations/202610260001_plan_reminders.sql`, posterior a las decisiones
de planes de `202610230001`. No recrea la tabla ni modifica políticas RLS.

- `notifications.plan_id`: FK explícita a `plans`, `ON DELETE CASCADE`.
- CHECK que exige plan para `plan_reminder` y conserva las reglas de invitaciones.
- Índice único parcial `(user_id, plan_id, type) WHERE type = 'plan_reminder'`.
- Índice de búsqueda de fechas sobre planes activos con decisión definitiva.
- RPC `generate_plan_reminders()` con `SECURITY DEFINER`, `search_path=''`, objetos
  cualificados y EXECUTE exclusivamente para `service_role`. Sin parámetros de
  usuario, plan, grupo ni reloj proporcionados por el cliente.
- Se extiende el retorno de `list_notifications(integer)`: PostgreSQL requiere
  recrear esa función, dentro de la misma transacción, y restablecer sus permisos
  sólo para `authenticated`. Conserva los campos de invitaciones y paginación 12+1.

El INSERT usa `ON CONFLICT DO NOTHING`, protegido por el índice único también en
concurrencia. Marcar como leído no elimina ni rearma esa garantía. Reabrir y
reconfirmar el mismo plan **no genera otro recordatorio para ese usuario**: la
unidad de idempotencia es usuario + plan + tipo, no versión de fecha.

Se bloquean grupo, planes elegibles y membresías, en ese orden. El estado/fecha
se revalida al adquirir el lock del plan; finalizar, reabrir o eliminar se
serializa con el procesamiento. Las membresías seleccionadas quedan protegidas
contra UPDATE/DELETE mientras se insertan sus avisos. Todas las inserciones de
una ejecución se confirman o revierten juntas. Un fallo de red puede dejar un
resultado incierto, pero volver a ejecutar es seguro; el comando no reintenta
automáticamente ni imprime errores del proveedor/credenciales.

No se conceden nuevos permisos directos sobre las tablas. `authenticated` sigue
leyendo únicamente sus notificaciones mediante RLS; la bandeja y las escrituras
de lectura usan el JWT de sesión. La generación no usa el rol de conexión de
sesiones `planb_django_runtime`.

El payload guarda sólo una copia histórica de `plan_name` y `scheduled_at`.
Identidad, referencia al plan, grupo y disponibilidad se obtienen de relaciones
reales. La RPC revalida membresía para ofrecer el enlace; si el destinatario ya
salió, conserva su aviso histórico sin nombre actual del grupo ni enlace. El
detalle del plan vuelve a validar permisos con JWT/RLS y membresía vigente. Un
identificador enviado desde frontend no otorga acceso.

## Interfaz

Se conserva la bandeja actual, estilos, paginación, badge y acción POST con CSRF
para marcar como leído. Los recordatorios se mezclan con las invitaciones.
Ejemplo: **Mañana · Mates a la plaza**, “El encuentro es a las 16:22.”, grupo y
botón **Ver plan** al detalle existente. Se muestra **Hoy** cuando corresponde;
para otras fechas se usa `dd/mm/aaaa`, evitando un “Mañana” perpetuo en el historial.
Abrir el enlace no marca automáticamente el aviso como leído, siguiendo la
convención de la bandeja. No se agregan email, push, polling ni otra bandeja.
Nombre y hora son los del momento de generar el aviso; si el plan se reabre,
se renombra, se finaliza o cambia su fecha después, el detalle informa su estado
vigente. Eliminar el plan elimina sus recordatorios; no afecta sus invitaciones.

## Ejecución manual y programación en despliegue

Primero revisar/aplicar la nueva migración en una instancia local/de pruebas;
el código Django no aplica SQL Supabase automáticamente. Para un entorno
desplegado, su aplicación debe formar parte del procedimiento autorizado de
despliegue, antes de habilitar el comando y la nueva bandeja.

En desarrollo, con el virtualenv activo, desde `backend/` y apuntando a una
instancia **local/de pruebas** mediante las variables existentes del proceso:

```powershell
python manage.py generate_plan_reminders
```

El comando escribe notificaciones en el Supabase configurado: no simula datos.
No ejecutarlo usando credenciales del proyecto compartido durante validaciones
locales. No requiere editar `.env` ni agregar variables, dependencias o modelos.
Devuelve `Recordatorios creados: N.`; un fallo termina con código distinto de cero
y un mensaje seguro. Una nueva ejecución sin destinatarios pendientes devuelve 0.

En despliegue, configurar un trabajo periódico del hosting o cron cada hora.
Debe funcionar independientemente del proceso web y ejecutar el Python del
virtualenv con las variables de servidor existentes, incluida la secret key.
Ejemplo Linux para adaptar a las rutas reales:

```cron
0 * * * * cd /srv/planb/backend && /srv/planb/.venv/bin/python manage.py generate_plan_reminders
```

En Windows se puede usar el Programador de tareas con repetición de una hora,
`python.exe` del virtualenv como programa, `manage.py generate_plan_reminders`
como argumentos y `backend` como directorio de inicio. No registrar secretos
en argumentos, crontab o archivos versionados. Estos ejemplos no crean tareas.

El equipo de despliegue debe habilitar el job, conservar logs de salida/errores y
supervisar ejecuciones fallidas. Una ejecución cada hora puede perder un plan
confirmado que empiece antes de la siguiente ejecución; si esos casos son
frecuentes, programar cada 5 minutos con el mismo comando y la misma ventana.
No hay garantía si el scheduler nunca corre o está caído hasta después del evento.
Con muchos planes, medir duración/locks y límites de tiempo de Data API antes de
aumentar volumen; la operación es transaccional y el cliente tiene timeout de 10s.

## Validación y cobertura

Desde `backend/` con el virtualenv activo:

```powershell
python manage.py check
python manage.py check --settings=config.settings_test
python manage.py test apps.notifications
python manage.py test
```

Desde la raíz:

```powershell
git diff --check
git status
```

`test_reminders.py` ejecuta el comando real con SDK real y transporte HTTP en
memoria: clave administrativa sin JWT de usuario, RPC sin parámetros, conteos,
repetición, errores, cierre de transportes y timeout sin reintentos. Es la prueba
segura/local del management command: **no ejecutar el comando real con `.env`**.

`test_reminder_inbox.py` cubre SDK de lectura con JWT, bandeja mixta, badge, lectura
POST/CSRF, enlaces, revalidación al entrar al detalle, hora/zona/día local,
historial sin acceso, escape HTML, respuestas inválidas, tipos futuros y varios
planes/grupos. Las regresiones existentes cubren invitaciones y privacidad.

`supabase/tests/plan_reminders.sql` es una integración reversible con `ROLLBACK`.
Usar únicamente una instancia local/de pruebas aislada con todas las migraciones,
como administrador; por ejemplo con `psql` y una conexión local explícita:

```powershell
psql -h 127.0.0.1 -p 54322 -U postgres -d postgres -v ON_ERROR_STOP=1 -f supabase/tests/plan_reminders.sql
```

Cubre confirmados, owner/todos los miembros, outsider y exmiembro, incorporación
antes y después de procesar, organización/finalizado/inactivo, NULL, pasado,
límites exactos 0/24h, demasiado lejano, confirmación con 15 minutos, varias
ejecuciones, constraint de unicidad, varios planes/grupos, cambio de timezone SQL,
snapshots, reconfirmación, atomicidad con fallo inducido y recuperación,
permisos/EXECUTE/RLS, bandeja, badge, lectura idempotente, invitaciones existentes,
pérdida de acceso y borrado CASCADE. Un confirmado con `scheduled_at NULL` ya es
inválido por el CHECK del esquema anterior; el caso sin fecha usa un plan válido
sin decisión. La prueba del índice único verifica la garantía de concurrencia;
no sustituye una prueba de carga con dos sesiones simultáneas.

La suite Django es offline y no demuestra la ejecución de PL/pgSQL ni RLS real.
La ejecución SQL queda pendiente cuando no hay PostgreSQL/Docker local disponible;
no se utiliza Supabase compartido como reemplazo.

Resultado local de esta entrega: **475 tests correctos** (455 existentes y 20
nuevos); el subconjunto de notificaciones ejecutó **77 tests correctos**. Ambos
`manage.py check` finalizaron sin problemas y `git diff --check` no informó
errores. El comando se ejecutó dentro de los tests con SDK/transporte simulado.
El script SQL no se ejecutó: no había PostgreSQL local instalado ni motor Docker
disponible. No hubo commit, push, SQL remoto ni modificaciones de `.env`.

## Archivos de esta entrega

Creados:

```text
backend/apps/notifications/reminders.py
backend/apps/notifications/management/__init__.py
backend/apps/notifications/management/commands/__init__.py
backend/apps/notifications/management/commands/generate_plan_reminders.py
backend/apps/notifications/tests/test_reminders.py
backend/apps/notifications/tests/test_reminder_inbox.py
supabase/migrations/202610260001_plan_reminders.sql
supabase/tests/plan_reminders.sql
docs/testing/recordatorios.md
```

Modificados: `backend/apps/notifications/services.py`,
`backend/apps/notifications/templates/notifications/index.html`, `README.md` y
`docs/architecture/esquema-datos.md`. No se modifica el Calendario.
