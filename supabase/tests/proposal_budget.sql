-- INTEGRACIÓN: sólo instancia Supabase de prueba autorizada, nunca el compartido.
-- Ejecutar como administrador después de 202610090001_proposal_budget_range.sql.
-- Fixtures reversibles; no envía correo y termina con ROLLBACK.
BEGIN;
SELECT set_config('planb.budget_owner', gen_random_uuid()::text, true);
SELECT set_config('planb.budget_member', gen_random_uuid()::text, true);
SELECT set_config('planb.budget_outsider', gen_random_uuid()::text, true);

INSERT INTO auth.users(id, aud, role, email, raw_user_meta_data, created_at, updated_at)
SELECT id, 'authenticated', 'authenticated', id::text || '@example.invalid',
       jsonb_build_object('username', 'B_' || substr(replace(id::text, '-', ''), 1, 20)), now(), now()
FROM (VALUES (current_setting('planb.budget_owner')::uuid), (current_setting('planb.budget_member')::uuid),
             (current_setting('planb.budget_outsider')::uuid)) AS fixture(id);

SELECT set_config('request.jwt.claim.sub', current_setting('planb.budget_owner'), true);
SET LOCAL ROLE authenticated;
SELECT set_config('planb.budget_group', public.create_group('Presupuesto de prueba', NULL)::text, true);
WITH created AS (
    INSERT INTO public.plans(name, description, status, group_id, created_by)
    VALUES ('Plan de presupuesto', 'Prueba reversible', true, current_setting('planb.budget_group')::uuid, auth.uid())
    RETURNING id
)
SELECT set_config('planb.budget_plan', id::text, true) FROM created;
-- Compatibilidad: INSERT antiguo omite ambas columnas nuevas.
WITH created AS (
    INSERT INTO public.proposals(tittle, description, created_by, plan_id, type, posicion)
    VALUES ('Sin presupuesto', NULL, auth.uid(), current_setting('planb.budget_plan')::uuid,
            'juntada', 'SRID=4326;POINT(-58.38 -34.6)'::postgis.geography)
    RETURNING id
)
SELECT set_config('planb.budget_owner_proposal', id::text, true) FROM created;
RESET ROLE;
INSERT INTO public.group_members(group_id, user_id, role)
VALUES (current_setting('planb.budget_group')::uuid, current_setting('planb.budget_member')::uuid, 'member');

SELECT set_config('request.jwt.claim.sub', current_setting('planb.budget_member'), true);
SET LOCAL ROLE authenticated;
WITH created AS (
    INSERT INTO public.proposals(tittle, description, created_by, plan_id, type, posicion, budget_min, budget_max)
    VALUES ('Con presupuesto', NULL, auth.uid(), current_setting('planb.budget_plan')::uuid,
            'salida', 'SRID=4326;POINT(-58.4 -34.61)'::postgis.geography, 1.25, 2.75)
    RETURNING id
)
SELECT set_config('planb.budget_member_proposal', id::text, true) FROM created;
DO $$
DECLARE p bigint := current_setting('planb.budget_member_proposal')::bigint;
        budget record; affected integer;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM public.proposals WHERE id = current_setting('planb.budget_owner_proposal')::bigint
                   AND budget_min IS NULL AND budget_max IS NULL) THEN
        RAISE EXCEPTION 'Compatibilidad o lectura de propuesta ajena sin presupuesto rota';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM public.proposals WHERE id = p AND budget_min = 1.25 AND budget_max = 2.75) THEN
        RAISE EXCEPTION 'INSERT de presupuesto por miembro falló';
    END IF;
    BEGIN
        INSERT INTO public.proposals(tittle, description, created_by, plan_id, type, posicion, budget_min, budget_max)
        VALUES ('Duplicada', NULL, auth.uid(), current_setting('planb.budget_plan')::uuid,
                'salida', 'SRID=4326;POINT(-58.4 -34.61)'::postgis.geography, 0, 10);
        RAISE EXCEPTION 'Presupuesto permitió una segunda propuesta por autor/plan';
    EXCEPTION WHEN unique_violation THEN NULL;
    END;
    FOR budget IN SELECT * FROM (VALUES
        (NULL::numeric, NULL::numeric), (10, 20), (10, NULL), (NULL, 20),
        (0, NULL), (NULL, 0), (0, 0), (1.25, 2.75), (9999999999.99, 9999999999.99)
    ) AS valid(minimum, maximum) LOOP
        UPDATE public.proposals SET budget_min = budget.minimum, budget_max = budget.maximum WHERE id = p;
        GET DIAGNOSTICS affected = ROW_COUNT;
        IF affected <> 1 OR NOT EXISTS (SELECT 1 FROM public.proposals WHERE id = p
            AND budget_min IS NOT DISTINCT FROM budget.minimum
            AND budget_max IS NOT DISTINCT FROM budget.maximum) THEN
            RAISE EXCEPTION 'UPDATE válido de presupuesto falló';
        END IF;
    END LOOP;
    FOR budget IN SELECT * FROM (VALUES
        (-0.01::numeric, NULL::numeric), (NULL, -0.01), (20, 10),
        ('NaN'::numeric, NULL), (NULL, 'NaN'::numeric)
    ) AS invalid(minimum, maximum) LOOP
        BEGIN
            UPDATE public.proposals SET budget_min = budget.minimum, budget_max = budget.maximum WHERE id = p;
            RAISE EXCEPTION 'Se aceptó presupuesto negativo, NaN o invertido';
        EXCEPTION WHEN check_violation THEN NULL;
        END;
    END LOOP;
    BEGIN
        UPDATE public.proposals SET budget_min = 10000000000, budget_max = NULL WHERE id = p;
        RAISE EXCEPTION 'Se aceptó presupuesto fuera de numeric(12,2)';
    EXCEPTION WHEN numeric_value_out_of_range THEN NULL;
    END;
    UPDATE public.proposals SET budget_min = NULL, budget_max = NULL WHERE id = p;
    IF NOT EXISTS (SELECT 1 FROM public.proposals WHERE id = p AND budget_min IS NULL AND budget_max IS NULL) THEN
        RAISE EXCEPTION 'No se pudieron borrar ambos límites';
    END IF;
    UPDATE public.proposals SET budget_min = 10, budget_max = 20
        WHERE id = current_setting('planb.budget_owner_proposal')::bigint;
    GET DIAGNOSTICS affected = ROW_COUNT;
    IF affected <> 0 THEN RAISE EXCEPTION 'Miembro editó presupuesto ajeno'; END IF;
    -- Mantener un rango visible al otro integrante.
    UPDATE public.proposals SET budget_min = 1.25, budget_max = 2.75 WHERE id = p;
