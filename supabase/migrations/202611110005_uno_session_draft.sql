-- Borrador: requiere revisar y probar las migraciones UNO anteriores.
-- La aplicación aún no utiliza estos RPC; no aplicar al proyecto hasta completar las pruebas.
BEGIN;

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
       OR target.election_method IS DISTINCT FROM 'combat'
       OR target.combat_game IS DISTINCT FROM 'uno' THEN
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
        WHERE plan.id = p_plan_id AND plan.election_method = 'combat'
          AND plan.combat_game = 'uno' AND member.user_id = auth.uid()
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
COMMIT;
