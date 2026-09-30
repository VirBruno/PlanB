-- Auth es la única autoridad de identidad. No se duplican email ni contraseña.
-- Requiere Supabase Auth instalado y revisión de usuarios/tablas preexistentes.
BEGIN;

CREATE SCHEMA planb_private;
REVOKE ALL ON SCHEMA planb_private FROM PUBLIC, anon, authenticated, service_role;

CREATE TABLE public.profiles (
    id uuid PRIMARY KEY REFERENCES auth.users (id) ON DELETE CASCADE,
    username text COLLATE "C" NOT NULL,
    username_normalized text COLLATE "C"
        GENERATED ALWAYS AS (lower(username COLLATE "C")) STORED NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT profiles_username_format CHECK (
        username ~ '^[A-Za-z0-9][A-Za-z0-9_.-]{2,29}$'
    ),
    CONSTRAINT profiles_username_normalized_key UNIQUE (username_normalized)
);

CREATE FUNCTION planb_private.create_profile_for_auth_user()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
BEGIN
    -- La constraint es la autoridad incluso ante registros concurrentes.
    -- Si falla, también se revierte el INSERT original en auth.users.
    INSERT INTO public.profiles (id, username)
    VALUES (NEW.id, NEW.raw_user_meta_data ->> 'username');
    RETURN NEW;
END;
$$;

CREATE FUNCTION planb_private.touch_profile_updated_at()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = ''
AS $$
BEGIN
    NEW.updated_at := statement_timestamp();
    RETURN NEW;
END;
$$;

REVOKE ALL ON FUNCTION planb_private.create_profile_for_auth_user()
    FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION planb_private.touch_profile_updated_at()
    FROM PUBLIC, anon, authenticated, service_role;

CREATE TRIGGER planb_auth_user_created
AFTER INSERT ON auth.users
FOR EACH ROW EXECUTE FUNCTION planb_private.create_profile_for_auth_user();

CREATE TRIGGER planb_profile_updated_at
BEFORE UPDATE ON public.profiles
FOR EACH ROW EXECUTE FUNCTION planb_private.touch_profile_updated_at();

ALTER TABLE public.profiles ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.profiles FROM PUBLIC, anon, authenticated, service_role;
GRANT SELECT ON TABLE public.profiles TO authenticated;
GRANT UPDATE (username) ON public.profiles TO authenticated;
-- La secret key sólo necesita estas columnas en Data API.
GRANT SELECT (id, username_normalized) ON public.profiles TO service_role;

CREATE POLICY profiles_select_own ON public.profiles
FOR SELECT TO authenticated
USING ((SELECT auth.uid()) = id);

CREATE POLICY profiles_update_own ON public.profiles
FOR UPDATE TO authenticated
USING ((SELECT auth.uid()) = id)
WITH CHECK ((SELECT auth.uid()) = id);

COMMENT ON TABLE public.profiles IS 'Perfil mínimo de Plan B vinculado a Supabase Auth.';
COMMENT ON COLUMN public.profiles.username_normalized IS
    'Normalización ASCII con collation C; el índice UNIQUE resuelve duplicados y carreras.';

COMMIT;
