-- Aplicar después de 202610090001_proposal_budget_range.sql.
-- Las métricas sólo se modifican mediante RPCs autenticados y validados.
BEGIN;

ALTER TABLE public.plans
    ADD COLUMN IF NOT EXISTS election_method text;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'public.plans'::regclass
          AND conname = 'plans_election_method_valid'
    ) THEN
        ALTER TABLE public.plans ADD CONSTRAINT plans_election_method_valid
            CHECK (election_method IS NULL OR election_method IN ('ideal', 'votes', 'combat'));
    END IF;
END;
$$;

ALTER TABLE public.proposals ADD COLUMN IF NOT EXISTS votes bigint;
ALTER TABLE public.proposals ADD COLUMN IF NOT EXISTS "Score" bigint;
UPDATE public.proposals
SET votes = coalesce(votes, 0), "Score" = coalesce("Score", 0);
ALTER TABLE public.proposals ALTER COLUMN votes SET DEFAULT 0;
ALTER TABLE public.proposals ALTER COLUMN votes SET NOT NULL;
ALTER TABLE public.proposals ALTER COLUMN "Score" SET DEFAULT 0;
ALTER TABLE public.proposals ALTER COLUMN "Score" SET NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'public.proposals'::regclass
          AND conname = 'proposals_votes_nonnegative'
    ) THEN
        ALTER TABLE public.proposals ADD CONSTRAINT proposals_votes_nonnegative CHECK (votes >= 0);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'public.proposals'::regclass
          AND conname = 'proposals_score_nonnegative'
    ) THEN
        ALTER TABLE public.proposals ADD CONSTRAINT proposals_score_nonnegative CHECK ("Score" >= 0);
    END IF;
END;
$$;

CREATE TABLE public.proposal_votes (
    plan_id uuid NOT NULL REFERENCES public.plans(id) ON DELETE CASCADE,
    proposal_id bigint NOT NULL REFERENCES public.proposals(id) ON DELETE CASCADE,
    voter_id uuid NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (plan_id, voter_id),
    UNIQUE (proposal_id, voter_id)
);
CREATE INDEX proposal_votes_proposal_idx ON public.proposal_votes(proposal_id);
ALTER TABLE public.proposal_votes ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.proposal_votes
    FROM PUBLIC, anon, authenticated, service_role, planb_django_runtime;

CREATE OR REPLACE FUNCTION public.set_proposal_election_method(p_plan_id uuid, p_method text)
RETURNS text
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = '' AS $$
DECLARE
    actor uuid := auth.uid();
    target_group uuid;
    current_method text;
BEGIN
    IF actor IS NULL OR p_method IS NULL OR p_method NOT IN ('ideal', 'votes', 'combat') THEN
        RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'Invalid election method';
    END IF;
    SELECT plan.group_id, plan.election_method
        INTO target_group, current_method
        FROM public.plans AS plan WHERE plan.id = p_plan_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'Plan not found';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM public.group_members AS membership
        WHERE membership.group_id = target_group
          AND membership.user_id = actor AND membership.role = 'owner'
    ) THEN
        RAISE EXCEPTION USING ERRCODE = '42501', MESSAGE = 'Group owner required';
    END IF;
    IF current_method IS NOT NULL AND current_method IS DISTINCT FROM p_method AND EXISTS (
        SELECT 1 FROM public.proposals AS proposal
        WHERE proposal.plan_id = p_plan_id AND (proposal.votes > 0 OR proposal."Score" > 0)
    ) THEN
        RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'Election already has results';
    END IF;
    UPDATE public.plans SET election_method = p_method WHERE id = p_plan_id;
    RETURN p_method;
END;
$$;

CREATE OR REPLACE FUNCTION public.proposal_vote_for_user(p_plan_id uuid)
RETURNS bigint
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = '' AS $$
DECLARE
    actor uuid := auth.uid();
    selected_proposal bigint;
