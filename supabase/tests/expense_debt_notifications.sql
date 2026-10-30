-- Integración reversible. Sólo en una base LOCAL/de pruebas autorizada como admin.
-- Requiere todas las migraciones, incluida 202610300003_expense_debt_notifications.
-- No ejecutado en remoto. Concurrencia real: expense_debt_concurrency.py (sólo localhost).
BEGIN;
SELECT set_config('planb.debt_owner', '20000000-0000-0000-0000-000000000001', true);
SELECT set_config('planb.debt_member', '20000000-0000-0000-0000-000000000002', true);
SELECT set_config('planb.debt_third', '20000000-0000-0000-0000-000000000003', true);
SELECT set_config('planb.debt_other', '20000000-0000-0000-0000-000000000004', true);
SELECT set_config('planb.debt_outsider', '20000000-0000-0000-0000-000000000005', true);
INSERT INTO auth.users(id, aud, role, email, raw_user_meta_data, created_at, updated_at)
SELECT id, 'authenticated', 'authenticated', id::text || '@example.invalid',
       jsonb_build_object('username', 'Deuda_' || substr(replace(id::text, '-', ''), 13, 20)), now(), now()
FROM (VALUES (current_setting('planb.debt_owner')::uuid), (current_setting('planb.debt_member')::uuid),
             (current_setting('planb.debt_third')::uuid), (current_setting('planb.debt_other')::uuid),
             (current_setting('planb.debt_outsider')::uuid)) fixture(id);
SELECT set_config('request.jwt.claim.sub', current_setting('planb.debt_owner'), true);
SET LOCAL ROLE authenticated;
SELECT set_config('planb.debt_group', public.create_group('Deudas de prueba', NULL)::text, true);
WITH created AS (
    INSERT INTO public.plans(name, description, status, group_id, created_by)
    VALUES ('Cena del viernes', 'Avisos de prueba', true, current_setting('planb.debt_group')::uuid, auth.uid()) RETURNING id
) SELECT set_config('planb.debt_plan', id::text, true) FROM created;
WITH created AS (
    INSERT INTO public.plans(name, description, status, group_id, created_by)
    VALUES ('Otro plan', 'Avisos independientes', true, current_setting('planb.debt_group')::uuid, auth.uid()) RETURNING id
) SELECT set_config('planb.debt_plan2', id::text, true) FROM created;
RESET ROLE;
INSERT INTO public.group_members(group_id, user_id, role)
SELECT current_setting('planb.debt_group')::uuid, id, 'member'
FROM (VALUES (current_setting('planb.debt_member')::uuid), (current_setting('planb.debt_third')::uuid),
             (current_setting('planb.debt_other')::uuid)) fixture(id);

SET LOCAL ROLE authenticated;
DO $$
DECLARE
    p uuid := current_setting('planb.debt_plan')::uuid;
    owner_id uuid := current_setting('planb.debt_owner')::uuid;
    member_id uuid := current_setting('planb.debt_member')::uuid;
    third_id uuid := current_setting('planb.debt_third')::uuid;
    outsider_id uuid := current_setting('planb.debt_outsider')::uuid;
    result jsonb; first_id uuid; second_result jsonb; t jsonb;
