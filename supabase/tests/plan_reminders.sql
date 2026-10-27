-- INTEGRACIÓN REVERSIBLE: sólo instancia local/de pruebas aislada, como administrador.
-- Requiere TODAS las migraciones hasta 202610260002. Nunca ejecutar en el compartido.
-- El reloj es statement_timestamp(): dentro del DO permite probar límites exactos.
BEGIN;

CREATE FUNCTION planb_private.test_reminder_plan(
    p_group uuid, p_name text, p_date timestamptz, p_confirmed boolean, p_active boolean
) RETURNS uuid LANGUAGE plpgsql SET search_path = '' AS $$
DECLARE plan uuid; proposal bigint;
BEGIN
    INSERT INTO public.plans(name, description, status, group_id, created_by)
    VALUES (p_name, 'Prueba reversible de recordatorios', true, p_group, auth.uid())
    RETURNING id INTO plan;
    IF p_confirmed THEN
        INSERT INTO public.proposals(tittle, plan_id, created_by, date_pick, type, posicion)
        VALUES (p_name, plan, auth.uid(), p_date, 'juntada', 'SRID=4326;POINT(-58.38 -34.60)')
        RETURNING id INTO proposal;
        PERFORM public.confirm_plan_proposal(plan, proposal);
    ELSIF p_date IS NOT NULL THEN
        -- Fecha tentativa: NO convierte el plan en Confirmado.
        UPDATE public.plans SET scheduled_at = p_date, weather_province = 'BA',
            weather_city_id = 1, weather_city_name = 'Ciudad de prueba' WHERE id = plan;
    END IF;
    UPDATE public.plans SET status = p_active WHERE id = plan;
    RETURN plan;
END;
$$;
REVOKE ALL ON FUNCTION planb_private.test_reminder_plan(uuid, text, timestamptz, boolean, boolean) FROM PUBLIC;

CREATE FUNCTION planb_private.test_reminder_failure()
RETURNS trigger LANGUAGE plpgsql SET search_path = '' AS $$
BEGIN
    IF NEW.type = 'plan_reminder' AND NEW.payload->>'plan_name' = 'Atomicidad'
       AND NEW.user_id::text = current_setting('planb.reminder_fail_user', true) THEN
        RAISE EXCEPTION USING ERRCODE = 'PT500', MESSAGE = 'Fallo inducido del recordatorio';
    END IF;
    RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION planb_private.test_reminder_failure() FROM PUBLIC;
CREATE TRIGGER test_reminder_failure BEFORE INSERT ON public.notifications
    FOR EACH ROW EXECUTE FUNCTION planb_private.test_reminder_failure();

DO $$
DECLARE
    owner_id uuid := gen_random_uuid();
    member_id uuid := gen_random_uuid();
    former_id uuid := gen_random_uuid();
    newcomer_id uuid := gen_random_uuid();
    outsider_id uuid := gen_random_uuid();
    group_a uuid; group_b uuid;
    at_boundary uuid; in_window uuid; short_notice uuid; other_group uuid;
    far_away uuid; organizing uuid; finished uuid; inactive uuid; undated uuid; past uuid; at_now uuid;
    moment timestamptz := statement_timestamp();
    first_notification uuid; invitation uuid; invitation_notification uuid; atomic_plan uuid;
    count_before bigint; actual integer;
