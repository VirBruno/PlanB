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