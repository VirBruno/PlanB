-- Prepared installation. Execute only after the integration tests pass.
BEGIN;
-- Borrador privado: no habilita RPC ni modifica partidas existentes.
-- Requiere las migraciones de UNO hasta 202611110007.

-- La tabla es privada y no concede acceso directo. Las operaciones públicas
-- deberán validar membresía, propuestas y estado del plan en una transacción.
CREATE TABLE planb_private.uno_duel_matches (
    match_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    plan_id uuid NOT NULL REFERENCES public.plans(id) ON DELETE CASCADE,
    inviter_id uuid NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
    invitee_id uuid NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
    inviter_proposal_id bigint NOT NULL REFERENCES public.proposals(id) ON DELETE CASCADE,
    invitee_proposal_id bigint REFERENCES public.proposals(id) ON DELETE CASCADE,
    status text NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'playing', 'rejected', 'cancelled', 'finished')),
    game jsonb,
    winner_id uuid REFERENCES public.profiles(id) ON DELETE CASCADE,
    finish_reason text CHECK (finish_reason IN ('played', 'forfeit')),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (inviter_id <> invitee_id),
    CHECK (updated_at >= created_at),
    CHECK (
        (status IN ('pending', 'rejected', 'cancelled') AND game IS NULL
            AND invitee_proposal_id IS NULL AND winner_id IS NULL AND finish_reason IS NULL)
        OR (status = 'playing' AND invitee_proposal_id IS NOT NULL AND game IS NOT NULL
            AND jsonb_typeof(game) = 'object' AND winner_id IS NULL AND finish_reason IS NULL)
        OR (status = 'finished' AND invitee_proposal_id IS NOT NULL AND game IS NOT NULL
            AND jsonb_typeof(game) = 'object' AND winner_id IS NOT NULL
            AND winner_id IN (inviter_id, invitee_id) AND finish_reason IS NOT NULL)
    ),
    CHECK (game IS NULL OR coalesce(game->>'id' = match_id::text, false))
);
ALTER TABLE planb_private.uno_duel_matches ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE planb_private.uno_duel_matches
    FROM PUBLIC, anon, authenticated, service_role, planb_django_runtime;

-- El reparto existente usa los nombres internos player y bot para los dos
-- asientos. Aquí ambos asientos son humanos; nunca se ejecuta el bot.
CREATE FUNCTION planb_private.uno_duel_action(
    p_state jsonb, p_seat text, p_action text,
    p_card_id text DEFAULT NULL, p_color text DEFAULT NULL
) RETURNS jsonb LANGUAGE plpgsql VOLATILE SET search_path = '' AS $$
DECLARE
    s jsonb := p_state;
    other_seat text;
    drawn jsonb;
    card jsonb;
BEGIN
    IF p_seat IS NULL OR p_seat NOT IN ('player', 'bot')
       OR p_action IS NULL OR p_action NOT IN ('play', 'draw', 'pass')
       OR s->>'turn' IS DISTINCT FROM p_seat OR s->>'winner' IS NOT NULL THEN
        RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'Invalid UNO duel turn';
    END IF;
    other_seat := CASE WHEN p_seat = 'player' THEN 'bot' ELSE 'player' END;
    s := jsonb_set(s, '{announcements}', '[]'::jsonb, true);
    s := jsonb_set(s, '{events}', '[]'::jsonb, true);

    IF p_action = 'play' THEN
        -- La función compartida solo aplica esta restricción al asiento player;
        -- en un duelo debe valer para ambos asientos.
        IF s->>'drawn' IS NOT NULL AND s->>'drawn' IS DISTINCT FROM p_card_id THEN
            RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'Play the drawn card or pass';
        END IF;
        s := planb_private.uno_play(s, p_seat, p_card_id, p_color);
    ELSIF p_action = 'draw' AND s->>'drawn' IS NULL THEN
        drawn := planb_private.uno_draw(s, 1);
        s := jsonb_set(s, '{deck}', drawn->'deck');
        s := jsonb_set(s, '{discard}', drawn->'discard');
        s := jsonb_set(s, ARRAY[p_seat], (s->p_seat) || (drawn->'drawn'));
        card := drawn->'drawn'->0;
        IF card IS NOT NULL THEN
            s := jsonb_set(s, '{events}', jsonb_build_array(jsonb_build_object(
                'type', 'draw', 'actor', p_seat, 'count', 1, 'reason', 'deck')));
        END IF;
        IF card IS NOT NULL AND planb_private.uno_can_play(card, s->p_seat, s->'discard'->-1, s->>'color') THEN
            s := jsonb_set(s, '{drawn}', card->'id');
        ELSE
            s := jsonb_set(s, '{turn}', to_jsonb(other_seat));
        END IF;
    ELSIF p_action = 'pass' AND s->>'drawn' IS NOT NULL THEN
        s := jsonb_set(s, '{drawn}', 'null'::jsonb);
        s := jsonb_set(s, '{turn}', to_jsonb(other_seat));
    ELSE
        RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'Invalid UNO duel action';
    END IF;
    RETURN s;
