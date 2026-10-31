-- Prueba de integración reversible para una instancia LOCAL/de pruebas autorizada.
-- Requiere las migraciones existentes y 202610300002_shared_expenses.sql.
-- No forma parte de manage.py test. No ejecutar contra Supabase remoto en esta tarea.
BEGIN;
SELECT set_config('planb.exp_owner', '10000000-0000-0000-0000-000000000001', true);
SELECT set_config('planb.exp_member', '10000000-0000-0000-0000-000000000002', true);
SELECT set_config('planb.exp_third', '10000000-0000-0000-0000-000000000003', true);
SELECT set_config('planb.exp_outsider', '10000000-0000-0000-0000-000000000004', true);
INSERT INTO auth.users(id, aud, role, email, raw_user_meta_data, created_at, updated_at)
SELECT id, 'authenticated', 'authenticated', id::text || '@example.invalid',
       jsonb_build_object('username', 'Gasto_' || substr(replace(id::text, '-', ''), 13, 20)), now(), now()
FROM (VALUES (current_setting('planb.exp_owner')::uuid), (current_setting('planb.exp_member')::uuid),
             (current_setting('planb.exp_third')::uuid), (current_setting('planb.exp_outsider')::uuid)) AS fixture(id);

SELECT set_config('request.jwt.claim.sub', current_setting('planb.exp_owner'), true);
SET LOCAL ROLE authenticated;
SELECT set_config('planb.exp_group', public.create_group('Gastos de prueba', NULL)::text, true);
WITH created AS (
    INSERT INTO public.plans(name, description, status, group_id, created_by)
    VALUES ('Cena del viernes', 'Prueba reversible', true, current_setting('planb.exp_group')::uuid, auth.uid()) RETURNING id
) SELECT set_config('planb.exp_plan', id::text, true) FROM created;
RESET ROLE;
INSERT INTO public.group_members(group_id, user_id, role)
VALUES (current_setting('planb.exp_group')::uuid, current_setting('planb.exp_member')::uuid, 'member'),
       (current_setting('planb.exp_group')::uuid, current_setting('planb.exp_third')::uuid, 'member');

SELECT set_config('request.jwt.claim.sub', current_setting('planb.exp_member'), true);
SET LOCAL ROLE authenticated;
DO $$
DECLARE
    p uuid := current_setting('planb.exp_plan')::uuid;
    owner_id uuid := current_setting('planb.exp_owner')::uuid;
    member_id uuid := auth.uid();
    third_id uuid := current_setting('planb.exp_third')::uuid;
    outsider_id uuid := current_setting('planb.exp_outsider')::uuid;
    e uuid; value numeric; before_count bigint; data jsonb;
