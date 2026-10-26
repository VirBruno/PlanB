-- Recordatorios internos. Aplicar después de 202610230001_plan_decisions.sql.
-- No instala un scheduler: el comando Django debe programarse por separado.
BEGIN;

ALTER TABLE public.notifications
    ADD COLUMN plan_id uuid REFERENCES public.plans(id) ON DELETE CASCADE,
    ADD CONSTRAINT notification_plan_required CHECK (
        (type = 'plan_reminder' AND plan_id IS NOT NULL)
        OR (type <> 'plan_reminder' AND plan_id IS NULL)
    );
CREATE UNIQUE INDEX notifications_one_plan_reminder_idx
    ON public.notifications(user_id, plan_id, type) WHERE type = 'plan_reminder';
CREATE INDEX plans_reminder_schedule_idx ON public.plans(scheduled_at, group_id, id)
    WHERE status IS TRUE AND selected_proposal_id IS NOT NULL;

-- Única escritura nueva: no se conceden permisos directos sobre las tablas.
-- Sin parámetros de identidad, fecha ni plan controlables desde el navegador.
CREATE FUNCTION public.generate_plan_reminders()
RETURNS integer LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = '' AS $$
DECLARE
    processing_at timestamptz := statement_timestamp();
    target_group uuid;
    target public.plans%ROWTYPE;
    recipient uuid;
    inserted integer;
    total integer := 0;
BEGIN
    FOR target_group IN
        SELECT DISTINCT p.group_id FROM public.plans p
        WHERE p.status IS TRUE AND p.selected_proposal_id IS NOT NULL
          AND p.scheduled_at > processing_at
          AND p.scheduled_at <= processing_at + interval '24 hours'
        ORDER BY p.group_id
    LOOP
        -- Orden grupo -> plan -> membresía, compatible con eliminación del grupo.
        -- Los ingresos por invitación también bloquean primero el grupo.
        PERFORM g.id FROM public.groups g WHERE g.id = target_group FOR UPDATE;
        IF NOT FOUND THEN CONTINUE; END IF;
        FOR target IN
            SELECT p.* FROM public.plans p
            WHERE p.group_id = target_group AND p.status IS TRUE
              AND p.selected_proposal_id IS NOT NULL
              AND p.scheduled_at > processing_at
              AND p.scheduled_at <= processing_at + interval '24 hours'
            ORDER BY p.id FOR UPDATE OF p
        LOOP
            -- Revalida estado/fecha al adquirir el lock del plan; finalizar,
            -- reabrir o borrar se serializa con esta operación.
            FOR recipient IN
                SELECT m.user_id FROM public.group_members m
                WHERE m.group_id = target.group_id
                ORDER BY m.user_id FOR SHARE OF m
            LOOP
                INSERT INTO public.notifications(user_id, type, plan_id, payload)
                VALUES (recipient, 'plan_reminder', target.id,
                    jsonb_build_object('plan_name', target.name,
                                       'scheduled_at', target.scheduled_at))
                ON CONFLICT (user_id, plan_id, type) WHERE type = 'plan_reminder'
                DO NOTHING;
                GET DIAGNOSTICS inserted = ROW_COUNT;
                total := total + inserted;
            END LOOP;
        END LOOP;
    END LOOP;
    RETURN total;
END;
$$;
REVOKE ALL ON FUNCTION public.generate_plan_reminders()
    FROM PUBLIC, anon, authenticated, service_role, planb_django_runtime;
GRANT EXECUTE ON FUNCTION public.generate_plan_reminders() TO service_role;

-- PostgreSQL requiere recrear la función al extender su tipo de retorno.
-- Se conservan firma de entrada, paginación, destinatario y datos de invitación.
DROP FUNCTION public.list_notifications(integer);
CREATE FUNCTION public.list_notifications(p_page integer DEFAULT 1)
RETURNS TABLE(id uuid, type text, created_at timestamptz, read_at timestamptz,
              invitation_id uuid, group_id uuid, group_name text, inviter_username text, status text,
              plan_id uuid, plan_name text, scheduled_at text, plan_available boolean)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = '' AS $$
DECLARE actor uuid := auth.uid();
BEGIN
    IF actor IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = '42501', MESSAGE = 'Authentication required';
    END IF;
    IF p_page IS NULL OR p_page NOT BETWEEN 1 AND 10000 THEN
        RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'Invalid page';
    END IF;
    RETURN QUERY SELECT n.id, n.type, n.created_at, n.read_at, i.id,
        coalesce(i.group_id, p.group_id), g.name, inviter.username, i.status,
        n.plan_id, n.payload->>'plan_name', n.payload->>'scheduled_at', p.id IS NOT NULL
        FROM public.notifications n
        LEFT JOIN public.group_invitations i ON i.id = n.invitation_id AND i.invited_user_id = actor
        -- El payload sólo contiene texto/fecha históricos, nunca autorización.
        LEFT JOIN public.plans p ON p.id = n.plan_id AND EXISTS (
            SELECT 1 FROM public.group_members m WHERE m.group_id = p.group_id AND m.user_id = actor
        )
        LEFT JOIN public.groups g ON g.id = coalesce(i.group_id, p.group_id)
        LEFT JOIN public.profiles inviter ON inviter.id = i.invited_by
        WHERE n.user_id = actor ORDER BY n.created_at DESC, n.id DESC
        LIMIT 13 OFFSET ((p_page - 1) * 12);
END;
$$;
REVOKE ALL ON FUNCTION public.list_notifications(integer)
    FROM PUBLIC, anon, authenticated, service_role, planb_django_runtime;
GRANT EXECUTE ON FUNCTION public.list_notifications(integer) TO authenticated;

-- RLS, mark_notification_read y las RPCs de invitaciones no se alteran.
COMMENT ON COLUMN public.notifications.plan_id IS 'Plan del recordatorio; se elimina junto con el plan.';
COMMENT ON FUNCTION public.generate_plan_reminders() IS
    'Sólo servidor: confirmados en (ahora, ahora + 24h], miembros actuales, un recordatorio por usuario/plan/tipo.';
COMMENT ON TABLE public.notifications IS
    'Notificaciones privadas extensibles; referencias explícitas y payload histórico sin autoridad sobre permisos.';
NOTIFY pgrst, 'reload schema';
COMMIT;