BEGIN
    IF actor IS NULL OR NOT EXISTS (
        SELECT 1 FROM public.plans AS plan
        JOIN public.group_members AS membership ON membership.group_id = plan.group_id
        WHERE plan.id = p_plan_id AND membership.user_id = actor
    ) THEN
        RAISE EXCEPTION USING ERRCODE = '42501', MESSAGE = 'Plan membership required';
    END IF;
    SELECT vote.proposal_id INTO selected_proposal
        FROM public.proposal_votes AS vote
        WHERE vote.plan_id = p_plan_id AND vote.voter_id = actor;
    RETURN selected_proposal;
END;
$$;

CREATE OR REPLACE FUNCTION public.cast_proposal_vote(p_plan_id uuid, p_proposal_id bigint)
RETURNS bigint
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = '' AS $$
DECLARE
    actor uuid := auth.uid();
    updated_votes bigint;
BEGIN
    IF actor IS NULL OR NOT EXISTS (
        SELECT 1 FROM public.plans AS plan
        JOIN public.group_members AS membership ON membership.group_id = plan.group_id
        WHERE plan.id = p_plan_id AND membership.user_id = actor
    ) THEN
        RAISE EXCEPTION USING ERRCODE = '42501', MESSAGE = 'Plan membership required';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM public.plans AS plan
        WHERE plan.id = p_plan_id AND plan.election_method = 'votes'
    ) OR NOT EXISTS (
        SELECT 1 FROM public.proposals AS proposal
        WHERE proposal.id = p_proposal_id AND proposal.plan_id = p_plan_id
    ) THEN
        RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'Proposal unavailable for voting';
    END IF;
    BEGIN
        INSERT INTO public.proposal_votes(plan_id, proposal_id, voter_id)
            VALUES (p_plan_id, p_proposal_id, actor);
    EXCEPTION WHEN unique_violation THEN
        RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'Vote already cast';
    END;
    UPDATE public.proposals AS proposal
        SET votes = proposal.votes + 1
        WHERE proposal.id = p_proposal_id AND proposal.plan_id = p_plan_id
        RETURNING proposal.votes INTO updated_votes;
    RETURN updated_votes;
END;
$$;

CREATE OR REPLACE FUNCTION public.save_proposal_score(
    p_plan_id uuid, p_proposal_id bigint, p_score bigint
)
RETURNS bigint
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = '' AS $$
DECLARE
    actor uuid := auth.uid();
    saved_score bigint;
BEGIN
    IF actor IS NULL OR p_score IS NULL OR p_score < 0 OR p_score > 99999 THEN
        RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'Invalid score';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM public.plans AS plan
        JOIN public.group_members AS membership ON membership.group_id = plan.group_id
        WHERE plan.id = p_plan_id AND membership.user_id = actor
          AND plan.election_method = 'combat'
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

REVOKE ALL ON FUNCTION public.set_proposal_election_method(uuid, text)
    FROM PUBLIC, anon, authenticated, service_role, planb_django_runtime;
REVOKE ALL ON FUNCTION public.proposal_vote_for_user(uuid)
    FROM PUBLIC, anon, authenticated, service_role, planb_django_runtime;
REVOKE ALL ON FUNCTION public.cast_proposal_vote(uuid, bigint)
    FROM PUBLIC, anon, authenticated, service_role, planb_django_runtime;
REVOKE ALL ON FUNCTION public.save_proposal_score(uuid, bigint, bigint)
    FROM PUBLIC, anon, authenticated, service_role, planb_django_runtime;
GRANT EXECUTE ON FUNCTION public.set_proposal_election_method(uuid, text) TO authenticated;
GRANT EXECUTE ON FUNCTION public.proposal_vote_for_user(uuid) TO authenticated;
GRANT EXECUTE ON FUNCTION public.cast_proposal_vote(uuid, bigint) TO authenticated;
GRANT EXECUTE ON FUNCTION public.save_proposal_score(uuid, bigint, bigint) TO authenticated;

COMMENT ON TABLE public.proposal_votes IS
    'Un voto por integrante y plan; escritura exclusivamente por cast_proposal_vote.';
COMMENT ON FUNCTION public.save_proposal_score(uuid, bigint, bigint) IS
    'Guarda el máximo score de combate únicamente en la propuesta del integrante autenticado.';
NOTIFY pgrst, 'reload schema';
COMMIT;