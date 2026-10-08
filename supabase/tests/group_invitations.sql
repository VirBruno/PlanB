-- INTEGRACIÓN: únicamente Supabase local o proyecto de prueba autorizado, como administrador.
-- Requiere todas las migraciones, incluidas las dos 20261008. NO ejecutar en el compartido.
-- Fixtures de Auth sin correo y fallos inducidos; todo termina con ROLLBACK.
BEGIN;
SELECT set_config('planb.inv_owner', gen_random_uuid()::text, true);
SELECT set_config('planb.inv_member', gen_random_uuid()::text, true);
SELECT set_config('planb.inv_guest', gen_random_uuid()::text, true);
SELECT set_config('planb.inv_outsider', gen_random_uuid()::text, true);
INSERT INTO auth.users(id, aud, role, email, raw_user_meta_data, created_at, updated_at)
SELECT id, 'authenticated', 'authenticated', id::text || '@example.invalid',
       jsonb_build_object('username', 'I_' || substr(replace(id::text, '-', ''), 1, 20)), now(), now()
FROM (VALUES (current_setting('planb.inv_owner')::uuid), (current_setting('planb.inv_member')::uuid),
             (current_setting('planb.inv_guest')::uuid), (current_setting('planb.inv_outsider')::uuid)) fixture(id);
SELECT set_config('planb.inv_username', (SELECT username FROM public.profiles
    WHERE id = current_setting('planb.inv_guest')::uuid), true);
SELECT set_config('request.jwt.claim.sub', current_setting('planb.inv_owner'), true);
SET LOCAL ROLE authenticated;
SELECT set_config('planb.inv_group', public.create_group('Futboleros', NULL)::text, true);
RESET ROLE;
INSERT INTO public.group_members(group_id, user_id, role)
VALUES (current_setting('planb.inv_group')::uuid, current_setting('planb.inv_member')::uuid, 'member');

-- Un fallo después del INSERT de membresía debe revertirlo junto con el estado.
CREATE FUNCTION planb_private.test_invitation_failure() RETURNS trigger
LANGUAGE plpgsql SET search_path = '' AS $$
BEGIN
    IF current_setting('planb.inv_fail_accept', true) = 'true' AND NEW.status = 'accepted' THEN
        RAISE EXCEPTION USING ERRCODE = 'PT500', MESSAGE = 'Fixture: update failure';
    END IF;
    RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION planb_private.test_invitation_failure() FROM PUBLIC;
CREATE TRIGGER test_invitation_failure BEFORE UPDATE ON public.group_invitations
    FOR EACH ROW EXECUTE FUNCTION planb_private.test_invitation_failure();
CREATE FUNCTION planb_private.test_notification_failure() RETURNS trigger
LANGUAGE plpgsql SET search_path = '' AS $$
BEGIN
    IF current_setting('planb.inv_fail_notification', true) = 'true' THEN
        RAISE EXCEPTION USING ERRCODE = 'PT500', MESSAGE = 'Fixture: notification failure';
    END IF;
    RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION planb_private.test_notification_failure() FROM PUBLIC;
CREATE TRIGGER test_notification_failure BEFORE INSERT ON public.notifications
    FOR EACH ROW EXECUTE FUNCTION planb_private.test_notification_failure();

SET LOCAL ROLE authenticated;
DO $$
DECLARE g uuid := current_setting('planb.inv_group')::uuid;
        guest uuid := current_setting('planb.inv_guest')::uuid; result uuid;