BEGIN
    INSERT INTO auth.users(id, aud, role, email, raw_user_meta_data, created_at, updated_at)
    SELECT u.id, 'authenticated', 'authenticated', u.id::text || '@example.invalid',
        jsonb_build_object('username', 'R_' || substr(replace(u.id::text, '-', ''), 1, 20)), now(), now()
    FROM unnest(ARRAY[owner_id, member_id, former_id, newcomer_id, outsider_id]) AS u(id);

    PERFORM set_config('request.jwt.claim.sub', owner_id::text, true);
    SET LOCAL ROLE authenticated;
    group_a := public.create_group('Recordatorios A', NULL);
    group_b := public.create_group('Recordatorios B', NULL);
    RESET ROLE;
    INSERT INTO public.group_members(group_id, user_id, role)
    VALUES (group_a, member_id, 'member'), (group_b, member_id, 'member'), (group_a, former_id, 'member');

    at_boundary := planb_private.test_reminder_plan(group_a, 'Exactamente 24h', moment + interval '24 hours', true, true);
    in_window := planb_private.test_reminder_plan(group_a, 'Dentro de ventana', moment + interval '23 hours', true, true);
    short_notice := planb_private.test_reminder_plan(group_a, 'Confirmado tarde', moment + interval '15 minutes', true, true);
    other_group := planb_private.test_reminder_plan(group_b, 'Segundo grupo', moment + interval '18 hours', true, true);
    far_away := planb_private.test_reminder_plan(group_a, 'Demasiado lejos', moment + interval '24 hours 1 second', true, true);
    organizing := planb_private.test_reminder_plan(group_a, 'En organización', moment + interval '23 hours', false, true);
    finished := planb_private.test_reminder_plan(group_a, 'Finalizado antes del proceso', moment + interval '23 hours', true, false);
    inactive := planb_private.test_reminder_plan(group_a, 'Inactivo', moment + interval '23 hours', false, false);
    undated := planb_private.test_reminder_plan(group_a, 'Sin fecha', NULL, false, true);
    past := planb_private.test_reminder_plan(group_a, 'Ya pasó', moment - interval '1 second', true, true);
    at_now := planb_private.test_reminder_plan(group_a, 'Comienza ahora', moment, true, true);

    -- Cambios DESPUÉS de confirmar y ANTES de procesar: se usa la membresía actual.
    DELETE FROM public.group_members WHERE group_id = group_a AND user_id = former_id;
    INSERT INTO public.group_members(group_id, user_id, role) VALUES (group_a, newcomer_id, 'member');

    -- No se requieren SELECT/INSERT de service_role sobre las tablas de dominio.
    IF has_table_privilege('service_role', 'public.notifications', 'INSERT')
       OR has_table_privilege('authenticated', 'public.notifications', 'INSERT')
       OR has_table_privilege('authenticated', 'public.notifications', 'UPDATE') THEN
        RAISE EXCEPTION 'Se ampliaron permisos directos de escritura';
    END IF;
    IF NOT (SELECT relrowsecurity FROM pg_class WHERE oid = 'public.notifications'::regclass) THEN
        RAISE EXCEPTION 'RLS fue deshabilitado';
    END IF;
    IF has_function_privilege('anon', 'public.generate_plan_reminders()', 'EXECUTE')
       OR has_function_privilege('authenticated', 'public.generate_plan_reminders()', 'EXECUTE')
       OR has_function_privilege('planb_django_runtime', 'public.generate_plan_reminders()', 'EXECUTE') THEN
        RAISE EXCEPTION 'Un rol de usuario/runtime puede generar recordatorios';
    END IF;
    SET LOCAL ROLE authenticated;
    BEGIN
        PERFORM public.generate_plan_reminders();
        RAISE EXCEPTION 'El navegador pudo generar recordatorios';
    EXCEPTION WHEN insufficient_privilege THEN NULL; END;
    RESET ROLE;

    -- También prueba que 24 horas significa duración, independiente del timezone SQL.
    SET LOCAL TIME ZONE 'Pacific/Auckland';
    SET LOCAL ROLE service_role;
    actual := public.generate_plan_reminders();
    RESET ROLE;
    IF actual < 11 THEN RAISE EXCEPTION 'No se generaron los 11 recordatorios esperados'; END IF;
    IF (SELECT count(*) FROM public.notifications WHERE type = 'plan_reminder'
        AND plan_id IN (at_boundary, in_window, short_notice, other_group)) <> 11 THEN
        RAISE EXCEPTION 'Cantidad incorrecta para miembros actuales/múltiples grupos';
    END IF;
    IF EXISTS (SELECT 1 FROM public.notifications WHERE type = 'plan_reminder' AND plan_id IN
               (far_away, organizing, finished, inactive, undated, past, at_now)) THEN
        RAISE EXCEPTION 'Un estado o fecha excluidos recibió recordatorio';
    END IF;
    IF EXISTS (SELECT 1 FROM public.notifications WHERE type = 'plan_reminder' AND user_id IN (outsider_id, former_id)) THEN
        RAISE EXCEPTION 'Outsider o antiguo miembro recibió recordatorio';
    END IF;
    IF (SELECT count(*) FROM public.notifications WHERE type = 'plan_reminder' AND user_id = owner_id
        AND plan_id IN (at_boundary, in_window, short_notice, other_group)) <> 4 THEN
        RAISE EXCEPTION 'El owner no recibió todos los planes';
    END IF;
    IF (SELECT count(*) FROM public.notifications WHERE type = 'plan_reminder' AND user_id = member_id
        AND plan_id IN (at_boundary, in_window, short_notice, other_group)) <> 4 THEN
        RAISE EXCEPTION 'Un miembro de varios grupos no recibió todos los planes';
    END IF;
    IF (SELECT count(*) FROM public.notifications WHERE type = 'plan_reminder' AND user_id = newcomer_id
        AND plan_id IN (at_boundary, in_window, short_notice)) <> 3 THEN
        RAISE EXCEPTION 'No se notificó al miembro incorporado antes del procesamiento';
    END IF;
    IF EXISTS (SELECT 1 FROM public.notifications n JOIN public.plans p ON p.id = n.plan_id
               WHERE n.type = 'plan_reminder' AND n.plan_id IN (at_boundary, in_window, short_notice, other_group)
                 AND ((n.payload->>'scheduled_at')::timestamptz IS DISTINCT FROM p.scheduled_at
                      OR n.payload->>'plan_name' IS DISTINCT FROM p.name)) THEN
        RAISE EXCEPTION 'La fecha/nombre del aviso no coincide con el encuentro confirmado';
    END IF;
    SET LOCAL ROLE service_role;
    IF public.generate_plan_reminders() <> 0 THEN RAISE EXCEPTION 'La repetición creó duplicados'; END IF;
    RESET ROLE;

    -- Prueba la garantía de DB que también arbitra INSERTs concurrentes.
    BEGIN
        INSERT INTO public.notifications(user_id, type, plan_id) VALUES (owner_id, 'plan_reminder', in_window);
        RAISE EXCEPTION 'El índice único permitió duplicados';
    EXCEPTION WHEN unique_violation THEN NULL; END;
    BEGIN
        INSERT INTO public.notifications(user_id, type) VALUES (owner_id, 'plan_reminder');
        RAISE EXCEPTION 'Recordatorio sin plan permitido';
    EXCEPTION WHEN check_violation THEN NULL; END;
    BEGIN
        INSERT INTO public.notifications(user_id, type, plan_id) VALUES (owner_id, 'other_type', in_window);
        RAISE EXCEPTION 'Referencia al plan incoherente con el tipo';
    EXCEPTION WHEN check_violation THEN NULL; END;

    -- Al entrar en la ventana en una ejecución posterior, el plan lejano es elegible.
    PERFORM public.reopen_plan_decision(far_away);
    UPDATE public.proposals SET date_pick = moment + interval '22 hours' WHERE plan_id = far_away;
    PERFORM public.confirm_plan_proposal(far_away, (SELECT id FROM public.proposals WHERE plan_id = far_away));
    SET LOCAL ROLE service_role;
    IF public.generate_plan_reminders() <> 3 THEN RAISE EXCEPTION 'No detectó plan que entró en ventana'; END IF;
    RESET ROLE;

    -- Nuevo miembro durante la ventana: recibe uno en la siguiente ejecución;
    -- los destinatarios existentes no vuelven a recibirlo.
    INSERT INTO public.group_members(group_id, user_id, role) VALUES (group_b, newcomer_id, 'member');
    SET LOCAL ROLE service_role;
    IF public.generate_plan_reminders() <> 1 THEN RAISE EXCEPTION 'Membresía nueva no se resolvió idempotentemente'; END IF;
    RESET ROLE;

    -- Abrir/reconfirmar el mismo plan NO rearma el recordatorio usuario/plan/tipo.
    PERFORM public.reopen_plan_decision(short_notice);
    UPDATE public.proposals SET date_pick = moment + interval '2 hours' WHERE plan_id = short_notice;
    PERFORM public.confirm_plan_proposal(short_notice, (SELECT id FROM public.proposals WHERE plan_id = short_notice));
    SET LOCAL ROLE service_role;
    IF public.generate_plan_reminders() <> 0 THEN RAISE EXCEPTION 'Reconfirmación duplicó el aviso'; END IF;
    RESET ROLE;

    -- Un fallo después de insertar destinatarios revierte toda la operación.
    atomic_plan := planb_private.test_reminder_plan(group_a, 'Atomicidad', moment + interval '10 hours', true, true);
    PERFORM set_config('planb.reminder_fail_user', (SELECT m.user_id::text FROM public.group_members m
                       WHERE m.group_id = group_a ORDER BY m.user_id DESC LIMIT 1), true);
    SET LOCAL ROLE service_role;
    BEGIN
        PERFORM public.generate_plan_reminders();
        RAISE EXCEPTION 'No se indujo el fallo posterior a otros INSERTs';
    EXCEPTION WHEN SQLSTATE 'PT500' THEN NULL; END;
    RESET ROLE;
    IF EXISTS (SELECT 1 FROM public.notifications WHERE type = 'plan_reminder' AND plan_id = atomic_plan) THEN
        RAISE EXCEPTION 'El fallo dejó destinatarios con notificaciones parciales';
    END IF;
    PERFORM set_config('planb.reminder_fail_user', '', true);
    SET LOCAL ROLE service_role;
    IF public.generate_plan_reminders() <> 3 THEN RAISE EXCEPTION 'El reintento no recuperó el fallo atómico'; END IF;
    RESET ROLE;

    -- Invitaciones siguen generándose y conviven con los recordatorios.
    SET LOCAL ROLE authenticated;
    invitation := public.create_group_invitation(group_a, outsider_id);
    RESET ROLE;
    SELECT id INTO invitation_notification FROM public.notifications WHERE invitation_id = invitation;
    IF invitation_notification IS NULL THEN RAISE EXCEPTION 'Se rompió group_invitation'; END IF;
    SELECT id INTO first_notification FROM public.notifications
        WHERE user_id = owner_id AND plan_id = in_window AND type = 'plan_reminder';

    PERFORM set_config('request.jwt.claim.sub', outsider_id::text, true);
    SET LOCAL ROLE authenticated;
    IF EXISTS (SELECT 1 FROM public.notifications WHERE plan_id IS NOT NULL)
       OR EXISTS (SELECT 1 FROM public.list_notifications() WHERE type = 'plan_reminder') THEN
        RAISE EXCEPTION 'Outsider consultó recordatorios ajenos';
    END IF;
    BEGIN
        PERFORM public.mark_notification_read(first_notification);
        RAISE EXCEPTION 'Otro usuario marcó el recordatorio';
    EXCEPTION WHEN SQLSTATE 'PT404' THEN NULL; END;
    IF NOT EXISTS (SELECT 1 FROM public.list_notifications() WHERE id = invitation_notification AND status = 'pending') THEN
        RAISE EXCEPTION 'La invitación no aparece en la bandeja';
    END IF;
    PERFORM public.reject_group_invitation(invitation);
    IF NOT EXISTS (SELECT 1 FROM public.list_notifications() WHERE id = invitation_notification AND status = 'rejected' AND read_at IS NOT NULL) THEN
        RAISE EXCEPTION 'No funciona responder/leer invitación';
    END IF;
    RESET ROLE;

    PERFORM set_config('request.jwt.claim.sub', owner_id::text, true);
    SET LOCAL ROLE authenticated;
    IF NOT EXISTS (SELECT 1 FROM generate_series(1, 3) pages(page)
                   CROSS JOIN LATERAL public.list_notifications(pages.page) n
                   WHERE n.id = first_notification AND n.plan_id = in_window
                     AND n.group_id = group_a AND n.plan_available AND n.read_at IS NULL) THEN
        RAISE EXCEPTION 'El recordatorio no aparece en la bandeja propia';
    END IF;
    SELECT count(*) INTO count_before FROM public.notifications WHERE read_at IS NULL;
    PERFORM public.mark_notification_read(first_notification);
    PERFORM public.mark_notification_read(first_notification);
    IF (SELECT count(*) FROM public.notifications WHERE read_at IS NULL) <> count_before - 1 THEN
        RAISE EXCEPTION 'Lectura no decrementó el badge de forma idempotente';
    END IF;
    RESET ROLE;

    -- Si pierde membresía después, conserva su aviso histórico sin acceso al plan.
    DELETE FROM public.group_members WHERE group_id = group_a AND user_id = newcomer_id;
    PERFORM set_config('request.jwt.claim.sub', newcomer_id::text, true);
    SET LOCAL ROLE authenticated;
    IF EXISTS (SELECT 1 FROM generate_series(1, 3) pages(page)
               CROSS JOIN LATERAL public.list_notifications(pages.page) n
               WHERE n.plan_id = in_window
                 AND (n.plan_available OR n.group_id IS NOT NULL OR n.group_name IS NOT NULL)) THEN
        RAISE EXCEPTION 'La bandeja habilita acceso tras perder membresía';
    END IF;
    IF EXISTS (SELECT 1 FROM public.plans WHERE id = in_window) THEN
        RAISE EXCEPTION 'El detalle no está protegido por RLS';
    END IF;
    RESET ROLE;

    -- Borrar un plan elimina los recordatorios y conserva las invitaciones.
    PERFORM set_config('request.jwt.claim.sub', owner_id::text, true);
    PERFORM public.reopen_plan_decision(in_window);
    DELETE FROM public.plans WHERE id = in_window;
    IF EXISTS (SELECT 1 FROM public.notifications WHERE plan_id = in_window) THEN
        RAISE EXCEPTION 'Recordatorio huérfano tras borrar el plan';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM public.notifications WHERE id = invitation_notification) THEN
        RAISE EXCEPTION 'El borrado del plan afectó las invitaciones';
    END IF;
    RAISE NOTICE 'Recordatorios: ventana, estados, miembros, idempotencia, RLS, bandeja, lectura e invitaciones OK';
END;
$$;
ROLLBACK;
