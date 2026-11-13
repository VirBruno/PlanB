-- Guarda en el plan el juego de combate compartido por todo el grupo.
BEGIN;

ALTER TABLE public.plans
    ADD COLUMN IF NOT EXISTS combat_game text NOT NULL DEFAULT 'arcade';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'public.plans'::regclass
          AND conname = 'plans_combat_game_valid'
    ) THEN
        ALTER TABLE public.plans ADD CONSTRAINT plans_combat_game_valid
            CHECK (combat_game IN ('arcade', 'truco', 'uno'));
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION public.set_plan_combat_game(p_plan_id uuid, p_game text)
RETURNS text
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = '' AS $$
DECLARE
    actor uuid := auth.uid();
    target public.plans%ROWTYPE;
BEGIN
    IF actor IS NULL OR p_plan_id IS NULL OR p_game IS NULL
       OR p_game NOT IN ('arcade', 'truco', 'uno') THEN
        RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'Invalid combat game';
    END IF;
    SELECT * INTO target FROM public.plans WHERE id = p_plan_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'Plan not found';
    END IF;
    PERFORM membership.user_id FROM public.group_members AS membership
    WHERE membership.group_id = target.group_id
      AND membership.user_id = actor AND membership.role = 'owner'
    FOR SHARE OF membership;
    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE = '42501', MESSAGE = 'Group owner required';
    END IF;
    IF target.election_method IS DISTINCT FROM 'combat'
       OR target.selected_proposal_id IS NOT NULL THEN
        RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'Combat election is unavailable';
    END IF;
    UPDATE public.plans SET combat_game = p_game WHERE id = p_plan_id;
    RETURN p_game;
END;
$$;

CREATE OR REPLACE FUNCTION public.save_proposal_score(
    p_plan_id uuid, p_proposal_id bigint, p_score bigint
)
RETURNS bigint
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = '' AS $$
DECLARE
    actor uuid := auth.uid();
    active_game text;
    saved_score bigint;
BEGIN
    IF actor IS NULL OR p_score IS NULL OR p_score < 0 OR p_score > 99999 THEN
        RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'Invalid score';
    END IF;
    SELECT plan.combat_game INTO active_game
    FROM public.plans AS plan
    WHERE plan.id = p_plan_id AND plan.election_method = 'combat'
      AND plan.selected_proposal_id IS NULL
    FOR SHARE OF plan;
    IF NOT FOUND OR active_game IS DISTINCT FROM 'arcade' THEN
        RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'Arcade is not selected for this plan';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM public.plans AS plan
        JOIN public.group_members AS membership ON membership.group_id = plan.group_id
        WHERE plan.id = p_plan_id AND membership.user_id = actor
    ) THEN
        RAISE EXCEPTION USING ERRCODE = '42501', MESSAGE = 'Combat mode membership required';
    END IF;
    UPDATE public.proposals AS proposal
        SET "Score" = greatest(proposal."Score", p_score)
        WHERE proposal.id = p_proposal_id AND proposal.plan_id = p_plan_id
          AND proposal.created_by = actor
        RETURNING proposal."Score" INTO saved_score;
    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'Own proposal required';
    END IF;
    RETURN saved_score;
END;
$$;

CREATE OR REPLACE FUNCTION public.save_combat_game_score(
    p_plan_id uuid, p_proposal_id bigint, p_score bigint, p_game text
)
RETURNS bigint
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = '' AS $$
DECLARE
    actor uuid := auth.uid();
    active_game text;
    saved_score bigint;
BEGIN
    IF actor IS NULL OR p_score IS NULL OR p_game IS NULL
       OR p_game NOT IN ('arcade', 'truco')
       OR p_score < 0 OR (p_game = 'truco' AND p_score > 30)
       OR (p_game = 'arcade' AND p_score > 99999) THEN
        RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'Invalid score or game';
    END IF;
    SELECT plan.combat_game INTO active_game
    FROM public.plans AS plan
    WHERE plan.id = p_plan_id AND plan.election_method = 'combat'
      AND plan.selected_proposal_id IS NULL
    FOR SHARE OF plan;
    IF NOT FOUND OR active_game IS DISTINCT FROM p_game THEN
        RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'Game is not selected for this plan';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM public.plans AS plan
        JOIN public.group_members AS membership ON membership.group_id = plan.group_id
        WHERE plan.id = p_plan_id AND membership.user_id = actor
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

REVOKE ALL ON FUNCTION public.set_plan_combat_game(uuid, text)
    FROM PUBLIC, anon, authenticated, service_role, planb_django_runtime;
GRANT EXECUTE ON FUNCTION public.set_plan_combat_game(uuid, text) TO authenticated;

REVOKE ALL ON FUNCTION public.save_proposal_score(uuid, bigint, bigint)
    FROM PUBLIC, anon, authenticated, service_role, planb_django_runtime;
GRANT EXECUTE ON FUNCTION public.save_proposal_score(uuid, bigint, bigint) TO authenticated;

REVOKE ALL ON FUNCTION public.save_combat_game_score(uuid, bigint, bigint, text)
    FROM PUBLIC, anon, authenticated, service_role, planb_django_runtime;
GRANT EXECUTE ON FUNCTION public.save_combat_game_score(uuid, bigint, bigint, text)
    TO authenticated;

COMMENT ON FUNCTION public.set_plan_combat_game(uuid, text) IS
    'Cambia el juego de combate del plan únicamente por su owner; el valor se comparte con todo el grupo.';
COMMENT ON FUNCTION public.save_proposal_score(uuid, bigint, bigint) IS
    'Guarda el mejor puntaje solo cuando Arcade es el juego de combate activo en el plan.';
COMMENT ON FUNCTION public.save_combat_game_score(uuid, bigint, bigint, text) IS
    'Guarda el mejor puntaje solo para el juego de combate activo en el plan.';

NOTIFY pgrst, 'reload schema';
COMMIT;
