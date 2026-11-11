-- Borrador de reglas para el motor confiable de UNO en Supabase.
-- No crea RPC pública ni habilita el registro de victorias.
BEGIN;

CREATE FUNCTION planb_private.uno_can_play(
    p_card jsonb, p_hand jsonb, p_top jsonb, p_color text
) RETURNS boolean LANGUAGE sql IMMUTABLE SET search_path = '' AS $$
    SELECT CASE
        WHEN p_card->>'value' = 'mas_cuatro' THEN NOT EXISTS (
            SELECT 1 FROM jsonb_array_elements(p_hand) AS item
            WHERE item->>'color' = p_color
        )
        ELSE p_card->>'value' = 'comodin'
          OR p_card->>'color' = p_color
          OR p_card->>'value' = p_top->>'value'
    END
$$;

CREATE FUNCTION planb_private.uno_draw(p_state jsonb, p_count integer)
RETURNS jsonb LANGUAGE plpgsql VOLATILE SET search_path = '' AS $$
DECLARE
    deck jsonb := p_state->'deck';
    discard jsonb := p_state->'discard';
    drawn jsonb := '[]'::jsonb;
    top jsonb;
    i integer;
BEGIN
    IF p_count < 0 OR p_count > 4 THEN
        RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'Invalid draw count';
    END IF;
    FOR i IN 1..p_count LOOP
        IF jsonb_array_length(deck) = 0 THEN
            IF jsonb_array_length(discard) <= 1 THEN EXIT; END IF;
            top := discard->(jsonb_array_length(discard) - 1);
            SELECT coalesce(jsonb_agg(card ORDER BY random()), '[]'::jsonb)
              INTO deck FROM jsonb_array_elements(discard - (jsonb_array_length(discard) - 1)) AS card;
            discard := jsonb_build_array(top);
        END IF;
        drawn := drawn || jsonb_build_array(deck->(jsonb_array_length(deck) - 1));
        deck := deck - (jsonb_array_length(deck) - 1);
    END LOOP;
    RETURN jsonb_build_object('deck', deck, 'discard', discard, 'drawn', drawn);
END;
$$;

CREATE FUNCTION planb_private.uno_play(
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
        END IF;
    END IF;
    RETURN jsonb_set(next_state, '{announcements}', notices);
END;
$$;

REVOKE ALL ON FUNCTION planb_private.uno_can_play(jsonb,jsonb,jsonb,text),
    planb_private.uno_draw(jsonb,integer), planb_private.uno_play(jsonb,text,text,text)
    FROM PUBLIC, anon, authenticated, service_role, planb_django_runtime;
COMMIT;