END;
$$;

-- La función llamadora debe autenticar a la persona y determinar su asiento.
-- Esta vista solo construye datos públicos para el asiento indicado.
CREATE FUNCTION planb_private.uno_duel_visible(p_state jsonb, p_seat text)
RETURNS jsonb LANGUAGE plpgsql STABLE SET search_path = '' AS $$
DECLARE
    other_seat text;
    visible_hand jsonb;
    visible_events jsonb;
BEGIN
    IF p_seat IS NULL OR p_seat NOT IN ('player', 'bot') THEN
        RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'Invalid UNO duel seat';
    END IF;
    other_seat := CASE WHEN p_seat = 'player' THEN 'bot' ELSE 'player' END;
    SELECT coalesce(jsonb_agg(card || jsonb_build_object('playable',
        p_state->>'turn' = p_seat AND p_state->>'winner' IS NULL
        AND (p_state->>'drawn' IS NULL OR p_state->>'drawn' = card->>'id')
        AND planb_private.uno_can_play(card, p_state->p_seat, p_state->'discard'->-1, p_state->>'color')
    ) ORDER BY position), '[]'::jsonb) INTO visible_hand
    FROM jsonb_array_elements(p_state->p_seat) WITH ORDINALITY AS items(card, position);

    SELECT coalesce(jsonb_agg((event - 'actor') || jsonb_build_object('actor',
        CASE WHEN event->>'actor' = p_seat THEN 'player' ELSE 'opponent' END
    ) ORDER BY position), '[]'::jsonb) INTO visible_events
    FROM jsonb_array_elements(coalesce(p_state->'events', '[]'::jsonb))
        WITH ORDINALITY AS items(event, position);

    RETURN jsonb_build_object(
        'id', p_state->>'id', 'hand', visible_hand,
        'opponent_count', jsonb_array_length(p_state->other_seat),
        'top', p_state->'discard'->-1, 'color', p_state->>'color',
        'turn', CASE WHEN p_state->>'turn' = p_seat THEN 'player'
                     WHEN p_state->>'turn' = other_seat THEN 'opponent' ELSE NULL END,
        'drawn', coalesce(p_state->>'turn' = p_seat AND p_state->>'drawn' IS NOT NULL, false),
        'winner', CASE WHEN p_state->>'winner' = p_seat THEN 'player'
                       WHEN p_state->>'winner' = other_seat THEN 'opponent' ELSE NULL END,
        'events', visible_events);
END;
$$;

REVOKE ALL ON FUNCTION planb_private.uno_duel_action(jsonb,text,text,text,text),
    planb_private.uno_duel_visible(jsonb,text)
    FROM PUBLIC, anon, authenticated, service_role, planb_django_runtime;

-- Borrador: requiere uno_multijugador_reglas.sql. Sin permisos de ejecución.

-- La invitación también pertenece a un plan. Se conservan las restricciones
-- de los tipos existentes y los demás tipos no pueden indicar plan_id.
ALTER TABLE public.notifications DROP CONSTRAINT notification_plan_required;
ALTER TABLE public.notifications ADD CONSTRAINT notification_plan_required CHECK (
    (type IN ('plan_reminder', 'plan_confirmed', 'expense_debt_reminder', 'uno_duel_invitation') AND plan_id IS NOT NULL)
    OR (type NOT IN ('plan_reminder', 'plan_confirmed', 'expense_debt_reminder', 'uno_duel_invitation') AND plan_id IS NULL)
);

-- Una pareja solo puede tener una invitación pendiente o partida activa por plan.
CREATE UNIQUE INDEX uno_duel_one_active_pair
ON planb_private.uno_duel_matches (
    plan_id, least(inviter_id, invitee_id), greatest(inviter_id, invitee_id)
) WHERE status IN ('pending', 'playing');

CREATE FUNCTION public.uno_duel_invite(
    p_plan_id uuid, p_proposal_id bigint, p_invitee_id uuid
) RETURNS uuid LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = '' AS $$
DECLARE
    actor uuid := auth.uid();
    target_group_id uuid;
    plan_row public.plans%ROWTYPE;
    member_count integer;
    match_id uuid;
