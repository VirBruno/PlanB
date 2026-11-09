-- Puntajes separados para los juegos disponibles en combate.
BEGIN;

ALTER TABLE public.proposals
    ADD COLUMN IF NOT EXISTS truco_score bigint NOT NULL DEFAULT 0;

ALTER TABLE public.proposals
    DROP CONSTRAINT IF EXISTS proposals_truco_score_valid;
ALTER TABLE public.proposals
    ADD CONSTRAINT proposals_truco_score_valid
    CHECK (truco_score BETWEEN 0 AND 30);

CREATE OR REPLACE FUNCTION public.save_combat_game_score(
    p_plan_id uuid, p_proposal_id bigint, p_score bigint, p_game text
)
RETURNS bigint
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = '' AS $$
DECLARE
    actor uuid := auth.uid();
    saved_score bigint;
BEGIN
    IF actor IS NULL OR p_score IS NULL OR p_game IS NULL
       OR p_game NOT IN ('arcade', 'truco')
       OR p_score < 0 OR (p_game = 'truco' AND p_score > 30)
       OR (p_game = 'arcade' AND p_score > 99999) THEN
        RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'Invalid score or game';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM public.plans AS plan
        JOIN public.group_members AS membership ON membership.group_id = plan.group_id
        WHERE plan.id = p_plan_id AND membership.user_id = actor
          AND plan.election_method = 'combat'
    ) THEN
        RAISE EXCEPTION USING ERRCODE = '42501', MESSAGE = 'Combat mode membership required';
    END IF;
    IF p_game = 'truco' THEN
        UPDATE public.proposals AS proposal
            SET truco_score = greatest(proposal.truco_score, p_score)
            WHERE proposal.id = p_proposal_id AND proposal.plan_id = p_plan_id
              AND proposal.created_by = actor
            RETURNING proposal.truco_score INTO saved_score;
    ELSE
        UPDATE public.proposals AS proposal
            SET "Score" = greatest(proposal."Score", p_score)
            WHERE proposal.id = p_proposal_id AND proposal.plan_id = p_plan_id
              AND proposal.created_by = actor
            RETURNING proposal."Score" INTO saved_score;
    END IF;
    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'Own proposal required';
    END IF;
    RETURN saved_score;
END;
$$;

CREATE OR REPLACE FUNCTION public.confirm_truco_plan_proposal(
    p_plan_id uuid, p_proposal_id bigint
)
RETURNS bigint LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = '' AS $$
DECLARE
    actor uuid := auth.uid();
    chosen public.proposals%ROWTYPE;
    target public.plans%ROWTYPE;
    target_group uuid;
    recipient uuid;
    top_score bigint;
    tied_count bigint;
BEGIN
    IF actor IS NULL OR p_plan_id IS NULL OR p_proposal_id IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'Invalid plan or proposal';
    END IF;
    SELECT p.group_id INTO target_group FROM public.plans AS p WHERE p.id = p_plan_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'Plan is unavailable or already confirmed';
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
    SELECT MAX(truco_score) INTO top_score
      FROM public.proposals WHERE plan_id = p_plan_id;
    SELECT count(*) INTO tied_count FROM public.proposals
      WHERE plan_id = p_plan_id AND truco_score = top_score;
    IF top_score IS DISTINCT FROM 30 OR chosen.truco_score <> top_score
       OR tied_count <> 1 THEN
        RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'Truco has no unique winner';
    END IF;
    UPDATE public.plans SET
        selected_proposal_id = chosen.id,
        scheduled_at = chosen.date_pick,
        confirmed_at = now(),
        weather_province = NULL, weather_city_id = NULL, weather_city_name = NULL
    WHERE id = p_plan_id;
    FOR recipient IN
        SELECT m.user_id FROM public.group_members AS m
        WHERE m.group_id = target.group_id ORDER BY m.user_id FOR SHARE OF m
    LOOP
        INSERT INTO public.notifications(user_id, type, plan_id, payload)
        VALUES (
            recipient, 'plan_confirmed', target.id,
            jsonb_build_object('plan_name', target.name, 'scheduled_at', chosen.date_pick)
        )
        ON CONFLICT (user_id, plan_id, type)
            WHERE type = 'plan_confirmed' DO NOTHING;
    END LOOP;
    RETURN chosen.id;
END;
$$;

REVOKE ALL ON FUNCTION public.save_combat_game_score(uuid, bigint, bigint, text)
    FROM PUBLIC, anon, authenticated, service_role, planb_django_runtime;
REVOKE ALL ON FUNCTION public.confirm_truco_plan_proposal(uuid, bigint)
    FROM PUBLIC, anon, authenticated, service_role, planb_django_runtime;
GRANT EXECUTE ON FUNCTION public.save_combat_game_score(uuid, bigint, bigint, text)
    TO authenticated;
GRANT EXECUTE ON FUNCTION public.confirm_truco_plan_proposal(uuid, bigint)
    TO authenticated;

COMMENT ON FUNCTION public.save_combat_game_score(uuid, bigint, bigint, text) IS
    'Guarda el mejor puntaje separado por juego en la propuesta del integrante autenticado.';
COMMENT ON FUNCTION public.confirm_truco_plan_proposal(uuid, bigint) IS
    'Confirma la propuesta campeona de Truco y notifica a los miembros actuales en la misma transacción.';
NOTIFY pgrst, 'reload schema';
COMMIT;
