-- Ejecutar en una base de pruebas que tenga los borradores de UNO aplicados.
-- No modifica datos existentes.
BEGIN;
DO $$
DECLARE
    rojo jsonb := '{"id":"r","color":"rojo","value":"7"}';
    azul jsonb := '{"id":"b","color":"azul","value":"7"}';
    salto jsonb := '{"id":"s","color":"rojo","value":"salto"}';
    top jsonb := '{"id":"t","color":"rojo","value":"3"}';
    state jsonb;
    result jsonb;
    visible jsonb;
    rejected boolean;
BEGIN
    IF has_table_privilege('authenticated', 'planb_private.uno_duel_matches', 'SELECT')
       OR has_function_privilege('authenticated',
           'planb_private.uno_duel_action(jsonb,text,text,text,text)', 'EXECUTE')
       OR has_function_privilege('authenticated',
           'planb_private.uno_duel_visible(jsonb,text)', 'EXECUTE') THEN
        RAISE EXCEPTION 'Los objetos privados no deben ser accesibles directamente';
    END IF;
    state := jsonb_build_object('id', 'duel', 'player', jsonb_build_array(rojo, azul),
        'bot', jsonb_build_array(salto, azul),
        'deck', jsonb_build_array('{"id":"d","color":"rojo","value":"1"}'::jsonb),
        'discard', jsonb_build_array(top), 'color', 'rojo', 'drawn', NULL,
        'winner', NULL, 'turn', 'player', 'announcements', '[]'::jsonb,
        'events', '[]'::jsonb);

    visible := planb_private.uno_duel_visible(state, 'player');
    IF visible ? 'deck' OR visible ? 'bot' OR visible ? 'player'
       OR visible->'hand' <> jsonb_build_array(
           rojo || '{"playable":true}'::jsonb, azul || '{"playable":false}'::jsonb)
       OR (visible->>'opponent_count')::integer <> 2 THEN
        RAISE EXCEPTION 'Vista privada incorrecta para el primer asiento';
    END IF;
    visible := planb_private.uno_duel_visible(state, 'bot');
    IF visible ? 'deck' OR visible ? 'player' OR visible->>'turn' <> 'opponent'
       OR visible->'hand'->0->>'id' <> 's' THEN
        RAISE EXCEPTION 'Vista privada incorrecta para el segundo asiento';
    END IF;

    result := planb_private.uno_duel_action(state, 'player', 'play', 'r', NULL);
    IF result->>'turn' <> 'bot' OR jsonb_array_length(result->'bot') <> 2 THEN
        RAISE EXCEPTION 'La jugada debe ceder el turno sin ejecutar un bot';
    END IF;
    visible := planb_private.uno_duel_visible(result, 'bot');
    IF visible->'events'->0->>'actor' <> 'opponent'
       OR visible->'events'->0->>'value' <> '7' THEN
        RAISE EXCEPTION 'Evento visible incorrecto para el rival';
    END IF;

    rejected := false;
    BEGIN
        PERFORM planb_private.uno_duel_action(result, 'player', 'play', 'b', NULL);
    EXCEPTION WHEN invalid_parameter_value THEN rejected := true;
    END;
    IF NOT rejected THEN RAISE EXCEPTION 'Se aceptó una jugada fuera de turno'; END IF;

    -- El segundo asiento también debe jugar únicamente la carta recién robada.
    result := jsonb_set(state, '{turn}', '"bot"'::jsonb);
    result := jsonb_set(result, '{drawn}', '"s"'::jsonb);
    rejected := false;
    BEGIN
        PERFORM planb_private.uno_duel_action(result, 'bot', 'play', 'b', NULL);
    EXCEPTION WHEN invalid_parameter_value THEN rejected := true;
    END;
    IF NOT rejected THEN RAISE EXCEPTION 'Se permitió jugar otra carta después de robar'; END IF;
    result := planb_private.uno_duel_action(result, 'bot', 'play', 's', NULL);
    IF result->>'turn' <> 'bot' OR jsonb_array_length(result->'bot') <> 1 THEN
        RAISE EXCEPTION 'El salto del segundo asiento no conserva el turno';
    END IF;

    result := jsonb_set(state, '{turn}', '"bot"'::jsonb);
    result := planb_private.uno_duel_action(result, 'bot', 'draw', NULL, NULL);
    IF result->>'drawn' <> 'd' OR result->>'turn' <> 'bot' THEN
        RAISE EXCEPTION 'Robo jugable incorrecto para el segundo asiento';
    END IF;
    visible := planb_private.uno_duel_visible(result, 'player');
    IF (visible->>'drawn')::boolean OR visible::text LIKE '%"id": "d"%' THEN
        RAISE EXCEPTION 'El rival no debe ver la carta robada';
    END IF;
    result := planb_private.uno_duel_action(result, 'bot', 'pass', NULL, NULL);
    IF result->>'turn' <> 'player' OR result->>'drawn' IS NOT NULL THEN
        RAISE EXCEPTION 'Pasar después de robar no cedió el turno';
    END IF;

    -- Una última carta +4 debe penalizar al rival y cerrar el juego una sola vez.
    result := jsonb_set(state, '{turn}', '"bot"'::jsonb);
    result := jsonb_set(result, '{bot}', '[{"id":"w","color":null,"value":"mas_cuatro"}]'::jsonb);
    result := jsonb_set(result, '{deck}', '[
        {"id":"d1","color":"rojo","value":"1"},
        {"id":"d2","color":"verde","value":"2"},
        {"id":"d3","color":"azul","value":"3"},
        {"id":"d4","color":"amarillo","value":"4"}
    ]'::jsonb);
    result := planb_private.uno_duel_action(result, 'bot', 'play', 'w', 'azul');
    IF result->>'winner' <> 'bot' OR result->>'turn' IS NOT NULL
       OR jsonb_array_length(result->'player') <> 6 THEN
        RAISE EXCEPTION 'La última carta +4 no resolvió la victoria y penalización';
    END IF;
    visible := planb_private.uno_duel_visible(result, 'player');
    IF visible->>'winner' <> 'opponent' OR visible->'events'->1->>'reason' <> 'mas_cuatro' THEN
        RAISE EXCEPTION 'El resultado o la penalización no se muestran al rival';
    END IF;
    rejected := false;
    BEGIN
        PERFORM planb_private.uno_duel_action(result, 'bot', 'draw', NULL, NULL);
    EXCEPTION WHEN invalid_parameter_value THEN rejected := true;
    END;
    IF NOT rejected THEN RAISE EXCEPTION 'Se aceptó una acción después de terminar'; END IF;
END;
$$;
ROLLBACK;
SELECT 'OK: turnos y privacidad de UNO entre integrantes; cambios revertidos.' AS resultado;
