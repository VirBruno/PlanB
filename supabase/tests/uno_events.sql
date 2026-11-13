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