BEGIN
    IF (SELECT count(*) FROM public.search_group_invitees(g, upper(current_setting('planb.inv_username')))) <> 1 THEN
        RAISE EXCEPTION 'Owner no pudo buscar case-insensitive';
    END IF;
    IF EXISTS (SELECT 1 FROM public.search_group_invitees(g, 'I_' || substr(replace(auth.uid()::text, '-', ''), 1, 20))) THEN
        RAISE EXCEPTION 'Búsqueda devolvió al owner';
    END IF;
    IF EXISTS (SELECT 1 FROM public.search_group_invitees(g, 'I_' || substr(replace(current_setting('planb.inv_member'), '-', ''), 1, 20))) THEN
        RAISE EXCEPTION 'Búsqueda devolvió miembro existente';
    END IF;
    BEGIN
        PERFORM public.search_group_invitees(g, '%');
        RAISE EXCEPTION 'Búsqueda indiscriminada permitida';
    EXCEPTION WHEN invalid_parameter_value THEN NULL; END;
    BEGIN
        PERFORM public.create_group_invitation(g, auth.uid());
        RAISE EXCEPTION 'Auto-invitación permitida';
    EXCEPTION WHEN invalid_parameter_value THEN NULL; END;
    BEGIN
        PERFORM public.create_group_invitation(g, current_setting('planb.inv_member')::uuid);
        RAISE EXCEPTION 'Se invitó a miembro existente';
    EXCEPTION WHEN invalid_parameter_value THEN NULL; END;
    PERFORM set_config('planb.inv_fail_notification', 'true', true);
    BEGIN
        PERFORM public.create_group_invitation(g, guest);
        RAISE EXCEPTION 'No se provocó el fallo de notificación';
    EXCEPTION WHEN SQLSTATE 'PT500' THEN NULL; END;
    PERFORM set_config('planb.inv_fail_notification', 'false', true);
    IF EXISTS (SELECT 1 FROM public.group_invitations WHERE group_id = g) THEN
        RAISE EXCEPTION 'Invitación sin notificación tras fallo: falta atomicidad';
    END IF;
    result := public.create_group_invitation(g, guest);
    PERFORM set_config('planb.inv_first', result::text, true);
    IF NOT EXISTS (SELECT 1 FROM public.group_invitations
        WHERE id = result AND status = 'pending' AND responded_at IS NULL AND invited_by = auth.uid()) THEN
        RAISE EXCEPTION 'Invitación pendiente inválida';
    END IF;
    IF (SELECT count(*) FROM public.list_pending_group_invitations(g)) <> 1 THEN
        RAISE EXCEPTION 'Listado de pendientes incorrecto';
    END IF;
    BEGIN
        PERFORM public.create_group_invitation(g, guest);
        RAISE EXCEPTION 'Duplicado pending permitido';
    EXCEPTION WHEN SQLSTATE 'PT409' THEN NULL; END;
    IF EXISTS (SELECT 1 FROM public.notifications) THEN RAISE EXCEPTION 'Owner leyó notificación ajena'; END IF;
    IF (SELECT count(*) FROM public.profiles) <> 1 THEN RAISE EXCEPTION 'SELECT de perfiles abierto'; END IF;
END;
$$;

-- Member común y outsider: mismas negativas incluso saltándose Django.
DO $$
DECLARE actor text; g uuid := current_setting('planb.inv_group')::uuid;
        invitation uuid := current_setting('planb.inv_first')::uuid;
BEGIN
    FOREACH actor IN ARRAY ARRAY[current_setting('planb.inv_owner'),
                                current_setting('planb.inv_member'), current_setting('planb.inv_outsider')] LOOP
        PERFORM set_config('request.jwt.claim.sub', actor, true);
        BEGIN
            PERFORM public.accept_group_invitation(invitation);
            RAISE EXCEPTION 'Otro usuario aceptó invitación';
        EXCEPTION WHEN SQLSTATE 'PT404' THEN NULL; END;
        BEGIN
            PERFORM public.reject_group_invitation(invitation);
            RAISE EXCEPTION 'Otro usuario rechazó invitación';
        EXCEPTION WHEN SQLSTATE 'PT404' THEN NULL; END;
        IF actor <> current_setting('planb.inv_owner') THEN
            IF EXISTS (SELECT 1 FROM public.group_invitations WHERE id = invitation) THEN
                RAISE EXCEPTION 'No-owner leyó invitación ajena';
            END IF;
            BEGIN
                PERFORM public.create_group_invitation(g, current_setting('planb.inv_guest')::uuid);
                RAISE EXCEPTION 'No-owner invitó';
            EXCEPTION WHEN SQLSTATE 'PT404' THEN NULL; END;
            BEGIN
                PERFORM public.search_group_invitees(g, current_setting('planb.inv_username'));
                RAISE EXCEPTION 'No-owner buscó perfiles';
            EXCEPTION WHEN SQLSTATE 'PT404' THEN NULL; END;
            BEGIN
                PERFORM public.list_pending_group_invitations(g);
                RAISE EXCEPTION 'No-owner administró pendientes';
            EXCEPTION WHEN SQLSTATE 'PT404' THEN NULL; END;
        END IF;
    END LOOP;
    IF EXISTS (SELECT 1 FROM public.groups WHERE id = g) THEN RAISE EXCEPTION 'Outsider leyó grupo'; END IF;
    BEGIN
        PERFORM public.list_group_members(g);
        RAISE EXCEPTION 'Outsider vio integrantes';
    EXCEPTION WHEN SQLSTATE 'PT404' THEN NULL; END;
