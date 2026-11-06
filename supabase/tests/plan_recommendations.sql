-- Integración MANUAL: Supabase LOCAL/de pruebas autorizado con migrations aplicadas.
-- No forma parte de manage.py test. No ejecutar contra remoto en esta tarea.
-- Todo se revierte. Verifica permisos SQL reales, no sólo mocks Python.
BEGIN;
SELECT set_config('planb.rec_owner', gen_random_uuid()::text, true);
SELECT set_config('planb.rec_member', gen_random_uuid()::text, true);
SELECT set_config('planb.rec_outsider', gen_random_uuid()::text, true);
INSERT INTO auth.users(id, aud, role, email, raw_user_meta_data, created_at, updated_at)
SELECT id, 'authenticated', 'authenticated', id::text || '@example.invalid',
       jsonb_build_object('username', 'Rec_' || substr(replace(id::text, '-', ''), 1, 20)), now(), now()
FROM (VALUES (current_setting('planb.rec_owner')::uuid), (current_setting('planb.rec_member')::uuid),
             (current_setting('planb.rec_outsider')::uuid)) AS fixture(id);

SELECT set_config('request.jwt.claim.sub', current_setting('planb.rec_owner'), true);
SET LOCAL ROLE authenticated;
SELECT set_config('planb.rec_group', public.create_group('Recomendaciones de prueba', NULL)::text, true);
WITH created AS (
    INSERT INTO public.plans(name, description, status, group_id, created_by)
    VALUES ('Merienda', 'Prueba reversible', true, current_setting('planb.rec_group')::uuid, auth.uid()) RETURNING id
) SELECT set_config('planb.rec_plan', id::text, true) FROM created;
WITH created AS (
    INSERT INTO public.proposals(tittle, description, date_pick, created_by, plan_id, type, posicion)
    VALUES ('Merienda', NULL, now() + interval '1 day', auth.uid(),
            current_setting('planb.rec_plan')::uuid, 'juntada', 'SRID=4326;POINT(-58.38 -34.6)') RETURNING id
) SELECT set_config('planb.rec_proposal', id::text, true) FROM created;
RESET ROLE;
INSERT INTO public.group_members(group_id, user_id, role)
VALUES (current_setting('planb.rec_group')::uuid, current_setting('planb.rec_member')::uuid, 'member');

DO $$ BEGIN
    IF public.claim_plan_recommendations(current_setting('planb.rec_plan')::uuid,
        current_setting('planb.rec_proposal')::bigint, now(), false) IS NOT NULL THEN
        RAISE EXCEPTION 'Se generó para un plan sin confirmar';
    END IF;
END $$;
SET LOCAL ROLE authenticated;
SELECT public.confirm_plan_proposal(current_setting('planb.rec_plan')::uuid,
                                   current_setting('planb.rec_proposal')::bigint);
RESET ROLE;
SELECT set_config('planb.rec_confirmed', confirmed_at::text, true)
FROM public.plans WHERE id = current_setting('planb.rec_plan')::uuid;
SELECT set_config('planb.rec_items', '[{"provider":"geoapify","provider_place_id":"test-poi",
    "name":"Café fixture","categories":["catering.cafe"],"address":null,"latitude":-34.6,
    "longitude":-58.38,"distance_meters":0,"website":null,"opening_hours":null,"media":null,"rank":1}]', true);

SET LOCAL ROLE service_role;
SELECT set_config('planb.rec_ticket', public.claim_plan_recommendations(
    current_setting('planb.rec_plan')::uuid, current_setting('planb.rec_proposal')::bigint,
    current_setting('planb.rec_confirmed')::timestamptz, false)::text, true);
DO $$
DECLARE p uuid := current_setting('planb.rec_plan')::uuid; t uuid := current_setting('planb.rec_ticket')::uuid;
BEGIN
    IF t IS NULL THEN RAISE EXCEPTION 'Backend no pudo reservar'; END IF;
    IF public.claim_plan_recommendations(p, current_setting('planb.rec_proposal')::bigint,
        current_setting('planb.rec_confirmed')::timestamptz, false) IS NOT NULL THEN
        RAISE EXCEPTION 'Se permitió reserva duplicada';
    END IF;
    IF public.claim_plan_recommendations(p, current_setting('planb.rec_proposal')::bigint,
        current_setting('planb.rec_confirmed')::timestamptz, true) IS NOT NULL THEN
        RAISE EXCEPTION 'Se robó un pending reciente';
    END IF;
    BEGIN
        PERFORM public.finish_plan_recommendations(p, t, '[{},{},{},{},{},{}]'::jsonb);
        RAISE EXCEPTION 'Se permitieron más de cinco lugares';
    EXCEPTION WHEN check_violation THEN NULL; END;
    IF public.finish_plan_recommendations(p, gen_random_uuid(), '[]'::jsonb) THEN
        RAISE EXCEPTION 'Se aceptó ticket falso';
    END IF;
    IF NOT public.finish_plan_recommendations(p, t, current_setting('planb.rec_items')::jsonb) THEN
        RAISE EXCEPTION 'No se guardó snapshot';
    END IF;
    IF public.finish_plan_recommendations(p, t, '[]'::jsonb) THEN
        RAISE EXCEPTION 'Se pudo completar dos veces';
    END IF;
END $$;
RESET ROLE;

