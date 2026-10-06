-- Permite al backend resolver usernames de autores de propuestas visibles.
-- service_role sólo se usa en el servidor; los perfiles siguen sujetos a RLS
-- para peticiones con el JWT normal del usuario.
BEGIN;
GRANT SELECT (username) ON TABLE public.profiles TO service_role;
COMMIT;