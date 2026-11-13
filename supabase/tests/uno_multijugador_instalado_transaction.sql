-- Test of the installed objects. Fixtures and temporary grants are rolled back.
BEGIN;
-- Comprobación de solo lectura para ejecutar después de la instalación.
-- Las funciones públicas deben existir, pero no estar habilitadas todavía.
DO $$
DECLARE
    signature text;
    routine regprocedure;
    table_name text;
    relation regclass;
BEGIN
    FOREACH signature IN ARRAY ARRAY[
        'public.uno_duel_invite(uuid,bigint,uuid)',
        'public.uno_duel_respond(uuid,boolean,bigint)',
        'public.uno_duel_game(uuid,text,text,text,bigint,bigint)',
        'public.uno_duel_list(uuid)'
    ] LOOP
        routine := to_regprocedure(signature);
        IF routine IS NULL THEN RAISE EXCEPTION 'Falta la función %', signature; END IF;
        IF has_function_privilege('authenticated', routine, 'EXECUTE')
           OR has_function_privilege('anon', routine, 'EXECUTE') THEN
            RAISE EXCEPTION 'La función % ya tiene acceso habilitado', signature;
        END IF;
    END LOOP;
    FOREACH table_name IN ARRAY ARRAY['planb_private.uno_duel_matches', 'planb_private.uno_duel_events'] LOOP
        relation := to_regclass(table_name);
        IF relation IS NULL THEN RAISE EXCEPTION 'Falta la tabla %', table_name; END IF;
        IF has_table_privilege('authenticated', relation, 'SELECT')
           OR has_table_privilege('anon', relation, 'SELECT')
           OR NOT EXISTS (SELECT 1 FROM pg_catalog.pg_class WHERE oid = relation AND relrowsecurity) THEN
            RAISE EXCEPTION 'Revisar permisos o RLS de %', table_name;
        END IF;
    END LOOP;
    IF NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_constraint
        WHERE conrelid = 'public.notifications'::regclass AND conname = 'notification_plan_required'
          AND pg_catalog.pg_get_constraintdef(oid) LIKE '%uno_duel_invitation%'
    ) THEN
        RAISE EXCEPTION 'Falta la regla de notificaciones de UNO';
    END IF;
END;
$$;


-- Solo para ejecutar dentro de una transacción reversible de prueba.
SELECT set_config('planb.duel_owner', gen_random_uuid()::text, true);
SELECT set_config('planb.duel_member', gen_random_uuid()::text, true);
SELECT set_config('planb.duel_outsider', gen_random_uuid()::text, true);
INSERT INTO auth.users(id, aud, role, email, raw_user_meta_data, created_at, updated_at)
SELECT id, 'authenticated', 'authenticated', id::text || '@example.invalid',
       jsonb_build_object('username', 'D_' || substr(replace(id::text, '-', ''), 1, 20)), now(), now()
FROM (VALUES (current_setting('planb.duel_owner')::uuid),
             (current_setting('planb.duel_member')::uuid),
             (current_setting('planb.duel_outsider')::uuid)) AS fixture(id);

SELECT set_config('request.jwt.claim.sub', current_setting('planb.duel_owner'), true);
SET LOCAL ROLE authenticated;
SELECT set_config('planb.duel_group', public.create_group('Duelo UNO de prueba', NULL)::text, true);
WITH created AS (
    INSERT INTO public.plans(name, description, status, group_id, created_by)
    VALUES ('Plan duelo temporal', 'Prueba', true, current_setting('planb.duel_group')::uuid, auth.uid()) RETURNING id
) SELECT set_config('planb.duel_plan', id::text, true) FROM created;
WITH created AS (
    INSERT INTO public.plans(name, description, status, group_id, created_by)
    VALUES ('Otro plan temporal', 'Prueba', true, current_setting('planb.duel_group')::uuid, auth.uid()) RETURNING id
) SELECT set_config('planb.duel_other_plan', id::text, true) FROM created;
WITH created AS (
    INSERT INTO public.proposals(tittle, created_by, plan_id, type, posicion, date_pick)
    VALUES ('Propuesta A', auth.uid(), current_setting('planb.duel_plan')::uuid,
            'juntada', 'SRID=4326;POINT(-58.38 -34.6)', '2035-01-01 18:00:00+00') RETURNING id
) SELECT set_config('planb.duel_proposal', id::text, true) FROM created;
WITH created AS (
    INSERT INTO public.proposals(tittle, created_by, plan_id, type, posicion, date_pick)
    VALUES ('Propuesta de otro plan', auth.uid(), current_setting('planb.duel_other_plan')::uuid,
            'juntada', 'SRID=4326;POINT(-58.38 -34.6)', '2035-01-01 18:00:00+00') RETURNING id
) SELECT set_config('planb.duel_other_proposal', id::text, true) FROM created;
RESET ROLE;
UPDATE public.plans SET election_method = 'combat' WHERE id = current_setting('planb.duel_plan')::uuid;
INSERT INTO public.group_members(group_id, user_id, role)
VALUES (current_setting('planb.duel_group')::uuid, current_setting('planb.duel_member')::uuid, 'member');

