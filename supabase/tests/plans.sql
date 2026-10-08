-- INTEGRACIÓN: instancia Supabase de prueba autorizada, como administrador.
-- Requiere infraestructura, perfiles, grupos, ambas migraciones iniciales de planes
-- y 202610080002_member_plan_creation.sql;
-- no depende de la migración opcional de edición de grupos.
-- Fixtures y cambios de datos se revierten; no ejecutar sobre producción.
BEGIN;
SELECT set_config('planb.plans_owner', gen_random_uuid()::text, true);
SELECT set_config('planb.plans_member', gen_random_uuid()::text, true);
SELECT set_config('planb.plans_outsider', gen_random_uuid()::text, true);

INSERT INTO auth.users(id, aud, role, email, raw_user_meta_data, created_at, updated_at)
SELECT id, 'authenticated', 'authenticated', id::text || '@example.invalid',
       jsonb_build_object('username', 'P_' || substr(replace(id::text, '-', ''), 1, 20)), now(), now()
FROM (VALUES (current_setting('planb.plans_owner')::uuid),
             (current_setting('planb.plans_member')::uuid),
             (current_setting('planb.plans_outsider')::uuid)) AS fixture(id);

SELECT set_config('request.jwt.claim.sub', current_setting('planb.plans_owner'), true);
SET LOCAL ROLE authenticated;
SELECT set_config('planb.plans_group', public.create_group('Planes de prueba', NULL)::text, true);
WITH created AS (
    INSERT INTO public.plans(name, description, status, group_id, created_by)
    VALUES ('Salida inicial', 'Plan compartido', true,
            current_setting('planb.plans_group')::uuid, auth.uid())
    RETURNING id
)
SELECT set_config('planb.plans_plan', id::text, true) FROM created;
RESET ROLE;

INSERT INTO public.group_members(group_id, user_id, role)
VALUES (current_setting('planb.plans_group')::uuid,
        current_setting('planb.plans_member')::uuid, 'member');

SELECT set_config('request.jwt.claim.sub', current_setting('planb.plans_member'), true);
SET LOCAL ROLE authenticated;
DO $$
DECLARE affected integer; g uuid := current_setting('planb.plans_group')::uuid;
        p uuid := current_setting('planb.plans_plan')::uuid;
BEGIN
    IF (SELECT count(*) FROM public.plans WHERE group_id = g) <> 1 THEN
        RAISE EXCEPTION 'El miembro no puede leer el plan de su grupo';
    END IF;
    INSERT INTO public.plans(name, description, status, group_id, created_by)
        VALUES ('Plan del miembro', 'Creación autorizada', true, g, auth.uid());
    IF NOT EXISTS (SELECT 1 FROM public.plans WHERE group_id = g AND name = 'Plan del miembro' AND created_by = auth.uid()) THEN
        RAISE EXCEPTION 'El miembro no pudo crear su plan';
    END IF;
    UPDATE public.plans SET name = 'Edición no autorizada' WHERE id = p;
    GET DIAGNOSTICS affected = ROW_COUNT;
    IF affected <> 0 THEN RAISE EXCEPTION 'Un miembro pudo editar planes'; END IF;
    DELETE FROM public.plans WHERE id = p;
    GET DIAGNOSTICS affected = ROW_COUNT;
    IF affected <> 0 THEN RAISE EXCEPTION 'Un miembro pudo eliminar planes'; END IF;
END;
$$;

SELECT set_config('request.jwt.claim.sub', current_setting('planb.plans_outsider'), true);
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM public.plans WHERE id = current_setting('planb.plans_plan')::uuid) THEN
        RAISE EXCEPTION 'Un usuario ajeno pudo leer el plan';
    END IF;
END;
$$;
RESET ROLE;

-- Simular transferencia administrativa: created_by no concede permisos.
UPDATE public.group_members SET role = 'member'
WHERE group_id = current_setting('planb.plans_group')::uuid AND role = 'owner';
UPDATE public.group_members SET role = 'owner'
WHERE group_id = current_setting('planb.plans_group')::uuid
  AND user_id = current_setting('planb.plans_member')::uuid;

SELECT set_config('request.jwt.claim.sub', current_setting('planb.plans_owner'), true);
SET LOCAL ROLE authenticated;
DO $$
DECLARE affected integer; p uuid := current_setting('planb.plans_plan')::uuid;
BEGIN
    UPDATE public.plans SET name = 'Autor ya no administra' WHERE id = p;
    GET DIAGNOSTICS affected = ROW_COUNT;
    IF affected <> 0 THEN RAISE EXCEPTION 'created_by conservó permiso sin ser owner'; END IF;
END;
$$;
RESET ROLE;

SELECT set_config('request.jwt.claim.sub', current_setting('planb.plans_member'), true);
SET LOCAL ROLE authenticated;
DO $$
DECLARE affected integer; p uuid := current_setting('planb.plans_plan')::uuid;
BEGIN
    UPDATE public.plans SET name = 'Admin vigente' WHERE id = p;
    GET DIAGNOSTICS affected = ROW_COUNT;
    IF affected <> 1 THEN RAISE EXCEPTION 'El owner actual no pudo editar el plan'; END IF;
    DELETE FROM public.plans WHERE id = p;
    GET DIAGNOSTICS affected = ROW_COUNT;
    IF affected <> 1 THEN RAISE EXCEPTION 'El owner actual no pudo eliminar el plan'; END IF;
END;
$$;
RESET ROLE;

DO $$
DECLARE role_name text;
BEGIN
    FOREACH role_name IN ARRAY ARRAY['anon', 'service_role', 'planb_django_runtime'] LOOP
        IF has_table_privilege(role_name, 'public.plans', 'SELECT')
           OR has_table_privilege(role_name, 'public.plans', 'DELETE')
           OR has_any_column_privilege(role_name, 'public.plans', 'INSERT')
           OR has_any_column_privilege(role_name, 'public.plans', 'UPDATE') THEN
            RAISE EXCEPTION 'Permiso excesivo para % sobre plans', role_name;
        END IF;
    END LOOP;
    IF NOT (SELECT relrowsecurity FROM pg_class WHERE oid = 'public.plans'::regclass) THEN
        RAISE EXCEPTION 'RLS deshabilitado en plans';
    END IF;
END;
$$;
ROLLBACK;
SELECT 'OK: miembros leen/crean, sólo owner edita/elimina; fixtures revertidas.' AS resultado;
