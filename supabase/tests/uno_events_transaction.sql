-- Prueba de eventos: modifica funciones temporalmente y revierte los cambios.
BEGIN;
-- Jugadas visibles, sin revelar cartas ocultas.

CREATE OR REPLACE FUNCTION planb_private.uno_play(
    p_state jsonb, p_actor text, p_card_id text, p_color text
) RETURNS jsonb LANGUAGE plpgsql VOLATILE SET search_path = '' AS $$
DECLARE
    hand jsonb;
    played jsonb;
    remaining jsonb;
    other_player text;
    next_color text;
    card_value text;
    draw_result jsonb;
    next_state jsonb := p_state;
    notices jsonb := coalesce(p_state->'announcements', '[]'::jsonb);
    events jsonb := coalesce(p_state->'events', '[]'::jsonb);
BEGIN
    IF p_actor NOT IN ('player', 'bot') OR p_state->>'turn' IS DISTINCT FROM p_actor
       OR p_state->>'winner' IS NOT NULL THEN
        RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'Invalid UNO turn';
    END IF;
    hand := p_state->p_actor;
    SELECT card INTO played FROM jsonb_array_elements(hand) AS card WHERE card->>'id' = p_card_id;
    IF played IS NULL OR (p_actor = 'player' AND p_state->>'drawn' IS NOT NULL
       AND p_state->>'drawn' IS DISTINCT FROM p_card_id)
       OR NOT planb_private.uno_can_play(played, hand, p_state->'discard'->-1, p_state->>'color') THEN
        RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'Invalid UNO card';
    END IF;
    card_value := played->>'value';
    events := events || jsonb_build_array(jsonb_build_object(
        'type', 'play', 'actor', p_actor, 'value', card_value, 'color', played->>'color'));
    IF card_value IN ('comodin', 'mas_cuatro') AND (p_color IS NULL OR p_color NOT IN ('rojo', 'amarillo', 'verde', 'azul')) THEN
        RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'A wild card requires a valid color';
    END IF;
    next_color := CASE WHEN card_value IN ('comodin', 'mas_cuatro') THEN p_color ELSE played->>'color' END;
    SELECT coalesce(jsonb_agg(card ORDER BY position), '[]'::jsonb) INTO remaining
      FROM jsonb_array_elements(hand) WITH ORDINALITY AS cards(card, position)
      WHERE card->>'id' <> p_card_id;
    other_player := CASE WHEN p_actor = 'player' THEN 'bot' ELSE 'player' END;
    next_state := jsonb_set(next_state, ARRAY[p_actor], remaining);
    next_state := jsonb_set(next_state, '{discard}', (next_state->'discard') || jsonb_build_array(played));
    next_state := jsonb_set(next_state, '{color}', to_jsonb(next_color));
    next_state := jsonb_set(next_state, '{drawn}', 'null'::jsonb);
    IF card_value IN ('mas_dos', 'mas_cuatro') THEN
        draw_result := planb_private.uno_draw(next_state, CASE WHEN card_value = 'mas_dos' THEN 2 ELSE 4 END);
        next_state := jsonb_set(next_state, '{deck}', draw_result->'deck');
        next_state := jsonb_set(next_state, '{discard}', draw_result->'discard');
        next_state := jsonb_set(next_state, ARRAY[other_player], (next_state->other_player) || (draw_result->'drawn'));
        events := events || jsonb_build_array(jsonb_build_object(
            'type', 'draw', 'actor', other_player, 'count', jsonb_array_length(draw_result->'drawn'),
            'reason', card_value));
    END IF;
    IF jsonb_array_length(remaining) = 0 THEN
        next_state := jsonb_set(next_state, '{winner}', to_jsonb(p_actor));
        next_state := jsonb_set(next_state, '{turn}', 'null'::jsonb);
        next_state := jsonb_set(next_state, '{message}', to_jsonb(
            CASE WHEN p_actor = 'player' THEN '¡Ganaste la partida!' ELSE 'El bot ganó la partida.' END));
    ELSE
        next_state := jsonb_set(next_state, '{turn}', to_jsonb(
            CASE WHEN card_value IN ('salto', 'reversa', 'mas_dos', 'mas_cuatro') THEN p_actor ELSE other_player END));
        next_state := jsonb_set(next_state, '{message}', to_jsonb(
            (CASE WHEN p_actor = 'player' THEN 'Jugaste ' ELSE 'El bot jugó ' END) || replace(card_value, '_', ' ') || '.'));
        IF jsonb_array_length(remaining) = 1 THEN
            notices := notices || jsonb_build_array(CASE WHEN p_actor = 'player'
                THEN '¡UNO! Te queda una carta.' ELSE '¡UNO! Al bot le queda una carta.' END);
            events := events || jsonb_build_array(jsonb_build_object('type', 'uno', 'actor', p_actor));
        END IF;
    END IF;
    next_state := jsonb_set(next_state, '{announcements}', notices);
    RETURN jsonb_set(next_state, '{events}', events);
END;
$$;