BEGIN
    IF actor IS NULL OR p_plan_id IS NULL OR p_proposal_id IS NULL
       OR p_invitee_id IS NULL OR actor = p_invitee_id THEN
        RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'Invalid UNO invitation';
    END IF;
    SELECT p.group_id INTO target_group_id FROM public.plans AS p WHERE p.id = p_plan_id;
    IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE = '42501', MESSAGE = 'UNO invitation unavailable'; END IF;
    -- Coincide con el orden de bloqueo usado al confirmar un plan.
    PERFORM g.id FROM public.groups AS g WHERE g.id = target_group_id FOR UPDATE;
    SELECT * INTO plan_row FROM public.plans AS p
      WHERE p.id = p_plan_id AND p.group_id = target_group_id FOR UPDATE;
    IF NOT FOUND OR plan_row.status IS NOT TRUE OR plan_row.selected_proposal_id IS NOT NULL
       OR plan_row.election_method IS DISTINCT FROM 'combat' THEN
        RAISE EXCEPTION USING ERRCODE = '42501', MESSAGE = 'UNO invitation unavailable';
    END IF;
    PERFORM m.user_id FROM public.group_members AS m
      WHERE m.group_id = target_group_id AND m.user_id IN (actor, p_invitee_id)
      ORDER BY m.user_id FOR SHARE OF m;
    GET DIAGNOSTICS member_count = ROW_COUNT;
    IF member_count <> 2 THEN
        RAISE EXCEPTION USING ERRCODE = '42501', MESSAGE = 'UNO invitation unavailable';
    END IF;
    PERFORM p.id FROM public.proposals AS p
      WHERE p.id = p_proposal_id AND p.plan_id = p_plan_id FOR SHARE OF p;
    IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'Invalid UNO proposal'; END IF;
    INSERT INTO planb_private.uno_duel_matches(plan_id, inviter_id, invitee_id, inviter_proposal_id)
    VALUES (p_plan_id, actor, p_invitee_id, p_proposal_id) RETURNING uno_duel_matches.match_id INTO match_id;
    INSERT INTO public.notifications(user_id, type, plan_id, payload)
    VALUES (p_invitee_id, 'uno_duel_invitation', p_plan_id,
            jsonb_build_object('match_id', match_id, 'plan_name', plan_row.name));
    RETURN match_id;
END;
$$;

CREATE FUNCTION public.uno_duel_respond(
    p_match_id uuid, p_accept boolean, p_proposal_id bigint DEFAULT NULL
) RETURNS uuid LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = '' AS $$
DECLARE
    actor uuid := auth.uid();
    target_plan_id uuid;
    target_group_id uuid;
    plan_row public.plans%ROWTYPE;
    match_row planb_private.uno_duel_matches%ROWTYPE;
    member_count integer;
    initial_game jsonb;
BEGIN
    IF actor IS NULL OR p_match_id IS NULL OR p_accept IS NULL
       OR (p_accept AND p_proposal_id IS NULL) OR (NOT p_accept AND p_proposal_id IS NOT NULL) THEN
        RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'Invalid UNO response';
    END IF;
    SELECT m.plan_id INTO target_plan_id FROM planb_private.uno_duel_matches AS m
      WHERE m.match_id = p_match_id AND m.invitee_id = actor;
    IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE = '42501', MESSAGE = 'UNO invitation unavailable'; END IF;
    SELECT p.group_id INTO target_group_id FROM public.plans AS p WHERE p.id = target_plan_id;
    IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE = '42501', MESSAGE = 'UNO invitation unavailable'; END IF;
    PERFORM g.id FROM public.groups AS g WHERE g.id = target_group_id FOR UPDATE;
    SELECT * INTO plan_row FROM public.plans AS p
      WHERE p.id = target_plan_id AND p.group_id = target_group_id FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE = '42501', MESSAGE = 'UNO invitation unavailable'; END IF;
    SELECT * INTO match_row FROM planb_private.uno_duel_matches AS m
      WHERE m.match_id = p_match_id AND m.invitee_id = actor FOR UPDATE;
    IF NOT FOUND OR match_row.status <> 'pending' THEN
        RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'UNO invitation already answered';
    END IF;
    PERFORM m.user_id FROM public.group_members AS m
      WHERE m.group_id = target_group_id AND m.user_id IN (match_row.inviter_id, actor)
      ORDER BY m.user_id FOR SHARE OF m;
    GET DIAGNOSTICS member_count = ROW_COUNT;
    IF member_count <> 2 THEN
        RAISE EXCEPTION USING ERRCODE = '42501', MESSAGE = 'UNO invitation unavailable';
    END IF;
    IF p_accept THEN
        IF plan_row.status IS NOT TRUE OR plan_row.selected_proposal_id IS NOT NULL
           OR plan_row.election_method IS DISTINCT FROM 'combat' THEN
            RAISE EXCEPTION USING ERRCODE = '42501', MESSAGE = 'UNO invitation unavailable';
        END IF;
        PERFORM p.id FROM public.proposals AS p
          WHERE p.id IN (p_proposal_id, match_row.inviter_proposal_id) AND p.plan_id = target_plan_id
          ORDER BY p.id FOR SHARE OF p;
        -- Las dos personas pueden representar la misma propuesta.
        GET DIAGNOSTICS member_count = ROW_COUNT;
        IF member_count <> (CASE WHEN p_proposal_id = match_row.inviter_proposal_id THEN 1 ELSE 2 END) THEN
            RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'Invalid UNO proposal';
        END IF;
        initial_game := jsonb_set(planb_private.uno_new_game(), '{id}', to_jsonb(p_match_id::text));
        UPDATE planb_private.uno_duel_matches SET status = 'playing', game = initial_game,
            invitee_proposal_id = p_proposal_id, updated_at = now() WHERE match_id = p_match_id;
    ELSE
        UPDATE planb_private.uno_duel_matches SET status = 'rejected', updated_at = now()
          WHERE match_id = p_match_id;
    END IF;
    UPDATE public.notifications SET read_at = coalesce(read_at, now())
      WHERE user_id = actor AND type = 'uno_duel_invitation' AND plan_id = target_plan_id
        AND payload->>'match_id' = p_match_id::text;
    RETURN p_match_id;
