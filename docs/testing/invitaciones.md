# Invitaciones a grupos y notificaciones

Implementación en `feature/group-invitations`. Django coordina forms, views y
templates; Supabase conserva Auth, perfiles, grupos, membresías, invitaciones y
notificaciones. No se agregan modelos de dominio al ORM, dependencias ni variables
de entorno. Las propuestas geográficas que ya existían se preservan; esta entrega
no agrega cuestionarios ni nuevas propuestas personales.

## Esquema y permisos

`group_invitations` es la fuente de verdad: `pending`, `accepted` o `rejected`,
con constraints explícitos para estado, respuesta y auto-invitación. El índice
parcial impide dos pendientes para el mismo grupo/destinatario, incluso con
requests concurrentes. Los estados resueltos no se sobrescriben ni borran al
volver a invitar. El owner se comprueba mediante el rol vigente en `group_members`.

`notifications` guarda destinatario, tipo, payload mínimo, fechas y una referencia
a la invitación. Una FK compuesta verifica que notificación e invitación tienen
el mismo destinatario. `payload` no guarda estado de negocio. El tipo permite
futuras extensiones, pero sólo `group_invitation` se genera en esta entrega.

`group_invite_links` conserva hashes SHA-256 de tokens aleatorios; el token en
claro sólo se muestra al owner al generarlo. Un enlace vence a los 7 días y crear
otro revoca el anterior. Sólo un usuario autenticado puede consultar el nombre
del grupo desde el enlace y confirmar la unión; el canje asigna `role='member'`.
Las tablas no conceden DML directo y los enlaces se eliminan al borrar el grupo.

| Operación | Owner actual | Member | Destinatario sin membresía | Outsider/anon |
| --- | --- | --- | --- | --- |
| Buscar username e invitar | Sí | No | No | No |
| Ver integrantes | Sí | Sí | No | No |
| Ver invitaciones | De sus grupos | Sólo propias | Sólo propias | No ajenas |
| Aceptar/rechazar | Sólo si es destinatario | Sólo propias | Sólo propias | No ajenas |
| Ver/marcar notificaciones | Sólo propias | Sólo propias | Sólo propias | No ajenas |
| Leer y crear planes | Sí | Sí | No | No |
| Editar/eliminar grupos y planes | Sí | No | No | No |

Para un outsider autenticado, sus propias notificaciones siempre son accesibles;
ser outsider de un grupo no habilita lectura de ese grupo. Un anónimo no recibe
grants de lectura ni ejecución.

RLS nuevo: `invitations_select_recipient_or_owner` y `notifications_select_own`;
`group_invite_links` tiene RLS activo sin policies ni grants directos. Invitaciones
y notificaciones sólo conceden SELECT a `authenticated`; ninguna tabla concede DML.
`profiles` y `group_members` mantienen sus políticas de lectura propia. La búsqueda
es exacta, requiere el username completo válido y usa `username_normalized`;
devuelve únicamente id/username. El estado pendiente se consulta por separado.
No hay acceso nuevo a emails ni a `auth.users` desde la aplicación.

RPCs nuevas, con `SECURITY DEFINER`, `search_path=''`, objetos cualificados,
`auth.uid()` validado y EXECUTE sólo para `authenticated`:

- `search_group_invitees(p_group_id uuid, p_username text)`.
- `list_group_members(p_group_id uuid)`.
- `list_pending_group_invitations(p_group_id uuid)`.
- `create_group_invitation(p_group_id uuid, p_invited_user_id uuid)`.
- `accept_group_invitation(p_invitation_id uuid)`.
- `reject_group_invitation(p_invitation_id uuid)`.
- `list_notifications(p_page integer default 1)`.
- `mark_notification_read(p_notification_id uuid)`.
- `create_group_invite_link(p_group_id uuid, p_token_hash text)`.
- `get_group_from_invite_link(p_token_hash text)`.
- `join_group_by_invite_link(p_token_hash text)`.

Las escrituras no aceptan actor ni role. Aceptar/rechazar recibe sólo el id de
invitación y revalida destinatario/estado en PostgreSQL. Aceptar bloquea primero
el grupo y después la invitación, revalida membresía e inserta `role='member'`;
invitación y notificación se actualizan en la misma transacción. Si falla una
escritura, todo se revierte. Crear invitación/notificación también es atómico.
No se reintentan automáticamente escrituras con resultado incierto.

La política `plans_insert_group_owner` se reemplaza por
`plans_insert_group_member`; sigue exigiendo `created_by=auth.uid()` y pertenencia
al grupo. SELECT, UPDATE, DELETE y sus grants actuales se preservan. Django deja
de exigir owner para crear planes; sí lo exige para editar/eliminar.