DO $$
DECLARE rejected boolean := false;
BEGIN
    BEGIN
        INSERT INTO public.notifications(user_id, type, payload)
        VALUES (current_setting('planb.duel_member')::uuid, 'uno_duel_invitation', '{}'::jsonb);
    EXCEPTION WHEN check_violation THEN rejected := true;
    END;
    IF NOT rejected THEN RAISE EXCEPTION 'Se permitió una invitación UNO sin plan'; END IF;
    rejected := false;
    BEGIN
        INSERT INTO public.notifications(user_id, type, plan_id, payload)
        VALUES (current_setting('planb.duel_member')::uuid, 'otro_tipo',
                current_setting('planb.duel_plan')::uuid, '{}'::jsonb);
    EXCEPTION WHEN check_violation THEN rejected := true;
    END;
    IF NOT rejected THEN RAISE EXCEPTION 'Otro tipo de notificación pudo asociarse a un plan'; END IF;
END;
$$;

-- Concedidos únicamente dentro de la transacción de prueba.
GRANT EXECUTE ON FUNCTION public.uno_duel_invite(uuid,bigint,uuid),
    public.uno_duel_respond(uuid,boolean,bigint) TO authenticated;
SET LOCAL ROLE authenticated;
DO $$
DECLARE rejected boolean;
BEGIN
    rejected := false;
    BEGIN
        PERFORM public.uno_duel_invite(current_setting('planb.duel_plan')::uuid,
            current_setting('planb.duel_proposal')::bigint, current_setting('planb.duel_outsider')::uuid);
    EXCEPTION WHEN insufficient_privilege THEN rejected := true;
    END;
    IF NOT rejected THEN RAISE EXCEPTION 'Se permitió invitar a una persona ajena al grupo'; END IF;
    rejected := false;
    BEGIN
        PERFORM public.uno_duel_invite(current_setting('planb.duel_plan')::uuid,
            current_setting('planb.duel_other_proposal')::bigint, current_setting('planb.duel_member')::uuid);
    EXCEPTION WHEN invalid_parameter_value THEN rejected := true;
    END;
    IF NOT rejected THEN RAISE EXCEPTION 'Se aceptó una propuesta de otro plan'; END IF;
END;
$$;
SELECT set_config('planb.duel_match', public.uno_duel_invite(
    current_setting('planb.duel_plan')::uuid, current_setting('planb.duel_proposal')::bigint,
    current_setting('planb.duel_member')::uuid)::text, true);
DO $$
DECLARE rejected boolean := false;
BEGIN
    BEGIN
        PERFORM public.uno_duel_invite(current_setting('planb.duel_plan')::uuid,
            current_setting('planb.duel_proposal')::bigint, current_setting('planb.duel_member')::uuid);
    EXCEPTION WHEN unique_violation THEN rejected := true;
    END;
    IF NOT rejected THEN RAISE EXCEPTION 'Se permitió duplicar una invitación activa'; END IF;
    rejected := false;
    BEGIN
        PERFORM public.uno_duel_respond(current_setting('planb.duel_match')::uuid, true,
            current_setting('planb.duel_proposal')::bigint);
    EXCEPTION WHEN insufficient_privilege THEN rejected := true;
    END;
    IF NOT rejected THEN RAISE EXCEPTION 'El invitador pudo aceptar su propia invitación'; END IF;
