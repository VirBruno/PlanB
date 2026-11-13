-- Borrador: requiere las reglas y las invitaciones de UNO multijugador.
-- No concede permisos a los roles de la aplicación.
BEGIN;

ALTER TABLE planb_private.uno_duel_matches
    ADD COLUMN version bigint NOT NULL DEFAULT 0 CHECK (version >= 0);

-- Conserva los eventos visibles para que una reconexión o consulta tardía no
-- pierda jugadas. Nunca se guarda en esta tabla una mano ni el mazo.
CREATE TABLE planb_private.uno_duel_events (
    match_id uuid NOT NULL REFERENCES planb_private.uno_duel_matches(match_id) ON DELETE CASCADE,
    version bigint NOT NULL CHECK (version > 0),
    position integer NOT NULL CHECK (position > 0),
    event jsonb NOT NULL CHECK (jsonb_typeof(event) = 'object'),
    PRIMARY KEY (match_id, version, position)
);
ALTER TABLE planb_private.uno_duel_events ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE planb_private.uno_duel_events
    FROM PUBLIC, anon, authenticated, service_role, planb_django_runtime;

CREATE FUNCTION public.uno_duel_game(
    p_match_id uuid, p_action text DEFAULT 'status', p_card_id text DEFAULT NULL,
    p_color text DEFAULT NULL, p_expected_version bigint DEFAULT NULL,
    p_after_version bigint DEFAULT 0
) RETURNS jsonb LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = '' AS $$
DECLARE
    actor uuid := auth.uid();
    target_plan_id uuid;
    target_group_id uuid;
    plan_row public.plans%ROWTYPE;
    match_row planb_private.uno_duel_matches%ROWTYPE;
    member_count integer;
    seat text;
    other_seat text;
    next_game jsonb;
    next_version bigint;
    winner uuid;
    winning_proposal bigint;
    result_reason text;
    visible jsonb;
    visible_events jsonb;
