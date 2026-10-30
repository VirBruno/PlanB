-- Avisos internos de deuda. Incremental; no ejecutar automáticamente en remoto.
-- Requiere 202610300002_shared_expenses y las migraciones de notificaciones.
BEGIN;

ALTER TABLE public.notifications ADD COLUMN creditor_id uuid REFERENCES public.profiles(id) ON DELETE RESTRICT;
ALTER TABLE public.notifications DROP CONSTRAINT notification_plan_required;
ALTER TABLE public.notifications ADD CONSTRAINT notification_plan_required CHECK (
    (type IN ('plan_reminder', 'plan_confirmed', 'expense_debt_reminder') AND plan_id IS NOT NULL)
    OR (type NOT IN ('plan_reminder', 'plan_confirmed', 'expense_debt_reminder') AND plan_id IS NULL)
);
ALTER TABLE public.notifications ADD CONSTRAINT notification_creditor_required CHECK (
    (type = 'expense_debt_reminder' AND creditor_id IS NOT NULL AND creditor_id <> user_id)
    OR (type <> 'expense_debt_reminder' AND creditor_id IS NULL)
);
CREATE UNIQUE INDEX notifications_one_unread_expense_debt_idx
ON public.notifications(user_id, creditor_id, plan_id, type)
WHERE type = 'expense_debt_reminder' AND read_at IS NULL;
CREATE INDEX notifications_creditor_idx ON public.notifications(creditor_id) WHERE creditor_id IS NOT NULL;

-- ÚNICA implementación productiva del reparto de balances en transferencias.
-- Los intervalos acumulados equivalen al algoritmo previo de dos colas por UUID:
-- cada intersección consume min(saldo deudor, saldo acreedor), sin redondear.
CREATE FUNCTION planb_private.plan_expense_transfers(p_plan_id uuid)
RETURNS TABLE(from_id uuid, to_id uuid, amount numeric)
LANGUAGE sql STABLE SET search_path = '' AS $$
    WITH movements AS (
        SELECT e.paid_by AS user_id, e.amount AS value FROM public.plan_expenses e WHERE e.plan_id = p_plan_id
        UNION ALL
        SELECT ep.user_id, -ep.share_amount FROM public.plan_expense_participants ep
        JOIN public.plan_expenses e ON e.id = ep.expense_id WHERE e.plan_id = p_plan_id
    ), balances AS (
        SELECT user_id, sum(value) AS balance FROM movements GROUP BY user_id
    ), creditors AS (
        SELECT user_id, balance,
            sum(balance) OVER (ORDER BY user_id ROWS UNBOUNDED PRECEDING) AS endpoint
        FROM balances WHERE balance > 0
    ), debtors AS (
        SELECT user_id, -balance AS debt,
            sum(-balance) OVER (ORDER BY user_id ROWS UNBOUNDED PRECEDING) AS endpoint
        FROM balances WHERE balance < 0
    )
    SELECT d.user_id, c.user_id,
        least(d.endpoint, c.endpoint) - greatest(d.endpoint - d.debt, c.endpoint - c.balance)
    FROM debtors d JOIN creditors c
      ON least(d.endpoint, c.endpoint) > greatest(d.endpoint - d.debt, c.endpoint - c.balance)
    ORDER BY d.user_id, c.user_id;
$$;
REVOKE ALL ON FUNCTION planb_private.plan_expense_transfers(uuid)
    FROM PUBLIC, anon, authenticated, service_role, planb_django_runtime;

-- Conservar la lectura previa, con sus validaciones, como auxiliar privado.
-- No se toca la migración aplicada ni se duplica su consulta de gastos/personas.
ALTER FUNCTION public.get_plan_expenses(uuid) SET SCHEMA planb_private;
REVOKE ALL ON FUNCTION planb_private.get_plan_expenses(uuid)
    FROM PUBLIC, anon, authenticated, service_role, planb_django_runtime;
CREATE FUNCTION public.get_plan_expenses(p_plan_id uuid)
RETURNS jsonb LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = '' AS $$
DECLARE snapshot jsonb;
BEGIN
    -- La función previa verifica auth.uid() y pertenencia antes de calcular.
    snapshot := planb_private.get_plan_expenses(p_plan_id);
    RETURN snapshot || jsonb_build_object('transfers', COALESCE((
        SELECT jsonb_agg(jsonb_build_object('from_id', t.from_id, 'to_id', t.to_id,
                                          'amount', t.amount::text) ORDER BY t.from_id, t.to_id)
        FROM planb_private.plan_expense_transfers(p_plan_id) t
    ), '[]'::jsonb));
END;
$$;
REVOKE ALL ON FUNCTION public.get_plan_expenses(uuid)
    FROM PUBLIC, anon, authenticated, service_role, planb_django_runtime;
GRANT EXECUTE ON FUNCTION public.get_plan_expenses(uuid) TO authenticated;

CREATE FUNCTION public.notify_expense_debt(p_plan_id uuid, p_debtor_id uuid)
RETURNS jsonb LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = '' AS $$
DECLARE
    actor uuid := auth.uid();
    target_group uuid;
    target_name text;
    creditor_name text;
    debtor_name text;
    debt_amount numeric;
    notice_id uuid;
    was_created boolean := false;
    notice_payload jsonb;
