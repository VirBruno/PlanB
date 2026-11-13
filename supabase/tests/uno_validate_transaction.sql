-- Prueba transaccional: crea objetos temporales en la transacci?n y los revierte.
-- No instala UNO ni modifica registros existentes.
BEGIN;

-- Archivo: 202611110001_uno_victory_storage.sql
-- Preparación aislada para resultados verificados de UNO.
-- Esta migración NO habilita un RPC para escribir ni modifica las propuestas.
-- La escritura se habilitará únicamente junto con una partida validada en el servidor.

CREATE TABLE planb_private.uno_victories (
    match_id uuid PRIMARY KEY,
    proposal_id bigint NOT NULL REFERENCES public.proposals(id) ON DELETE CASCADE,
    player_id uuid NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX uno_victories_proposal_idx ON planb_private.uno_victories(proposal_id);
ALTER TABLE planb_private.uno_victories ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE planb_private.uno_victories
    FROM PUBLIC, anon, authenticated, service_role, planb_django_runtime;

COMMENT ON TABLE planb_private.uno_victories IS
    'Reserva privada para victorias verificadas de UNO; sin acceso ni escritura desde clientes.';
COMMENT ON COLUMN planb_private.uno_victories.match_id IS
    'Identificador único de la partida; una victoria solo puede registrarse una vez.';


-- Archivo: 202611110002_uno_confirmation_draft.sql
-- Preparación para revisión: no concede EXECUTE a ningún rol de aplicación.
-- La futura operación que registre victorias deberá bloquear primero el grupo
-- y después el plan, en este mismo orden, antes de insertar el resultado.

CREATE FUNCTION public.confirm_uno_plan_proposal(p_plan_id uuid, p_proposal_id bigint)
RETURNS bigint LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = '' AS $$
DECLARE
    actor uuid := auth.uid();
    chosen public.proposals%ROWTYPE;
    target public.plans%ROWTYPE;
    target_group uuid;
    recipient uuid;
    top_score bigint;
    chosen_score bigint;
    tied_count bigint;
BEGIN
    IF actor IS NULL OR p_plan_id IS NULL OR p_proposal_id IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'Invalid plan or proposal';
    END IF;
    SELECT p.group_id INTO target_group FROM public.plans AS p WHERE p.id = p_plan_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'Plan is unavailable';
    END IF;
    PERFORM g.id FROM public.groups AS g WHERE g.id = target_group FOR UPDATE;
    SELECT * INTO target FROM public.plans
      WHERE id = p_plan_id AND group_id = target_group FOR UPDATE;
    IF NOT FOUND OR target.selected_proposal_id IS NOT NULL OR target.status IS NOT TRUE
       OR target.election_method IS DISTINCT FROM 'combat' THEN
        RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'Plan is unavailable or already confirmed';
    END IF;
    PERFORM m.user_id FROM public.group_members AS m
      WHERE m.group_id = target.group_id AND m.user_id = actor AND m.role = 'owner'
      FOR SHARE OF m;
    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE = '42501', MESSAGE = 'Only group owner may confirm';
    END IF;
    SELECT * INTO chosen FROM public.proposals
      WHERE id = p_proposal_id AND plan_id = p_plan_id FOR UPDATE;
    IF NOT FOUND OR chosen.date_pick IS NULL OR chosen.posicion IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'Proposal needs date and location';
    END IF;

    WITH scores AS (
        SELECT proposal.id, count(victory.match_id) AS score
        FROM public.proposals AS proposal
        LEFT JOIN planb_private.uno_victories AS victory ON victory.proposal_id = proposal.id
        WHERE proposal.plan_id = p_plan_id
        GROUP BY proposal.id
    ), maximum AS (SELECT max(score) AS score FROM scores)
    SELECT maximum.score, max(scores.score) FILTER (WHERE scores.id = p_proposal_id),
           count(*) FILTER (WHERE scores.score = maximum.score)
    INTO top_score, chosen_score, tied_count FROM scores CROSS JOIN maximum GROUP BY maximum.score;
    IF top_score IS NULL OR top_score < 1 OR chosen_score IS DISTINCT FROM top_score OR tied_count <> 1 THEN
        RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'UNO has no unique winner';
    END IF;

    UPDATE public.plans SET selected_proposal_id = chosen.id, scheduled_at = chosen.date_pick,
        confirmed_at = now(), weather_province = NULL, weather_city_id = NULL, weather_city_name = NULL
    WHERE id = p_plan_id;
    FOR recipient IN
        SELECT m.user_id FROM public.group_members AS m
        WHERE m.group_id = target.group_id ORDER BY m.user_id FOR SHARE OF m
    LOOP
        INSERT INTO public.notifications(user_id, type, plan_id, payload)
        VALUES (recipient, 'plan_confirmed', target.id,
                jsonb_build_object('plan_name', target.name, 'scheduled_at', chosen.date_pick))
        ON CONFLICT (user_id, plan_id, type) WHERE type = 'plan_confirmed' DO NOTHING;
    END LOOP;
    RETURN chosen.id;
END;
$$;

REVOKE ALL ON FUNCTION public.confirm_uno_plan_proposal(uuid, bigint)
    FROM PUBLIC, anon, authenticated, service_role, planb_django_runtime;
COMMENT ON FUNCTION public.confirm_uno_plan_proposal(uuid, bigint) IS
    'Borrador inaccesible para confirmar una propuesta líder de UNO; pendiente de pruebas y de resultados verificados.';


-- Archivo: 202611110003_uno_rules_draft.sql
-- Borrador de reglas para el motor confiable de UNO en Supabase.
-- No crea RPC pública ni habilita el registro de victorias.

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


-- Archivo: 202611110004_uno_game_draft.sql
-- Continúa las reglas privadas de UNO. No expone RPC ni registra victorias.

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


-- Archivo: 202611110005_uno_session_draft.sql
-- Borrador: requiere revisar y probar las migraciones UNO anteriores.
-- La aplicación aún no utiliza estos RPC; no aplicar al proyecto hasta completar las pruebas.

CREATE TABLE planb_private.uno_bot_matches (
    match_id uuid PRIMARY KEY,
    proposal_id bigint NOT NULL REFERENCES public.proposals(id) ON DELETE CASCADE,
    player_id uuid NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
    game jsonb NOT NULL CHECK (jsonb_typeof(game) = 'object' AND coalesce(game->>'id' = match_id::text, false)),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (proposal_id, player_id)
);
ALTER TABLE planb_private.uno_bot_matches ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE planb_private.uno_bot_matches
    FROM PUBLIC, anon, authenticated, service_role, planb_django_runtime;

-- Esta función solo devuelve la mano del jugador; el estado completo permanece privado.
CREATE FUNCTION public.uno_bot_game(
    p_plan_id uuid, p_proposal_id bigint, p_action text,
    p_card_id text DEFAULT NULL, p_color text DEFAULT NULL
) RETURNS jsonb LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = '' AS $$
DECLARE
    actor uuid := auth.uid();
    target_group_id uuid;
    target public.plans%ROWTYPE;
    current_game jsonb;
    result jsonb;
    victories bigint;
BEGIN
    IF actor IS NULL OR p_plan_id IS NULL OR p_proposal_id IS NULL
       OR p_action IS NULL OR p_action NOT IN ('status', 'start', 'play', 'draw', 'pass') THEN
        RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'Invalid UNO request';
    END IF;
    SELECT p.group_id INTO target_group_id FROM public.plans AS p WHERE p.id = p_plan_id;
    IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE = '42501', MESSAGE = 'UNO game unavailable'; END IF;
    -- Mantener el mismo orden de bloqueo que la confirmación de la propuesta.
    PERFORM g.id FROM public.groups AS g WHERE g.id = target_group_id FOR UPDATE;
    SELECT * INTO target FROM public.plans AS p WHERE p.id = p_plan_id AND p.group_id = target_group_id FOR UPDATE;
    IF NOT FOUND OR target.status IS NOT TRUE OR target.selected_proposal_id IS NOT NULL
       OR target.election_method IS DISTINCT FROM 'combat' THEN
        RAISE EXCEPTION USING ERRCODE = '42501', MESSAGE = 'UNO game unavailable';
    END IF;
    PERFORM m.user_id FROM public.group_members AS m
      WHERE m.group_id = target_group_id AND m.user_id = actor FOR SHARE OF m;
    IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE = '42501', MESSAGE = 'UNO game unavailable'; END IF;
    PERFORM proposal.id FROM public.proposals AS proposal
      WHERE proposal.id = p_proposal_id AND proposal.plan_id = p_plan_id
        AND proposal.created_by = actor FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE = '42501', MESSAGE = 'UNO game unavailable'; END IF;

    SELECT match.game INTO current_game FROM planb_private.uno_bot_matches AS match
      WHERE match.proposal_id = p_proposal_id AND match.player_id = actor FOR UPDATE;
    IF p_action = 'start' THEN
        current_game := planb_private.uno_new_game();
        INSERT INTO planb_private.uno_bot_matches(match_id, proposal_id, player_id, game)
        VALUES ((current_game->>'id')::uuid, p_proposal_id, actor, current_game)
        ON CONFLICT (proposal_id, player_id) DO UPDATE SET
            match_id = EXCLUDED.match_id, game = EXCLUDED.game, updated_at = now();
    ELSIF p_action <> 'status' THEN
        IF current_game IS NULL THEN
            RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'Start an UNO game first';
        END IF;
        current_game := planb_private.uno_action(current_game, p_action, p_card_id, p_color);
        UPDATE planb_private.uno_bot_matches SET game = current_game, updated_at = now()
          WHERE proposal_id = p_proposal_id AND player_id = actor;
        IF current_game->>'winner' = 'player' THEN
            INSERT INTO planb_private.uno_victories(match_id, proposal_id, player_id)
            VALUES ((current_game->>'id')::uuid, p_proposal_id, actor)
            ON CONFLICT (match_id) DO NOTHING;
        END IF;
    END IF;
    SELECT count(*) INTO victories FROM planb_private.uno_victories AS victory
      WHERE victory.proposal_id = p_proposal_id;
    result := CASE WHEN current_game IS NULL THEN NULL ELSE planb_private.uno_public_state(current_game) END;
    RETURN jsonb_build_object('game', result, 'victories', victories);
END;
$$;

CREATE FUNCTION public.uno_proposal_scores(p_plan_id uuid)
RETURNS TABLE(proposal_id bigint, victories bigint)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = '' AS $$
BEGIN
    IF auth.uid() IS NULL OR p_plan_id IS NULL OR NOT EXISTS (
        SELECT 1 FROM public.plans AS plan
        JOIN public.group_members AS member ON member.group_id = plan.group_id
        WHERE plan.id = p_plan_id AND member.user_id = auth.uid()
    ) THEN
        RAISE EXCEPTION USING ERRCODE = '42501', MESSAGE = 'UNO results unavailable';
    END IF;
    RETURN QUERY SELECT proposal.id, count(victory.match_id)
      FROM public.proposals AS proposal
      LEFT JOIN planb_private.uno_victories AS victory ON victory.proposal_id = proposal.id
      WHERE proposal.plan_id = p_plan_id GROUP BY proposal.id;
END;
$$;

REVOKE ALL ON FUNCTION public.uno_bot_game(uuid,bigint,text,text,text), public.uno_proposal_scores(uuid)
    FROM PUBLIC, anon, authenticated, service_role, planb_django_runtime;
-- Los permisos de ejecución se agregarán solo después de probar la integración.


-- Archivo: uno_rules_draft.sql
-- Ejecutar como administrador en una base preparada con las migraciones UNO.
-- Todo el contenido se revierte; no modifica datos existentes.

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



-- Archivo: uno_game_draft.sql
-- Ejecutar como administrador con las migraciones UNO de reglas y juego.
-- No inserta ni modifica datos de la aplicación.

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



DO $$
BEGIN
    IF has_table_privilege('authenticated', 'planb_private.uno_victories', 'INSERT')
       OR has_table_privilege('authenticated', 'planb_private.uno_bot_matches', 'SELECT')
       OR has_function_privilege('authenticated', 'public.uno_bot_game(uuid,bigint,text,text,text)', 'EXECUTE') THEN
        RAISE EXCEPTION 'Los borradores no deben ser accesibles para clientes';
    END IF;
END;
$$;
ROLLBACK;
SELECT 'OK: reglas y partidas de UNO verificadas; cambios revertidos.' AS resultado;