BEGIN
    -- Estado vacío: nadie puede notificar saldos inventados.
    BEGIN
        PERFORM public.notify_expense_debt(p, member_id);
        RAISE EXCEPTION 'Se notificó una deuda inexistente';
    EXCEPTION WHEN invalid_parameter_value THEN NULL; END;
    PERFORM public.create_plan_expense(p, 'Cena', 30000, owner_id, ARRAY[owner_id, member_id, third_id]);
    SELECT value INTO t FROM jsonb_array_elements(public.get_plan_expenses(p)->'transfers')
        WHERE value->>'from_id' = member_id::text AND value->>'to_id' = owner_id::text;
    IF t IS NULL OR (t->>'amount')::numeric <> 10000 THEN
        RAISE EXCEPTION 'La fuente compartida no coincide con Para saldar';
    END IF;
    result := public.notify_expense_debt(p, member_id);
    first_id := (result->>'notification_id')::uuid;
    PERFORM set_config('planb.debt_notice', first_id::text, true);
    IF NOT (result->>'created')::boolean OR (result->>'amount')::numeric <> 10000 THEN
        RAISE EXCEPTION 'Monto/estado inicial incorrecto';
    END IF;
    -- Repetir devuelve el mismo pendiente, sin insertar veinte filas.
    FOR i IN 1..20 LOOP
        second_result := public.notify_expense_debt(p, member_id);
        IF second_result->>'notification_id' <> first_id::text OR (second_result->>'created')::boolean THEN
            RAISE EXCEPTION 'Retry duplicó el aviso';
        END IF;
    END LOOP;
    -- El gasto real cambia el monto; nunca se recibe un monto para notificar.
    PERFORM public.create_plan_expense(p, 'Otro consumo', 2000, owner_id, ARRAY[member_id]);
    result := public.notify_expense_debt(p, member_id);
    IF result->>'notification_id' <> first_id::text OR (result->>'amount')::numeric <> 12000 THEN
        RAISE EXCEPTION 'No se actualizó el pendiente con el monto real';
    END IF;
    -- Deudores y planes distintos son independientes.
    result := public.notify_expense_debt(p, third_id);
    IF result->>'notification_id' = first_id::text THEN RAISE EXCEPTION 'Se mezclaron deudores'; END IF;
    PERFORM public.create_plan_expense(current_setting('planb.debt_plan2')::uuid, 'Otro plan', 100,
                                      owner_id, ARRAY[member_id]);
    result := public.notify_expense_debt(current_setting('planb.debt_plan2')::uuid, member_id);
    IF result->>'notification_id' = first_id::text THEN RAISE EXCEPTION 'Se mezclaron planes'; END IF;
    BEGIN
        PERFORM public.notify_expense_debt(p, owner_id);
        RAISE EXCEPTION 'Se permitió autoaviso';
    EXCEPTION WHEN invalid_parameter_value THEN NULL; END;
    BEGIN
        PERFORM public.notify_expense_debt(p, outsider_id);
        RAISE EXCEPTION 'Se aceptó destinatario ajeno';
    EXCEPTION WHEN invalid_parameter_value THEN NULL; END;
    BEGIN
        PERFORM public.notify_expense_debt(p_plan_id => p, p_debtor_id => member_id, p_amount => 1);
        RAISE EXCEPTION 'Se aceptó monto del cliente';
    EXCEPTION WHEN undefined_function THEN NULL; END;
    BEGIN
        PERFORM public.notify_expense_debt(p_plan_id => p, p_debtor_id => member_id, p_creditor_id => third_id);
        RAISE EXCEPTION 'Se aceptó acreedor del cliente';
    EXCEPTION WHEN undefined_function THEN NULL; END;
    -- Ni el deudor ni otro miembro pueden hablar en nombre del acreedor.
    PERFORM set_config('request.jwt.claim.sub', member_id::text, true);
    IF NOT EXISTS (SELECT 1 FROM public.notifications WHERE id = first_id AND user_id = member_id
                   AND creditor_id = owner_id AND plan_id = p AND payload->>'amount' = '12000.00') THEN
        RAISE EXCEPTION 'Destinatario, FK, plan o actualización incorrectos';
    END IF;
    IF (SELECT count(*) FROM public.notifications WHERE read_at IS NULL) <> 2 THEN
        RAISE EXCEPTION 'Badge/RLS no refleja ambos planes del deudor';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM public.list_notifications(1) n WHERE n.id = first_id
        AND n.expense_amount::numeric = 12000 AND n.creditor_id = owner_id AND n.plan_available) THEN
        RAISE EXCEPTION 'Bandeja no proyecta deuda';
    END IF;
    BEGIN
        PERFORM public.notify_expense_debt(p, third_id);
        RAISE EXCEPTION 'Deudor pudo notificar a otro';
    EXCEPTION WHEN invalid_parameter_value THEN NULL; END;
    PERFORM public.mark_notification_read(first_id);
    PERFORM set_config('request.jwt.claim.sub', third_id::text, true);
    IF EXISTS (SELECT 1 FROM public.notifications WHERE id = first_id) THEN
        RAISE EXCEPTION 'Otro miembro leyó una notificación ajena';
    END IF;
    BEGIN
        PERFORM public.mark_notification_read(first_id);
        RAISE EXCEPTION 'Otro miembro marcó como leído';
    EXCEPTION WHEN SQLSTATE 'PT404' THEN NULL; END;
    BEGIN
        PERFORM public.notify_expense_debt(p, member_id);
        RAISE EXCEPTION 'Tercero pudo notificar';
    EXCEPTION WHEN invalid_parameter_value THEN NULL; END;
    PERFORM set_config('request.jwt.claim.sub', owner_id::text, true);
    result := public.notify_expense_debt(p, member_id);
    IF NOT (result->>'created')::boolean OR result->>'notification_id' = first_id::text THEN
        RAISE EXCEPTION 'Leer no habilitó un nuevo aviso';
    END IF;
    PERFORM set_config('planb.debt_pending', result->>'notification_id', true);
    -- Un nuevo gasto salda exactamente la deuda: no se crea ni actualiza aviso.
    PERFORM public.create_plan_expense(p, 'Compensación', 12000, member_id, ARRAY[owner_id]);
    BEGIN
        PERFORM public.notify_expense_debt(p, member_id);
        RAISE EXCEPTION 'Se notificó después de desaparecer la transferencia';
    EXCEPTION WHEN invalid_parameter_value THEN NULL; END;
    PERFORM set_config('request.jwt.claim.sub', outsider_id::text, true);
    BEGIN
        PERFORM public.notify_expense_debt(p, member_id);
        RAISE EXCEPTION 'Outsider invocó la acción';
    EXCEPTION WHEN SQLSTATE 'PT404' THEN NULL; END;
