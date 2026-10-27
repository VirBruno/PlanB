-- INTEGRACIÓN REVERSIBLE: sólo Supabase local/de pruebas aislado, como administrador.
-- Requiere todas las migraciones hasta 202610260002. NO ejecutar en el compartido.
BEGIN;

CREATE FUNCTION planb_private.test_confirmation_notification_failure()
RETURNS trigger LANGUAGE plpgsql SET search_path = '' AS $$
BEGIN
    IF NEW.type = 'plan_confirmed'
       AND NEW.plan_id::text = current_setting('planb.confirm_fail_plan', true)
       AND NEW.user_id::text = current_setting('planb.confirm_fail_user', true) THEN
        RAISE EXCEPTION USING ERRCODE = 'PT500', MESSAGE = 'Fallo inducido de confirmación';
    END IF;
    RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION planb_private.test_confirmation_notification_failure() FROM PUBLIC;
CREATE TRIGGER test_confirmation_notification_failure BEFORE INSERT ON public.notifications
    FOR EACH ROW EXECUTE FUNCTION planb_private.test_confirmation_notification_failure();

DO $$
DECLARE
    owner_id uuid := gen_random_uuid(); member_id uuid := gen_random_uuid();
    former_id uuid := gen_random_uuid(); outsider_id uuid := gen_random_uuid(); newcomer_id uuid := gen_random_uuid();
    target_group uuid; target_plan uuid; atomic_plan uuid;
    chosen bigint; alternative bigint; atomic_chosen bigint;
    first_notice uuid; invitation uuid; initial_created timestamptz;
    initial_date timestamptz := statement_timestamp() + interval '10 days';
    changed_date timestamptz := statement_timestamp() + interval '12 hours';
    before_unread bigint; recipient_id uuid;
