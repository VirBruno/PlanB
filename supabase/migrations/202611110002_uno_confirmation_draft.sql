-- Preparación para revisión: no concede EXECUTE a ningún rol de aplicación.
-- La futura operación que registre victorias deberá bloquear primero el grupo
-- y después el plan, en este mismo orden, antes de insertar el resultado.
BEGIN;

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
       OR target.election_method IS DISTINCT FROM 'combat'
       OR target.combat_game IS DISTINCT FROM 'uno' THEN
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
COMMIT;