## Pasos exactos en Supabase

Estos archivos están versionados y **no fueron aplicados al proyecto remoto**.
No ejecutar `manage.py migrate` para estas entidades.

1. Verificar que están aplicadas las migraciones previas hasta
   `202610060004_proposal_author_usernames.sql`. No repetir migraciones históricas.
2. En Supabase → SQL Editor, con el rol administrador, abrir una consulta nueva.
   Copiar y ejecutar **todo** `supabase/migrations/202610080001_group_invitations.sql`,
   incluido `BEGIN`/`COMMIT`. Debe finalizar sin errores.
3. Abrir otra consulta y copiar/ejecutar **todo**
   `supabase/migrations/202610080002_member_plan_creation.sql`.
4. Abrir otra consulta y copiar/ejecutar **todo**
   `supabase/migrations/202610180001_group_invite_links.sql`.
5. Verificar tablas y políticas con esta consulta de sólo lectura:

   ```sql
      SELECT to_regclass('public.group_invitations'), to_regclass('public.notifications'),
         to_regclass('public.group_invite_links');
   SELECT tablename, policyname, cmd, roles
   FROM pg_policies
   WHERE schemaname = 'public'
   AND tablename IN ('group_invitations', 'notifications', 'group_invite_links', 'plans')
   ORDER BY tablename, policyname;
   ```

   Deben existir las tres tablas de invitaciones y las políticas nuevas; plans debe conservar
   `plans_select_group_members`, `plans_update_group_owner`,
   `plans_delete_group_owner`, junto a `plans_insert_group_member`.
5. Mantener `planb_private`/`django_internal` fuera de los esquemas expuestos.
   Las migraciones notifican la recarga de Data API; no requieren editar `.env`,
   Auth ni secretos. Reiniciar Django si todavía estaba ejecutando código previo.

El script `supabase/tests/group_invitations.sql` se ejecuta únicamente en una
instancia local o proyecto de pruebas autorizado, después de aplicar las
migraciones. Crea fixtures de Auth, simula JWTs y fallos de triggers y termina
con `ROLLBACK`. **No ejecutarlo en el compartido.** Lo mismo aplica a los scripts
de regresión SQL; `supabase/tests/plans.sql` ya refleja la nueva regla de creación.
El flujo de enlaces se comprueba aparte con `supabase/tests/group_invite_links.sql`,
que prueba preview autenticado, canje idempotente, revocación al regenerar y
denegación a `anon`; también termina con `ROLLBACK`.

## Prueba manual con dos usuarios registrados

1. Iniciar sesión como owner. Abrir un grupo existente y comprobar planes y
   acciones de edición/eliminación. En Integrantes, seleccionar **Invitar integrante**.
2. Buscar el username completo del segundo usuario, también probando mayúsculas.
   Enviar la invitación. Debe aparecer como pendiente en el grupo. Repetir búsqueda:
   se indica pendiente y el botón queda deshabilitado.
3. En otro navegador/sesión, iniciar sesión como invitado. Recargar una página:
   la campana debe mostrar una notificación sin leer. Abrir **Notificaciones**.
   Comprobar nombre del grupo, username del owner, fecha y botones.
4. Rechazar. Debe quedar **Invitación rechazada**, sin botones de respuesta; el
   grupo todavía no aparece en el dashboard y el badge disminuye.
5. Volver al owner y enviar una nueva invitación al mismo username. Debe funcionar.
6. En el invitado, recargar Notificaciones y aceptar. Debe verse **Invitación
   aceptada**, **Ya sos integrante** e **Ir al grupo**. El rechazo anterior se
   conserva como historial.
7. Abrir Mi espacio: el grupo aparece por la consulta existente de membresía.
   Entrar al grupo y comprobar integrantes y planes existentes. No se muestran
   Editar grupo, Eliminar grupo ni Invitar integrante para el member.
8. Crear un plan como member. Debe aparecer en el grupo y poder abrirse.
   Intentar URLs de edición/eliminación/invitación: acceso denegado. El owner
   sigue pudiendo crear, editar y eliminar grupos/planes.
9. Probar un tercer usuario sin membresía: no accede al grupo, integrantes,
   búsqueda ni creación de planes. No ve notificaciones ni invitaciones ajenas.
10. Probar teclado (Tab, Enter), foco visible, viewport móvil y reducción de
    movimiento. Las acciones POST requieren CSRF y no hay dependencias nuevas.
11. Como owner, generar un enlace. Abrirlo en una sesión del tercer usuario:
   debe mostrar el grupo sin agregarlo todavía; confirmar **Unirme al grupo**
   lo agrega como member. Generar otro enlace y comprobar que el anterior da 404;
   dejar vencer uno permite verificar el mismo rechazo por expiración. Los dos
   enlaces generados no deben confundirse: sólo el último está activo.