BEGIN
    INSERT INTO auth.users(id, aud, role, email, raw_user_meta_data, created_at, updated_at)
    SELECT u.id, 'authenticated', 'authenticated', u.id::text || '@example.invalid',
        jsonb_build_object('username', 'C_' || substr(replace(u.id::text, '-', ''), 1, 20)), now(), now()
    FROM unnest(ARRAY[owner_id, member_id, former_id, outsider_id, newcomer_id]) AS u(id);
    PERFORM set_config('request.jwt.claim.sub', owner_id::text, true);
    SET LOCAL ROLE authenticated;
    target_group := public.create_group('Confirmaciones de prueba', NULL);
    INSERT INTO public.plans(name, description, status, group_id, created_by)
    VALUES ('Mates a la plaza', 'Prueba reversible', true, target_group, owner_id) RETURNING id INTO target_plan;
    RESET ROLE;
    INSERT INTO public.group_members(group_id, user_id, role)
    VALUES (target_group, member_id, 'member'), (target_group, former_id, 'member');
    INSERT INTO public.proposals(tittle, plan_id, created_by, date_pick, type, posicion)
    VALUES ('Propuesta original', target_plan, owner_id, initial_date, 'juntada', 'SRID=4326;POINT(-58.38 -34.60)')
    RETURNING id INTO chosen;
    INSERT INTO public.proposals(tittle, plan_id, created_by, date_pick, type, posicion)
    VALUES ('Otra propuesta', target_plan, member_id, changed_date, 'juntada', 'SRID=4326;POINT(-58.40 -34.61)')
    RETURNING id INTO alternative;
    DELETE FROM public.group_members WHERE group_id = target_group AND user_id = former_id;
    IF EXISTS (SELECT 1 FROM public.notifications WHERE plan_id = target_plan) THEN
        RAISE EXCEPTION 'Se notificó antes de confirmar';
    END IF;

    -- La RPC conserva los permisos; no hay INSERT directo ni acceso anónimo.
    IF has_table_privilege('authenticated', 'public.notifications', 'INSERT')
       OR has_table_privilege('authenticated', 'public.notifications', 'UPDATE')
       OR has_function_privilege('anon', 'public.confirm_plan_proposal(uuid,bigint)', 'EXECUTE')
       OR NOT (SELECT relrowsecurity FROM pg_class WHERE oid = 'public.notifications'::regclass) THEN
        RAISE EXCEPTION 'Se debilitaron permisos o RLS';
    END IF;
    FOREACH recipient_id IN ARRAY ARRAY[member_id, outsider_id, former_id] LOOP
        PERFORM set_config('request.jwt.claim.sub', recipient_id::text, true);
        SET LOCAL ROLE authenticated;
        BEGIN
            PERFORM public.confirm_plan_proposal(target_plan, chosen);
            RAISE EXCEPTION 'No-owner pudo confirmar';
        EXCEPTION WHEN insufficient_privilege THEN NULL; END;
        RESET ROLE;
    END LOOP;
    PERFORM set_config('request.jwt.claim.sub', owner_id::text, true);
    SET LOCAL ROLE authenticated;
    IF public.confirm_plan_proposal(target_plan, chosen) <> chosen THEN
        RAISE EXCEPTION 'Cambió el contrato de retorno';
    END IF;
    RESET ROLE;
    IF NOT EXISTS (SELECT 1 FROM public.plans WHERE id = target_plan AND status IS TRUE
                   AND selected_proposal_id = chosen AND scheduled_at = initial_date AND confirmed_at IS NOT NULL) THEN
        RAISE EXCEPTION 'No se fijó la decisión y fecha definitiva';
    END IF;
    IF (SELECT count(*) FROM public.notifications WHERE plan_id = target_plan AND type = 'plan_confirmed') <> 2
       OR NOT EXISTS (SELECT 1 FROM public.notifications WHERE plan_id = target_plan AND type = 'plan_confirmed' AND user_id = owner_id)
       OR NOT EXISTS (SELECT 1 FROM public.notifications WHERE plan_id = target_plan AND type = 'plan_confirmed' AND user_id = member_id)
       OR EXISTS (SELECT 1 FROM public.notifications WHERE user_id IN (outsider_id, former_id, newcomer_id)) THEN
        RAISE EXCEPTION 'Destinatarios incorrectos: owner, miembros actuales o outsiders';
    END IF;
    IF EXISTS (SELECT 1 FROM public.notifications WHERE plan_id = target_plan
               AND (payload->>'plan_name' IS DISTINCT FROM 'Mates a la plaza'
                    OR (payload->>'scheduled_at')::timestamptz IS DISTINCT FROM initial_date)) THEN
        RAISE EXCEPTION 'Snapshot de confirmación incorrecto';
    END IF;
    -- Confirmación inmediata incluso a más de 24h: no depende del job de recordatorios.
    SET LOCAL ROLE service_role;
    PERFORM public.generate_plan_reminders();
    RESET ROLE;
    IF EXISTS (SELECT 1 FROM public.notifications WHERE plan_id = target_plan AND type = 'plan_reminder') THEN
        RAISE EXCEPTION 'El recordatorio ignoró su ventana';
    END IF;

    SELECT id, created_at INTO first_notice, initial_created FROM public.notifications
        WHERE plan_id = target_plan AND user_id = owner_id AND type = 'plan_confirmed';
    SET LOCAL ROLE authenticated;
    BEGIN
        PERFORM public.confirm_plan_proposal(target_plan, chosen);
        RAISE EXCEPTION 'Retry de confirmación no conservó el rechazo existente';
    EXCEPTION WHEN invalid_parameter_value THEN NULL; END;
    BEGIN
        PERFORM public.confirm_plan_proposal(target_plan, alternative);
        RAISE EXCEPTION 'Cambió propuesta sin reabrir';
    EXCEPTION WHEN invalid_parameter_value THEN NULL; END;
    RESET ROLE;
    BEGIN
        INSERT INTO public.notifications(user_id, type, plan_id) VALUES (owner_id, 'plan_confirmed', target_plan);
        RAISE EXCEPTION 'Falta unicidad PostgreSQL para INSERTs/retries concurrentes';
    EXCEPTION WHEN unique_violation THEN NULL; END;
    BEGIN
        INSERT INTO public.notifications(user_id, type) VALUES (owner_id, 'plan_confirmed');
        RAISE EXCEPTION 'Confirmación sin referencia al plan';
    EXCEPTION WHEN check_violation THEN NULL; END;
    BEGIN
        INSERT INTO public.notifications(user_id, type, plan_id, invitation_id)
        VALUES (owner_id, 'plan_confirmed', target_plan, gen_random_uuid());
        RAISE EXCEPTION 'Se mezclaron referencias de plan e invitación';
    EXCEPTION WHEN check_violation THEN NULL; END;
    BEGIN
        INSERT INTO public.notifications(user_id, type, plan_id) VALUES (owner_id, 'other_notice', target_plan);
        RAISE EXCEPTION 'Otro tipo utilizó indebidamente plan_id';
    EXCEPTION WHEN check_violation THEN NULL; END;

    -- Reapertura existente: permite cambiar propuesta/fecha y confirmar otra vez.
    SET LOCAL ROLE authenticated;
    PERFORM public.reopen_plan_decision(target_plan);
    RESET ROLE;
    INSERT INTO public.group_members(group_id, user_id, role) VALUES (target_group, newcomer_id, 'member');
    SET LOCAL ROLE authenticated;
    PERFORM public.confirm_plan_proposal(target_plan, alternative);
    RESET ROLE;
    IF (SELECT count(*) FROM public.notifications WHERE plan_id = target_plan AND type = 'plan_confirmed') <> 3
       OR NOT EXISTS (SELECT 1 FROM public.notifications WHERE id = first_notice AND created_at = initial_created
                      AND (payload->>'scheduled_at')::timestamptz = initial_date) THEN
        RAISE EXCEPTION 'Reconfirmación duplicó o sobrescribió avisos históricos';
    END IF;
    SET LOCAL ROLE service_role;
    PERFORM public.generate_plan_reminders();
    PERFORM public.generate_plan_reminders();
    RESET ROLE;
    IF (SELECT count(*) FROM public.notifications WHERE plan_id = target_plan AND type = 'plan_reminder') <> 3 THEN
        RAISE EXCEPTION 'Confirmación bloqueó o duplicó los recordatorios';
    END IF;

    -- Atomicidad: un fallo al notificar al último miembro revierte plan y avisos.
    INSERT INTO public.plans(name, description, status, group_id, created_by)
    VALUES ('Confirmación atómica', 'Prueba reversible', true, target_group, owner_id) RETURNING id INTO atomic_plan;
    INSERT INTO public.proposals(tittle, plan_id, created_by, date_pick, type, posicion)
    VALUES ('Propuesta atómica', atomic_plan, owner_id, changed_date, 'juntada', 'SRID=4326;POINT(-58.38 -34.60)')
    RETURNING id INTO atomic_chosen;
    -- Se preservan las validaciones de propuesta propia del plan y ganador positivo.
    SET LOCAL ROLE authenticated;
    BEGIN
        PERFORM public.confirm_plan_proposal(atomic_plan, chosen);
        RAISE EXCEPTION 'Se aceptó propuesta de otro plan';
    EXCEPTION WHEN invalid_parameter_value THEN NULL; END;
    RESET ROLE;
    UPDATE public.plans SET election_method = 'votes' WHERE id = atomic_plan;
    SET LOCAL ROLE authenticated;
    BEGIN
        PERFORM public.confirm_plan_proposal(atomic_plan, atomic_chosen);
        RAISE EXCEPTION 'Se confirmó sin votos positivos';
    EXCEPTION WHEN invalid_parameter_value THEN NULL; END;
    RESET ROLE;
    UPDATE public.plans SET election_method = 'combat' WHERE id = atomic_plan;
    SET LOCAL ROLE authenticated;
    BEGIN
        PERFORM public.confirm_plan_proposal(atomic_plan, atomic_chosen);
        RAISE EXCEPTION 'Se confirmó sin puntaje positivo';
    EXCEPTION WHEN invalid_parameter_value THEN NULL; END;
    RESET ROLE;
    UPDATE public.plans SET election_method = 'ideal' WHERE id = atomic_plan;
    PERFORM set_config('planb.confirm_fail_plan', atomic_plan::text, true);
    PERFORM set_config('planb.confirm_fail_user', (SELECT user_id::text FROM public.group_members
                       WHERE group_id = target_group ORDER BY user_id DESC LIMIT 1), true);
    SET LOCAL ROLE authenticated;
    BEGIN
        PERFORM public.confirm_plan_proposal(atomic_plan, atomic_chosen);
        RAISE EXCEPTION 'No se produjo el fallo inducido';
    EXCEPTION WHEN SQLSTATE 'PT500' THEN NULL; END;
    RESET ROLE;
    IF EXISTS (SELECT 1 FROM public.plans WHERE id = atomic_plan
               AND (selected_proposal_id IS NOT NULL OR scheduled_at IS NOT NULL OR confirmed_at IS NOT NULL))
       OR EXISTS (SELECT 1 FROM public.notifications WHERE plan_id = atomic_plan) THEN
        RAISE EXCEPTION 'Falló la atomicidad de confirmación y notificaciones';
    END IF;
    PERFORM set_config('planb.confirm_fail_plan', '', true);
    SET LOCAL ROLE authenticated;
    PERFORM public.confirm_plan_proposal(atomic_plan, atomic_chosen);
    invitation := public.create_group_invitation(target_group, outsider_id);
    RESET ROLE;
    IF (SELECT count(*) FROM public.notifications WHERE plan_id = atomic_plan AND type = 'plan_confirmed') <> 3 THEN
        RAISE EXCEPTION 'Retry tras rollback no recuperó todos los avisos';
    END IF;

    -- Bandeja/RLS/badge y lectura mantienen destinatario y tipo independiente.
    SET LOCAL ROLE authenticated;
    IF NOT EXISTS (SELECT 1 FROM public.list_notifications() WHERE id = first_notice
                   AND type = 'plan_confirmed' AND plan_id = target_plan AND group_id = target_group AND plan_available) THEN
        RAISE EXCEPTION 'Falta confirmación en bandeja';
    END IF;
    SELECT count(*) INTO before_unread FROM public.notifications WHERE read_at IS NULL;
    PERFORM public.mark_notification_read(first_notice);
    PERFORM public.mark_notification_read(first_notice);
    IF (SELECT count(*) FROM public.notifications WHERE read_at IS NULL) <> before_unread - 1
       OR NOT EXISTS (SELECT 1 FROM public.notifications WHERE plan_id = target_plan AND type = 'plan_reminder' AND read_at IS NULL) THEN
        RAISE EXCEPTION 'Lectura de confirmación alteró otro tipo o el badge';
    END IF;
    RESET ROLE;
    PERFORM set_config('request.jwt.claim.sub', outsider_id::text, true);
    SET LOCAL ROLE authenticated;
    IF EXISTS (SELECT 1 FROM public.notifications WHERE plan_id IS NOT NULL)
       OR EXISTS (SELECT 1 FROM public.list_notifications() WHERE type IN ('plan_confirmed', 'plan_reminder')) THEN
        RAISE EXCEPTION 'Se filtraron avisos ajenos';
    END IF;
    BEGIN
        PERFORM public.mark_notification_read(first_notice);
        RAISE EXCEPTION 'Otro usuario marcó aviso ajeno';
    EXCEPTION WHEN SQLSTATE 'PT404' THEN NULL; END;
    PERFORM public.reject_group_invitation(invitation);
    IF NOT EXISTS (SELECT 1 FROM public.list_notifications() WHERE invitation_id = invitation
                   AND type = 'group_invitation' AND status = 'rejected' AND read_at IS NOT NULL) THEN
        RAISE EXCEPTION 'Regresión en invitaciones';
    END IF;
    RESET ROLE;
    DELETE FROM public.group_members WHERE group_id = target_group AND user_id = member_id;
    PERFORM set_config('request.jwt.claim.sub', member_id::text, true);
    SET LOCAL ROLE authenticated;
    IF NOT EXISTS (SELECT 1 FROM public.list_notifications() WHERE plan_id = target_plan AND type = 'plan_confirmed'
                   AND NOT plan_available AND group_id IS NULL AND group_name IS NULL)
       OR EXISTS (SELECT 1 FROM public.plans WHERE id = target_plan) THEN
        RAISE EXCEPTION 'No se retiró acceso al perder membresía';
    END IF;
    RESET ROLE;
    PERFORM set_config('request.jwt.claim.sub', owner_id::text, true);
    PERFORM public.reopen_plan_decision(target_plan);
    DELETE FROM public.plans WHERE id = target_plan;
    IF EXISTS (SELECT 1 FROM public.notifications WHERE plan_id = target_plan) THEN
        RAISE EXCEPTION 'CASCADE no eliminó ambos tipos de aviso';
    END IF;
    RAISE NOTICE 'plan_confirmed: transición, destinatarios, unicidad, atomicidad, RLS y convivencia OK';
END;
$$;
ROLLBACK;
