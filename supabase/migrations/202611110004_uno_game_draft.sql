-- Continúa las reglas privadas de UNO. No expone RPC ni registra victorias.
BEGIN;

CREATE FUNCTION planb_private.uno_new_game()
RETURNS jsonb LANGUAGE plpgsql VOLATILE SET search_path = '' AS $$
DECLARE
    cards jsonb;
    player jsonb;
    bot jsonb;
    deck jsonb;
    first_card jsonb;
    first_position integer;
BEGIN
    WITH colored AS (
        SELECT color, value, CASE WHEN value = '0' THEN 1 ELSE 2 END AS copies
        FROM unnest(ARRAY['rojo','amarillo','verde','azul']) AS color
        CROSS JOIN unnest(ARRAY['0','1','2','3','4','5','6','7','8','9','salto','reversa','mas_dos']) AS value
    ), all_cards AS (
        SELECT jsonb_build_object('id', gen_random_uuid()::text, 'color', color, 'value', value) AS card
        FROM colored CROSS JOIN LATERAL generate_series(1, copies)
        UNION ALL
        SELECT jsonb_build_object('id', gen_random_uuid()::text, 'color', NULL, 'value', value)
        FROM unnest(ARRAY['comodin','mas_cuatro']) AS value CROSS JOIN generate_series(1, 4)
    )
    SELECT jsonb_agg(card ORDER BY random()) INTO cards FROM all_cards;
    SELECT jsonb_agg(card ORDER BY position) FILTER (WHERE position <= 7),
           jsonb_agg(card ORDER BY position) FILTER (WHERE position BETWEEN 8 AND 14),
           jsonb_agg(card ORDER BY position) FILTER (WHERE position > 14)
      INTO player, bot, deck FROM jsonb_array_elements(cards) WITH ORDINALITY AS items(card, position);
    SELECT (position - 1)::integer INTO first_position
      FROM jsonb_array_elements(deck) WITH ORDINALITY AS items(card, position)
      WHERE card->>'value' ~ '^[0-9]$' ORDER BY position LIMIT 1;
    first_card := deck->first_position;
    deck := deck - first_position;
    RETURN jsonb_build_object('id', gen_random_uuid()::text, 'deck', deck,
        'discard', jsonb_build_array(first_card), 'player', player, 'bot', bot,
        'color', first_card->>'color', 'turn', 'player', 'drawn', NULL, 'winner', NULL,
        'announcements', '[]'::jsonb, 'message', 'Elegí una carta o robá del mazo.');
END;
$$;

CREATE FUNCTION planb_private.uno_public_state(p_state jsonb)
RETURNS jsonb LANGUAGE sql IMMUTABLE SET search_path = '' AS $$
    SELECT jsonb_build_object(
        'id', p_state->>'id',
        'hand', coalesce((SELECT jsonb_agg(card || jsonb_build_object('playable',
            p_state->>'turn' = 'player' AND p_state->>'winner' IS NULL
            AND (p_state->>'drawn' IS NULL OR p_state->>'drawn' = card->>'id')
            AND planb_private.uno_can_play(card, p_state->'player', p_state->'discard'->-1, p_state->>'color')
        ) ORDER BY position) FROM jsonb_array_elements(p_state->'player') WITH ORDINALITY AS items(card, position)), '[]'::jsonb),
        'top', p_state->'discard'->-1, 'color', p_state->>'color',
        'bot_count', jsonb_array_length(p_state->'bot'), 'drawn', p_state->>'drawn' IS NOT NULL,
        'turn', p_state->>'turn', 'winner', p_state->>'winner', 'message', p_state->>'message',
        'announcements', coalesce(p_state->'announcements', '[]'::jsonb))
$$;

CREATE FUNCTION planb_private.uno_bot_turns(p_state jsonb)
RETURNS jsonb LANGUAGE plpgsql VOLATILE SET search_path = '' AS $$
DECLARE
    s jsonb := p_state;
    candidate jsonb;
    drawn jsonb;
    chosen_color text;
