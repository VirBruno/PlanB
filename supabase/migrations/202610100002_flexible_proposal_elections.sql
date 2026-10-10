-- Aplicar después de 202610100001_proposal_election_methods.sql.
-- Permite cambiar el método y actualizar/eliminar el voto propio sin perder conteos.
BEGIN;

CREATE OR REPLACE FUNCTION public.set_proposal_election_method(p_plan_id uuid, p_method text)
RETURNS text
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = '' AS $$
DECLARE
    actor uuid := auth.uid();
    target_group uuid;
BEGIN
    IF actor IS NULL OR p_method IS NULL OR p_method NOT IN ('ideal', 'votes', 'combat') THEN
        RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'Invalid election method';
    END IF;
    SELECT plan.group_id INTO target_group
        FROM public.plans AS plan WHERE plan.id = p_plan_id FOR UPDATE;
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
    UPDATE public.plans SET election_method = p_method WHERE id = p_plan_id;
    RETURN p_method;
END;
$$;

CREATE OR REPLACE FUNCTION public.cast_proposal_vote(p_plan_id uuid, p_proposal_id bigint)
RETURNS bigint
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = '' AS $$
DECLARE
    actor uuid := auth.uid();
    current_method text;
    previous_proposal bigint;
    updated_votes bigint;
BEGIN
    IF actor IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = '42501', MESSAGE = 'Authentication required';
    END IF;
    SELECT plan.election_method INTO current_method
        FROM public.plans AS plan WHERE plan.id = p_plan_id FOR UPDATE;
    IF NOT FOUND OR current_method IS DISTINCT FROM 'votes' THEN
        RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'Voting mode is not active';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM public.group_members AS membership
        WHERE membership.group_id = (
            SELECT plan.group_id FROM public.plans AS plan WHERE plan.id = p_plan_id
        ) AND membership.user_id = actor
    ) THEN
        RAISE EXCEPTION USING ERRCODE = '42501', MESSAGE = 'Plan membership required';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM public.proposals AS proposal
        WHERE proposal.id = p_proposal_id AND proposal.plan_id = p_plan_id
    ) THEN
        RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'Proposal unavailable for voting';
    END IF;
    SELECT vote.proposal_id INTO previous_proposal
        FROM public.proposal_votes AS vote
        WHERE vote.plan_id = p_plan_id AND vote.voter_id = actor
        FOR UPDATE;
    IF previous_proposal IS NULL THEN
        INSERT INTO public.proposal_votes(plan_id, proposal_id, voter_id)
            VALUES (p_plan_id, p_proposal_id, actor);
        UPDATE public.proposals AS proposal
            SET votes = proposal.votes + 1
            WHERE proposal.id = p_proposal_id AND proposal.plan_id = p_plan_id;
    ELSIF previous_proposal <> p_proposal_id THEN
        UPDATE public.proposal_votes AS vote
            SET proposal_id = p_proposal_id, created_at = statement_timestamp()
            WHERE vote.plan_id = p_plan_id AND vote.voter_id = actor;
        UPDATE public.proposals AS proposal
            SET votes = greatest(proposal.votes - 1, 0)
            WHERE proposal.id = previous_proposal AND proposal.plan_id = p_plan_id;
        UPDATE public.proposals AS proposal
            SET votes = proposal.votes + 1
            WHERE proposal.id = p_proposal_id AND proposal.plan_id = p_plan_id;
    END IF;
    SELECT proposal.votes INTO updated_votes
        FROM public.proposals AS proposal
        WHERE proposal.id = p_proposal_id AND proposal.plan_id = p_plan_id;
    RETURN updated_votes;
END;
$$;

CREATE OR REPLACE FUNCTION public.remove_proposal_vote(p_plan_id uuid)
RETURNS bigint
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = '' AS $$
DECLARE
    actor uuid := auth.uid();
    target_group uuid;
    previous_proposal bigint;
BEGIN
    IF actor IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = '42501', MESSAGE = 'Authentication required';
    END IF;
    SELECT plan.group_id INTO target_group
        FROM public.plans AS plan WHERE plan.id = p_plan_id FOR UPDATE;
    IF NOT FOUND OR NOT EXISTS (
        SELECT 1 FROM public.group_members AS membership
        WHERE membership.group_id = target_group AND membership.user_id = actor
    ) THEN
        RAISE EXCEPTION USING ERRCODE = '42501', MESSAGE = 'Plan membership required';
    END IF;
    SELECT vote.proposal_id INTO previous_proposal
        FROM public.proposal_votes AS vote
        WHERE vote.plan_id = p_plan_id AND vote.voter_id = actor
        FOR UPDATE;
    IF previous_proposal IS NULL THEN
        RETURN NULL;
    END IF;
    DELETE FROM public.proposal_votes AS vote
        WHERE vote.plan_id = p_plan_id AND vote.voter_id = actor;
    UPDATE public.proposals AS proposal
        SET votes = greatest(proposal.votes - 1, 0)
        WHERE proposal.id = previous_proposal AND proposal.plan_id = p_plan_id;
    RETURN previous_proposal;
END;
$$;

REVOKE ALL ON FUNCTION public.set_proposal_election_method(uuid, text)
    FROM PUBLIC, anon, authenticated, service_role, planb_django_runtime;
REVOKE ALL ON FUNCTION public.cast_proposal_vote(uuid, bigint)
    FROM PUBLIC, anon, authenticated, service_role, planb_django_runtime;
REVOKE ALL ON FUNCTION public.remove_proposal_vote(uuid)
    FROM PUBLIC, anon, authenticated, service_role, planb_django_runtime;
GRANT EXECUTE ON FUNCTION public.set_proposal_election_method(uuid, text) TO authenticated;
GRANT EXECUTE ON FUNCTION public.cast_proposal_vote(uuid, bigint) TO authenticated;
GRANT EXECUTE ON FUNCTION public.remove_proposal_vote(uuid) TO authenticated;

COMMENT ON FUNCTION public.cast_proposal_vote(uuid, bigint) IS
    'Crea o reemplaza el voto del integrante y mantiene los conteos por propuesta.';
COMMENT ON FUNCTION public.remove_proposal_vote(uuid) IS
    'Elimina únicamente el voto del integrante autenticado y actualiza el conteo.';
COMMENT ON TABLE public.proposal_votes IS
    'Voto vigente por integrante/plan; escritura autenticada vía RPC de emitir, reemplazar o quitar voto.';
NOTIFY pgrst, 'reload schema';
COMMIT;