END;
$$;

SELECT set_config('request.jwt.claim.sub', current_setting('planb.inv_guest'), true);
DO $$
DECLARE first_id uuid := current_setting('planb.inv_first')::uuid; notification uuid;
BEGIN
    IF (SELECT count(*) FROM public.group_invitations WHERE id = first_id) <> 1 THEN
        RAISE EXCEPTION 'Destinatario no leyó invitación';
    END IF;
    IF (SELECT count(*) FROM public.list_notifications()) <> 1
       OR (SELECT count(*) FROM public.notifications WHERE read_at IS NULL) <> 1 THEN
        RAISE EXCEPTION 'Notificación o badge incorrectos';
    END IF;
    SELECT id INTO notification FROM public.notifications WHERE invitation_id = first_id;
    PERFORM set_config('planb.inv_notification', notification::text, true);
    PERFORM public.mark_notification_read(notification);
    PERFORM public.mark_notification_read(notification); -- idempotente
    IF EXISTS (SELECT 1 FROM public.notifications WHERE read_at IS NULL) THEN
        RAISE EXCEPTION 'Badge no decrementó al leer';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM public.group_invitations WHERE id = first_id AND status = 'pending') THEN
        RAISE EXCEPTION 'Leer notificación respondió invitación';
    END IF;
    PERFORM public.reject_group_invitation(first_id);
    IF EXISTS (SELECT 1 FROM public.group_members WHERE group_id = current_setting('planb.inv_group')::uuid) THEN
        RAISE EXCEPTION 'Rechazar creó membresía';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM public.group_invitations WHERE id = first_id AND status = 'rejected' AND responded_at IS NOT NULL) THEN
        RAISE EXCEPTION 'Rechazo no quedó como historial';
    END IF;
    BEGIN
        PERFORM public.accept_group_invitation(first_id);
        RAISE EXCEPTION 'Se aceptó una invitación rechazada';
    EXCEPTION WHEN SQLSTATE 'PT409' THEN NULL; END;
END;
$$;
SELECT set_config('request.jwt.claim.sub', current_setting('planb.inv_outsider'), true);
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM public.notifications) OR EXISTS (SELECT 1 FROM public.list_notifications()) THEN
        RAISE EXCEPTION 'Outsider vio notificación ajena';
    END IF;
    BEGIN
        PERFORM public.mark_notification_read(current_setting('planb.inv_notification')::uuid);
        RAISE EXCEPTION 'Outsider marcó notificación ajena';
    EXCEPTION WHEN SQLSTATE 'PT404' THEN NULL; END;
END;
$$;
SELECT set_config('request.jwt.claim.sub', current_setting('planb.inv_owner'), true);
SELECT set_config('planb.inv_second', public.create_group_invitation(
    current_setting('planb.inv_group')::uuid, current_setting('planb.inv_guest')::uuid)::text, true);
