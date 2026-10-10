# Planes

## Preparación manual

La app no aplica cambios al proyecto remoto. Primero confirmar que
`public.plans` existe con las columnas y claves foráneas acordadas. El esquema
inicial permite `group_id` nulo; antes de la segunda migración, revisar y
reasignar los registros sin grupo a un grupo válido o eliminarlos según las
reglas del equipo:

```sql
CREATE TABLE public.plans (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  created_at timestamptz NOT NULL DEFAULT now(),
  name varchar NOT NULL,
  description varchar NOT NULL,
  status boolean NOT NULL,
  group_id uuid REFERENCES public.groups(id) ON UPDATE CASCADE,
  created_by uuid REFERENCES public.profiles(id) ON UPDATE CASCADE
);
```

Para localizar registros previos que necesitan resolución:

```sql
SELECT id, name, created_by FROM public.plans WHERE group_id IS NULL;
```

Con un administrador autorizado, aplicar en orden una sola vez
`supabase/migrations/202610010001_plans_security.sql` y
`supabase/migrations/202610010002_group_owned_plans.sql`, después de las tablas
`profiles`, `groups` y `group_members`. La segunda migración cambia `group_id`
a NOT NULL; revisar y resolver los registros existentes antes de aplicarla.
El rol `owner` de `group_members` es el administrador del grupo en esta versión.
El listado contextual se abre desde el detalle en `/grupos/<uuid>/planes/`.

## Pruebas offline

Desde `backend/`, con la virtualenv activada:

```powershell
python manage.py test apps.plans.tests
python manage.py check --settings=config.settings_test
```

Los tests usan transporte HTTP simulado; no prueban la instalación real de la
migración ni el comportamiento de RLS en Supabase.

La prueba de integración SQL `supabase/tests/plans.sql` verifica lectura por
miembros, escritura exclusiva del owner vigente y aislamiento de usuarios
ajenos. Ejecutarla sólo en una instancia de prueba con las migraciones aplicadas.

## Validación de integración

En un proyecto Supabase de prueba, verificar con dos usuarios autenticados:

- Anon no puede leer ni mutar `plans`.
- Un owner actual puede administrar el plan aunque `created_by` sea otro usuario.
- Un miembro puede leer los planes del grupo, pero no crearlos, editarlos ni borrarlos.
- Un owner puede editar y borrar cualquier plan de su grupo, independientemente de `created_by`.
- Un usuario ajeno no puede leer el plan de un grupo privado.
- El rol `planb_django_runtime` no tiene acceso directo a `public.plans`.

La migración SQL no se ha aplicado desde la suite Django; la validación remota
requiere autorización y un entorno de prueba.

## Propuestas geográficas

Confirmar que `public.proposals` existe con el esquema indicado por el equipo,
que `posicion` es `geography(Point,4326)` y que
`public.types_of_proposals` contiene `juntada`, `reunión` y `salida`. Aplicar
`supabase/migrations/202610060001_proposals_security.sql` y luego
`202610060002_proposals_one_per_user_plan.sql`, después de las migraciones de
grupos y planes. La segunda migración requiere resolver duplicados preexistentes
por `plan_id` y `created_by`. Aplicar también
`202610060003_proposals_postgis_schema_usage.sql` para conceder `USAGE` al rol
`authenticated` en el esquema `postgis`, requerido al insertar `geography` por
Data API, y `202610060004_proposal_author_usernames.sql` para permitir que el
backend resuelva usernames de los autores de propuestas visibles. La app no
aplica SQL remoto automáticamente.

Desde `backend/`, ejecutar `python manage.py test apps.plans.tests` con
`config.settings_test`. En una instancia Supabase de prueba, ejecutar
`supabase/tests/proposals.sql` como administrador. Verificar que un miembro
puede leer y crear propuestas, que no puede editar ni borrar la propuesta de
otro usuario, que sólo puede tener una por plan, que sí puede administrar la
propia y que una persona ajena no puede leerlas. Probar en navegador la selección por clic, búsqueda de ciudad y
geolocalización (requiere permiso del navegador y HTTPS o localhost).

Leaflet se instala con `npm ci` y sus assets se sirven desde Django.
OpenStreetMap/Nominatim provee las teselas y la búsqueda; la ubicación elegida
se envía al backend sólo al guardar la propuesta.
> Regla vigente desde `feature/group-invitations`: todos los miembros crean
> planes; sólo el owner los edita/elimina. Las referencias anteriores a creación
> exclusiva del owner corresponden a las dos migraciones iniciales. Aplicar
> `202610080002_member_plan_creation.sql` después de ellas. Ver
> [despliegue y pruebas de invitaciones](invitaciones.md).

## Presupuesto opcional

Con las migraciones anteriores aplicadas, ejecutar manualmente como administrador
el contenido completo de `supabase/migrations/202610090001_proposal_budget_range.sql`
en SQL Editor, incluidos BEGIN/COMMIT. No requiere migraciones Django ni cambios
en `.env`. Aplicar el SQL antes de servir el código nuevo: las consultas de
propuestas pasan a seleccionar `budget_min` y `budget_max`.

