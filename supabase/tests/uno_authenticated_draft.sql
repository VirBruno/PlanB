-- Prueba de integración con roles reales, siempre dentro de una transacción reversible.
BEGIN;
SELECT set_config('planb.uno_owner', gen_random_uuid()::text, true);
SELECT set_config('planb.uno_member', gen_random_uuid()::text, true);
SELECT set_config('planb.uno_outsider', gen_random_uuid()::text, true);
INSERT INTO auth.users(id, aud, role, email, raw_user_meta_data, created_at, updated_at)
SELECT id, 'authenticated', 'authenticated', id::text || '@example.invalid',
       jsonb_build_object('username', 'U_' || substr(replace(id::text, '-', ''), 1, 20)), now(), now()
FROM (VALUES (current_setting('planb.uno_owner')::uuid),
             (current_setting('planb.uno_member')::uuid),
             (current_setting('planb.uno_outsider')::uuid)) AS fixture(id);

SELECT set_config('request.jwt.claim.sub', current_setting('planb.uno_owner'), true);
SET LOCAL ROLE authenticated;
SELECT set_config('planb.uno_group', public.create_group('Permisos UNO de prueba', NULL)::text, true);
WITH created AS (
    INSERT INTO public.plans(name, description, status, group_id, created_by)
    VALUES ('Plan UNO autenticado', 'Temporal', true, current_setting('planb.uno_group')::uuid, auth.uid()) RETURNING id
)
SELECT set_config('planb.uno_plan', id::text, true) FROM created;
WITH created AS (
    INSERT INTO public.proposals(tittle, created_by, plan_id, type, posicion, date_pick)
    VALUES ('Propuesta UNO', auth.uid(), current_setting('planb.uno_plan')::uuid,
            'juntada', 'SRID=4326;POINT(-58.38 -34.6)', '2035-01-01 18:00:00+00') RETURNING id
)
SELECT set_config('planb.uno_proposal', id::text, true) FROM created;
RESET ROLE;
UPDATE public.plans SET election_method = 'combat' WHERE id = current_setting('planb.uno_plan')::uuid;
INSERT INTO public.group_members(group_id, user_id, role)
VALUES (current_setting('planb.uno_group')::uuid, current_setting('planb.uno_member')::uuid, 'member');

SET LOCAL ROLE authenticated;
DO $$
DECLARE response jsonb;
BEGIN
    IF has_schema_privilege('authenticated', 'planb_private', 'USAGE') THEN
        RAISE EXCEPTION 'Un integrante tiene acceso directo a datos privados';
    END IF;
    BEGIN
        PERFORM 1 FROM planb_private.uno_bot_matches LIMIT 1;
        RAISE EXCEPTION 'Se pudo consultar el estado privado';
    EXCEPTION WHEN insufficient_privilege THEN NULL;
    END;
    response := public.uno_bot_game(current_setting('planb.uno_plan')::uuid,
                                   current_setting('planb.uno_proposal')::bigint, 'start');
    IF response->'game' ? 'deck' OR response->'game' ? 'bot'
       OR jsonb_array_length(response->'game'->'hand') <> 7 THEN
        RAISE EXCEPTION 'La partida expone cartas ocultas o reparte incorrectamente';
    END IF;
    BEGIN
        PERFORM public.uno_bot_game(current_setting('planb.uno_plan')::uuid,
                                    current_setting('planb.uno_proposal')::bigint, 'play', 'inventada');
        RAISE EXCEPTION 'Una carta inventada fue aceptada';
    EXCEPTION WHEN invalid_parameter_value THEN NULL;
    END;
END;
$$;
RESET ROLE;

-- Dejar una última carta de forma determinista, solo como administrador de la
-- prueba. Los integrantes no pueden modificar directamente el estado privado.
UPDATE planb_private.uno_bot_matches
SET game = jsonb_set(jsonb_set(jsonb_set(game,
    '{player}', '[{"id":"ultima","color":"rojo","value":"7"}]'::jsonb),
    '{discard}', '[{"id":"mesa","color":"rojo","value":"3"}]'::jsonb),
    '{color}', '"rojo"'::jsonb)
WHERE proposal_id = current_setting('planb.uno_proposal')::bigint;

SET LOCAL ROLE authenticated;
DO $$
DECLARE response jsonb;
BEGIN
    response := public.uno_bot_game(current_setting('planb.uno_plan')::uuid,
                                   current_setting('planb.uno_proposal')::bigint, 'play', 'ultima');
    IF response->'game'->>'winner' <> 'player' OR (response->>'victories')::integer <> 1 THEN
        RAISE EXCEPTION 'La victoria no se registró correctamente';
    END IF;
    BEGIN
        PERFORM public.uno_bot_game(current_setting('planb.uno_plan')::uuid,
                                    current_setting('planb.uno_proposal')::bigint, 'play', 'ultima');
        RAISE EXCEPTION 'Se aceptó repetir una jugada después de ganar';
    EXCEPTION WHEN invalid_parameter_value THEN NULL;
    END;
    IF (SELECT victories FROM public.uno_proposal_scores(current_setting('planb.uno_plan')::uuid)
        WHERE proposal_id = current_setting('planb.uno_proposal')::bigint) <> 1 THEN
        RAISE EXCEPTION 'La victoria no se registró una sola vez';
    END IF;
END;
$$;
RESET ROLE;

SELECT set_config('request.jwt.claim.sub', current_setting('planb.uno_member'), true);
SET LOCAL ROLE authenticated;
DO $$
BEGIN
    BEGIN
        PERFORM public.uno_bot_game(current_setting('planb.uno_plan')::uuid,
                                    current_setting('planb.uno_proposal')::bigint, 'status');
        RAISE EXCEPTION 'Un integrante pudo ver una partida ajena';
    EXCEPTION WHEN insufficient_privilege THEN NULL;
    END;
    IF (SELECT count(*) FROM public.uno_proposal_scores(current_setting('planb.uno_plan')::uuid)) <> 1 THEN
        RAISE EXCEPTION 'Un integrante no puede consultar la clasificación';
    END IF;
    BEGIN
        PERFORM public.confirm_uno_plan_proposal(current_setting('planb.uno_plan')::uuid,
                                                current_setting('planb.uno_proposal')::bigint);
        RAISE EXCEPTION 'Un integrante pudo confirmar el plan';
    EXCEPTION WHEN insufficient_privilege THEN NULL;
    END;
END;
$$;
RESET ROLE;

SELECT set_config('request.jwt.claim.sub', current_setting('planb.uno_outsider'), true);
SET LOCAL ROLE authenticated;
DO $$
BEGIN
    BEGIN
        PERFORM * FROM public.uno_proposal_scores(current_setting('planb.uno_plan')::uuid);
        RAISE EXCEPTION 'Una persona ajena pudo ver la clasificación';
    EXCEPTION WHEN insufficient_privilege THEN NULL;
    END;
END;
$$;
RESET ROLE;

SELECT set_config('request.jwt.claim.sub', current_setting('planb.uno_owner'), true);
SET LOCAL ROLE authenticated;
DO $$
BEGIN
    IF public.confirm_uno_plan_proposal(current_setting('planb.uno_plan')::uuid,
                                       current_setting('planb.uno_proposal')::bigint)
       IS DISTINCT FROM current_setting('planb.uno_proposal')::bigint THEN
        RAISE EXCEPTION 'No se confirmó la propuesta líder';
    END IF;
END;
$$;
RESET ROLE;
ROLLBACK;
SELECT 'OK: acceso autenticado y victoria verificados; cambios revertidos.' AS resultado;