END;
$$;
RESET ROLE;
DO $$
BEGIN
    IF (SELECT count(*) FROM public.notifications WHERE user_id = current_setting('planb.duel_member')::uuid
        AND type = 'uno_duel_invitation' AND payload->>'match_id' = current_setting('planb.duel_match')) <> 1 THEN
        RAISE EXCEPTION 'La invitación no generó exactamente una notificación';
    END IF;
END;
$$;

SELECT set_config('request.jwt.claim.sub', current_setting('planb.duel_member'), true);
SET LOCAL ROLE authenticated;
DO $$
DECLARE rejected boolean := false;
BEGIN
    BEGIN
        PERFORM public.uno_duel_respond(current_setting('planb.duel_match')::uuid, true,
            current_setting('planb.duel_other_proposal')::bigint);
    EXCEPTION WHEN invalid_parameter_value THEN rejected := true;
    END;
    IF NOT rejected THEN RAISE EXCEPTION 'El invitado pudo elegir una propuesta de otro plan'; END IF;
END;
$$;
SELECT public.uno_duel_respond(current_setting('planb.duel_match')::uuid, true,
    current_setting('planb.duel_proposal')::bigint);
DO $$
DECLARE rejected boolean := false;
BEGIN
    BEGIN
        PERFORM public.uno_duel_respond(current_setting('planb.duel_match')::uuid, true,
            current_setting('planb.duel_proposal')::bigint);
    EXCEPTION WHEN invalid_parameter_value THEN rejected := true;
    END;
    IF NOT rejected THEN RAISE EXCEPTION 'Se permitió responder dos veces'; END IF;
    rejected := false;
    BEGIN
        PERFORM public.uno_duel_invite(current_setting('planb.duel_plan')::uuid,
            current_setting('planb.duel_proposal')::bigint, current_setting('planb.duel_owner')::uuid);
    EXCEPTION WHEN unique_violation THEN rejected := true;
    END;
    IF NOT rejected THEN RAISE EXCEPTION 'Se permitió duplicar la partida con los jugadores invertidos'; END IF;
END;
$$;
RESET ROLE;
DO $$
DECLARE match_row planb_private.uno_duel_matches%ROWTYPE;
BEGIN
    SELECT * INTO match_row FROM planb_private.uno_duel_matches
      WHERE match_id = current_setting('planb.duel_match')::uuid;
    IF match_row.status <> 'playing' OR match_row.invitee_proposal_id <> current_setting('planb.duel_proposal')::bigint
       OR jsonb_array_length(match_row.game->'player') <> 7 OR jsonb_array_length(match_row.game->'bot') <> 7
       OR match_row.game->>'id' <> match_row.match_id::text THEN
        RAISE EXCEPTION 'La aceptación no inició correctamente la partida compartida';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM public.notifications WHERE user_id = match_row.invitee_id
        AND payload->>'match_id' = match_row.match_id::text AND read_at IS NOT NULL) THEN
        RAISE EXCEPTION 'La notificación de la invitación no se marcó como leída';
    END IF;
END;
$$;

SELECT set_config('request.jwt.claim.sub', current_setting('planb.duel_outsider'), true);
SET LOCAL ROLE authenticated;
DO $$
DECLARE rejected boolean := false;
BEGIN
    BEGIN
        PERFORM public.uno_duel_respond(current_setting('planb.duel_match')::uuid, false, NULL);
    EXCEPTION WHEN insufficient_privilege THEN rejected := true;
    END;
    IF NOT rejected THEN RAISE EXCEPTION 'Una persona ajena pudo responder la invitación'; END IF;
END;
$$;
RESET ROLE;

-- Se cierra la primera partida ficticia para comprobar el rechazo de otra.
UPDATE planb_private.uno_duel_matches SET status = 'finished',
    winner_id = inviter_id, finish_reason = 'forfeit', updated_at = now()
WHERE match_id = current_setting('planb.duel_match')::uuid;
SELECT set_config('request.jwt.claim.sub', current_setting('planb.duel_owner'), true);
SET LOCAL ROLE authenticated;
SELECT set_config('planb.duel_rejected', public.uno_duel_invite(
    current_setting('planb.duel_plan')::uuid, current_setting('planb.duel_proposal')::bigint,
    current_setting('planb.duel_member')::uuid)::text, true);
