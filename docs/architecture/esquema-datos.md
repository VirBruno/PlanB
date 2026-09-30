# Esquema de datos y migraciones

## Identidad y perfil

`auth.users` pertenece a Supabase Auth. No se altera su estructura ni se
guardan contraseñas propias. `public.profiles` tiene esta definición:

| Columna | Tipo y reglas |
| --- | --- |
| `id` | UUID, PK, FK a `auth.users(id)`, `ON DELETE CASCADE`. |
| `username` | Texto NOT NULL con collation `C`; conserva la forma visible. |
| `username_normalized` | Texto NOT NULL, generado STORED por `lower(username COLLATE "C")`, UNIQUE, collation `C`. |
| `created_at` | `timestamptz`, NOT NULL, DEFAULT `now()`. |
| `updated_at` | `timestamptz`, NOT NULL, DEFAULT `now()`; trigger en UPDATE. |

La constraint `profiles_username_format` exige
`^[A-Za-z0-9][A-Za-z0-9_.-]{2,29}$`. Son 3–30 caracteres ASCII y el primero es
alfanumérico. `Facundo`, `facundo` y `FACUNDO` colisionan por el índice UNIQUE
`profiles_username_normalized_key`. Las búsquedas usan igualdad, evitando que
`_` funcione como comodín de `ILIKE`. No se duplican email, password ni tokens.

`planb_auth_user_created`, AFTER INSERT de `auth.users`, llama a
`planb_private.create_profile_for_auth_user()`. La función SECURITY DEFINER
tiene `search_path=''`, nombres cualificados y usa metadata `username`.
Una falta de username, formato inválido o colisión aborta también el alta Auth.
No hay `ON CONFLICT DO NOTHING` ni reparación silenciosa de perfiles faltantes.

`planb_profile_updated_at` actualiza el timestamp con `statement_timestamp()`.
La función no necesita SECURITY DEFINER. Ambas funciones tienen EXECUTE
revocado para los roles API y su esquema no tiene USAGE para ellos; el trigger
las ejecuta sin exponer una RPC pública. Metadata sólo es entrada del alta;
el username de referencia posterior es el de `profiles`.

## Grants y RLS

RLS está habilitado. Las policies de authenticated limitan SELECT/UPDATE al
UUID `(SELECT auth.uid()) = id`; UPDATE incluye USING y WITH CHECK.

| Rol | Acceso a profiles |
| --- | --- |
| `anon`, `PUBLIC` | Ninguno. |
| `authenticated` | SELECT de su fila; UPDATE sólo de la columna `username` en su fila. |
| `service_role` | SELECT únicamente de `id` y `username_normalized`; usado por el servicio administrativo. |

No se concede INSERT ni DELETE a roles API. RLS controla filas y grants
controlan columnas. La secret key sigue siendo privilegiada para otras APIs
de Supabase; el grant reducido en esta tabla no vuelve pública esa clave.
La edición de perfil aún no tiene pantalla ni endpoint de Plan B.