END;
$$;
RESET ROLE;

-- Vector con dos acreedores y dos deudores: mismo orden y semántica anterior.
-- Se usa un plan separado para no alterar los snapshots verificados arriba.
SELECT set_config('request.jwt.claim.sub', current_setting('planb.debt_owner'), true);
SET LOCAL ROLE authenticated;
WITH created AS (
    INSERT INTO public.plans(name, description, status, group_id, created_by)
    VALUES ('Varias transferencias', 'Orden estable', false, current_setting('planb.debt_group')::uuid, auth.uid()) RETURNING id
) SELECT set_config('planb.debt_multi', id::text, true) FROM created;
DO $$
DECLARE
    p uuid := current_setting('planb.debt_multi')::uuid;
    a uuid := current_setting('planb.debt_owner')::uuid;
    b uuid := current_setting('planb.debt_member')::uuid;
    c uuid := current_setting('planb.debt_third')::uuid;
    d uuid := current_setting('planb.debt_other')::uuid;
    rows jsonb; result jsonb;
BEGIN
    PERFORM public.create_plan_expense(p, 'Acreedor uno', 15000.01, a, ARRAY[c]);
    PERFORM public.create_plan_expense(p, 'Acreedor dos', 5000.02, b, ARRAY[d]);
    PERFORM public.create_plan_expense(p, 'Ajuste de distribución', 3000.01, c, ARRAY[d]);
    -- Balances A +15000.01, B +5000.02, C -12000, D -8000.03.
    rows := public.get_plan_expenses(p)->'transfers';
    IF rows <> jsonb_build_array(
        jsonb_build_object('from_id', c, 'to_id', a, 'amount', '12000.00'),
        jsonb_build_object('from_id', d, 'to_id', a, 'amount', '3000.01'),
        jsonb_build_object('from_id', d, 'to_id', b, 'amount', '5000.02')) THEN
        RAISE EXCEPTION 'Algoritmo centralizado divergió: %', rows;
    END IF;
    result := public.notify_expense_debt(p, d);
    IF (result->>'amount')::numeric <> 3000.01 THEN RAISE EXCEPTION 'Monto de transferencia parcial incorrecto'; END IF;
    PERFORM set_config('request.jwt.claim.sub', b::text, true);
    result := public.notify_expense_debt(p, d);
    IF (result->>'amount')::numeric <> 5000.02 THEN RAISE EXCEPTION 'Se mezclaron acreedores'; END IF;
    BEGIN
        PERFORM public.notify_expense_debt(p, c);
        RAISE EXCEPTION 'Se notificó una pareja sin transferencia aunque ambos tenían saldo';
    EXCEPTION WHEN invalid_parameter_value THEN NULL; END;