SELECT set_config('request.jwt.claim.sub', current_setting('planb.rec_member'), true);
SET LOCAL ROLE authenticated;
DO $$
DECLARE p uuid := current_setting('planb.rec_plan')::uuid;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM public.plan_recommendations WHERE plan_id = p AND status = 'ready'
                   AND items = current_setting('planb.rec_items')::jsonb) THEN
        RAISE EXCEPTION 'Miembro no puede leer snapshot compartido';
    END IF;
    BEGIN
        PERFORM public.claim_plan_recommendations(p, current_setting('planb.rec_proposal')::bigint,
            current_setting('planb.rec_confirmed')::timestamptz, true);
        RAISE EXCEPTION 'authenticated pudo reservar directamente';
    EXCEPTION WHEN insufficient_privilege THEN NULL; END;
    BEGIN
        PERFORM public.finish_plan_recommendations(p, gen_random_uuid(), '[]'::jsonb);
        RAISE EXCEPTION 'authenticated pudo falsificar un snapshot';
    EXCEPTION WHEN insufficient_privilege THEN NULL; END;
    BEGIN
        PERFORM generation_id FROM public.plan_recommendations WHERE plan_id = p;
        RAISE EXCEPTION 'Se expuso ticket interno';
    EXCEPTION WHEN insufficient_privilege THEN NULL; END;
    BEGIN
        UPDATE public.plan_recommendations SET items = '[]'::jsonb WHERE plan_id = p;
        RAISE EXCEPTION 'authenticated pudo editar snapshot';
    EXCEPTION WHEN insufficient_privilege THEN NULL; END;
    BEGIN
        DELETE FROM public.plan_recommendations WHERE plan_id = p;
        RAISE EXCEPTION 'authenticated pudo borrar snapshot';
    EXCEPTION WHEN insufficient_privilege THEN NULL; END;
END $$;
RESET ROLE;

SELECT set_config('request.jwt.claim.sub', current_setting('planb.rec_outsider'), true);
SET LOCAL ROLE authenticated;
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM public.plan_recommendations WHERE plan_id = current_setting('planb.rec_plan')::uuid) THEN
        RAISE EXCEPTION 'Ajeno pudo leer recomendaciones';
    END IF;
END $$;
RESET ROLE;

-- Recálculo fallido conserva el conjunto previo. Un callback anterior no puede escribir.
SET LOCAL ROLE service_role;
DO $$
DECLARE p uuid := current_setting('planb.rec_plan')::uuid; t uuid;
BEGIN
    t := public.claim_plan_recommendations(p, current_setting('planb.rec_proposal')::bigint,
          current_setting('planb.rec_confirmed')::timestamptz, true);
    IF t IS NULL THEN RAISE EXCEPTION 'No se permitió recálculo explícito'; END IF;
    IF public.finish_plan_recommendations(p, current_setting('planb.rec_ticket')::uuid, '[]'::jsonb) THEN
        RAISE EXCEPTION 'Un callback viejo sobrescribió el recálculo';
    END IF;
    PERFORM public.finish_plan_recommendations(p, t, '[]'::jsonb, 'timeout');
END $$;
RESET ROLE;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM public.plan_recommendations
        WHERE plan_id = current_setting('planb.rec_plan')::uuid AND status = 'failed'
          AND error_code = 'timeout' AND items = current_setting('planb.rec_items')::jsonb) THEN
        RAISE EXCEPTION 'Fallo borró snapshot previo';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM public.plans WHERE id = current_setting('planb.rec_plan')::uuid
                   AND status IS TRUE AND selected_proposal_id IS NOT NULL) THEN
        RAISE EXCEPTION 'Fallo cambió el plan';
    END IF;
END $$;

DELETE FROM public.group_members WHERE group_id = current_setting('planb.rec_group')::uuid
    AND user_id = current_setting('planb.rec_member')::uuid;
SELECT set_config('request.jwt.claim.sub', current_setting('planb.rec_member'), true);
SET LOCAL ROLE authenticated;
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM public.plan_recommendations WHERE plan_id = current_setting('planb.rec_plan')::uuid) THEN
        RAISE EXCEPTION 'Ex miembro pudo leer';
    END IF;
END $$;
RESET ROLE;

SELECT set_config('request.jwt.claim.sub', current_setting('planb.rec_owner'), true);
SET LOCAL ROLE authenticated;
UPDATE public.plans SET status = false WHERE id = current_setting('planb.rec_plan')::uuid;
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM public.plan_recommendations WHERE plan_id = current_setting('planb.rec_plan')::uuid) THEN
        RAISE EXCEPTION 'Se expuso snapshot de encuentro finalizado';
    END IF;
END $$;
UPDATE public.plans SET status = true WHERE id = current_setting('planb.rec_plan')::uuid;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM public.plan_recommendations WHERE plan_id = current_setting('planb.rec_plan')::uuid) THEN
        RAISE EXCEPTION 'Reactivación perdió snapshot';
    END IF;
END $$;
SELECT public.reopen_plan_decision(current_setting('planb.rec_plan')::uuid);
RESET ROLE;
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM public.plan_recommendations WHERE plan_id = current_setting('planb.rec_plan')::uuid) THEN
        RAISE EXCEPTION 'Reapertura no invalidó snapshot';
    END IF;
    IF public.finish_plan_recommendations(current_setting('planb.rec_plan')::uuid,
        current_setting('planb.rec_ticket')::uuid, current_setting('planb.rec_items')::jsonb) THEN
        RAISE EXCEPTION 'Se guardó un resultado tardío tras reapertura';
    END IF;
END $$;
ROLLBACK;