La migración agrega ambos campos como `numeric(12,2) NULL`, tres constraints y
grants INSERT/UPDATE por columna sólo para `authenticated`. Las políticas RLS,
identidad, permisos de autor y unicidad por autor/plan se mantienen. Los checks
no negativos rechazan también `NaN`, que PostgreSQL puede almacenar como numeric.
Referencia: [tipos numéricos de PostgreSQL](https://www.postgresql.org/docs/current/datatype-numeric.html).
Los campos no tienen moneda ni participan del plan ideal.

El formulario incorpora Presupuesto con Desde/Hasta opcionales. Acepta uno o
ambos límites, cero y decimales. Rechaza negativos, valores no numéricos/no finitos,
rangos invertidos, más de dos decimales o más de diez dígitos enteros; no redondea
entradas inválidas. Usar punto decimal en la request (por ejemplo `10.50`), sin
símbolos de moneda. La visualización usa el formato local, por ejemplo `10,50`.

La edición sigue siendo un formulario completo: precarga los valores actuales;
modificar un límite conserva el otro enviado en el formulario. Vaciar cualquiera
lo guarda como NULL; vaciar ambos elimina el rango. En el detalle se muestra
`10,50 – 20,75`, `Desde 10,50` o `Hasta 20,75`; ambos NULL ocultan Presupuesto.

Prueba manual con dos integrantes del mismo grupo:

1. Crear una propuesta sin presupuesto y verificar que sigue siendo válida.
2. Editarla agregando ambos límites, después modificando sólo uno, vaciando uno
   y finalmente vaciando ambos. Recargar el detalle tras cada guardado.
3. Probar límites iguales y cero; luego negativos, mínimo mayor al máximo,
   letras y tres decimales. Los inválidos no deben guardarse ni redondearse.
4. Con otro integrante, leer el presupuesto y comprobar que no puede editarlo,
   incluso intentando la URL POST de edición de la propuesta ajena.
5. Intentar una segunda propuesta del mismo autor/plan: debe seguir bloqueada.

La suite normal permanece offline. Sus pruebas están en `apps/plans/tests/`
(`test_forms.py`, `test_services.py`, `test_views.py`) y cubren validación Decimal,
compatibilidad sin campos nuevos, payloads exactos del SDK, precarga/edición/borrado,
lectura por miembros, ownership y la prohibición de duplicados.

`supabase/tests/proposal_budget.sql` agrega integración de constraints, tipos,
nullable, INSERT/UPDATE con los grants nuevos, RLS, aislamiento de outsiders,
presupuesto ajeno y una propuesta por usuario/plan. Contiene fixtures reversibles
y termina con ROLLBACK. Ejecutarlo sólo en Supabase local o en un proyecto de
prueba autorizado, junto al script existente `supabase/tests/proposals.sql`.
No ejecutar ninguno en el compartido. La suite Django no ejecuta ni valida RLS
de PostgreSQL; no se aplicó SQL remoto durante esta implementación.

## Métodos de elección

Con las migraciones de propuestas y presupuesto aplicadas, ejecutar manualmente
en orden, como administrador y en una instancia de prueba,
`supabase/migrations/202610100001_proposal_election_methods.sql` y luego
`supabase/migrations/202610100002_flexible_proposal_elections.sql`. La primera
agrega el modo compartido, las métricas `votes` y `"Score"`, el registro privado
`proposal_votes` y RPCs. La segunda permite cambiar de método y cambiar/eliminar
el voto propio, manteniendo los conteos. No se ejecutan con Django ni conceden
escritura directa de métricas a usuarios.

Prueba manual con dos integrantes:

1. Como owner del grupo, elegir Punto de encuentro y comprobar que abre el
  cálculo geográfico existente; elegir Votación y comprobar que cada persona
  puede emitir un voto y que el conteo y la propuesta campeona se actualizan.
2. Cambiar el voto a otra propuesta y confirmar que baja el conteo anterior y
  sube el nuevo; quitar el voto y confirmar que desaparece y el conteo baja.
3. Cambiar entre Votación y Combate después de tener resultados; verificar que
  el método cambia y que los votos/puntajes anteriores se conservan.
4. Elegir Combate, jugar con Espacio, las flechas, el botón Volar o tocando el
  canvas, guardar el resultado y comprobar el leaderboard después de recargar.
  Repetir con una puntuación menor y confirmar que conserva el máximo.
5. Confirmar que un integrante no puede cambiar el método del plan ni guardar
  puntaje en la propuesta de otra persona.

`supabase/tests/proposal_election.sql` valida selección por owner, voto único,
cambio/eliminación del voto, cambio de método con resultados, persistencia del
score máximo y grants.
Contiene fixtures reversibles y termina con ROLLBACK. Ejecutarlo como
administrador sólo en Supabase local o en un proyecto de prueba autorizado.
La puntuación se calcula en el navegador: el RPC restringe quién puede guardar
y en qué propuesta, pero no valida que el puntaje provenga de una partida legítima.

Resultado local de esta entrega: 61 tests de planes/propuestas pasan, incluidos
22 nuevos de presupuesto. La suite completa ejecutó 314 tests: 307 correctos y
7 fallos preexistentes de interfaz. La ejecución inicial, antes del cambio,
ya fallaba en esos mismos tests por expectativas del diseño anterior de grupos,
marca y badge de notificaciones. No se modificaron funcionalidades ni tests ajenos
para ocultarlos. `manage.py check`, `check --settings=config.settings_test`,
`makemigrations --check --dry-run --settings=config.settings_test`, `pip check` y
`git diff --check` finalizaron correctamente. La integración SQL queda pendiente
en un entorno de prueba autorizado.