CREATE OR REPLACE FUNCTION planb_private.uno_public_state(p_state jsonb)
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
        'announcements', coalesce(p_state->'announcements', '[]'::jsonb),
        'events', coalesce(p_state->'events', '[]'::jsonb))
$$;

CREATE OR REPLACE FUNCTION planb_private.uno_bot_turns(p_state jsonb)
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
            IF jsonb_array_length(drawn->'drawn') > 0 THEN
                s := jsonb_set(s, '{events}', coalesce(s->'events', '[]'::jsonb) ||
                    jsonb_build_array(jsonb_build_object('type', 'draw', 'actor', 'bot', 'count', 1, 'reason', 'deck')));
            END IF;
            candidate := drawn->'drawn'->0;
            IF candidate IS NULL OR NOT planb_private.uno_can_play(candidate, s->'bot', s->'discard'->-1, s->>'color') THEN
                s := jsonb_set(s, '{turn}', '"player"'::jsonb);
                s := jsonb_set(s, '{message}', to_jsonb(
                    CASE WHEN candidate IS NULL THEN 'No hay cartas para robar. Es tu turno.'::text
                         ELSE 'El bot robó una carta. Es tu turno.'::text END));
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

CREATE OR REPLACE FUNCTION planb_private.uno_action(p_state jsonb, p_action text, p_card_id text, p_color text)
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
    s := jsonb_set(s, '{events}', '[]'::jsonb);
    IF p_action = 'play' THEN
        s := planb_private.uno_play(s, 'player', p_card_id, p_color);
    ELSIF p_action = 'draw' AND s->>'drawn' IS NULL THEN
        drawn := planb_private.uno_draw(s, 1);
        s := jsonb_set(s, '{deck}', drawn->'deck');
        s := jsonb_set(s, '{discard}', drawn->'discard');
        s := jsonb_set(s, '{player}', (s->'player') || (drawn->'drawn'));
        IF jsonb_array_length(drawn->'drawn') > 0 THEN
            s := jsonb_set(s, '{events}', (s->'events') ||
                jsonb_build_array(jsonb_build_object('type', 'draw', 'actor', 'player', 'count', 1, 'reason', 'deck')));
        END IF;
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

-- Validar después de cargar el borrador de eventos dentro de la transacción.
DO $$
DECLARE
    state jsonb;
    result jsonb;
    visible jsonb;
BEGIN
    state := jsonb_build_object(
        'id', gen_random_uuid()::text,
        'player', '[{"id":"a","color":"rojo","value":"7"},{"id":"p1","color":"verde","value":"1"},{"id":"p2","color":"azul","value":"1"}]'::jsonb,
        'bot', '[{"id":"b1","color":"rojo","value":"mas_dos"},{"id":"b2","color":"rojo","value":"5"},{"id":"b3","color":"azul","value":"2"}]'::jsonb,
        'deck', '[{"id":"d1","color":"verde","value":"2"},{"id":"d2","color":"verde","value":"3"}]'::jsonb,
        'discard', '[{"id":"t","color":"rojo","value":"3"}]'::jsonb,
        'color', 'rojo', 'turn', 'player', 'drawn', NULL, 'winner', NULL,
        'message', '', 'announcements', '[]'::jsonb);
    result := planb_private.uno_action(state, 'play', 'a', NULL);
    visible := planb_private.uno_public_state(result);
    IF jsonb_array_length(result->'player') <> 4 OR NOT EXISTS (
        SELECT 1 FROM jsonb_array_elements(visible->'events') AS event
        WHERE event->>'type' = 'draw' AND event->>'actor' = 'player'
          AND event->>'reason' = 'mas_dos' AND (event->>'count')::integer = 2
    ) OR NOT EXISTS (
        SELECT 1 FROM jsonb_array_elements(visible->'events') AS event
        WHERE event->>'type' = 'play' AND event->>'actor' = 'bot' AND event->>'value' = '5'
    ) THEN
        RAISE EXCEPTION 'La penalización o la jugada siguiente del bot no se informaron';
    END IF;
    IF visible ? 'deck' OR visible ? 'bot' OR EXISTS (
        SELECT 1 FROM jsonb_array_elements(visible->'events') AS event
        WHERE event ? 'id' OR event ? 'hand' OR event ? 'card'
    ) THEN
        RAISE EXCEPTION 'Los eventos revelan información privada';
    END IF;
    state := jsonb_set(state, '{bot}', '[{"id":"b1","color":"rojo","value":"5"},{"id":"b2","color":"azul","value":"2"}]'::jsonb);
    result := planb_private.uno_action(state, 'play', 'a', NULL);
    IF NOT EXISTS (SELECT 1 FROM jsonb_array_elements(result->'events') AS event
                   WHERE event->>'type' = 'uno' AND event->>'actor' = 'bot') THEN
        RAISE EXCEPTION 'No se informó que al bot le queda una carta';
    END IF;
END;
$$;

ROLLBACK;
SELECT 'OK: avisos de UNO y penalizaciones verificados; cambios revertidos.' AS resultado;
