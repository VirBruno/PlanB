-- Borrador: requiere los borradores anteriores. No habilita acceso todavía.
BEGIN;

CREATE FUNCTION public.uno_duel_list(p_plan_id uuid)
RETURNS jsonb LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = '' AS $$
DECLARE
    actor uuid := auth.uid();
    result jsonb;
BEGIN
    IF actor IS NULL OR p_plan_id IS NULL OR NOT EXISTS (
        SELECT 1 FROM public.plans AS p JOIN public.group_members AS m ON m.group_id = p.group_id
        WHERE p.id = p_plan_id AND m.user_id = actor
    ) THEN
        RAISE EXCEPTION USING ERRCODE = '42501', MESSAGE = 'UNO duels unavailable';
    END IF;
    SELECT coalesce(jsonb_agg(item ORDER BY updated_at DESC, match_id DESC), '[]'::jsonb) INTO result
    FROM (
        SELECT m.match_id, m.updated_at, jsonb_build_object(
            'id', m.match_id,
            'status', m.status,
            'role', CASE WHEN actor = m.inviter_id THEN 'inviter' ELSE 'invitee' END,
            'opponent_id', CASE WHEN actor = m.inviter_id THEN m.invitee_id ELSE m.inviter_id END,
            'opponent_username', coalesce(opponent.username, 'Integrante'),
            'proposal_id', CASE WHEN actor = m.inviter_id THEN m.inviter_proposal_id ELSE m.invitee_proposal_id END,
            'opponent_proposal_id', CASE WHEN actor = m.inviter_id THEN m.invitee_proposal_id ELSE m.inviter_proposal_id END,
            'winner', CASE WHEN m.winner_id = actor THEN 'player'
                           WHEN m.winner_id IS NOT NULL THEN 'opponent' ELSE NULL END,
            'finish_reason', m.finish_reason,
            'version', m.version
        ) AS item
        FROM planb_private.uno_duel_matches AS m
        LEFT JOIN public.profiles AS opponent ON opponent.id =
            CASE WHEN actor = m.inviter_id THEN m.invitee_id ELSE m.inviter_id END
        WHERE m.plan_id = p_plan_id AND actor IN (m.inviter_id, m.invitee_id)
        ORDER BY m.updated_at DESC, m.match_id DESC LIMIT 50
    ) AS matches;
    RETURN result;
END;
$$;
REVOKE ALL ON FUNCTION public.uno_duel_list(uuid)
    FROM PUBLIC, anon, authenticated, service_role, planb_django_runtime;
COMMIT;
