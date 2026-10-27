-- Notificación inmediata al confirmar. Aplicar después de 202610260001.
-- Se extiende la RPC existente; no hay scheduler ni escrituras HTTP adicionales.
BEGIN;

ALTER TABLE public.notifications DROP CONSTRAINT notification_plan_required;
ALTER TABLE public.notifications ADD CONSTRAINT notification_plan_required CHECK (
    (type IN ('plan_reminder', 'plan_confirmed') AND plan_id IS NOT NULL)
    OR (type NOT IN ('plan_reminder', 'plan_confirmed') AND plan_id IS NULL)
);
CREATE UNIQUE INDEX notifications_one_plan_confirmed_idx
    ON public.notifications(user_id, plan_id, type) WHERE type = 'plan_confirmed';

CREATE OR REPLACE FUNCTION public.confirm_plan_proposal(p_plan_id uuid, p_proposal_id bigint)
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
    -- Mismo orden grupo -> plan -> membresías que recordatorios e invitaciones.
    -- La lectura inicial sólo determina el grupo; se revalida al bloquear el plan.
    SELECT p.group_id INTO target_group FROM public.plans p WHERE p.id = p_plan_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'Plan is unavailable or already confirmed';
    END IF;
    PERFORM g.id FROM public.groups g WHERE g.id = target_group FOR UPDATE;
    SELECT * INTO target FROM public.plans
        WHERE id = p_plan_id AND group_id = target_group FOR UPDATE;
    IF NOT FOUND OR target.selected_proposal_id IS NOT NULL OR target.status IS NOT TRUE THEN
        RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'Plan is unavailable or already confirmed';
    END IF;
    PERFORM m.user_id FROM public.group_members m
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
    IF target.election_method IN ('votes', 'combat') THEN
        IF target.election_method = 'votes' THEN
            SELECT MAX(votes) INTO top_score FROM public.proposals WHERE plan_id = p_plan_id;
            SELECT count(*) INTO tied_count FROM public.proposals WHERE plan_id = p_plan_id AND votes = top_score;
            IF top_score IS NULL OR top_score <= 0 OR chosen.votes != top_score OR tied_count != 1 THEN
                RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'Voting has no unique winner';
            END IF;
        ELSE
            SELECT MAX("Score") INTO top_score FROM public.proposals WHERE plan_id = p_plan_id;
            SELECT count(*) INTO tied_count FROM public.proposals WHERE plan_id = p_plan_id AND "Score" = top_score;
            IF top_score IS NULL OR top_score <= 0 OR chosen."Score" != top_score OR tied_count != 1 THEN
                RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'Combat has no unique winner';
            END IF;
        END IF;
    END IF;
    UPDATE public.plans SET
        selected_proposal_id = chosen.id,
        scheduled_at = chosen.date_pick,
        confirmed_at = now(),
        weather_province = NULL, weather_city_id = NULL, weather_city_name = NULL
    WHERE id = p_plan_id;

    -- Snapshot histórico sin autoridad sobre permisos. El plan y todos los avisos
    -- se confirman juntos; un fallo de INSERT revierte también la decisión.
    FOR recipient IN
        SELECT m.user_id FROM public.group_members m
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

-- CREATE OR REPLACE conserva los grants de la RPC. No se amplían permisos.
-- list_notifications ya proyecta cualquier notificación con plan_id mediante
-- la FK y membresía actual; no requiere cambiar firma, paginación ni permisos.
-- notification_invitation_required impide mezclar invitation_id con estos tipos.
COMMENT ON COLUMN public.notifications.plan_id IS
    'Plan del recordatorio o confirmación; ambos avisos se eliminan junto con el plan.';
COMMENT ON FUNCTION public.confirm_plan_proposal(uuid, bigint) IS
    'Sólo owner: confirma propuesta y fecha; genera plan_confirmed para miembros actuales en la misma transacción.';
NOTIFY pgrst, 'reload schema';
COMMIT;
