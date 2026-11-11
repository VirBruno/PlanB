-- Integración: ejecutar solo en una base de pruebas con las migraciones aplicadas.
-- Los datos temporales se revierten al finalizar.
BEGIN;

DO $$
BEGIN
    IF has_schema_privilege('authenticated', 'planb_private', 'USAGE')
       OR has_table_privilege('authenticated', 'planb_private.uno_victories', 'SELECT')
       OR has_table_privilege('authenticated', 'planb_private.uno_victories', 'INSERT')
       OR has_table_privilege('anon', 'planb_private.uno_victories', 'INSERT')
       OR has_table_privilege('planb_django_runtime', 'planb_private.uno_victories', 'INSERT') THEN
        RAISE EXCEPTION 'El almacenamiento privado no debe ser accesible desde clientes ni runtime';
    END IF;
    IF has_function_privilege('authenticated', 'public.confirm_uno_plan_proposal(uuid,bigint)', 'EXECUTE')
       OR has_function_privilege('anon', 'public.confirm_uno_plan_proposal(uuid,bigint)', 'EXECUTE') THEN
        RAISE EXCEPTION 'La confirmación de UNO aún no debe estar habilitada para clientes';
    END IF;
END;
$$;

SELECT set_config('planb.uno_test_user', gen_random_uuid()::text, true);
SELECT set_config('planb.uno_test_match', gen_random_uuid()::text, true);

INSERT INTO auth.users(id, aud, role, email, raw_user_meta_data, created_at, updated_at)
SELECT id, 'authenticated', 'authenticated', id::text || '@example.invalid',
       jsonb_build_object('username', 'U_' || substr(replace(id::text, '-', ''), 1, 20)), now(), now()
FROM (VALUES (current_setting('planb.uno_test_user')::uuid)) AS fixture(id);

SELECT set_config('request.jwt.claim.sub', current_setting('planb.uno_test_user'), true);
SET LOCAL ROLE authenticated;
SELECT set_config('planb.uno_test_group', public.create_group('Grupo UNO de prueba', NULL)::text, true);
WITH created AS (
    INSERT INTO public.plans(name, description, status, group_id, created_by)
    VALUES ('Plan UNO de prueba', 'Datos temporales', true,
            current_setting('planb.uno_test_group')::uuid, auth.uid()) RETURNING id
)
SELECT set_config('planb.uno_test_plan', id::text, true) FROM created;
WITH created AS (
    INSERT INTO public.proposals(tittle, created_by, plan_id, type, posicion)
    VALUES ('Propuesta UNO', auth.uid(), current_setting('planb.uno_test_plan')::uuid,
            'juntada', 'SRID=4326;POINT(-58.38 -34.6)') RETURNING id
)
SELECT set_config('planb.uno_test_proposal', id::text, true) FROM created;
RESET ROLE;

INSERT INTO planb_private.uno_victories(match_id, proposal_id, player_id)
VALUES (current_setting('planb.uno_test_match')::uuid,
        current_setting('planb.uno_test_proposal')::bigint,
        current_setting('planb.uno_test_user')::uuid);

DO $$
BEGIN
    BEGIN
        INSERT INTO planb_private.uno_victories(match_id, proposal_id, player_id)
        VALUES (current_setting('planb.uno_test_match')::uuid,
                current_setting('planb.uno_test_proposal')::bigint,
                current_setting('planb.uno_test_user')::uuid);
        RAISE EXCEPTION 'Se permitió registrar dos veces la misma partida';
    EXCEPTION WHEN unique_violation THEN NULL;
    END;
END;
$$;

-- El administrador de la prueba invoca el borrador directamente; el rol
-- authenticated no tiene permiso para llamarlo mientras esté incompleto.
SELECT set_config('planb.uno_test_member', gen_random_uuid()::text, true);
INSERT INTO auth.users(id, aud, role, email, raw_user_meta_data, created_at, updated_at)
SELECT id, 'authenticated', 'authenticated', id::text || '@example.invalid',
       jsonb_build_object('username', 'U_' || substr(replace(id::text, '-', ''), 1, 20)), now(), now()
FROM (VALUES (current_setting('planb.uno_test_member')::uuid)) AS fixture(id);
INSERT INTO public.group_members(group_id, user_id, role)
VALUES (current_setting('planb.uno_test_group')::uuid, current_setting('planb.uno_test_member')::uuid, 'member');
UPDATE public.proposals SET date_pick = '2035-01-01 18:00:00+00'
WHERE id = current_setting('planb.uno_test_proposal')::bigint;
UPDATE public.plans SET election_method = 'combat' WHERE id = current_setting('planb.uno_test_plan')::uuid;

SELECT set_config('request.jwt.claim.sub', current_setting('planb.uno_test_member'), true);
SET LOCAL ROLE authenticated;
WITH created AS (
    INSERT INTO public.proposals(tittle, created_by, plan_id, type, posicion, date_pick)
    VALUES ('Otra propuesta UNO', auth.uid(), current_setting('planb.uno_test_plan')::uuid,
            'salida', 'SRID=4326;POINT(-58.39 -34.61)', '2035-01-01 18:00:00+00') RETURNING id
)
SELECT set_config('planb.uno_test_other', id::text, true) FROM created;
RESET ROLE;

DO $$
BEGIN
    BEGIN
        PERFORM public.confirm_uno_plan_proposal(current_setting('planb.uno_test_plan')::uuid,
                                                 current_setting('planb.uno_test_proposal')::bigint);
        RAISE EXCEPTION 'Un integrante sin administración confirmó el plan';
    EXCEPTION WHEN insufficient_privilege THEN NULL;
    END;
END;
$$;
INSERT INTO planb_private.uno_victories(match_id, proposal_id, player_id)
VALUES (gen_random_uuid(), current_setting('planb.uno_test_other')::bigint,
        current_setting('planb.uno_test_member')::uuid);
SELECT set_config('request.jwt.claim.sub', current_setting('planb.uno_test_user'), true);
DO $$
BEGIN
    BEGIN
        PERFORM public.confirm_uno_plan_proposal(current_setting('planb.uno_test_plan')::uuid,
                                                 current_setting('planb.uno_test_proposal')::bigint);
        RAISE EXCEPTION 'Se confirmó una propuesta empatada';
    EXCEPTION WHEN invalid_parameter_value THEN NULL;
    END;
END;
$$;
INSERT INTO planb_private.uno_victories(match_id, proposal_id, player_id)
VALUES (gen_random_uuid(), current_setting('planb.uno_test_proposal')::bigint,
        current_setting('planb.uno_test_user')::uuid);
DO $$
BEGIN
    IF public.confirm_uno_plan_proposal(current_setting('planb.uno_test_plan')::uuid,
                                        current_setting('planb.uno_test_proposal')::bigint)
       IS DISTINCT FROM current_setting('planb.uno_test_proposal')::bigint THEN
        RAISE EXCEPTION 'No se confirmó la propuesta líder';
    END IF;
    IF (SELECT count(*) FROM public.notifications
        WHERE plan_id = current_setting('planb.uno_test_plan')::uuid AND type = 'plan_confirmed') <> 2 THEN
        RAISE EXCEPTION 'No se notificó a los integrantes';
    END IF;
END;
$$;

ROLLBACK;
SELECT 'OK: permisos, duplicados, empate y líder único; datos revertidos.' AS resultado;