END;
$$;

SELECT set_config('request.jwt.claim.sub', current_setting('planb.budget_owner'), true);
DO $$
DECLARE affected integer;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM public.proposals WHERE id = current_setting('planb.budget_member_proposal')::bigint
                   AND budget_min = 1.25 AND budget_max = 2.75) THEN
        RAISE EXCEPTION 'Integrante no puede leer presupuesto de otro autor';
    END IF;
    UPDATE public.proposals SET budget_min = 0, budget_max = 0
        WHERE id = current_setting('planb.budget_member_proposal')::bigint;
    GET DIAGNOSTICS affected = ROW_COUNT;
    IF affected <> 0 THEN RAISE EXCEPTION 'Owner editó presupuesto de otro autor'; END IF;
END;
$$;
SELECT set_config('request.jwt.claim.sub', current_setting('planb.budget_outsider'), true);
DO $$
DECLARE affected integer;
BEGIN
    IF EXISTS (SELECT 1 FROM public.proposals WHERE plan_id = current_setting('planb.budget_plan')::uuid) THEN
        RAISE EXCEPTION 'Outsider leyó presupuestos';
    END IF;
    UPDATE public.proposals SET budget_min = 0 WHERE id = current_setting('planb.budget_member_proposal')::bigint;
    GET DIAGNOSTICS affected = ROW_COUNT;
    IF affected <> 0 THEN RAISE EXCEPTION 'Outsider editó presupuesto'; END IF;
    BEGIN
        INSERT INTO public.proposals(tittle, created_by, plan_id, type, posicion, budget_min, budget_max)
        VALUES ('Fuera del grupo', auth.uid(), current_setting('planb.budget_plan')::uuid,
                'salida', 'SRID=4326;POINT(-58.4 -34.61)'::postgis.geography, 0, 10);
        RAISE EXCEPTION 'Outsider creó propuesta con presupuesto';
    EXCEPTION WHEN insufficient_privilege THEN NULL;
    END;
END;
$$;
RESET ROLE;

DO $$
DECLARE field_name text; role_name text;
BEGIN
    FOREACH field_name IN ARRAY ARRAY['budget_min', 'budget_max'] LOOP
        IF NOT EXISTS (SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'proposals' AND column_name = field_name
              AND data_type = 'numeric' AND numeric_precision = 12 AND numeric_scale = 2 AND is_nullable = 'YES') THEN
            RAISE EXCEPTION 'Tipo o nulabilidad incorrectos en %', field_name;
        END IF;
        IF NOT has_column_privilege('authenticated', 'public.proposals', field_name, 'INSERT')
           OR NOT has_column_privilege('authenticated', 'public.proposals', field_name, 'UPDATE') THEN
            RAISE EXCEPTION 'Falta grant por columna en %', field_name;
        END IF;
        FOREACH role_name IN ARRAY ARRAY['anon', 'service_role', 'planb_django_runtime'] LOOP
            IF has_column_privilege(role_name, 'public.proposals', field_name, 'SELECT')
               OR has_column_privilege(role_name, 'public.proposals', field_name, 'INSERT')
               OR has_column_privilege(role_name, 'public.proposals', field_name, 'UPDATE') THEN
                RAISE EXCEPTION 'Permiso excesivo para % en %', role_name, field_name;
            END IF;
        END LOOP;
    END LOOP;
    IF has_column_privilege('authenticated', 'public.proposals', 'created_by', 'UPDATE') THEN
        RAISE EXCEPTION 'Se amplió el permiso de identidad';
    END IF;
    IF NOT (SELECT relrowsecurity FROM pg_class WHERE oid = 'public.proposals'::regclass) THEN
        RAISE EXCEPTION 'RLS deshabilitado';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE schemaname = 'public'
        AND tablename = 'proposals' AND indexname = 'proposals_one_per_user_plan_idx') THEN
        RAISE EXCEPTION 'Falta índice de una propuesta por usuario/plan';
    END IF;
END;
$$;
ROLLBACK;
