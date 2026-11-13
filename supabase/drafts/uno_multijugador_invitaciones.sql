-- Borrador: requiere uno_multijugador_reglas.sql. Sin permisos de ejecución.
BEGIN;

-- La invitación también pertenece a un plan. Se conservan las restricciones
-- de los tipos existentes y los demás tipos no pueden indicar plan_id.
ALTER TABLE public.notifications DROP CONSTRAINT notification_plan_required;
ALTER TABLE public.notifications ADD CONSTRAINT notification_plan_required CHECK (
    (type IN ('plan_reminder', 'plan_confirmed', 'expense_debt_reminder', 'uno_duel_invitation') AND plan_id IS NOT NULL)
    OR (type NOT IN ('plan_reminder', 'plan_confirmed', 'expense_debt_reminder', 'uno_duel_invitation') AND plan_id IS NULL)
);

-- Una pareja solo puede tener una invitación pendiente o partida activa por plan.
CREATE UNIQUE INDEX uno_duel_one_active_pair
ON planb_private.uno_duel_matches (
    plan_id, least(inviter_id, invitee_id), greatest(inviter_id, invitee_id)
) WHERE status IN ('pending', 'playing');

CREATE FUNCTION public.uno_duel_invite(
    p_plan_id uuid, p_proposal_id bigint, p_invitee_id uuid
) RETURNS uuid LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = '' AS $$
DECLARE
    actor uuid := auth.uid();
    target_group_id uuid;
    plan_row public.plans%ROWTYPE;
    member_count integer;
    match_id uuid;
BEGIN
    IF actor IS NULL OR p_plan_id IS NULL OR p_proposal_id IS NULL
       OR p_invitee_id IS NULL OR actor = p_invitee_id THEN
        RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'Invalid UNO invitation';
    END IF;
    SELECT p.group_id INTO target_group_id FROM public.plans AS p WHERE p.id = p_plan_id;
    IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE = '42501', MESSAGE = 'UNO invitation unavailable'; END IF;
    -- Coincide con el orden de bloqueo usado al confirmar un plan.
    PERFORM g.id FROM public.groups AS g WHERE g.id = target_group_id FOR UPDATE;
    SELECT * INTO plan_row FROM public.plans AS p
      WHERE p.id = p_plan_id AND p.group_id = target_group_id FOR UPDATE;
    IF NOT FOUND OR plan_row.status IS NOT TRUE OR plan_row.selected_proposal_id IS NOT NULL
       OR plan_row.election_method IS DISTINCT FROM 'combat' THEN
        RAISE EXCEPTION USING ERRCODE = '42501', MESSAGE = 'UNO invitation unavailable';
    END IF;
    PERFORM m.user_id FROM public.group_members AS m
      WHERE m.group_id = target_group_id AND m.user_id IN (actor, p_invitee_id)
      ORDER BY m.user_id FOR SHARE OF m;
    GET DIAGNOSTICS member_count = ROW_COUNT;
    IF member_count <> 2 THEN
        RAISE EXCEPTION USING ERRCODE = '42501', MESSAGE = 'UNO invitation unavailable';
    END IF;
    PERFORM p.id FROM public.proposals AS p
      WHERE p.id = p_proposal_id AND p.plan_id = p_plan_id FOR SHARE OF p;
    IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'Invalid UNO proposal'; END IF;
    INSERT INTO planb_private.uno_duel_matches(plan_id, inviter_id, invitee_id, inviter_proposal_id)
    VALUES (p_plan_id, actor, p_invitee_id, p_proposal_id) RETURNING uno_duel_matches.match_id INTO match_id;
    INSERT INTO public.notifications(user_id, type, plan_id, payload)
    VALUES (p_invitee_id, 'uno_duel_invitation', p_plan_id,
            jsonb_build_object('match_id', match_id, 'plan_name', plan_row.name));
    RETURN match_id;
END;
$$;