Visitar Notificaciones es una lectura GET: no modifica `read_at`. **Marcar como
leída** lo modifica mediante POST sin responder la invitación; aceptar/rechazar
también la marca como leída. No hay polling, realtime ni push: se actualiza al
cargar páginas. El listado pagina de a 12. Un fallo del contador muestra `?` y
no bloquea el grupo; un fallo del inbox muestra error/reintento, no inbox vacío.

## Validaciones offline y alcance de la evidencia

Desde `backend/`, con `.venv` activo:

```powershell
python manage.py test
python manage.py check
python manage.py check --settings=config.settings_test
python manage.py makemigrations --check --dry-run --settings=config.settings_test
python -m pip check
git diff --check
```

Los tests nuevos están en `apps/notifications/tests/`: contratos del SDK real
con `httpx.MockTransport`, validación y sanitización, servicios, controles HTTP,
CSRF, sesiones expiradas, badge, templates, estados históricos y flujo completo
rechazar → reinvitar → aceptar → dashboard → integrantes → crear/leer plan.
Se mantienen los tests de auth/sesiones/grupos/propuestas. Las expectativas
anteriores que prohibían crear planes a members se actualizan a la regla nueva.
Se corrige un fixture previo de perfiles que omitía las fechas exigidas por su
servicio; no se modifica el servicio de identidad ni se debilita su validación.

La suite offline no prueba ejecución PostgreSQL ni RLS real. El script SQL cubre
identidades, constraints/permisos, rechazo, reinvitación, historial, ownership
actual, notificaciones privadas, creación de planes por miembros, bloqueo a
outsiders/anon y atomicidad con fallos posteriores al INSERT de membresía.
Su ejecución queda pendiente: no había motor Docker/PostgreSQL local disponible.
La prueba visual interactiva y la concurrencia en PostgreSQL también quedan para
el entorno de pruebas; el índice parcial y los locks sostienen las invariantes.

Resultado de validación local: **292 tests pasando**, ambos `check` sin problemas,
`makemigrations --check --dry-run` sin cambios, `pip check` sin conflictos y
`git diff --check` sin errores. No se ejecutó SQL remoto ni se hizo commit/push.

## Inventario de archivos

Creados:

```text
backend/apps/groups/services/invitation_service.py
backend/apps/groups/templates/groups/invite.html
backend/apps/notifications/__init__.py
backend/apps/notifications/apps.py
backend/apps/notifications/context_processors.py
backend/apps/notifications/services.py
backend/apps/notifications/views.py
backend/apps/notifications/urls.py
backend/apps/notifications/templates/notifications/index.html
backend/apps/notifications/tests/__init__.py
backend/apps/notifications/tests/test_services.py
backend/apps/notifications/tests/test_views.py
backend/apps/notifications/tests/test_flow.py
supabase/migrations/202610080001_group_invitations.sql
supabase/migrations/202610080002_member_plan_creation.sql
supabase/tests/group_invitations.sql
docs/testing/invitaciones.md
```

Modificados:

```text
README.md
backend/apps/groups/forms.py
backend/apps/groups/views.py
backend/apps/groups/urls.py
backend/apps/groups/templates/groups/detail.html
backend/apps/groups/tests/test_views.py
backend/apps/groups/tests/test_management.py
backend/apps/plans/views.py
backend/apps/plans/tests/test_views.py
backend/apps/users/templates/components/authenticated_base.html
backend/apps/users/static/users/css/planb.css
backend/apps/users/tests/test_services.py
backend/config/settings_base.py
backend/config/urls.py
docs/architecture/esquema-datos.md
docs/testing/planes.md
supabase/tests/plans.sql
```

## Decisiones y límites operativos

- El historial se conserva mientras existan grupo y perfiles referenciados.
  Al eliminar el grupo se eliminan sus invitaciones/notificaciones por CASCADE,
  preservando la eliminación de grupos existente y evitando acciones huérfanas.
- Una invitación pending cuyo usuario ya fue agregado por un administrador
  externo no se acepta ni crea duplicados; todavía puede rechazarse.
- No hay revocación, expiración, email, transferencia de owner, eliminar/abandonar
  membresías ni inscripción a planes individuales.
- Cada render autenticado agrega una consulta HEAD acotada para el badge; el
  detalle agrega RPCs de integrantes y pendientes (esta última sólo para owner).
- Los mensajes de red indican incertidumbre y piden revisar el estado antes de
  reenviar; un timeout puede haber ocurrido después del commit remoto.
