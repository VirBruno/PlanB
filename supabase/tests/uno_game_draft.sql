-- Ejecutar como administrador con las migraciones UNO de reglas y juego.
-- No inserta ni modifica datos de la aplicación.
BEGIN;
DO $$
DECLARE
    game jsonb;
    visible jsonb;
    played jsonb;
    total integer;
    unique_cards integer;
    seed integer;
    step integer;
BEGIN
    FOR seed IN 1..20 LOOP
        game := planb_private.uno_new_game();
        IF jsonb_array_length(game->'player') <> 7 OR jsonb_array_length(game->'bot') <> 7
           OR (game->'discard'->0->>'value') !~ '^[0-9]$' THEN
            RAISE EXCEPTION 'El reparto inicial no es válido';
        END IF;
        FOR step IN 1..1000 LOOP
            visible := planb_private.uno_public_state(game);
            IF visible ? 'deck' OR visible ? 'bot' OR visible->>'bot_count' IS NULL THEN
                RAISE EXCEPTION 'Se expuso información privada';
            END IF;
            SELECT count(*), count(DISTINCT card->>'id') INTO total, unique_cards
            FROM jsonb_array_elements((game->'deck') || (game->'discard') || (game->'player') || (game->'bot')) AS card;
            IF total <> 108 OR unique_cards <> 108 THEN
                RAISE EXCEPTION 'La partida perdió o duplicó cartas';
            END IF;
            EXIT WHEN game->>'winner' IS NOT NULL;
            SELECT card INTO played FROM jsonb_array_elements(visible->'hand') AS card
              WHERE (card->>'playable')::boolean LIMIT 1;
            IF played IS NOT NULL THEN
                game := planb_private.uno_action(game, 'play', played->>'id', 'rojo');
            ELSIF (visible->>'drawn')::boolean THEN
                game := planb_private.uno_action(game, 'pass', NULL, NULL);
            ELSE
                game := planb_private.uno_action(game, 'draw', NULL, NULL);
            END IF;
        END LOOP;
        IF game->>'winner' IS NULL THEN
            RAISE EXCEPTION 'La partida % no terminó', seed;
        END IF;
    END LOOP;
END;
$$;
ROLLBACK;
SELECT 'OK: partidas completas, privacidad y conservación del mazo.' AS resultado;
