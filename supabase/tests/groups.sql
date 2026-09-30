-- INTEGRACIÓN: ejecutar como administrador sólo en una instancia de prueba.
-- Requiere las tres migraciones. No envía correos; todas las fixtures y DDL
-- de prueba se revierten. No ejecutar en proyectos con triggers externos.
BEGIN;
SELECT set_config('planb.test_owner', gen_random_uuid()::text, true);
SELECT set_config('planb.test_member', gen_random_uuid()::text, true);
SELECT set_config('planb.test_outsider', gen_random_uuid()::text, true);
INSERT INTO auth.users(id, aud, role, email, raw_user_meta_data, created_at, updated_at)
SELECT id, 'authenticated', 'authenticated', id::text || '@example.invalid',
       jsonb_build_object('username', 'G_' || substr(replace(id::text, '-', ''), 1, 20)), now(), now()
FROM (VALUES (current_setting('planb.test_owner')::uuid),
             (current_setting('planb.test_member')::uuid),
             (current_setting('planb.test_outsider')::uuid)) AS fixture(id);

SELECT set_config('request.jwt.claim.sub', current_setting('planb.test_owner'), true);
SET LOCAL ROLE authenticated;
SELECT set_config('planb.test_group', public.create_group('  Escapadas  ', 'Montaña')::text, true);
DO $$
DECLARE g uuid := current_setting('planb.test_group')::uuid;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM public.groups
                   WHERE id = g AND name = 'Escapadas' AND created_by = auth.uid()
                     AND description = 'Montaña' AND created_at IS NOT NULL AND updated_at IS NOT NULL) THEN
        RAISE EXCEPTION 'Grupo ausente o creador incorrecto';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM public.group_members
                   WHERE group_id = g AND user_id = auth.uid() AND role = 'owner' AND joined_at IS NOT NULL) THEN
        RAISE EXCEPTION 'Owner ausente o incorrecto';
    END IF;
    BEGIN
        PERFORM public.create_group('   ', NULL);
        RAISE EXCEPTION 'Se aceptó nombre vacío';
    EXCEPTION WHEN invalid_parameter_value THEN NULL;
    END;
    BEGIN
        PERFORM public.create_group(repeat('x', 101), NULL);
        RAISE EXCEPTION 'Se aceptó nombre demasiado largo';
    EXCEPTION WHEN invalid_parameter_value THEN NULL;
    END;
    BEGIN
        PERFORM public.create_group('Nombre', repeat('x', 1001));
        RAISE EXCEPTION 'Se aceptó descripción demasiado larga';
    EXCEPTION WHEN invalid_parameter_value THEN NULL;
    END;
    BEGIN
        INSERT INTO public.groups(name, created_by) VALUES ('Bypass', auth.uid());
        RAISE EXCEPTION 'Se permitió INSERT directo de grupo';
    EXCEPTION WHEN insufficient_privilege THEN NULL;
    END;
    BEGIN
        INSERT INTO public.group_members(group_id, user_id, role)
            VALUES (g, current_setting('planb.test_outsider')::uuid, 'owner');
        RAISE EXCEPTION 'Se permitió fabricar membresías';
    EXCEPTION WHEN insufficient_privilege THEN NULL;
    END;
    BEGIN
        UPDATE public.group_members SET role = 'member' WHERE group_id = g;
        RAISE EXCEPTION 'Se permitió cambiar roles';
    EXCEPTION WHEN insufficient_privilege THEN NULL;
    END;
    BEGIN
        UPDATE public.groups SET name = 'Cambiado' WHERE id = g;
        RAISE EXCEPTION 'Se permitió editar grupo';
    EXCEPTION WHEN insufficient_privilege THEN NULL;
    END;
    BEGIN
        DELETE FROM public.groups WHERE id = g;
        RAISE EXCEPTION 'Se permitió borrar grupo';
    EXCEPTION WHEN insufficient_privilege THEN NULL;
    END;
    BEGIN
        DELETE FROM public.group_members WHERE group_id = g;
        RAISE EXCEPTION 'Se permitió borrar membresía';
    EXCEPTION WHEN insufficient_privilege THEN NULL;
    END;
END;
$$;
-- Sin identidad: incluso una invocación SQL bajo authenticated es rechazada.
SELECT set_config('request.jwt.claim.sub', '', true);
DO $$
BEGIN
    BEGIN
        PERFORM public.create_group('Sin identidad', NULL);
        RAISE EXCEPTION 'Se aceptó identidad ausente';
    EXCEPTION WHEN insufficient_privilege THEN NULL;
    END;
END;
$$;

RESET ROLE;
-- Miembro creado sólo como fixture administrativa; no se implementa unirse/invitar.
INSERT INTO public.group_members(group_id, user_id, role)
VALUES (current_setting('planb.test_group')::uuid, current_setting('planb.test_member')::uuid, 'member');

SELECT set_config('request.jwt.claim.sub', current_setting('planb.test_member'), true);
SET LOCAL ROLE authenticated;
DO $$
DECLARE g uuid := current_setting('planb.test_group')::uuid;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM public.groups WHERE id = g AND created_by <> auth.uid()) THEN
        RAISE EXCEPTION 'Un miembro no puede leer el grupo de otro creador';
    END IF;
    IF (SELECT count(*) FROM public.group_members WHERE group_id = g) <> 1
       OR NOT EXISTS (SELECT 1 FROM public.group_members WHERE group_id = g AND user_id = auth.uid()) THEN
        RAISE EXCEPTION 'Membresías ajenas visibles';
    END IF;
