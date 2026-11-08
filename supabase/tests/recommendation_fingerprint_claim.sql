-- MANUAL: sólo Supabase local/de pruebas, después de 202611060001.
-- No ejecutar remotamente en esta tarea. Los fixtures se revierten al terminar.
-- Complementa plan_recommendations.sql, que sigue probando la firma legacy.
BEGIN;
SELECT set_config('planb.fp_owner', gen_random_uuid()::text, true);
INSERT INTO auth.users(id, aud, role, email, raw_user_meta_data, created_at, updated_at)
VALUES (current_setting('planb.fp_owner')::uuid, 'authenticated', 'authenticated',
    current_setting('planb.fp_owner') || '@example.invalid',
    jsonb_build_object('username', 'Fp_' || substr(replace(current_setting('planb.fp_owner'), '-', ''), 1, 20)),
    now(), now());
SELECT set_config('request.jwt.claim.sub', current_setting('planb.fp_owner'), true);
SET LOCAL ROLE authenticated;
SELECT set_config('planb.fp_group', public.create_group('Fingerprint claim', NULL)::text, true);
WITH created AS (
    INSERT INTO public.plans(name, description, status, group_id, created_by)
    VALUES ('Fixture', NULL, true, current_setting('planb.fp_group')::uuid, auth.uid()) RETURNING id
) SELECT set_config('planb.fp_plan', id::text, true) FROM created;
WITH created AS (
    INSERT INTO public.proposals(tittle, description, date_pick, created_by, plan_id, type, posicion)
    VALUES ('Merienda', NULL, now() + interval '1 day', auth.uid(),
        current_setting('planb.fp_plan')::uuid, 'juntada', 'SRID=4326;POINT(-58.38 -34.6)') RETURNING id
) SELECT set_config('planb.fp_proposal', id::text, true) FROM created;
SELECT public.confirm_plan_proposal(current_setting('planb.fp_plan')::uuid,
    current_setting('planb.fp_proposal')::bigint);
RESET ROLE;
SELECT set_config('planb.fp_confirmed', confirmed_at::text, true)
FROM public.plans WHERE id = current_setting('planb.fp_plan')::uuid;

-- La nueva firma tiene exactamente el mismo acceso backend que la anterior.
DO $$
DECLARE signature text := 'public.claim_plan_recommendations(uuid,bigint,timestamptz,boolean,text)';
BEGIN
    IF NOT has_function_privilege('service_role', signature, 'EXECUTE')
       OR has_function_privilege('anon', signature, 'EXECUTE')
       OR has_function_privilege('authenticated', signature, 'EXECUTE')
       OR has_function_privilege('planb_django_runtime', signature, 'EXECUTE') THEN
        RAISE EXCEPTION 'ACL inesperado para la nueva firma';
    END IF;
END $$;
SET LOCAL ROLE authenticated;
DO $$ BEGIN
    BEGIN
        PERFORM public.claim_plan_recommendations(current_setting('planb.fp_plan')::uuid,
            current_setting('planb.fp_proposal')::bigint,
            current_setting('planb.fp_confirmed')::timestamptz, false, repeat('a', 64));
        RAISE EXCEPTION 'authenticated pudo reservar';
    EXCEPTION WHEN insufficient_privilege THEN NULL; END;
END $$;
RESET ROLE;

-- Se invocan las RPC como propietario de los fixtures para inspeccionar filas
-- entre pasos. La prueba ACL anterior verifica la restricción real del endpoint.
DO $$
DECLARE
    p uuid := current_setting('planb.fp_plan')::uuid;
    proposal bigint := current_setting('planb.fp_proposal')::bigint;
    confirmed timestamptz := current_setting('planb.fp_confirmed')::timestamptz;
    a text := repeat('a', 64); b text := repeat('b', 64); c text := repeat('c', 64);
    ticket uuid; previous_ticket uuid; before_row public.plan_recommendations%ROWTYPE;
    after_row public.plan_recommendations%ROWTYPE;
    items jsonb := '[{"provider":"geoapify","provider_place_id":"fixture-old","name":"Café fixture",
        "categories":["catering.cafe"],"latitude":-34.6,"longitude":-58.38,
        "distance_meters":0,"rank":1}]'::jsonb;
    base jsonb := '{"latitude":-34.6,"longitude":-58.38}'::jsonb;
    criteria_a jsonb;
