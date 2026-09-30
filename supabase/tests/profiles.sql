-- INTEGRACIÓN: sólo en una instancia Supabase de prueba autorizada.
-- Ejecutar como administrador DESPUÉS de ambas migraciones SQL.
-- No prueba Auth HTTP ni envía correo. Todas las fixtures se revierten.
BEGIN;

CREATE TEMPORARY TABLE planb_test_users (id uuid, username text) ON COMMIT DROP;
INSERT INTO planb_test_users VALUES
    (gen_random_uuid(), 'PlanB_' || substr(replace(gen_random_uuid()::text, '-', ''), 1, 18)),
    (gen_random_uuid(), 'PlanB_' || substr(replace(gen_random_uuid()::text, '-', ''), 1, 18));

INSERT INTO auth.users (id, aud, role, email, raw_user_meta_data, created_at, updated_at)
SELECT id, 'authenticated', 'authenticated', id::text || '@example.invalid',
       jsonb_build_object('username', username), now(), now()
FROM planb_test_users;

DO $$
DECLARE
    duplicate_id uuid := gen_random_uuid();
    invalid_id uuid := gen_random_uuid();
    duplicate_name text;
BEGIN
    IF (SELECT count(*) FROM public.profiles p JOIN planb_test_users t USING (id)) <> 2 THEN
        RAISE EXCEPTION 'El trigger no creó ambos perfiles';
    END IF;
    IF EXISTS (SELECT 1 FROM public.profiles p JOIN planb_test_users t USING (id)
               WHERE p.username_normalized <> lower(t.username COLLATE "C")) THEN
        RAISE EXCEPTION 'Normalización incorrecta';
    END IF;
    SELECT upper(username) INTO duplicate_name FROM planb_test_users LIMIT 1;
    BEGIN
        INSERT INTO auth.users (id, raw_user_meta_data)
        VALUES (duplicate_id, jsonb_build_object('username', duplicate_name));
        RAISE EXCEPTION 'Se aceptó un username duplicado con diferente casing';
    EXCEPTION WHEN unique_violation THEN NULL;
    END;
    IF EXISTS (SELECT 1 FROM auth.users WHERE id = duplicate_id) THEN
        RAISE EXCEPTION 'El registro duplicado dejó una identidad huérfana';
    END IF;
    BEGIN
        INSERT INTO auth.users (id, raw_user_meta_data)
        VALUES (invalid_id, '{"username":"mal nombre"}'::jsonb);
        RAISE EXCEPTION 'Se aceptó un username inválido';
    EXCEPTION WHEN check_violation THEN NULL;
    END;
    IF EXISTS (SELECT 1 FROM auth.users WHERE id = invalid_id) THEN
        RAISE EXCEPTION 'El username inválido dejó una identidad huérfana';
    END IF;
    BEGIN
        INSERT INTO public.profiles (id, username)
        VALUES (gen_random_uuid(), 'SinUsuario123');
        RAISE EXCEPTION 'Se aceptó un perfil sin usuario Auth';
    EXCEPTION WHEN foreign_key_violation THEN NULL;
    END;
    IF has_schema_privilege('anon', 'django_internal', 'USAGE')
       OR has_schema_privilege('authenticated', 'django_internal', 'USAGE')
       OR has_schema_privilege('service_role', 'django_internal', 'USAGE') THEN
        RAISE EXCEPTION 'La infraestructura Django es accesible a roles de Data API';
    END IF;
    IF has_schema_privilege('authenticated', 'planb_private', 'USAGE')
       OR has_function_privilege('authenticated',
           'planb_private.create_profile_for_auth_user()', 'EXECUTE') THEN
        RAISE EXCEPTION 'La función privilegiada está expuesta';
    END IF;
END;
$$;

SELECT set_config('request.jwt.claim.sub', (SELECT id::text FROM planb_test_users LIMIT 1), true);
SET LOCAL ROLE authenticated;
DO $$
DECLARE affected integer;
BEGIN
    IF (SELECT count(*) FROM public.profiles) <> 1
       OR NOT EXISTS (SELECT 1 FROM public.profiles WHERE id = auth.uid()) THEN
        RAISE EXCEPTION 'RLS permite leer perfiles ajenos o bloquea el propio';
    END IF;
    UPDATE public.profiles SET username = lower(username) WHERE id = auth.uid();
    GET DIAGNOSTICS affected = ROW_COUNT;
    IF affected <> 1 THEN RAISE EXCEPTION 'No se pudo actualizar el username propio'; END IF;
    UPDATE public.profiles SET username = 'Ajeno123456' WHERE id <> auth.uid();
    GET DIAGNOSTICS affected = ROW_COUNT;
    IF affected <> 0 THEN RAISE EXCEPTION 'RLS permitió actualizar un perfil ajeno'; END IF;
    BEGIN
        UPDATE public.profiles SET id = gen_random_uuid() WHERE id = auth.uid();
        RAISE EXCEPTION 'Se permitió modificar id';
    EXCEPTION WHEN insufficient_privilege THEN NULL;
    END;
    BEGIN
        UPDATE public.profiles SET created_at = now() WHERE id = auth.uid();
        RAISE EXCEPTION 'Se permitió modificar created_at';
    EXCEPTION WHEN insufficient_privilege THEN NULL;
    END;
    BEGIN
        UPDATE public.profiles SET updated_at = now() WHERE id = auth.uid();
        RAISE EXCEPTION 'Se permitió modificar updated_at directamente';
    EXCEPTION WHEN insufficient_privilege THEN NULL;
    END;
    BEGIN
        INSERT INTO public.profiles (id, username) VALUES (gen_random_uuid(), 'Inyectado123');
        RAISE EXCEPTION 'Se permitió INSERT a authenticated';
    EXCEPTION WHEN insufficient_privilege THEN NULL;
    END;
    BEGIN
        DELETE FROM public.profiles WHERE id = auth.uid();
        RAISE EXCEPTION 'Se permitió DELETE a authenticated';
    EXCEPTION WHEN insufficient_privilege THEN NULL;
    END;
END;
$$;

RESET ROLE;
SET LOCAL ROLE anon;
DO $$
BEGIN
    BEGIN
        PERFORM id FROM public.profiles;
        RAISE EXCEPTION 'Se permitió lectura anónima';
    EXCEPTION WHEN insufficient_privilege THEN NULL;
    END;
END;
$$;

RESET ROLE;
SET LOCAL ROLE service_role;
DO $$
BEGIN
    PERFORM id, username_normalized FROM public.profiles;
    BEGIN
        PERFORM username FROM public.profiles;
        RAISE EXCEPTION 'El cliente administrativo puede leer columnas innecesarias';
    EXCEPTION WHEN insufficient_privilege THEN NULL;
    END;
    BEGIN
        DELETE FROM public.profiles WHERE false;
        RAISE EXCEPTION 'El cliente administrativo puede borrar perfiles';
    EXCEPTION WHEN insufficient_privilege THEN NULL;
    END;
END;
$$;

RESET ROLE;
DO $$
DECLARE target_id uuid;
BEGIN
    SELECT id INTO target_id FROM planb_test_users LIMIT 1;
    DELETE FROM auth.users WHERE id = target_id;
    IF EXISTS (SELECT 1 FROM public.profiles WHERE id = target_id) THEN
        RAISE EXCEPTION 'La eliminación Auth no eliminó el perfil';
    END IF;
END;
$$;

ROLLBACK;
SELECT 'OK: fixtures revertidas; constraints, atomicidad, grants, RLS y cascade verificados.' AS resultado;