Referencia: [perfiles con Auth](https://supabase.com/docs/guides/auth/managing-user-data)
y [privilegios de columnas](https://supabase.com/docs/guides/database/postgres/column-level-security).

## Infraestructura Django

`django_internal` contiene `django_session`, `django_migrations` y
`users_supabasesession`. Los nombres de tabla en modelos/migraciones no
incluyen esquema. La conexión PostgreSQL establece exclusivamente
`options='-c search_path=django_internal'`, sin fallback a `public`.

`SupabaseSession` referencia uno a uno la sesión Django y almacena access token,
refresh token y expiración del access token. No almacena identidad, username,
email ni contraseña. La relación se elimina en cascada a través del ORM al
borrar/limpiar sesiones Django. Evitar borrar filas técnicas con SQL arbitrario:
la semántica `on_delete` de Django se ejecuta mediante su recolector ORM.

Se eligió esta tabla porque guardar tokens junto con mensajes de navegación en
el diccionario completo de sesión permite que una request tardía sobrescriba
un refresh nuevo con su copia vieja. La fila dedicada permite transacción,
`select_for_update()` y relectura, sin reemplazar `SessionMiddleware`.
Refresh y logout usan la misma coordinación; una fila ausente no se recrea.
El bloqueo se conserva durante la llamada remota acotada por timeout.

No hay una transacción distribuida con Auth. Si Auth rota tokens y la escritura
local falla, el usuario puede necesitar ingresar otra vez. SQLite no reproduce
los bloqueos PostgreSQL; esas carreras necesitan pruebas de integración.

## Roles y protección de tokens

La migración 001 crea dos roles NOLOGIN, sin contraseñas:

- `planb_django_migrator`: USAGE/CREATE en `django_internal`; propietario de las
  tablas que crea al ejecutar las migraciones Django.
- `planb_django_runtime`: USAGE en ese esquema y CRUD en sus tablas, más
  USAGE/SELECT de secuencias; sin CREATE ni pertenencia al rol migrador.

Los permisos por defecto se definen **para el rol migrador**. Ejecutar las
migraciones como `postgres` u otro usuario no obtiene ese contrato. El
administrador habilita LOGIN y establece passwords por un canal seguro,
sin escribirlos en scripts, historial, PRs ni repositorio. Si utiliza roles
de conexión con membresías en vez de LOGIN directo, debe asegurar que las
migraciones corran efectivamente como el rol migrador y revisar los grants.

Los esquemas `django_internal` y `planb_private` deben permanecer fuera de
**Project Settings → Data API → Exposed schemas**. La migración revoca acceso
a `PUBLIC`, `anon`, `authenticated` y `service_role`. Revisar además que no
existan membresías/grants heredados ajenos a estas migraciones. No usar el rol
`postgres` para servir Django.

Los tokens se guardan en campos de texto del servidor. No tienen cifrado de
campo: un administrador con permisos de lectura sobre la base podría leerlos.
Proteger conexiones con TLS verificado, cifrado del almacenamiento/respaldos,
acceso restringido a backups, roles mínimos y exclusión de logs/HTML. La
secret key de Supabase no permite acceder a este esquema mediante Data API.
Si se requiere protección frente a lectura de dumps, será necesaria una
decisión futura de cifrado de aplicación y gestión de claves externas.

Logout/invalidez eliminan la fila. Programar limpieza diaria de sesiones
vencidas con el rol de ejecución, desde `backend/`:

```powershell
python manage.py clearsessions
```

El vencimiento de una sesión no implica que PostgreSQL borre su fila solo.
La limpieza elimina también los pendientes de confirmación que hayan quedado
en sesiones abandonadas; su límite de uso se comprueba antes de verificar.

## Orden de preparación manual

Estos pasos son para un entorno previamente autorizado. Los archivos por sí
solos no los ejecutan. Mantener registro de qué migraciones se aplicaron y no
repetirlas a ciegas: fallan si encuentran los objetos, en vez de sobrescribirlos.

1. Revisar usuarios Auth, tablas, roles y policies preexistentes. El trigger
   sólo crea perfiles en altas futuras; no hace backfill. No borrar tablas
   `auth_user`/admin antiguas automáticamente. Si hay identidades anteriores,
   acordar su username y una migración de datos independiente antes de abrir
   registro. También las altas desde Dashboard/Admin necesitan metadata válida.
2. Con un rol administrador autorizado, aplicar:
   - `supabase/migrations/202609290001_django_infrastructure.sql`.
   - `supabase/migrations/202609290002_profiles.sql`.
3. Habilitar las credenciales de los roles. La conexión directa o pooler en
   modo sesión debe soportar los parámetros `options` y el rol propio; verificar
   `SHOW search_path` antes de migrar. No asumir compatibilidad del pooler de
   transacciones con opciones de arranque.
4. Cargar `DB_USER=planb_django_migrator` y su password de forma segura en una
   terminal dedicada. Desde `backend/`, con el resto del entorno configurado:

   ```powershell
   python manage.py check
   python manage.py migrate --plan
   python manage.py migrate
   ```

   Se aplican `sessions.0001_initial` y `users.0001_initial`; esta última crea
   el modelo técnico. `django_migrations` registra esas operaciones.
5. Revisar propietarios/grants de las tablas nuevas y confirmar que el rol
   runtime puede operar sesiones pero no crear tablas ni acceder a Auth.
6. Cerrar la terminal migradora y ejecutar el servidor como
   `planb_django_runtime`. No guardar credenciales migradoras en su `.env`.
7. En una instancia de prueba, aplicar la validación
   `supabase/tests/profiles.sql`; termina con ROLLBACK y no envía correo.
8. Completar la [configuración de Auth](autenticacion-supabase.md#configuración-manual-de-supabase-auth)
   y las [pruebas de integración](../testing/autenticacion.md).

Si las sesiones viven en otro PostgreSQL, preparar allí el esquema/roles de
infraestructura. El SQL 001 está dirigido a una instancia Supabase y referencia
sus roles API: para un PostgreSQL puro, revisar y omitir únicamente las
revocaciones a roles API inexistentes. No crear roles falsos de Supabase para
simular Auth; los perfiles y Auth siguen en la instancia Supabase real.

## Migraciones y tests portables

`config.settings_test` importa únicamente la configuración compartida y define
SQLite `:memory:`. No carga secretos ni hereda OPTIONS, SSL o search_path del
backend PostgreSQL. `python manage.py test` selecciona ese módulo antes de
inicializar Django y bloquea la red accidental.

Las migraciones Django usan tipos portables y nombres simples. No contienen
`CREATE SCHEMA`, SQL de RLS ni `db_table` cualificado. La suite sí ejecuta las
migraciones técnicas; la infraestructura SQL de Supabase está fuera de ese
grafo y no se ejecuta en SQLite. Esta separación prueba los flujos offline sin
presentarlos como prueba de los permisos o bloqueos del proveedor.