BEGIN
    IF actor IS NULL OR p_match_id IS NULL OR p_action IS NULL
       OR p_action NOT IN ('status', 'play', 'draw', 'pass', 'forfeit')
       OR p_after_version IS NULL OR p_after_version < 0
       OR (p_action <> 'status' AND (p_expected_version IS NULL OR p_expected_version < 0)) THEN
        RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'Invalid UNO duel request';
    END IF;
    SELECT m.plan_id INTO target_plan_id FROM planb_private.uno_duel_matches AS m
      WHERE m.match_id = p_match_id AND actor IN (m.inviter_id, m.invitee_id);
    IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE = '42501', MESSAGE = 'UNO duel unavailable'; END IF;
    SELECT p.group_id INTO target_group_id FROM public.plans AS p WHERE p.id = target_plan_id;
    IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE = '42501', MESSAGE = 'UNO duel unavailable'; END IF;
    PERFORM g.id FROM public.groups AS g WHERE g.id = target_group_id FOR UPDATE;
    SELECT * INTO plan_row FROM public.plans AS p
      WHERE p.id = target_plan_id AND p.group_id = target_group_id FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE = '42501', MESSAGE = 'UNO duel unavailable'; END IF;
    SELECT * INTO match_row FROM planb_private.uno_duel_matches AS m
      WHERE m.match_id = p_match_id AND actor IN (m.inviter_id, m.invitee_id) FOR UPDATE;
    IF NOT FOUND OR match_row.status NOT IN ('playing', 'finished') THEN
        RAISE EXCEPTION USING ERRCODE = '42501', MESSAGE = 'UNO duel unavailable';
    END IF;
    PERFORM m.user_id FROM public.group_members AS m
      WHERE m.group_id = target_group_id AND m.user_id IN (match_row.inviter_id, match_row.invitee_id)
      ORDER BY m.user_id FOR SHARE OF m;
    GET DIAGNOSTICS member_count = ROW_COUNT;
    IF member_count <> 2 THEN
        RAISE EXCEPTION USING ERRCODE = '42501', MESSAGE = 'UNO duel unavailable';
    END IF;
    seat := CASE WHEN actor = match_row.inviter_id THEN 'player' ELSE 'bot' END;
    other_seat := CASE WHEN seat = 'player' THEN 'bot' ELSE 'player' END;
    IF p_after_version > match_row.version THEN
        RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'Invalid UNO event version';
    END IF;

    IF p_action <> 'status' THEN
        IF match_row.status <> 'playing' OR plan_row.status IS NOT TRUE
           OR plan_row.selected_proposal_id IS NOT NULL OR plan_row.election_method IS DISTINCT FROM 'combat'
           OR plan_row.combat_game IS DISTINCT FROM 'uno' THEN
            RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'UNO duel is not active';
        END IF;
        IF p_expected_version <> match_row.version THEN
            RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'UNO duel state changed';
        END IF;
        PERFORM p.id FROM public.proposals AS p
          WHERE p.id IN (match_row.inviter_proposal_id, match_row.invitee_proposal_id)
            AND p.plan_id = target_plan_id ORDER BY p.id FOR SHARE OF p;
        GET DIAGNOSTICS member_count = ROW_COUNT;
        IF member_count <> (CASE WHEN match_row.inviter_proposal_id = match_row.invitee_proposal_id THEN 1 ELSE 2 END) THEN
            RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'UNO proposal unavailable';
        END IF;

        IF p_action = 'forfeit' THEN
            next_game := jsonb_set(match_row.game, '{winner}', to_jsonb(other_seat));
            next_game := jsonb_set(next_game, '{turn}', 'null'::jsonb);
            next_game := jsonb_set(next_game, '{events}', jsonb_build_array(
                jsonb_build_object('type', 'forfeit', 'actor', seat)), true);
            result_reason := 'forfeit';
        ELSE
            next_game := planb_private.uno_duel_action(match_row.game, seat, p_action, p_card_id, p_color);
            result_reason := 'played';
        END IF;
        next_version := match_row.version + 1;
        INSERT INTO planb_private.uno_duel_events(match_id, version, position, event)
        SELECT p_match_id, next_version, position::integer, event
          FROM jsonb_array_elements(coalesce(next_game->'events', '[]'::jsonb))
          WITH ORDINALITY AS items(event, position);
        IF next_game->>'winner' IS NOT NULL THEN
            winner := CASE WHEN next_game->>'winner' = 'player' THEN match_row.inviter_id ELSE match_row.invitee_id END;
            winning_proposal := CASE WHEN winner = match_row.inviter_id
                THEN match_row.inviter_proposal_id ELSE match_row.invitee_proposal_id END;
            INSERT INTO planb_private.uno_victories(match_id, proposal_id, player_id)
            VALUES (p_match_id, winning_proposal, winner)
            ON CONFLICT (match_id) DO NOTHING;
            IF NOT EXISTS (SELECT 1 FROM planb_private.uno_victories AS v
                WHERE v.match_id = p_match_id AND v.proposal_id = winning_proposal AND v.player_id = winner) THEN
                RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'UNO result conflict';
            END IF;
            UPDATE planb_private.uno_duel_matches SET game = next_game, version = next_version,
                status = 'finished', winner_id = winner, finish_reason = result_reason, updated_at = now()
                WHERE match_id = p_match_id RETURNING * INTO match_row;
        ELSE
            UPDATE planb_private.uno_duel_matches SET game = next_game, version = next_version, updated_at = now()
                WHERE match_id = p_match_id RETURNING * INTO match_row;
        END IF;
    END IF;
    visible := planb_private.uno_duel_visible(match_row.game, seat);
    SELECT coalesce(jsonb_agg((e.event - 'actor') || jsonb_build_object(
        'actor', CASE WHEN e.event->>'actor' = seat THEN 'player' ELSE 'opponent' END,
        'version', e.version) ORDER BY e.version, e.position), '[]'::jsonb)
      INTO visible_events FROM planb_private.uno_duel_events AS e
      WHERE e.match_id = p_match_id AND e.version > p_after_version;
    RETURN (visible - 'events') || jsonb_build_object(
        'events', visible_events, 'version', match_row.version, 'status', match_row.status,
        'proposal_id', CASE WHEN seat = 'player' THEN match_row.inviter_proposal_id ELSE match_row.invitee_proposal_id END,
        'opponent_proposal_id', CASE WHEN seat = 'player' THEN match_row.invitee_proposal_id ELSE match_row.inviter_proposal_id END,
        'opponent_id', CASE WHEN seat = 'player' THEN match_row.invitee_id ELSE match_row.inviter_id END,
        'finish_reason', match_row.finish_reason);
END;
$$;

REVOKE ALL ON FUNCTION public.uno_duel_game(uuid,text,text,text,bigint,bigint)
    FROM PUBLIC, anon, authenticated, service_role, planb_django_runtime;
COMMIT;
