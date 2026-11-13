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