END;
$$;

SELECT set_config('request.jwt.claim.sub', current_setting('planb.test_outsider'), true);
DO $$
DECLARE g uuid := current_setting('planb.test_group')::uuid;
BEGIN
    IF EXISTS (SELECT 1 FROM public.groups WHERE id = g)
       OR EXISTS (SELECT 1 FROM public.group_members WHERE group_id = g) THEN
        RAISE EXCEPTION 'RLS permitió leer un grupo ajeno';
    END IF;
END;
$$;
RESET ROLE;

-- Fallo deliberado del SEGUNDO INSERT: comprobar que el primer INSERT se revierte.
CREATE FUNCTION planb_private.test_reject_group_owner()
RETURNS trigger LANGUAGE plpgsql SET search_path = '' AS $$
BEGIN
    IF current_setting('planb.test_fail_owner', true) = 'on' THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'Fallo owner de prueba';
    END IF;
    RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION planb_private.test_reject_group_owner() FROM PUBLIC;
CREATE TRIGGER test_reject_group_owner BEFORE INSERT ON public.group_members
    FOR EACH ROW EXECUTE FUNCTION planb_private.test_reject_group_owner();
SELECT set_config('planb.test_fail_owner', 'on', true);
SELECT set_config('request.jwt.claim.sub', current_setting('planb.test_outsider'), true);
SET LOCAL ROLE authenticated;
DO $$
BEGIN
    BEGIN
        PERFORM public.create_group('Rollback requerido', NULL);
        RAISE EXCEPTION 'No falló el INSERT del owner';
    EXCEPTION WHEN check_violation THEN NULL;
    END;
END;
$$;
RESET ROLE;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM public.groups WHERE created_by = current_setting('planb.test_outsider')::uuid) THEN
        RAISE EXCEPTION 'El fallo de owner dejó un grupo huérfano';
    END IF;
END;
$$;
SELECT set_config('planb.test_fail_owner', 'off', true);

DO $$
DECLARE
    g uuid := current_setting('planb.test_group')::uuid;
    role_name text;
BEGIN
    FOREACH role_name IN ARRAY ARRAY['anon', 'service_role', 'planb_django_runtime'] LOOP
        IF has_table_privilege(role_name, 'public.groups', 'SELECT')
           OR has_table_privilege(role_name, 'public.groups', 'INSERT')
           OR has_table_privilege(role_name, 'public.group_members', 'SELECT')
           OR has_table_privilege(role_name, 'public.group_members', 'INSERT')
           OR has_function_privilege(role_name, 'public.create_group(text,text)', 'EXECUTE') THEN
            RAISE EXCEPTION 'Permiso excesivo para %', role_name;
        END IF;
    END LOOP;
    IF NOT (SELECT relrowsecurity FROM pg_class WHERE oid = 'public.groups'::regclass)
       OR NOT (SELECT relrowsecurity FROM pg_class WHERE oid = 'public.group_members'::regclass) THEN
        RAISE EXCEPTION 'RLS deshabilitado';
    END IF;
    BEGIN
        INSERT INTO public.group_members(group_id, user_id, role)
            VALUES (g, current_setting('planb.test_owner')::uuid, 'owner');
        RAISE EXCEPTION 'Se aceptó membresía duplicada';
    EXCEPTION WHEN unique_violation THEN NULL;
    END;
    BEGIN
        INSERT INTO public.group_members(group_id, user_id, role)
            VALUES (g, current_setting('planb.test_outsider')::uuid, 'owner');
        RAISE EXCEPTION 'Se aceptaron dos owners';
    EXCEPTION WHEN unique_violation THEN NULL;
    END;
    BEGIN
        INSERT INTO public.group_members(group_id, user_id, role)
            VALUES (g, current_setting('planb.test_outsider')::uuid, 'admin');
        RAISE EXCEPTION 'Se aceptó un rol desconocido';
    EXCEPTION WHEN check_violation THEN NULL;
    END;
    BEGIN
        DELETE FROM auth.users WHERE id = current_setting('planb.test_owner')::uuid;
        RAISE EXCEPTION 'Se pudo borrar al creador de un grupo';
    EXCEPTION WHEN foreign_key_violation THEN NULL;
    END;
    DELETE FROM auth.users WHERE id = current_setting('planb.test_member')::uuid;
    IF EXISTS (SELECT 1 FROM public.group_members WHERE user_id = current_setting('planb.test_member')::uuid) THEN
        RAISE EXCEPTION 'No se eliminó la membresía del usuario borrado';
    END IF;
    DELETE FROM public.groups WHERE id = g;
    IF EXISTS (SELECT 1 FROM public.group_members WHERE group_id = g) THEN
        RAISE EXCEPTION 'No se eliminaron membresías al borrar el grupo';
    END IF;
END;
$$;
ROLLBACK;
SELECT 'OK: grupos, owner atómico, constraints, grants y RLS; fixtures revertidas.' AS resultado;