SELECT set_config('request.jwt.claim.sub', current_setting('planb.inv_guest'), true);
DO $$
DECLARE g uuid := current_setting('planb.inv_group')::uuid; invitation uuid := current_setting('planb.inv_second')::uuid;
BEGIN
    PERFORM set_config('planb.inv_fail_accept', 'true', true);
    BEGIN
        PERFORM public.accept_group_invitation(invitation);
        RAISE EXCEPTION 'No se provocó fallo posterior a membresía';
    EXCEPTION WHEN SQLSTATE 'PT500' THEN NULL; END;
    PERFORM set_config('planb.inv_fail_accept', 'false', true);
    IF EXISTS (SELECT 1 FROM public.group_members WHERE group_id = g AND user_id = auth.uid())
       OR NOT EXISTS (SELECT 1 FROM public.group_invitations WHERE id = invitation AND status = 'pending' AND responded_at IS NULL)
       OR NOT EXISTS (SELECT 1 FROM public.notifications WHERE invitation_id = invitation AND read_at IS NULL) THEN
        RAISE EXCEPTION 'Falta atomicidad de aceptación';
    END IF;
    IF public.accept_group_invitation(invitation) <> g THEN RAISE EXCEPTION 'Grupo devuelto incorrecto'; END IF;
    IF NOT EXISTS (SELECT 1 FROM public.group_members WHERE group_id = g AND user_id = auth.uid() AND role = 'member')
       OR NOT EXISTS (SELECT 1 FROM public.group_invitations WHERE id = invitation AND status = 'accepted' AND responded_at IS NOT NULL)
       OR EXISTS (SELECT 1 FROM public.notifications WHERE invitation_id = invitation AND read_at IS NULL) THEN
        RAISE EXCEPTION 'Aceptación incorrecta';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM public.groups WHERE id = g) THEN RAISE EXCEPTION 'Grupo no visible al nuevo miembro'; END IF;
    IF (SELECT count(*) FROM public.list_group_members(g)) <> 3 THEN RAISE EXCEPTION 'Integrantes no visibles'; END IF;
    IF (SELECT count(*) FROM public.group_invitations) <> 2 THEN RAISE EXCEPTION 'Se perdió historial'; END IF;
    BEGIN
        PERFORM public.accept_group_invitation(invitation);
        RAISE EXCEPTION 'Doble aceptación permitida';
    EXCEPTION WHEN SQLSTATE 'PT409' THEN NULL; END;
    BEGIN
        PERFORM public.reject_group_invitation(invitation);
        RAISE EXCEPTION 'Rechazo tras aceptación permitido';
    EXCEPTION WHEN SQLSTATE 'PT409' THEN NULL; END;
    BEGIN
        PERFORM public.accept_group_invitation(p_invitation_id => invitation, role => 'owner');
        RAISE EXCEPTION 'RPC aceptó role falsificado';
    EXCEPTION WHEN undefined_function THEN NULL; END;
    BEGIN
        INSERT INTO public.group_members(group_id, user_id, role) VALUES (g, auth.uid(), 'owner');
        RAISE EXCEPTION 'INSERT directo de membresía permitido';
    EXCEPTION WHEN insufficient_privilege THEN NULL; END;
    BEGIN
        UPDATE public.group_invitations SET status = 'pending' WHERE id = invitation;
        RAISE EXCEPTION 'UPDATE directo de invitación permitido';
    EXCEPTION WHEN insufficient_privilege THEN NULL; END;
    BEGIN
        INSERT INTO public.notifications(user_id, type, payload) VALUES (auth.uid(), 'forged', '{}');
        RAISE EXCEPTION 'INSERT directo de notificación permitido';
    EXCEPTION WHEN insufficient_privilege THEN NULL; END;
    BEGIN
        PERFORM public.update_group(g, 'Ataque', NULL);
        RAISE EXCEPTION 'Miembro editó grupo';
    EXCEPTION WHEN SQLSTATE 'PT404' THEN NULL; END;
    BEGIN
        PERFORM public.delete_group(g);
        RAISE EXCEPTION 'Miembro eliminó grupo';
    EXCEPTION WHEN SQLSTATE 'PT404' THEN NULL; END;
    INSERT INTO public.plans(name, description, status, group_id, created_by)
        VALUES ('Plan del miembro', 'Parque', true, g, auth.uid());
    IF NOT EXISTS (SELECT 1 FROM public.plans WHERE group_id = g AND name = 'Plan del miembro') THEN
        RAISE EXCEPTION 'Miembro no leyó su plan';
    END IF;
    UPDATE public.plans SET name = 'Ataque' WHERE group_id = g;
    DELETE FROM public.plans WHERE group_id = g;
    IF NOT EXISTS (SELECT 1 FROM public.plans WHERE group_id = g AND name = 'Plan del miembro') THEN
        RAISE EXCEPTION 'Miembro editó/eliminó plan';
    END IF;