CREATE FUNCTION public.uno_duel_respond(
    p_match_id uuid, p_accept boolean, p_proposal_id bigint DEFAULT NULL
) RETURNS uuid LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = '' AS $$
DECLARE
    actor uuid := auth.uid();
    target_plan_id uuid;
    target_group_id uuid;
    plan_row public.plans%ROWTYPE;
    match_row planb_private.uno_duel_matches%ROWTYPE;
    member_count integer;
    initial_game jsonb;
BEGIN
    IF actor IS NULL OR p_match_id IS NULL OR p_accept IS NULL
       OR (p_accept AND p_proposal_id IS NULL) OR (NOT p_accept AND p_proposal_id IS NOT NULL) THEN
        RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'Invalid UNO response';
    END IF;
    SELECT m.plan_id INTO target_plan_id FROM planb_private.uno_duel_matches AS m
      WHERE m.match_id = p_match_id AND m.invitee_id = actor;
    IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE = '42501', MESSAGE = 'UNO invitation unavailable'; END IF;
    SELECT p.group_id INTO target_group_id FROM public.plans AS p WHERE p.id = target_plan_id;
    IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE = '42501', MESSAGE = 'UNO invitation unavailable'; END IF;
    PERFORM g.id FROM public.groups AS g WHERE g.id = target_group_id FOR UPDATE;
    SELECT * INTO plan_row FROM public.plans AS p
      WHERE p.id = target_plan_id AND p.group_id = target_group_id FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE = '42501', MESSAGE = 'UNO invitation unavailable'; END IF;
    SELECT * INTO match_row FROM planb_private.uno_duel_matches AS m
      WHERE m.match_id = p_match_id AND m.invitee_id = actor FOR UPDATE;
    IF NOT FOUND OR match_row.status <> 'pending' THEN
        RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'UNO invitation already answered';
    END IF;
    PERFORM m.user_id FROM public.group_members AS m
      WHERE m.group_id = target_group_id AND m.user_id IN (match_row.inviter_id, actor)
      ORDER BY m.user_id FOR SHARE OF m;
    GET DIAGNOSTICS member_count = ROW_COUNT;
    IF member_count <> 2 THEN
        RAISE EXCEPTION USING ERRCODE = '42501', MESSAGE = 'UNO invitation unavailable';
    END IF;
    IF p_accept THEN
        IF plan_row.status IS NOT TRUE OR plan_row.selected_proposal_id IS NOT NULL
           OR plan_row.election_method IS DISTINCT FROM 'combat' THEN
            RAISE EXCEPTION USING ERRCODE = '42501', MESSAGE = 'UNO invitation unavailable';
        END IF;
        PERFORM p.id FROM public.proposals AS p
          WHERE p.id IN (p_proposal_id, match_row.inviter_proposal_id) AND p.plan_id = target_plan_id
          ORDER BY p.id FOR SHARE OF p;
        -- Las dos personas pueden representar la misma propuesta.
        GET DIAGNOSTICS member_count = ROW_COUNT;
        IF member_count <> (CASE WHEN p_proposal_id = match_row.inviter_proposal_id THEN 1 ELSE 2 END) THEN
            RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'Invalid UNO proposal';
        END IF;
        initial_game := jsonb_set(planb_private.uno_new_game(), '{id}', to_jsonb(p_match_id::text));
        UPDATE planb_private.uno_duel_matches SET status = 'playing', game = initial_game,
            invitee_proposal_id = p_proposal_id, updated_at = now() WHERE match_id = p_match_id;
    ELSE
        UPDATE planb_private.uno_duel_matches SET status = 'rejected', updated_at = now()
          WHERE match_id = p_match_id;
    END IF;
    UPDATE public.notifications SET read_at = coalesce(read_at, now())
      WHERE user_id = actor AND type = 'uno_duel_invitation' AND plan_id = target_plan_id
        AND payload->>'match_id' = p_match_id::text;
    RETURN p_match_id;
END;
$$;

REVOKE ALL ON FUNCTION public.uno_duel_invite(uuid,bigint,uuid),
    public.uno_duel_respond(uuid,boolean,bigint)
    FROM PUBLIC, anon, authenticated, service_role, planb_django_runtime;
COMMIT;