END;
$$;

REVOKE ALL ON FUNCTION public.uno_duel_invite(uuid,bigint,uuid),
    public.uno_duel_respond(uuid,boolean,bigint)
    FROM PUBLIC, anon, authenticated, service_role, planb_django_runtime;

-- Borrador: requiere las reglas y las invitaciones de UNO multijugador.
-- No concede permisos a los roles de la aplicación.

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
           OR plan_row.selected_proposal_id IS NOT NULL OR plan_row.election_method IS DISTINCT FROM 'combat' THEN
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

-- Borrador: requiere los borradores anteriores. No habilita acceso todavía.

CREATE FUNCTION public.uno_duel_list(p_plan_id uuid)
RETURNS jsonb LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = '' AS $$
DECLARE
    actor uuid := auth.uid();
    result jsonb;
BEGIN
    IF actor IS NULL OR p_plan_id IS NULL OR NOT EXISTS (
        SELECT 1 FROM public.plans AS p JOIN public.group_members AS m ON m.group_id = p.group_id
        WHERE p.id = p_plan_id AND m.user_id = actor
    ) THEN
        RAISE EXCEPTION USING ERRCODE = '42501', MESSAGE = 'UNO duels unavailable';
    END IF;
    SELECT coalesce(jsonb_agg(item ORDER BY updated_at DESC, match_id DESC), '[]'::jsonb) INTO result
    FROM (
        SELECT m.match_id, m.updated_at, jsonb_build_object(
            'id', m.match_id,
            'status', m.status,
            'role', CASE WHEN actor = m.inviter_id THEN 'inviter' ELSE 'invitee' END,
            'opponent_id', CASE WHEN actor = m.inviter_id THEN m.invitee_id ELSE m.inviter_id END,
            'opponent_username', coalesce(opponent.username, 'Integrante'),
            'proposal_id', CASE WHEN actor = m.inviter_id THEN m.inviter_proposal_id ELSE m.invitee_proposal_id END,
            'opponent_proposal_id', CASE WHEN actor = m.inviter_id THEN m.invitee_proposal_id ELSE m.inviter_proposal_id END,
            'winner', CASE WHEN m.winner_id = actor THEN 'player'
                           WHEN m.winner_id IS NOT NULL THEN 'opponent' ELSE NULL END,
            'finish_reason', m.finish_reason,
            'version', m.version
        ) AS item
        FROM planb_private.uno_duel_matches AS m
        LEFT JOIN public.profiles AS opponent ON opponent.id =
            CASE WHEN actor = m.inviter_id THEN m.invitee_id ELSE m.inviter_id END
        WHERE m.plan_id = p_plan_id AND actor IN (m.inviter_id, m.invitee_id)
        ORDER BY m.updated_at DESC, m.match_id DESC LIMIT 50
    ) AS matches;
    RETURN result;
END;
$$;
REVOKE ALL ON FUNCTION public.uno_duel_list(uuid)
    FROM PUBLIC, anon, authenticated, service_role, planb_django_runtime;

COMMIT;
SELECT 'OK: UNO multiplayer installed; access remains disabled.' AS resultado;