END;
$$;

-- Confirmar nuevamente la pertenencia: fixture ya miembro con invitación pending.
SELECT set_config('request.jwt.claim.sub', current_setting('planb.inv_owner'), true);
SELECT set_config('planb.inv_existing', public.create_group_invitation(
    current_setting('planb.inv_group')::uuid, current_setting('planb.inv_outsider')::uuid)::text, true);
RESET ROLE;
INSERT INTO public.group_members(group_id, user_id, role)
VALUES (current_setting('planb.inv_group')::uuid, current_setting('planb.inv_outsider')::uuid, 'member');
SET LOCAL ROLE authenticated;
SELECT set_config('request.jwt.claim.sub', current_setting('planb.inv_outsider'), true);
DO $$
BEGIN
    BEGIN
        PERFORM public.accept_group_invitation(current_setting('planb.inv_existing')::uuid);
        RAISE EXCEPTION 'Aceptación no revalidó pertenencia';
    EXCEPTION WHEN SQLSTATE 'PT409' THEN NULL; END;
    PERFORM public.reject_group_invitation(current_setting('planb.inv_existing')::uuid);
END;
$$;
RESET ROLE;
DELETE FROM public.group_members WHERE group_id = current_setting('planb.inv_group')::uuid
    AND user_id = current_setting('planb.inv_outsider')::uuid;
SET LOCAL ROLE authenticated;
SELECT set_config('request.jwt.claim.sub', current_setting('planb.inv_outsider'), true);
DO $$
BEGIN
    BEGIN
        INSERT INTO public.plans(name, description, status, group_id, created_by)
            VALUES ('Outsider', '', true, current_setting('planb.inv_group')::uuid, auth.uid());
        RAISE EXCEPTION 'Outsider creó plan';
    EXCEPTION WHEN insufficient_privilege THEN NULL; END;
END;
$$;
SELECT set_config('request.jwt.claim.sub', current_setting('planb.inv_owner'), true);
DO $$
BEGIN
    INSERT INTO public.plans(name, description, status, group_id, created_by)
        VALUES ('Plan del owner', '', true, current_setting('planb.inv_group')::uuid, auth.uid());
    IF (SELECT count(*) FROM public.plans WHERE group_id = current_setting('planb.inv_group')::uuid) <> 2 THEN
        RAISE EXCEPTION 'Owner no leyó planes del grupo';
    END IF;
    BEGIN
        INSERT INTO public.plans(name, description, status, group_id, created_by)
            VALUES ('Identidad falsificada', '', true, current_setting('planb.inv_group')::uuid,
                    current_setting('planb.inv_member')::uuid);
        RAISE EXCEPTION 'Se falsificó created_by de plan';
    EXCEPTION WHEN insufficient_privilege THEN NULL; END;
END;
$$;

-- Sólo el owner ACTUAL puede invitar, aun cuando created_by siga apuntando al anterior.
RESET ROLE;
UPDATE public.group_members SET role = 'member' WHERE group_id = current_setting('planb.inv_group')::uuid
    AND user_id = current_setting('planb.inv_owner')::uuid;
UPDATE public.group_members SET role = 'owner' WHERE group_id = current_setting('planb.inv_group')::uuid
    AND user_id = current_setting('planb.inv_member')::uuid;
SET LOCAL ROLE authenticated;
DO $$
BEGIN
    BEGIN
        PERFORM public.create_group_invitation(current_setting('planb.inv_group')::uuid,
                                               current_setting('planb.inv_outsider')::uuid);
        RAISE EXCEPTION 'created_by conservó autorización sin role owner';
    EXCEPTION WHEN SQLSTATE 'PT404' THEN NULL; END;
    PERFORM set_config('request.jwt.claim.sub', current_setting('planb.inv_member'), true);
    PERFORM public.create_group_invitation(current_setting('planb.inv_group')::uuid,
                                           current_setting('planb.inv_outsider')::uuid);
    PERFORM set_config('request.jwt.claim.sub', '', true);
    BEGIN
        PERFORM public.accept_group_invitation(current_setting('planb.inv_second')::uuid);
        RAISE EXCEPTION 'RPC permitió identidad vacía';
    EXCEPTION WHEN insufficient_privilege THEN NULL; END;
