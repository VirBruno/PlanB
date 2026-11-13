-- Borrador privado: no habilita RPC ni modifica partidas existentes.
-- Requiere las migraciones de UNO hasta 202611110007.
BEGIN;

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
COMMIT;
