-- INTEGRACIÓN: instancia Supabase de prueba autorizada con migrations aplicadas.
-- Ejecutar como administrador; fixtures y cambios de datos se revierten.
BEGIN;
SELECT set_config('planb.proposals_owner', gen_random_uuid()::text, true);
SELECT set_config('planb.proposals_member', gen_random_uuid()::text, true);
SELECT set_config('planb.proposals_outsider', gen_random_uuid()::text, true);

INSERT INTO auth.users(id, aud, role, email, raw_user_meta_data, created_at, updated_at)
SELECT id, 'authenticated', 'authenticated', id::text || '@example.invalid',
       jsonb_build_object('username', 'Q_' || substr(replace(id::text, '-', ''), 1, 20)), now(), now()
FROM (VALUES (current_setting('planb.proposals_owner')::uuid),
             (current_setting('planb.proposals_member')::uuid),
             (current_setting('planb.proposals_outsider')::uuid)) AS fixture(id);

SELECT set_config('request.jwt.claim.sub', current_setting('planb.proposals_owner'), true);
SET LOCAL ROLE authenticated;
SELECT set_config('planb.proposals_group', public.create_group('Propuestas de prueba', NULL)::text, true);
WITH created AS (
    INSERT INTO public.plans(name, description, status, group_id, created_by)
    VALUES ('Plan de prueba', 'Propuestas', true,
            current_setting('planb.proposals_group')::uuid, auth.uid())
    RETURNING id
)
SELECT set_config('planb.proposals_plan', id::text, true) FROM created;
WITH created AS (
    INSERT INTO public.proposals(tittle, description, created_by, plan_id, type, posicion)
    VALUES ('Propuesta owner', 'Punto inicial', auth.uid(),
            current_setting('planb.proposals_plan')::uuid, 'juntada',
            'SRID=4326;POINT(-58.38 -34.6)'::geography)
    RETURNING id
)
SELECT set_config('planb.proposals_owner_proposal', id::text, true) FROM created;
RESET ROLE;

INSERT INTO public.group_members(group_id, user_id, role)
VALUES (current_setting('planb.proposals_group')::uuid,
        current_setting('planb.proposals_member')::uuid, 'member');

SELECT set_config('request.jwt.claim.sub', current_setting('planb.proposals_member'), true);
SET LOCAL ROLE authenticated;
WITH created AS (
    INSERT INTO public.proposals(tittle, description, created_by, plan_id, type, posicion)
    VALUES ('Propuesta member', NULL, auth.uid(),
            current_setting('planb.proposals_plan')::uuid, 'salida',
            'SRID=4326;POINT(-58.4 -34.61)'::geography)
    RETURNING id
)
SELECT set_config('planb.proposals_member_proposal', id::text, true) FROM created;
DO $$
DECLARE affected integer;
BEGIN
    BEGIN
        INSERT INTO public.proposals(tittle, description, created_by, plan_id, type, posicion)
        VALUES ('Segunda propuesta', NULL, auth.uid(),
                current_setting('planb.proposals_plan')::uuid, 'salida',
                'SRID=4326;POINT(-58.4 -34.61)'::geography);
        RAISE EXCEPTION 'Un miembro pudo crear dos propuestas para el mismo plan';
    EXCEPTION WHEN unique_violation THEN NULL;
    END;
    IF (SELECT count(*) FROM public.proposals
        WHERE plan_id = current_setting('planb.proposals_plan')::uuid) <> 2 THEN
        RAISE EXCEPTION 'El miembro no puede leer propuestas del plan';
    END IF;
    UPDATE public.proposals SET tittle = 'Edición ajena'
    WHERE id = current_setting('planb.proposals_owner_proposal')::bigint;
    GET DIAGNOSTICS affected = ROW_COUNT;
    IF affected <> 0 THEN RAISE EXCEPTION 'El miembro editó propuesta ajena'; END IF;
    UPDATE public.proposals SET tittle = 'Propuesta editada'
    WHERE id = current_setting('planb.proposals_member_proposal')::bigint;
    GET DIAGNOSTICS affected = ROW_COUNT;
    IF affected <> 1 THEN RAISE EXCEPTION 'El autor no pudo editar su propuesta'; END IF;
    DELETE FROM public.proposals
    WHERE id = current_setting('planb.proposals_owner_proposal')::bigint;
    GET DIAGNOSTICS affected = ROW_COUNT;
    IF affected <> 0 THEN RAISE EXCEPTION 'El miembro borró propuesta ajena'; END IF;
    DELETE FROM public.proposals
    WHERE id = current_setting('planb.proposals_member_proposal')::bigint;
    GET DIAGNOSTICS affected = ROW_COUNT;
    IF affected <> 1 THEN RAISE EXCEPTION 'El autor no pudo borrar su propuesta'; END IF;
END;
$$;
RESET ROLE;

SELECT set_config('request.jwt.claim.sub', current_setting('planb.proposals_outsider'), true);
SET LOCAL ROLE authenticated;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM public.proposals
               WHERE id = current_setting('planb.proposals_owner_proposal')::bigint) THEN
        RAISE EXCEPTION 'Un usuario ajeno pudo leer la propuesta';
    END IF;
END;
$$;
RESET ROLE;

DO $$
DECLARE role_name text;
BEGIN
    IF NOT has_schema_privilege('authenticated', 'postgis', 'USAGE') THEN
        RAISE EXCEPTION 'authenticated no tiene USAGE en el esquema postgis';
    END IF;
    IF NOT has_column_privilege('service_role', 'public.profiles', 'username', 'SELECT') THEN
        RAISE EXCEPTION 'service_role no puede resolver usernames de autores';
    END IF;
    FOREACH role_name IN ARRAY ARRAY['anon', 'service_role', 'planb_django_runtime'] LOOP
        IF has_table_privilege(role_name, 'public.proposals', 'SELECT')
           OR has_table_privilege(role_name, 'public.proposals', 'DELETE')
           OR has_any_column_privilege(role_name, 'public.proposals', 'INSERT')
           OR has_any_column_privilege(role_name, 'public.proposals', 'UPDATE') THEN
            RAISE EXCEPTION 'Permiso excesivo para % sobre proposals', role_name;
        END IF;
    END LOOP;
    IF NOT (SELECT relrowsecurity FROM pg_class WHERE oid = 'public.proposals'::regclass) THEN
        RAISE EXCEPTION 'RLS deshabilitado en proposals';
    END IF;
END;
$$;
ROLLBACK;
SELECT 'OK: lectura de miembros y gestión exclusiva del autor; fixtures revertidas.' AS resultado;