BEGIN
    WHILE s->>'turn' = 'bot' AND s->>'winner' IS NULL LOOP
        SELECT card INTO candidate FROM jsonb_array_elements(s->'bot') WITH ORDINALITY AS items(card, position)
        WHERE planb_private.uno_can_play(card, s->'bot', s->'discard'->-1, s->>'color')
        ORDER BY (card->>'value' IN ('comodin','mas_cuatro')), position LIMIT 1;
        IF candidate IS NULL THEN
            drawn := planb_private.uno_draw(s, 1);
            s := jsonb_set(s, '{deck}', drawn->'deck');
            s := jsonb_set(s, '{discard}', drawn->'discard');
            s := jsonb_set(s, '{bot}', (s->'bot') || (drawn->'drawn'));
            candidate := drawn->'drawn'->0;
            IF candidate IS NULL OR NOT planb_private.uno_can_play(candidate, s->'bot', s->'discard'->-1, s->>'color') THEN
                s := jsonb_set(s, '{turn}', '"player"'::jsonb);
                s := jsonb_set(s, '{message}', to_jsonb('El bot robó una carta. Es tu turno.'::text));
                EXIT;
            END IF;
        END IF;
        SELECT colors.color INTO chosen_color
        FROM unnest(ARRAY['rojo','amarillo','verde','azul']) WITH ORDINALITY AS colors(color, position)
        ORDER BY (SELECT count(*) FROM jsonb_array_elements(s->'bot') AS card WHERE card->>'color' = colors.color) DESC,
                 colors.position LIMIT 1;
        s := planb_private.uno_play(s, 'bot', candidate->>'id', chosen_color);
    END LOOP;
    RETURN s;
END;
$$;

CREATE FUNCTION planb_private.uno_action(p_state jsonb, p_action text, p_card_id text, p_color text)
RETURNS jsonb LANGUAGE plpgsql VOLATILE SET search_path = '' AS $$
DECLARE
    s jsonb := p_state;
    drawn jsonb;
    card jsonb;
BEGIN
    IF s->>'turn' IS DISTINCT FROM 'player' OR s->>'winner' IS NOT NULL THEN
        RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'No UNO turn available';
    END IF;
    s := jsonb_set(s, '{announcements}', '[]'::jsonb);
    IF p_action = 'play' THEN
        s := planb_private.uno_play(s, 'player', p_card_id, p_color);
    ELSIF p_action = 'draw' AND s->>'drawn' IS NULL THEN
        drawn := planb_private.uno_draw(s, 1);
        s := jsonb_set(s, '{deck}', drawn->'deck');
        s := jsonb_set(s, '{discard}', drawn->'discard');
        s := jsonb_set(s, '{player}', (s->'player') || (drawn->'drawn'));
        card := drawn->'drawn'->0;
        IF card IS NOT NULL AND planb_private.uno_can_play(card, s->'player', s->'discard'->-1, s->>'color') THEN
            s := jsonb_set(s, '{drawn}', card->'id');
            s := jsonb_set(s, '{message}', to_jsonb('Podés jugar la carta que robaste o pasar.'::text));
        ELSE
            s := jsonb_set(s, '{turn}', '"bot"'::jsonb);
            s := jsonb_set(s, '{message}', to_jsonb('Robaste una carta.'::text));
        END IF;
    ELSIF p_action = 'pass' AND s->>'drawn' IS NOT NULL THEN
        s := jsonb_set(s, '{drawn}', 'null'::jsonb);
        s := jsonb_set(s, '{turn}', '"bot"'::jsonb);
    ELSE
        RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'Invalid UNO action';
    END IF;
    RETURN planb_private.uno_bot_turns(s);
END;
$$;

REVOKE ALL ON FUNCTION planb_private.uno_new_game(), planb_private.uno_public_state(jsonb),
    planb_private.uno_bot_turns(jsonb), planb_private.uno_action(jsonb,text,text,text)
    FROM PUBLIC, anon, authenticated, service_role, planb_django_runtime;
COMMIT;