END;
$$;
RESET ROLE;
SET LOCAL ROLE anon;
DO $$
BEGIN
    BEGIN
        PERFORM public.search_group_invitees(current_setting('planb.inv_group')::uuid, 'Someone');
        RAISE EXCEPTION 'anon buscó perfiles';
    EXCEPTION WHEN insufficient_privilege THEN NULL; END;
    BEGIN
        PERFORM public.accept_group_invitation(current_setting('planb.inv_second')::uuid);
        RAISE EXCEPTION 'anon ejecutó aceptación';
    EXCEPTION WHEN insufficient_privilege THEN NULL; END;
    BEGIN
        PERFORM 1 FROM public.notifications;
        RAISE EXCEPTION 'anon leyó notificaciones';
    EXCEPTION WHEN insufficient_privilege THEN NULL; END;
    BEGIN
        INSERT INTO public.plans(name, description, status, group_id, created_by)
            VALUES ('Anon', '', true, current_setting('planb.inv_group')::uuid,
                    current_setting('planb.inv_owner')::uuid);
        RAISE EXCEPTION 'anon creó plan';
    EXCEPTION WHEN insufficient_privilege THEN NULL; END;
END;
$$;
RESET ROLE;
DO $$
DECLARE role_name text; object_name text; signature text;
BEGIN
    FOREACH object_name IN ARRAY ARRAY['public.group_invitations', 'public.notifications'] LOOP
        IF NOT (SELECT relrowsecurity FROM pg_class WHERE oid = object_name::regclass) THEN
            RAISE EXCEPTION 'RLS deshabilitado en %', object_name;
        END IF;
        FOREACH role_name IN ARRAY ARRAY['anon', 'service_role', 'planb_django_runtime', 'authenticated'] LOOP
            IF has_table_privilege(role_name, object_name, 'INSERT')
               OR has_table_privilege(role_name, object_name, 'UPDATE')
               OR has_table_privilege(role_name, object_name, 'DELETE')
               OR (role_name <> 'authenticated' AND has_table_privilege(role_name, object_name, 'SELECT')) THEN
                RAISE EXCEPTION 'Grant excesivo en % para %', object_name, role_name;
            END IF;
        END LOOP;
    END LOOP;
    FOREACH signature IN ARRAY ARRAY['public.search_group_invitees(uuid,text)', 'public.list_group_members(uuid)',
        'public.list_pending_group_invitations(uuid)', 'public.create_group_invitation(uuid,uuid)',
        'public.accept_group_invitation(uuid)', 'public.reject_group_invitation(uuid)',
        'public.list_notifications(integer)', 'public.mark_notification_read(uuid)'] LOOP
        IF NOT has_function_privilege('authenticated', signature, 'EXECUTE') THEN
            RAISE EXCEPTION 'Falta EXECUTE para authenticated en %', signature;
        END IF;
        FOREACH role_name IN ARRAY ARRAY['anon', 'service_role', 'planb_django_runtime'] LOOP
            IF has_function_privilege(role_name, signature, 'EXECUTE') THEN
                RAISE EXCEPTION 'EXECUTE excesivo para % en %', role_name, signature;
            END IF;
        END LOOP;
    END LOOP;
END;
$$;
-- Regresión de eliminación con las nuevas FK: no quedan comunicaciones huérfanas.
SELECT set_config('request.jwt.claim.sub', current_setting('planb.inv_member'), true);
SET LOCAL ROLE authenticated;
SELECT public.delete_group(current_setting('planb.inv_group')::uuid);
RESET ROLE;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM public.group_invitations WHERE group_id = current_setting('planb.inv_group')::uuid)
       OR EXISTS (SELECT 1 FROM public.notifications WHERE user_id IN (
           current_setting('planb.inv_guest')::uuid, current_setting('planb.inv_outsider')::uuid)) THEN
        RAISE EXCEPTION 'Eliminar grupo dejó invitaciones/notificaciones huérfanas';
    END IF;
END;
$$;
ROLLBACK;
