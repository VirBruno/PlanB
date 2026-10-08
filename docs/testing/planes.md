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