RESET ROLE;
SELECT set_config('request.jwt.claim.sub', current_setting('planb.duel_member'), true);
SET LOCAL ROLE authenticated;
SELECT public.uno_duel_respond(current_setting('planb.duel_rejected')::uuid, false, NULL);
RESET ROLE;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM planb_private.uno_duel_matches
        WHERE match_id = current_setting('planb.duel_rejected')::uuid
          AND status = 'rejected' AND game IS NULL AND winner_id IS NULL) THEN
        RAISE EXCEPTION 'Rechazar la invitación no conservó el estado correcto';
    END IF;
END;
$$;

-- Continúa la prueba de invitaciones con usuarios ficticios.
GRANT EXECUTE ON FUNCTION public.uno_duel_game(uuid,text,text,text,bigint,bigint) TO authenticated;
DO $$
BEGIN
    IF has_table_privilege('authenticated', 'planb_private.uno_duel_events', 'SELECT') THEN
        RAISE EXCEPTION 'Los eventos privados no deben ser accesibles directamente';
    END IF;
END;
$$;

-- La persona invitada crea una propuesta diferente en el mismo plan.
SELECT set_config('request.jwt.claim.sub', current_setting('planb.duel_member'), true);
SET LOCAL ROLE authenticated;
WITH created AS (
    INSERT INTO public.proposals(tittle, created_by, plan_id, type, posicion, date_pick)
    VALUES ('Propuesta B', auth.uid(), current_setting('planb.duel_plan')::uuid,
            'juntada', 'SRID=4326;POINT(-58.38 -34.6)', '2035-01-01 18:00:00+00') RETURNING id
) SELECT set_config('planb.duel_proposal_b', id::text, true) FROM created;
RESET ROLE;

-- Una partida termina por una jugada de quien invitó.
SELECT set_config('request.jwt.claim.sub', current_setting('planb.duel_owner'), true);
SET LOCAL ROLE authenticated;
SELECT set_config('planb.duel_play_match', public.uno_duel_invite(
    current_setting('planb.duel_plan')::uuid, current_setting('planb.duel_proposal')::bigint,
    current_setting('planb.duel_member')::uuid)::text, true);
RESET ROLE;
SELECT set_config('request.jwt.claim.sub', current_setting('planb.duel_member'), true);
SET LOCAL ROLE authenticated;
SELECT public.uno_duel_respond(current_setting('planb.duel_play_match')::uuid, true,
    current_setting('planb.duel_proposal_b')::bigint);
RESET ROLE;

-- Se fija un estado de juego ficticio para probar el último turno.
UPDATE planb_private.uno_duel_matches SET game = jsonb_build_object(
    'id', match_id::text, 'player', jsonb_build_array('{"id":"win","color":"rojo","value":"7"}'::jsonb),
    'bot', jsonb_build_array('{"id":"hidden","color":"azul","value":"2"}'::jsonb),
    'deck', jsonb_build_array('{"id":"secret","color":"verde","value":"3"}'::jsonb),
    'discard', jsonb_build_array('{"id":"top","color":"rojo","value":"1"}'::jsonb),
    'color', 'rojo', 'drawn', NULL, 'winner', NULL, 'turn', 'player',
    'announcements', '[]'::jsonb, 'events', '[]'::jsonb, 'message', '')
WHERE match_id = current_setting('planb.duel_play_match')::uuid;

SELECT set_config('request.jwt.claim.sub', current_setting('planb.duel_outsider'), true);
SET LOCAL ROLE authenticated;
DO $$
DECLARE rejected boolean := false;
BEGIN
    BEGIN
        PERFORM public.uno_duel_game(current_setting('planb.duel_play_match')::uuid);
    EXCEPTION WHEN insufficient_privilege THEN rejected := true;
    END;
    IF NOT rejected THEN RAISE EXCEPTION 'Una persona ajena pudo leer la partida'; END IF;
END;
$$;
RESET ROLE;