BEGIN
    IF actor IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = '42501', MESSAGE = 'Se requiere autenticación';
    END IF;
    SELECT p.group_id INTO target_group FROM public.plans p JOIN public.group_members m ON m.group_id = p.group_id
      WHERE p.id = p_plan_id AND m.user_id = actor;
    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE = 'PT404', MESSAGE = 'Plan no disponible';
    END IF;
    -- Serializa con create_plan_expense y otros avisos: grupo -> plan -> membresías.
    PERFORM g.id FROM public.groups g WHERE g.id = target_group FOR UPDATE;
    SELECT p.name INTO target_name FROM public.plans p
      WHERE p.id = p_plan_id AND p.group_id = target_group FOR SHARE;
    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE = 'PT404', MESSAGE = 'Plan no disponible';
    END IF;
    PERFORM m.user_id FROM public.group_members m WHERE m.group_id = target_group ORDER BY m.user_id FOR SHARE;
    IF NOT EXISTS (SELECT 1 FROM public.group_members m WHERE m.group_id = target_group AND m.user_id = actor) THEN
        RAISE EXCEPTION USING ERRCODE = 'PT404', MESSAGE = 'Plan no disponible';
    END IF;
    IF p_debtor_id IS NULL OR p_debtor_id = actor OR NOT EXISTS (
        SELECT 1 FROM public.group_members m WHERE m.group_id = target_group AND m.user_id = p_debtor_id
    ) THEN
        RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'Destinatario inválido';
    END IF;
    SELECT t.amount INTO debt_amount FROM planb_private.plan_expense_transfers(p_plan_id) t
      WHERE t.from_id = p_debtor_id AND t.to_id = actor;
    IF NOT FOUND OR debt_amount <= 0 THEN
        RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'Esa transferencia pendiente ya no existe';
    END IF;
    SELECT p.username INTO creditor_name FROM public.profiles p WHERE p.id = actor;
    SELECT p.username INTO debtor_name FROM public.profiles p WHERE p.id = p_debtor_id;
    notice_payload := jsonb_build_object('plan_name', target_name, 'creditor_username', creditor_name,
        'amount', debt_amount::text, 'notified_at', statement_timestamp());
    -- El lock del grupo serializa remitentes; el de la fila coordina mark_notification_read.
    -- El índice parcial impide duplicados incluso ante escrituras concurrentes futuras.
    SELECT n.id INTO notice_id FROM public.notifications n
      WHERE n.user_id = p_debtor_id AND n.creditor_id = actor AND n.plan_id = p_plan_id
        AND n.type = 'expense_debt_reminder' AND n.read_at IS NULL FOR UPDATE;
    IF FOUND THEN
        UPDATE public.notifications SET payload = notice_payload WHERE id = notice_id;
    ELSE
        INSERT INTO public.notifications(user_id, creditor_id, plan_id, type, payload)
        VALUES (p_debtor_id, actor, p_plan_id, 'expense_debt_reminder', notice_payload)
        RETURNING id INTO notice_id;
        was_created := true;
    END IF;
    RETURN jsonb_build_object('notification_id', notice_id, 'created', was_created,
                             'amount', debt_amount::text, 'debtor_username', debtor_name);
END;
$$;
REVOKE ALL ON FUNCTION public.notify_expense_debt(uuid, uuid)
    FROM PUBLIC, anon, authenticated, service_role, planb_django_runtime;
GRANT EXECUTE ON FUNCTION public.notify_expense_debt(uuid, uuid) TO authenticated;

-- Mismos campos anteriores y paginación; se agregan referencias y texto del aviso.
DROP FUNCTION public.list_notifications(integer);
CREATE FUNCTION public.list_notifications(p_page integer DEFAULT 1)
RETURNS TABLE(id uuid, type text, created_at timestamptz, read_at timestamptz,
              invitation_id uuid, group_id uuid, group_name text, inviter_username text, status text,
              plan_id uuid, plan_name text, scheduled_at text, plan_available boolean,
              creditor_id uuid, creditor_username text, expense_amount text)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = '' AS $$
DECLARE actor uuid := auth.uid();
BEGIN
    IF actor IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = '42501', MESSAGE = 'Se requiere autenticación';
    END IF;
    IF p_page IS NULL OR p_page NOT BETWEEN 1 AND 10000 THEN
        RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'Página inválida';
    END IF;
    RETURN QUERY SELECT n.id, n.type, n.created_at, n.read_at, i.id,
        coalesce(i.group_id, p.group_id), g.name, inviter.username, i.status,
        n.plan_id, n.payload->>'plan_name', n.payload->>'scheduled_at', p.id IS NOT NULL,
        n.creditor_id, n.payload->>'creditor_username', n.payload->>'amount'
    FROM public.notifications n
    LEFT JOIN public.group_invitations i ON i.id = n.invitation_id AND i.invited_user_id = actor
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
COMMENT ON COLUMN public.notifications.creditor_id IS 'Acreedor real del aviso de deuda; identidad obtenida de auth.uid(), nunca del payload.';
COMMENT ON FUNCTION public.notify_expense_debt(uuid, uuid) IS 'Aviso privado manual: valida transferencia real, actualiza el pendiente o crea uno nuevo después de leer.';
COMMENT ON FUNCTION public.get_plan_expenses(uuid) IS 'Instantánea con transferencias calculadas por el mismo auxiliar privado que autoriza avisos.';
NOTIFY pgrst, 'reload schema';
COMMIT;
