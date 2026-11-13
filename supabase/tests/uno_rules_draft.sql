-- Ejecutar como administrador en una base preparada con las migraciones UNO.
-- Todo el contenido se revierte; no modifica datos existentes.
BEGIN;
DO $$
DECLARE
    rojo jsonb := '{"id":"r","color":"rojo","value":"7"}';
    azul jsonb := '{"id":"b","color":"azul","value":"7"}';
    cuatro jsonb := '{"id":"w","color":null,"value":"mas_cuatro"}';
    salto jsonb := '{"id":"s","color":"rojo","value":"salto"}';
    top jsonb := '{"id":"t","color":"rojo","value":"3"}';
    hand jsonb;
    state jsonb;
    result jsonb;
BEGIN
    hand := jsonb_build_array(rojo, cuatro);
    IF NOT planb_private.uno_can_play(azul, jsonb_build_array(azul), rojo, 'rojo') THEN
        RAISE EXCEPTION 'No se aceptó una carta del mismo valor';
    END IF;
    IF planb_private.uno_can_play(cuatro, hand, top, 'rojo') THEN
        RAISE EXCEPTION 'Se permitió +4 teniendo una carta del color actual';
    END IF;
    IF NOT planb_private.uno_can_play(cuatro, jsonb_build_array(cuatro), top, 'rojo') THEN
        RAISE EXCEPTION 'No se permitió +4 sin carta del color actual';
    END IF;
    state := jsonb_build_object('player', jsonb_build_array(salto, azul), 'bot', jsonb_build_array(rojo),
        'deck', '[]'::jsonb, 'discard', jsonb_build_array(top), 'color', 'rojo',
        'drawn', NULL, 'winner', NULL, 'turn', 'player', 'announcements', '[]'::jsonb);
    result := planb_private.uno_play(state, 'player', 's', NULL);
    IF result->>'turn' <> 'player' OR jsonb_array_length(result->'player') <> 1
       OR result->'announcements'->>0 <> '¡UNO! Te queda una carta.' THEN
        RAISE EXCEPTION 'Salto o aviso de UNO incorrecto';
    END IF;
    BEGIN
        PERFORM planb_private.uno_play(state, 'player', 'no-existe', NULL);
        RAISE EXCEPTION 'Se aceptó una carta inexistente';
    EXCEPTION WHEN invalid_parameter_value THEN NULL;
    END;
    state := jsonb_set(state, '{player}', jsonb_build_array(cuatro));
    BEGIN
        PERFORM planb_private.uno_play(state, 'player', 'w', NULL);
        RAISE EXCEPTION 'Se aceptó un comodín sin color';
    EXCEPTION WHEN invalid_parameter_value THEN NULL;
    END;
    state := jsonb_set(state, '{deck}', jsonb_build_array(
        rojo, azul, '{"id":"d3","color":"verde","value":"1"}'::jsonb,
        '{"id":"d4","color":"amarillo","value":"2"}'::jsonb));
    result := planb_private.uno_play(state, 'player', 'w', 'azul');
    IF result->>'winner' <> 'player' OR result->>'color' <> 'azul'
       OR jsonb_array_length(result->'bot') <> 5 THEN
        RAISE EXCEPTION 'No se resolvió correctamente la victoria con +4';
    END IF;
    state := jsonb_set(state, '{deck}', '[]'::jsonb);
    state := jsonb_set(state, '{discard}', jsonb_build_array(rojo, azul, top));
    result := planb_private.uno_draw(state, 2);
    IF jsonb_array_length(result->'drawn') <> 2 OR result->'discard'->0 <> top THEN
        RAISE EXCEPTION 'No se reciclaron los descartes conservando la carta superior';
    END IF;
END;
$$;
ROLLBACK;
SELECT 'OK: reglas básicas de UNO; datos revertidos.' AS resultado;