SELECT set_config('request.jwt.claim.sub', current_setting('planb.duel_owner'), true);
SET LOCAL ROLE authenticated;
DO $$
DECLARE visible jsonb; rejected boolean := false;
BEGIN
    visible := public.uno_duel_game(current_setting('planb.duel_play_match')::uuid);
    IF visible::text LIKE '%hidden%' OR visible::text LIKE '%secret%'
       OR visible->>'turn' <> 'player' OR (visible->>'version')::integer <> 0
       OR (visible->>'proposal_id')::bigint <> current_setting('planb.duel_proposal')::bigint
       OR (visible->>'opponent_proposal_id')::bigint <> current_setting('planb.duel_proposal_b')::bigint THEN
        RAISE EXCEPTION 'La vista inicial expone cartas o propuestas incorrectas';
    END IF;
    BEGIN
        PERFORM public.uno_duel_game(current_setting('planb.duel_play_match')::uuid,
            'play', 'win', NULL, 1, 0);
    EXCEPTION WHEN invalid_parameter_value THEN rejected := true;
    END;
    IF NOT rejected THEN RAISE EXCEPTION 'Se aceptó una versión incorrecta'; END IF;
    visible := public.uno_duel_game(current_setting('planb.duel_play_match')::uuid,
        'play', 'win', NULL, 0, 0);
    IF visible->>'winner' <> 'player' OR visible->>'status' <> 'finished'
       OR visible->>'finish_reason' <> 'played' OR (visible->>'version')::integer <> 1
       OR visible->'events'->0->>'value' <> '7' THEN
        RAISE EXCEPTION 'La victoria por jugada no se procesó correctamente';
    END IF;
    rejected := false;
    BEGIN
        PERFORM public.uno_duel_game(current_setting('planb.duel_play_match')::uuid,
            'play', 'win', NULL, 0, 0);
    EXCEPTION WHEN invalid_parameter_value THEN rejected := true;
    END;
    IF NOT rejected THEN RAISE EXCEPTION 'Se aceptó una jugada después de la victoria'; END IF;
END;
$$;
RESET ROLE;
DO $$
BEGIN
    IF (SELECT count(*) FROM planb_private.uno_victories WHERE match_id = current_setting('planb.duel_play_match')::uuid
        AND proposal_id = current_setting('planb.duel_proposal')::bigint
        AND player_id = current_setting('planb.duel_owner')::uuid) <> 1 THEN
        RAISE EXCEPTION 'La victoria no se registró una sola vez para la propuesta ganadora';
    END IF;
END;
$$;
SELECT set_config('request.jwt.claim.sub', current_setting('planb.duel_member'), true);
SET LOCAL ROLE authenticated;
DO $$
DECLARE visible jsonb;
BEGIN
    visible := public.uno_duel_game(current_setting('planb.duel_play_match')::uuid);
    IF visible->>'winner' <> 'opponent' OR visible->'events'->0->>'actor' <> 'opponent'
       OR visible::text LIKE '%secret%' THEN
        RAISE EXCEPTION 'La vista de la persona invitada no refleja la victoria';
    END IF;
    visible := public.uno_duel_game(current_setting('planb.duel_play_match')::uuid,
        'status', NULL, NULL, NULL, 1);
    IF jsonb_array_length(visible->'events') <> 0 THEN
        RAISE EXCEPTION 'Se repitieron eventos ya recibidos';
    END IF;
END;
$$;
RESET ROLE;

-- Otra partida comprueba una jugada y luego un abandono voluntario.
SELECT set_config('request.jwt.claim.sub', current_setting('planb.duel_owner'), true);
SET LOCAL ROLE authenticated;
SELECT set_config('planb.duel_forfeit_match', public.uno_duel_invite(
    current_setting('planb.duel_plan')::uuid, current_setting('planb.duel_proposal')::bigint,
    current_setting('planb.duel_member')::uuid)::text, true);
RESET ROLE;
SELECT set_config('request.jwt.claim.sub', current_setting('planb.duel_member'), true);
SET LOCAL ROLE authenticated;
SELECT public.uno_duel_respond(current_setting('planb.duel_forfeit_match')::uuid, true,
    current_setting('planb.duel_proposal_b')::bigint);
