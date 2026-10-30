-- Gastos compartidos. Incremental; aplicar después de las migraciones existentes.
-- Archivo preparado para revisión: no ejecutarlo automáticamente contra Supabase remoto.
BEGIN;

CREATE TABLE public.plan_expenses (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    plan_id uuid NOT NULL REFERENCES public.plans(id) ON DELETE CASCADE,
    description text NOT NULL CHECK (
        char_length(description) BETWEEN 1 AND 160 AND description = btrim(description)
        AND description ~ '[^[:space:]]'
    ),
    amount numeric(14,2) NOT NULL CHECK (amount > 0 AND amount <= 999999999999.99),
    paid_by uuid NOT NULL REFERENCES public.profiles(id) ON DELETE RESTRICT,
    created_by uuid NOT NULL REFERENCES public.profiles(id) ON DELETE RESTRICT,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE public.plan_expense_participants (
    expense_id uuid NOT NULL REFERENCES public.plan_expenses(id) ON DELETE CASCADE,
    user_id uuid NOT NULL REFERENCES public.profiles(id) ON DELETE RESTRICT,
    share_amount numeric(14,2) NOT NULL CHECK (share_amount >= 0 AND share_amount <= 999999999999.99),
    PRIMARY KEY (expense_id, user_id)
);
CREATE INDEX plan_expenses_plan_created_idx ON public.plan_expenses(plan_id, created_at DESC, id DESC);
CREATE INDEX plan_expenses_paid_by_idx ON public.plan_expenses(paid_by);
CREATE INDEX plan_expenses_created_by_idx ON public.plan_expenses(created_by);
CREATE INDEX plan_expense_participants_user_idx ON public.plan_expense_participants(user_id);

ALTER TABLE public.plan_expenses ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.plan_expense_participants ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.plan_expenses, public.plan_expense_participants
    FROM PUBLIC, anon, authenticated, service_role, planb_django_runtime;
GRANT SELECT ON TABLE public.plan_expenses, public.plan_expense_participants TO authenticated;

CREATE POLICY plan_expenses_select_member ON public.plan_expenses
FOR SELECT TO authenticated USING (
    EXISTS (SELECT 1 FROM public.plans p JOIN public.group_members m ON m.group_id = p.group_id
            WHERE p.id = plan_expenses.plan_id AND m.user_id = (SELECT auth.uid()))
);
CREATE POLICY plan_expense_participants_select_member ON public.plan_expense_participants
FOR SELECT TO authenticated USING (
    EXISTS (SELECT 1 FROM public.plan_expenses e JOIN public.plans p ON p.id = e.plan_id
            JOIN public.group_members m ON m.group_id = p.group_id
            WHERE e.id = plan_expense_participants.expense_id AND m.user_id = (SELECT auth.uid()))
);

CREATE FUNCTION public.create_plan_expense(
    p_plan_id uuid, p_description text, p_amount numeric, p_paid_by uuid,
    p_participants uuid[] DEFAULT '{}'::uuid[], p_all_group boolean DEFAULT false
)
RETURNS uuid LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = '' AS $$
DECLARE
    actor uuid := auth.uid();
    target_group uuid;
    current_members uuid[];
    selected_members uuid[];
    new_id uuid;
    clean_description text := regexp_replace(p_description, '^[[:space:]]+|[[:space:]]+$', '', 'g');
    cents bigint;
    base_cents bigint;
    remainder bigint;
BEGIN
    IF actor IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = '42501', MESSAGE = 'Se requiere autenticación';
    END IF;
    SELECT p.group_id INTO target_group FROM public.plans p
      JOIN public.group_members m ON m.group_id = p.group_id
      WHERE p.id = p_plan_id AND m.user_id = actor;
    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE = 'PT404', MESSAGE = 'Plan no disponible';
    END IF;
    -- Mismo orden que altas de membresías: grupo antes que filas dependientes.
    -- FOR UPDATE también bloquea nuevas FK al grupo durante la expansión.
    PERFORM g.id FROM public.groups g WHERE g.id = target_group FOR UPDATE;
    PERFORM p.id FROM public.plans p WHERE p.id = p_plan_id AND p.group_id = target_group FOR SHARE;
    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE = 'PT404', MESSAGE = 'Plan no disponible';
    END IF;
    -- No permitir bajas/cambios de los integrantes entre validación e inserción.
    PERFORM m.user_id FROM public.group_members m WHERE m.group_id = target_group ORDER BY m.user_id FOR SHARE;
    SELECT array_agg(m.user_id ORDER BY m.user_id) INTO current_members
      FROM public.group_members m WHERE m.group_id = target_group;
    IF current_members IS NULL OR NOT (actor = ANY(current_members)) THEN
        RAISE EXCEPTION USING ERRCODE = 'PT404', MESSAGE = 'Plan no disponible';
    END IF;
    IF clean_description IS NULL OR char_length(clean_description) NOT BETWEEN 1 AND 160
       OR clean_description !~ '[^[:space:]]'
       OR p_amount IS NULL OR p_amount::text IN ('NaN', 'Infinity', '-Infinity')
       OR p_amount <= 0 OR p_amount > 999999999999.99 OR scale(p_amount) > 2 OR p_amount <> round(p_amount, 2)
       OR p_paid_by IS NULL OR NOT (p_paid_by = ANY(current_members))
       OR p_all_group IS NULL OR p_participants IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'Datos de gasto inválidos';
    END IF;
    IF p_all_group THEN
        IF cardinality(p_participants) <> 0 THEN
            RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'Usá todo el grupo o participantes explícitos';
        END IF;
        selected_members := current_members;
    ELSE
        IF cardinality(p_participants) < 1 OR array_ndims(p_participants) <> 1
           OR array_position(p_participants, NULL) IS NOT NULL
           OR cardinality(p_participants) <> (SELECT count(DISTINCT uid) FROM unnest(p_participants) AS t(uid))
           OR NOT (p_participants <@ current_members) THEN
            RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'Participantes inválidos';
        END IF;
        SELECT array_agg(uid ORDER BY uid) INTO selected_members FROM unnest(p_participants) AS t(uid);
    END IF;
    -- Centavos enteros: sobrantes asignados al UUID ascendente, independientemente
    -- del orden enviado, el nombre visible o quién pagó. Puede haber partes de $0.
    cents := (p_amount * 100)::bigint;
    base_cents := cents / cardinality(selected_members);
    remainder := cents % cardinality(selected_members);
    INSERT INTO public.plan_expenses(plan_id, description, amount, paid_by, created_by)
      VALUES (p_plan_id, clean_description, p_amount, p_paid_by, actor) RETURNING id INTO new_id;
    INSERT INTO public.plan_expense_participants(expense_id, user_id, share_amount)
      SELECT new_id, uid, (base_cents + CASE WHEN ordinal <= remainder THEN 1 ELSE 0 END)::numeric / 100
      FROM unnest(selected_members) WITH ORDINALITY AS t(uid, ordinal);
    RETURN new_id;
END;
$$;

CREATE FUNCTION public.get_plan_expenses(p_plan_id uuid)
RETURNS jsonb LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = '' AS $$
DECLARE actor uuid := auth.uid(); target_group uuid; result jsonb;
BEGIN
    IF actor IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = '42501', MESSAGE = 'Se requiere autenticación';
    END IF;
    SELECT p.group_id INTO target_group FROM public.plans p
      JOIN public.group_members m ON m.group_id = p.group_id
      WHERE p.id = p_plan_id AND m.user_id = actor;
    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE = 'PT404', MESSAGE = 'Plan no disponible';
    END IF;
    -- Una instantánea y agregaciones por conjunto; sin consultas por gasto/persona.
    WITH expenses AS (
        SELECT e.* FROM public.plan_expenses e WHERE e.plan_id = p_plan_id
    ), parts AS (
        SELECT ep.* FROM public.plan_expense_participants ep JOIN expenses e ON e.id = ep.expense_id
    ), members AS (
        SELECT m.user_id AS id FROM public.group_members m WHERE m.group_id = target_group
    ), identities AS (
        SELECT id FROM members UNION SELECT paid_by FROM expenses UNION SELECT user_id FROM parts
    ), grouped_parts AS (
        SELECT expense_id, jsonb_agg(jsonb_build_object(
            'user_id', user_id, 'share_amount', share_amount::text) ORDER BY user_id) AS participants
        FROM parts GROUP BY expense_id
    )
    SELECT jsonb_build_object(
        'members', COALESCE((SELECT jsonb_agg(jsonb_build_object('id', id) ORDER BY id) FROM members), '[]'::jsonb),
        'people', COALESCE((SELECT jsonb_agg(jsonb_build_object('id', p.id, 'username', p.username) ORDER BY p.id)
                           FROM identities i JOIN public.profiles p ON p.id = i.id), '[]'::jsonb),
        'expenses', COALESCE((SELECT jsonb_agg(jsonb_build_object(
            'id', e.id, 'description', e.description, 'amount', e.amount::text,
            'paid_by', e.paid_by, 'created_at', e.created_at,
            'participants', COALESCE(gp.participants, '[]'::jsonb)) ORDER BY e.created_at DESC, e.id DESC)
            FROM expenses e LEFT JOIN grouped_parts gp ON gp.expense_id = e.id), '[]'::jsonb)
    ) INTO result;
    RETURN result;
END;
$$;

REVOKE ALL ON FUNCTION public.create_plan_expense(uuid, text, numeric, uuid, uuid[], boolean),
    public.get_plan_expenses(uuid) FROM PUBLIC, anon, authenticated, service_role, planb_django_runtime;
GRANT EXECUTE ON FUNCTION public.create_plan_expense(uuid, text, numeric, uuid, uuid[], boolean),
    public.get_plan_expenses(uuid) TO authenticated;
COMMENT ON TABLE public.plan_expenses IS 'Gastos históricos del plan; sólo creación atómica por RPC autenticada.';
COMMENT ON TABLE public.plan_expense_participants IS 'Partes congeladas; no dependen de membresías futuras. Identidad preservada por FK RESTRICT.';
COMMENT ON FUNCTION public.get_plan_expenses(uuid) IS 'Instantánea privada para miembros actuales; importes JSON como texto decimal, sin ampliar lectura de perfiles.';
NOTIFY pgrst, 'reload schema';
COMMIT;