END;
$$;
RESET ROLE;

-- Pérdida de membresía: historia sin enlace; RLS y acción siguen negando acceso.
DELETE FROM public.group_members WHERE group_id = current_setting('planb.debt_group')::uuid
    AND user_id = current_setting('planb.debt_member')::uuid;
SELECT set_config('request.jwt.claim.sub', current_setting('planb.debt_member'), true);
SET LOCAL ROLE authenticated;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM public.list_notifications(1) n WHERE n.id = current_setting('planb.debt_pending')::uuid
                    AND NOT n.plan_available) THEN RAISE EXCEPTION 'Exmiembro perdió historia o retuvo enlace'; END IF;
    BEGIN
        PERFORM public.get_plan_expenses(current_setting('planb.debt_plan')::uuid);
        RAISE EXCEPTION 'Exmiembro pudo abrir gastos';
    EXCEPTION WHEN SQLSTATE 'PT404' THEN NULL; END;
    BEGIN
        PERFORM public.notify_expense_debt(current_setting('planb.debt_plan')::uuid, current_setting('planb.debt_third')::uuid);
        RAISE EXCEPTION 'Exmiembro pudo notificar';
    EXCEPTION WHEN SQLSTATE 'PT404' THEN NULL; END;
END;
$$;
RESET ROLE;

DO $$
DECLARE r text;
BEGIN
    IF NOT (SELECT relrowsecurity FROM pg_class WHERE oid = 'public.notifications'::regclass)
        THEN RAISE EXCEPTION 'Se deshabilitó RLS'; END IF;
    FOREACH r IN ARRAY ARRAY['anon', 'authenticated', 'service_role', 'planb_django_runtime'] LOOP
        IF has_table_privilege(r, 'public.notifications', 'INSERT')
           OR has_table_privilege(r, 'public.notifications', 'UPDATE')
           OR has_any_column_privilege(r, 'public.notifications', 'INSERT')
           OR has_any_column_privilege(r, 'public.notifications', 'UPDATE') THEN
            RAISE EXCEPTION 'DML directo excesivo para %', r;
        END IF;
        IF has_function_privilege(r, 'planb_private.plan_expense_transfers(uuid)', 'EXECUTE')
           OR has_function_privilege(r, 'planb_private.get_plan_expenses(uuid)', 'EXECUTE') THEN
            RAISE EXCEPTION 'Auxiliar privado expuesto para %', r;
        END IF;
        IF r <> 'authenticated' AND has_function_privilege(r, 'public.notify_expense_debt(uuid,uuid)', 'EXECUTE') THEN
            RAISE EXCEPTION 'RPC expuesta para %', r;
        END IF;
    END LOOP;
END;
$$;
ROLLBACK;
SELECT 'OK: autorización, fuente única, snapshots privados, lectura e idempotencia; fixtures revertidas.' AS resultado;