RESET ROLE;
UPDATE planb_private.uno_duel_matches SET game = jsonb_build_object(
    'id', match_id::text, 'player', jsonb_build_array(
        '{"id":"play","color":"rojo","value":"7"}'::jsonb,
        '{"id":"remain","color":"verde","value":"2"}'::jsonb),
    'bot', jsonb_build_array('{"id":"rival","color":"azul","value":"2"}'::jsonb),
    'deck', jsonb_build_array('{"id":"secret","color":"verde","value":"3"}'::jsonb),
    'discard', jsonb_build_array('{"id":"top","color":"rojo","value":"1"}'::jsonb),
    'color', 'rojo', 'drawn', NULL, 'winner', NULL, 'turn', 'player',
    'announcements', '[]'::jsonb, 'events', '[]'::jsonb, 'message', '')
WHERE match_id = current_setting('planb.duel_forfeit_match')::uuid;
SELECT set_config('request.jwt.claim.sub', current_setting('planb.duel_owner'), true);
SET LOCAL ROLE authenticated;
SELECT public.uno_duel_game(current_setting('planb.duel_forfeit_match')::uuid,
    'play', 'play', NULL, 0, 0);
DO $$
DECLARE rejected boolean := false; visible jsonb;
BEGIN
    BEGIN
        PERFORM public.uno_duel_game(current_setting('planb.duel_forfeit_match')::uuid,
            'forfeit', NULL, NULL, 0, 0);
    EXCEPTION WHEN invalid_parameter_value THEN rejected := true;
    END;
    IF NOT rejected THEN RAISE EXCEPTION 'Se aceptó una acción con versión anterior'; END IF;
    visible := public.uno_duel_game(current_setting('planb.duel_forfeit_match')::uuid,
        'forfeit', NULL, NULL, 1, 0);
    IF visible->>'winner' <> 'opponent' OR visible->>'finish_reason' <> 'forfeit'
       OR visible->'events'->2->>'type' <> 'forfeit' THEN
        RAISE EXCEPTION 'El abandono o el historial de eventos no se procesaron';
    END IF;
END;
$$;
RESET ROLE;
DO $$
BEGIN
    IF (SELECT count(*) FROM planb_private.uno_victories WHERE match_id = current_setting('planb.duel_forfeit_match')::uuid
        AND proposal_id = current_setting('planb.duel_proposal_b')::bigint
        AND player_id = current_setting('planb.duel_member')::uuid) <> 1 THEN
        RAISE EXCEPTION 'El abandono no otorgó el punto a la propuesta del rival';
    END IF;
END;
$$;

-- Continúa la prueba de partidas y usa sus usuarios y planes ficticios.
GRANT EXECUTE ON FUNCTION public.uno_duel_list(uuid) TO authenticated;
SELECT set_config('request.jwt.claim.sub', current_setting('planb.duel_owner'), true);
SET LOCAL ROLE authenticated;
DO $$
DECLARE matches jsonb;
BEGIN
    matches := public.uno_duel_list(current_setting('planb.duel_plan')::uuid);
    IF jsonb_typeof(matches) <> 'array' OR jsonb_array_length(matches) < 2
       OR matches::text LIKE '%"deck"%' OR matches::text LIKE '%"hand"%'
       OR matches::text LIKE '%"secret"%' THEN
        RAISE EXCEPTION 'El listado expone datos de juego o no incluye las partidas';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM jsonb_array_elements(matches) AS m
        WHERE m->>'id' = current_setting('planb.duel_forfeit_match')
          AND m->>'role' = 'inviter' AND m->>'status' = 'finished'
          AND m->>'winner' = 'opponent' AND m->>'finish_reason' = 'forfeit') THEN
        RAISE EXCEPTION 'El listado no informa correctamente la partida y su resultado';
    END IF;
END;
$$;
RESET ROLE;
SELECT set_config('request.jwt.claim.sub', current_setting('planb.duel_outsider'), true);
SET LOCAL ROLE authenticated;
DO $$
DECLARE rejected boolean := false;
BEGIN
    BEGIN
        PERFORM public.uno_duel_list(current_setting('planb.duel_plan')::uuid);
    EXCEPTION WHEN insufficient_privilege THEN rejected := true;
    END;
    IF NOT rejected THEN RAISE EXCEPTION 'Una persona ajena pudo listar las partidas'; END IF;
END;
$$;
RESET ROLE;

ROLLBACK;
SELECT 'OK: installed UNO multiplayer objects and permissions verified; changes rolled back.' AS resultado;
