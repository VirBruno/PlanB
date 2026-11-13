-- Borrador de integración. Ejecutar como administrador en una base preparada.
-- Crea datos ficticios y revierte toda la transacción.
BEGIN;
SELECT set_config('planb.uno_actor', gen_random_uuid()::text, true);
SELECT set_config('planb.uno_other', gen_random_uuid()::text, true);
INSERT INTO auth.users(id, aud, role, email, raw_user_meta_data, created_at, updated_at)
SELECT id, 'authenticated', 'authenticated', id::text || '@example.invalid',
       jsonb_build_object('username', 'U_' || substr(replace(id::text, '-', ''), 1, 20)), now(), now()
FROM (VALUES (current_setting('planb.uno_actor')::uuid),
             (current_setting('planb.uno_other')::uuid)) AS fixture(id);
SELECT set_config('request.jwt.claim.sub', current_setting('planb.uno_actor'), true);
SET LOCAL ROLE authenticated;
SELECT set_config('planb.uno_group', public.create_group('Partida UNO de prueba', NULL)::text, true);
WITH created AS (
    INSERT INTO public.plans(name, description, status, group_id, created_by)
    VALUES ('Plan UNO', 'Temporal', true, current_setting('planb.uno_group')::uuid, auth.uid()) RETURNING id
)
SELECT set_config('planb.uno_plan', id::text, true) FROM created;
WITH created AS (
    INSERT INTO public.proposals(tittle, created_by, plan_id, type, posicion)
    VALUES ('Propuesta UNO', auth.uid(), current_setting('planb.uno_plan')::uuid,
            'juntada', 'SRID=4326;POINT(-58.38 -34.6)') RETURNING id
)
SELECT set_config('planb.uno_proposal', id::text, true) FROM created;
RESET ROLE;
UPDATE public.plans SET election_method = 'combat' WHERE id = current_setting('planb.uno_plan')::uuid;
INSERT INTO public.group_members(group_id, user_id, role)
VALUES (current_setting('planb.uno_group')::uuid, current_setting('planb.uno_other')::uuid, 'member');

DO $$
DECLARE
    response jsonb;
    stored jsonb;
BEGIN
    IF has_function_privilege('authenticated', 'public.uno_bot_game(uuid,bigint,text,text,text)', 'EXECUTE')
       OR has_function_privilege('authenticated', 'public.uno_proposal_scores(uuid)', 'EXECUTE')
       OR has_table_privilege('authenticated', 'planb_private.uno_bot_matches', 'SELECT') THEN
        RAISE EXCEPTION 'La API y el estado privado aún no deben estar habilitados';
    END IF;
    response := public.uno_bot_game(current_setting('planb.uno_plan')::uuid,
                                   current_setting('planb.uno_proposal')::bigint, 'status');
    IF response->'game' <> 'null'::jsonb OR (response->>'victories')::integer <> 0 THEN
        RAISE EXCEPTION 'Una partida nueva debe empezar sin estado ni victorias';
    END IF;
    response := public.uno_bot_game(current_setting('planb.uno_plan')::uuid,
                                   current_setting('planb.uno_proposal')::bigint, 'start');
    IF response->'game' ? 'deck' OR response->'game' ? 'bot'
       OR jsonb_array_length(response->'game'->'hand') <> 7 THEN
        RAISE EXCEPTION 'La respuesta expuso cartas ocultas o repartió incorrectamente';
    END IF;
    BEGIN
        PERFORM public.uno_bot_game(current_setting('planb.uno_plan')::uuid,
                                    current_setting('planb.uno_proposal')::bigint, 'play', 'inventada');
        RAISE EXCEPTION 'Se aceptó una carta inventada';
    EXCEPTION WHEN invalid_parameter_value THEN NULL;
    END;
    SELECT game INTO stored FROM planb_private.uno_bot_matches
      WHERE proposal_id = current_setting('planb.uno_proposal')::bigint;
    -- Preparación determinista de la última jugada: solo el administrador del test
    -- modifica el estado; ningún cliente tiene acceso a esta tabla.
    stored := jsonb_set(stored, '{player}', '[{"id":"ultima","color":"rojo","value":"7"}]'::jsonb);
    stored := jsonb_set(stored, '{discard}', '[{"id":"mesa","color":"rojo","value":"3"}]'::jsonb);
    stored := jsonb_set(stored, '{color}', '"rojo"'::jsonb);
    UPDATE planb_private.uno_bot_matches SET game = stored
      WHERE proposal_id = current_setting('planb.uno_proposal')::bigint;
    response := public.uno_bot_game(current_setting('planb.uno_plan')::uuid,
                                   current_setting('planb.uno_proposal')::bigint, 'play', 'ultima');
    IF response->'game'->>'winner' <> 'player' OR (response->>'victories')::integer <> 1 THEN
        RAISE EXCEPTION 'La victoria no se registró al terminar la partida';
    END IF;
    BEGIN
        PERFORM public.uno_bot_game(current_setting('planb.uno_plan')::uuid,
                                    current_setting('planb.uno_proposal')::bigint, 'play', 'ultima');
        RAISE EXCEPTION 'Se aceptó una jugada posterior al final';
    EXCEPTION WHEN invalid_parameter_value THEN NULL;
    END;
    IF (SELECT victories FROM public.uno_proposal_scores(current_setting('planb.uno_plan')::uuid)
        WHERE proposal_id = current_setting('planb.uno_proposal')::bigint) <> 1 THEN
        RAISE EXCEPTION 'La victoria no quedó reflejada una sola vez';
    END IF;
END;
$$;

SELECT set_config('request.jwt.claim.sub', current_setting('planb.uno_other'), true);
DO $$
BEGIN
    BEGIN
        PERFORM public.uno_bot_game(current_setting('planb.uno_plan')::uuid,
                                    current_setting('planb.uno_proposal')::bigint, 'status');
        RAISE EXCEPTION 'Un integrante consultó una partida ajena';
    EXCEPTION WHEN insufficient_privilege THEN NULL;
    END;
END;
$$;
ROLLBACK;
SELECT 'OK: privacidad, jugada inválida y registro único de victoria.' AS resultado;
