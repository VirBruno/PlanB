-- Aplicar una sola vez, revisado por el equipo, con un rol administrador.
-- No contiene contraseñas y no crea identidades de usuarios de la aplicación.
BEGIN;

-- Los dos roles nacen sin LOGIN. El administrador habilitará el acceso y
-- asignará credenciales por un canal seguro, fuera de este repositorio.
CREATE ROLE planb_django_migrator NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
    NOREPLICATION NOBYPASSRLS;
CREATE ROLE planb_django_runtime NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
    NOREPLICATION NOBYPASSRLS;

GRANT planb_django_migrator TO postgres;

CREATE SCHEMA django_internal;
REVOKE ALL ON SCHEMA django_internal FROM PUBLIC, anon, authenticated, service_role;
GRANT USAGE, CREATE ON SCHEMA django_internal TO planb_django_migrator;
GRANT USAGE ON SCHEMA django_internal TO planb_django_runtime;

-- Las migraciones Django deben conectarse COMO planb_django_migrator.
-- El servidor usa planb_django_runtime: puede manejar sesiones, no hacer DDL.
ALTER DEFAULT PRIVILEGES FOR ROLE planb_django_migrator IN SCHEMA django_internal
    REVOKE ALL ON TABLES FROM PUBLIC, anon, authenticated, service_role;
ALTER DEFAULT PRIVILEGES FOR ROLE planb_django_migrator IN SCHEMA django_internal
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO planb_django_runtime;
ALTER DEFAULT PRIVILEGES FOR ROLE planb_django_migrator IN SCHEMA django_internal
    REVOKE ALL ON SEQUENCES FROM PUBLIC, anon, authenticated, service_role;
ALTER DEFAULT PRIVILEGES FOR ROLE planb_django_migrator IN SCHEMA django_internal
    GRANT USAGE, SELECT ON SEQUENCES TO planb_django_runtime;

COMMENT ON SCHEMA django_internal IS
    'Infraestructura Django; excluir siempre de los esquemas expuestos de Data API.';

COMMIT;