BEGIN
    criteria_a := jsonb_build_object('input_fingerprint', a, 'summary', 'Texto anterior.',
        'place_intents', jsonb_build_array('cafe'), 'categories', jsonb_build_array('catering.cafe'));
    BEGIN
        PERFORM public.claim_plan_recommendations(p, proposal, confirmed, false, 'invalid');
        RAISE EXCEPTION 'Se aceptó fingerprint inválido';
    EXCEPTION WHEN invalid_parameter_value THEN NULL; END;
    IF public.claim_plan_recommendations(p, proposal, confirmed + interval '1 second', false, a)
       IS NOT NULL THEN RAISE EXCEPTION 'Se aceptó decisión diferente'; END IF;

    ticket := public.claim_plan_recommendations(p, proposal, confirmed, false, a);
    IF ticket IS NULL THEN RAISE EXCEPTION 'No reservó primera generación'; END IF;
    IF NOT EXISTS (SELECT 1 FROM public.plan_recommendations WHERE plan_id = p
        AND criteria ->> 'attempted_input_fingerprint' = a) THEN
        RAISE EXCEPTION 'No registró el intento al reservar';
    END IF;
    IF public.claim_plan_recommendations(p, proposal, confirmed, false, a) IS NOT NULL
       OR public.claim_plan_recommendations(p, proposal, confirmed, true, a) IS NOT NULL
       OR public.claim_plan_recommendations(p, proposal, confirmed, false, b) IS NOT NULL THEN
        RAISE EXCEPTION 'Superpuso trabajo con pending reciente';
    END IF;
    previous_ticket := ticket;
    UPDATE public.plan_recommendations SET updated_at = now() - interval '3 minutes' WHERE plan_id = p;
    IF public.claim_plan_recommendations(p, proposal, confirmed, false, a) IS NOT NULL THEN
        RAISE EXCEPTION 'Pending vencido reintentó automáticamente el mismo fingerprint';
    END IF;
    ticket := public.claim_plan_recommendations(p, proposal, confirmed, true, a);
    IF ticket IS NULL OR ticket = previous_ticket THEN RAISE EXCEPTION 'No recuperó pending explícitamente'; END IF;
    IF public.finish_plan_recommendations(p, previous_ticket, '[]'::jsonb) THEN
        RAISE EXCEPTION 'Un ticket anterior pudo completar';
    END IF;
    IF NOT public.finish_plan_recommendations(p, ticket, items, NULL, base, criteria_a) THEN
        RAISE EXCEPTION 'No persistió primera generación';
    END IF;

    SELECT * INTO before_row FROM public.plan_recommendations WHERE plan_id = p;
    IF public.claim_plan_recommendations(p, proposal, confirmed, false, a) IS NOT NULL
       OR public.claim_plan_recommendations(p, proposal, confirmed, true, a) IS NOT NULL THEN
        RAISE EXCEPTION 'Reservó un snapshot vigente';
    END IF;
    SELECT * INTO after_row FROM public.plan_recommendations WHERE plan_id = p;
    IF to_jsonb(before_row) IS DISTINCT FROM to_jsonb(after_row) THEN
        RAISE EXCEPTION 'Snapshot vigente fue modificado';
    END IF;

    ticket := public.claim_plan_recommendations(p, proposal, confirmed, false, b);
    IF ticket IS NULL THEN RAISE EXCEPTION 'No reservó fingerprint nuevo'; END IF;
    SELECT * INTO after_row FROM public.plan_recommendations WHERE plan_id = p;
    IF after_row.items IS DISTINCT FROM before_row.items
       OR after_row.base_location IS DISTINCT FROM before_row.base_location
       OR after_row.generated_at IS DISTINCT FROM before_row.generated_at
       OR (after_row.criteria - 'attempted_input_fingerprint') IS DISTINCT FROM before_row.criteria
       OR after_row.criteria ->> 'attempted_input_fingerprint' IS DISTINCT FROM b THEN
        RAISE EXCEPTION 'La reserva dañó el snapshot previo';
    END IF;
    IF public.claim_plan_recommendations(p, proposal, confirmed, false, b) IS NOT NULL THEN
        RAISE EXCEPTION 'Solicitud simultánea duplicó la reserva';
    END IF;
    PERFORM public.finish_plan_recommendations(p, ticket, '[]'::jsonb, 'timeout');
    SELECT * INTO after_row FROM public.plan_recommendations WHERE plan_id = p;
    IF after_row.status <> 'failed' OR after_row.error_code <> 'timeout'
       OR after_row.items IS DISTINCT FROM before_row.items
       OR after_row.base_location IS DISTINCT FROM before_row.base_location
       OR after_row.generated_at IS DISTINCT FROM before_row.generated_at
       OR after_row.criteria ->> 'input_fingerprint' IS DISTINCT FROM a
       OR after_row.criteria ->> 'summary' IS DISTINCT FROM 'Texto anterior.'
       OR after_row.criteria ->> 'attempted_input_fingerprint' IS DISTINCT FROM b THEN
        RAISE EXCEPTION 'Fallo perdió el snapshot o el registro del intento';
    END IF;
    -- Competidor que había leído ready antes de que el primer intento fallara.
    IF public.claim_plan_recommendations(p, proposal, confirmed, false, b) IS NOT NULL THEN
        RAISE EXCEPTION 'Solicitud demorada volvió a generar tras el fallo';
    END IF;
    ticket := public.claim_plan_recommendations(p, proposal, confirmed, true, b);
    IF ticket IS NULL THEN RAISE EXCEPTION 'No permitió reintento explícito'; END IF;
    PERFORM public.finish_plan_recommendations(p, ticket, '[]'::jsonb, 'http_error');
    ticket := public.claim_plan_recommendations(p, proposal, confirmed, false, c);
    IF ticket IS NULL THEN RAISE EXCEPTION 'Fingerprint nuevo quedó bloqueado por fallo anterior'; END IF;
    PERFORM public.finish_plan_recommendations(p, ticket, '[]'::jsonb, NULL, base,
        jsonb_build_object('input_fingerprint', c, 'summary', 'Texto nuevo.'));
    -- Competidor que llega después de que el primero ya terminó con éxito.
    IF public.claim_plan_recommendations(p, proposal, confirmed, false, c) IS NOT NULL THEN
        RAISE EXCEPTION 'Solicitud demorada volvió a generar tras el éxito';
    END IF;

    UPDATE public.plan_recommendations SET criteria = '{"summary":"Legacy"}'::jsonb WHERE plan_id = p;
    IF public.claim_plan_recommendations(p, proposal, confirmed, false, a) IS NOT NULL THEN
        RAISE EXCEPTION 'Se invalidó un ready legacy sólo por falta de fingerprint';
    END IF;
    ticket := public.claim_plan_recommendations(p, proposal, confirmed, true, a);
    IF ticket IS NULL THEN RAISE EXCEPTION 'No permitió actualizar legacy explícitamente'; END IF;
    IF NOT EXISTS (SELECT 1 FROM public.plan_recommendations WHERE plan_id = p
        AND criteria ->> 'summary' = 'Legacy' AND criteria ->> 'attempted_input_fingerprint' = a) THEN
        RAISE EXCEPTION 'No preservó criterios legacy durante actualización';
    END IF;
    PERFORM public.finish_plan_recommendations(p, ticket, items, NULL, base, criteria_a);
    IF public.claim_plan_recommendations(p, proposal, confirmed, false, a) IS NOT NULL THEN
        RAISE EXCEPTION 'No incorporó fingerprint al actualizar legacy';
    END IF;
    -- La firma anterior conserva el contrato de reintento existente.
    ticket := public.claim_plan_recommendations(p, proposal, confirmed, true);
    IF ticket IS NULL THEN RAISE EXCEPTION 'Se rompió la firma legacy'; END IF;
    PERFORM public.finish_plan_recommendations(p, ticket, '[]'::jsonb, 'timeout');
    IF NOT EXISTS (SELECT 1 FROM public.plans WHERE id = p AND status IS TRUE
        AND selected_proposal_id = proposal AND confirmed_at = confirmed) THEN
        RAISE EXCEPTION 'Se modificó la confirmación';
    END IF;
    BEGIN
        UPDATE public.proposals SET tittle = 'No permitido' WHERE id = proposal;
        RAISE EXCEPTION 'Se permitió editar propuesta confirmada';
    EXCEPTION WHEN invalid_parameter_value THEN NULL; END;
END $$;
ROLLBACK;

-- Concurrencia real (opcional, sólo local): usar un plan fixture ya confirmado y
-- un snapshot ready con input_fingerprint A, visibles en DOS sesiones distintas.
-- Sesión 1: BEGIN; SET LOCAL ROLE service_role; SELECT claim(..., false, B);
-- Mantener la transacción abierta. Sesión 2: BEGIN; SET LOCAL ROLE service_role;
-- SELECT claim(..., false, B); debe esperar el lock. COMMIT en sesión 1: la 2
-- devuelve NULL al observar pending. Repetir dejando que 1 haga finish con éxito
-- o fallo antes del claim de 2: también debe devolver NULL. Usar los nombres
-- completos/5 argumentos como arriba y revertir o eliminar los fixtures locales.