BEGIN
    data := public.get_plan_expenses(p);
    IF jsonb_array_length(data->'expenses') <> 0 OR jsonb_array_length(data->'members') <> 3 THEN
        RAISE EXCEPTION 'Estado vacío o miembros incorrectos';
    END IF;
    e := public.create_plan_expense(p, '  Cena  ', 100.00, owner_id, '{}'::uuid[], true);
    PERFORM set_config('planb.exp_first', e::text, true);
    IF NOT EXISTS (SELECT 1 FROM public.plan_expenses WHERE id = e AND description = 'Cena' AND created_by = member_id) THEN
        RAISE EXCEPTION 'Un miembro no pudo crear o se falsificó identidad';
    END IF;
    IF (SELECT count(*) FROM public.plan_expense_participants WHERE expense_id = e) <> 3
       OR NOT EXISTS (SELECT 1 FROM public.plan_expense_participants WHERE expense_id = e AND user_id = owner_id AND share_amount = 33.34)
       OR (SELECT sum(share_amount) FROM public.plan_expense_participants WHERE expense_id = e) <> 100.00 THEN
        RAISE EXCEPTION 'TODO EL GRUPO, owner o reparto de centavos incorrecto';
    END IF;
    -- El orden enviado no determina a quién corresponde el sobrante.
    e := public.create_plan_expense(p, 'Orden invertido', 100.00, owner_id, ARRAY[third_id, member_id, owner_id]);
    IF NOT EXISTS (SELECT 1 FROM public.plan_expense_participants WHERE expense_id = e AND user_id = owner_id AND share_amount = 33.34) THEN
        RAISE EXCEPTION 'Reparto no determinístico';
    END IF;
    e := public.create_plan_expense(p, 'Pagador no participa', 20.00, owner_id, ARRAY[member_id, third_id]);
    IF EXISTS (SELECT 1 FROM public.plan_expense_participants WHERE expense_id = e AND user_id = owner_id)
       OR (SELECT count(*) FROM public.plan_expense_participants WHERE expense_id = e AND share_amount = 10.00) <> 2 THEN
        RAISE EXCEPTION 'Pagador fuera del reparto o división entre dos incorrecta';
    END IF;
    e := public.create_plan_expense(p, 'Un centavo', 0.01, owner_id, '{}'::uuid[], true);
    IF (SELECT sum(share_amount) FROM public.plan_expense_participants WHERE expense_id = e) <> 0.01
       OR (SELECT count(*) FROM public.plan_expense_participants WHERE expense_id = e AND share_amount = 0) <> 2 THEN
        RAISE EXCEPTION 'Se perdieron centavos o se rechazaron partes nulas';
    END IF;
    SELECT count(*) INTO before_count FROM public.plan_expenses WHERE plan_id = p;
    FOREACH value IN ARRAY ARRAY[0::numeric, -1, 0.001, 1.000, 1000000000000, 'NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric] LOOP
        BEGIN
            PERFORM public.create_plan_expense(p, 'Monto inválido', value, owner_id, ARRAY[member_id]);
            RAISE EXCEPTION 'Se aceptó un monto inválido: %', value;
        EXCEPTION WHEN invalid_parameter_value THEN NULL;
        END;
    END LOOP;
    BEGIN
        PERFORM public.create_plan_expense(p, 'Ajeno paga', 10, outsider_id, ARRAY[member_id]);
        RAISE EXCEPTION 'Se aceptó pagador ajeno';
    EXCEPTION WHEN invalid_parameter_value THEN NULL; END;
    BEGIN
        PERFORM public.create_plan_expense(p, 'Ajeno participa', 10, owner_id, ARRAY[outsider_id]);
        RAISE EXCEPTION 'Se aceptó participante ajeno';
    EXCEPTION WHEN invalid_parameter_value THEN NULL; END;
    BEGIN
        PERFORM public.create_plan_expense(p, 'Duplicados', 10, owner_id, ARRAY[member_id, member_id]);
        RAISE EXCEPTION 'Se aceptaron duplicados';
    EXCEPTION WHEN invalid_parameter_value THEN NULL; END;
    BEGIN
        PERFORM public.create_plan_expense(p, 'Nulo', 10, owner_id, ARRAY[NULL::uuid]);
        RAISE EXCEPTION 'Se aceptó participante NULL';
    EXCEPTION WHEN invalid_parameter_value THEN NULL; END;
    BEGIN
        PERFORM public.create_plan_expense(p, 'Sin participantes', 10, owner_id);
        RAISE EXCEPTION 'Se aceptó reparto vacío';
    EXCEPTION WHEN invalid_parameter_value THEN NULL; END;
    BEGIN
        PERFORM public.create_plan_expense(p, 'Ambiguo', 10, owner_id, ARRAY[owner_id], true);
        RAISE EXCEPTION 'Se aceptó grupo completo con selección explícita';
    EXCEPTION WHEN invalid_parameter_value THEN NULL; END;
    BEGIN
        PERFORM public.create_plan_expense(p, E' \t\n ', 10, owner_id, ARRAY[member_id]);
        RAISE EXCEPTION 'Se aceptó concepto vacío';
    EXCEPTION WHEN invalid_parameter_value THEN NULL; END;
    BEGIN
        PERFORM public.create_plan_expense(p, repeat('a', 161), 10, owner_id, ARRAY[member_id]);
        RAISE EXCEPTION 'Se aceptó concepto largo';
    EXCEPTION WHEN invalid_parameter_value THEN NULL; END;
    IF (SELECT count(*) FROM public.plan_expenses WHERE plan_id = p) <> before_count THEN
        RAISE EXCEPTION 'Una operación inválida dejó un gasto parcial';
    END IF;
    data := public.get_plan_expenses(p);
    IF jsonb_typeof(data->'expenses'->0->'amount') <> 'string'
       OR jsonb_typeof(data->'expenses'->0->'participants'->0->'share_amount') <> 'string' THEN
        RAISE EXCEPTION 'Dinero JSON expuesto como punto flotante';
    END IF;
END;
$$;
RESET ROLE;

-- Provocar un fallo DESPUÉS del INSERT de cabecera: tampoco debe quedar el gasto.
CREATE FUNCTION planb_private.test_expense_part_failure()
RETURNS trigger LANGUAGE plpgsql SET search_path = '' AS $$
BEGIN
    RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'Fallo de participante simulado';
END;
$$;
CREATE TRIGGER test_expense_part_failure BEFORE INSERT ON public.plan_expense_participants
FOR EACH ROW EXECUTE FUNCTION planb_private.test_expense_part_failure();
SET LOCAL ROLE authenticated;
DO $$
DECLARE before_count bigint;
BEGIN
    SELECT count(*) INTO before_count FROM public.plan_expenses WHERE plan_id = current_setting('planb.exp_plan')::uuid;
    BEGIN
        PERFORM public.create_plan_expense(current_setting('planb.exp_plan')::uuid, 'Fallo parcial', 10,
            current_setting('planb.exp_owner')::uuid, '{}'::uuid[], true);
        RAISE EXCEPTION 'No se provocó el fallo esperado';
    EXCEPTION WHEN check_violation THEN NULL; END;
    IF (SELECT count(*) FROM public.plan_expenses WHERE plan_id = current_setting('planb.exp_plan')::uuid) <> before_count THEN
        RAISE EXCEPTION 'La RPC dejó cabecera sin participantes';
    END IF;
END;
$$;
RESET ROLE;
DROP TRIGGER test_expense_part_failure ON public.plan_expense_participants;
DROP FUNCTION planb_private.test_expense_part_failure();

-- Owner también crea; sin nuevas restricciones por estado.
SELECT set_config('request.jwt.claim.sub', current_setting('planb.exp_owner'), true);
SET LOCAL ROLE authenticated;
UPDATE public.plans SET status = false WHERE id = current_setting('planb.exp_plan')::uuid;
SELECT public.create_plan_expense(current_setting('planb.exp_plan')::uuid, 'Inactivo', 10,
    auth.uid(), ARRAY[auth.uid()]);
RESET ROLE;

-- Alta posterior no modifica la foto histórica; baja conserva partes e identidad.
INSERT INTO public.group_members(group_id, user_id, role)
VALUES (current_setting('planb.exp_group')::uuid, current_setting('planb.exp_outsider')::uuid, 'member');
DO $$
BEGIN
    IF (SELECT count(*) FROM public.plan_expense_participants WHERE expense_id = current_setting('planb.exp_first')::uuid) <> 3
       OR EXISTS (SELECT 1 FROM public.plan_expense_participants WHERE expense_id = current_setting('planb.exp_first')::uuid
                  AND user_id = current_setting('planb.exp_outsider')::uuid) THEN
        RAISE EXCEPTION 'El alta alteró el reparto histórico';
    END IF;
END;
$$;
DELETE FROM public.group_members WHERE group_id = current_setting('planb.exp_group')::uuid
    AND user_id IN (current_setting('planb.exp_outsider')::uuid, current_setting('planb.exp_member')::uuid);

SELECT set_config('request.jwt.claim.sub', current_setting('planb.exp_owner'), true);
SET LOCAL ROLE authenticated;
DO $$
DECLARE data jsonb := public.get_plan_expenses(current_setting('planb.exp_plan')::uuid);
BEGIN
    IF NOT EXISTS (SELECT 1 FROM jsonb_array_elements(data->'people') person
                   WHERE person->>'id' = current_setting('planb.exp_member'))
       OR (SELECT count(*) FROM public.plan_expense_participants
           WHERE expense_id = current_setting('planb.exp_first')::uuid) <> 3 THEN
        RAISE EXCEPTION 'La baja destruyó identidad o partes históricas';
    END IF;
END;
$$;

-- Exmiembro y ajeno: no leen ninguna tabla ni RPC y no pueden crear.
DO $$
DECLARE actor text; p uuid := current_setting('planb.exp_plan')::uuid;
BEGIN
    FOREACH actor IN ARRAY ARRAY[current_setting('planb.exp_member'), current_setting('planb.exp_outsider')] LOOP
        PERFORM set_config('request.jwt.claim.sub', actor, true);
        IF EXISTS (SELECT 1 FROM public.plan_expenses WHERE plan_id = p)
           OR EXISTS (SELECT 1 FROM public.plan_expense_participants WHERE expense_id = current_setting('planb.exp_first')::uuid)
           OR EXISTS (SELECT 1 FROM public.plans WHERE id = p) THEN
            RAISE EXCEPTION 'RLS filtró datos a un exmiembro/ajeno';
        END IF;
        BEGIN
            PERFORM public.get_plan_expenses(p);
            RAISE EXCEPTION 'Exmiembro/ajeno leyó resumen';
        EXCEPTION WHEN SQLSTATE 'PT404' THEN NULL; END;
        BEGIN
            PERFORM public.create_plan_expense(p, 'Ataque', 10, current_setting('planb.exp_owner')::uuid, '{}'::uuid[], true);
            RAISE EXCEPTION 'Exmiembro/ajeno creó gasto';
        EXCEPTION WHEN SQLSTATE 'PT404' THEN NULL; END;
    END LOOP;
    PERFORM set_config('request.jwt.claim.sub', '', true);
    BEGIN
        PERFORM public.get_plan_expenses(p);
        RAISE EXCEPTION 'Identidad nula leyó resumen';
    EXCEPTION WHEN insufficient_privilege THEN NULL; END;
END;
$$;
RESET ROLE;

DO $$
DECLARE r text; t text;
BEGIN
    FOREACH r IN ARRAY ARRAY['anon', 'authenticated', 'service_role', 'planb_django_runtime'] LOOP
        FOREACH t IN ARRAY ARRAY['public.plan_expenses', 'public.plan_expense_participants'] LOOP
            IF has_table_privilege(r, t, 'INSERT') OR has_table_privilege(r, t, 'UPDATE') OR has_table_privilege(r, t, 'DELETE')
               OR has_any_column_privilege(r, t, 'INSERT') OR has_any_column_privilege(r, t, 'UPDATE') THEN
                RAISE EXCEPTION 'DML directo excesivo: % %', r, t;
            END IF;
            IF r <> 'authenticated' AND has_table_privilege(r, t, 'SELECT') THEN
                RAISE EXCEPTION 'Lectura excesiva: % %', r, t;
            END IF;
            IF NOT (SELECT relrowsecurity FROM pg_class WHERE oid = t::regclass) THEN
                RAISE EXCEPTION 'RLS deshabilitado: %', t;
            END IF;
        END LOOP;
        IF r <> 'authenticated' AND (
            has_function_privilege(r, 'public.get_plan_expenses(uuid)', 'EXECUTE')
            OR has_function_privilege(r, 'public.create_plan_expense(uuid,text,numeric,uuid,uuid[],boolean)', 'EXECUTE')
        ) THEN RAISE EXCEPTION 'EXECUTE excesivo: %', r; END IF;
    END LOOP;
    IF EXISTS (SELECT 1 FROM public.plan_expenses e WHERE e.plan_id = current_setting('planb.exp_plan')::uuid
               AND e.amount <> (SELECT sum(ep.share_amount) FROM public.plan_expense_participants ep WHERE ep.expense_id = e.id)) THEN
        RAISE EXCEPTION 'Partes y monto no coinciden';
    END IF;
END;
$$;
ROLLBACK;
SELECT 'OK: gastos, centavos, permisos e historial; fixtures revertidas.' AS resultado;
