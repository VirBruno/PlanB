-- INTEGRACIÓN: instancia Supabase de prueba autorizada, como administrador.
-- Requiere las cuatro migraciones. No envía correo; fixtures revertidas al final.
BEGIN;
SELECT set_config('planb.manage_owner', gen_random_uuid()::text, true);
SELECT set_config('planb.manage_member', gen_random_uuid()::text, true);
SELECT set_config('planb.manage_outsider', gen_random_uuid()::text, true);
INSERT INTO auth.users(id, aud, role, email, raw_user_meta_data, created_at, updated_at)
SELECT id, 'authenticated', 'authenticated', id::text || '@example.invalid',
       jsonb_build_object('username', 'M_' || substr(replace(id::text, '-', ''), 1, 20)), now(), now()
FROM (VALUES (current_setting('planb.manage_owner')::uuid),
             (current_setting('planb.manage_member')::uuid),
             (current_setting('planb.manage_outsider')::uuid)) AS fixture(id);

SELECT set_config('request.jwt.claim.sub', current_setting('planb.manage_owner'), true);
SET LOCAL ROLE authenticated;
SELECT set_config('planb.manage_group', public.create_group('Original', 'Antes')::text, true);
SELECT set_config('planb.manage_other', public.create_group('No eliminar', NULL)::text, true);
RESET ROLE;
INSERT INTO public.group_members(group_id, user_id, role)
VALUES (current_setting('planb.manage_group')::uuid, current_setting('planb.manage_member')::uuid, 'member');
UPDATE public.groups SET updated_at = '2000-01-01' WHERE id = current_setting('planb.manage_group')::uuid;
-- El trigger ya actualiza updated_at: guardar el valor actual para comparar luego.
SELECT set_config('planb.manage_created_at',
    (SELECT created_at::text FROM public.groups WHERE id = current_setting('planb.manage_group')::uuid), true);

SET LOCAL ROLE authenticated;
DO $$
DECLARE
    g uuid := current_setting('planb.manage_group')::uuid;
    result uuid;
    value text;
BEGIN
    result := public.update_group(g, '  Nuevo nombre  ', ' Nueva descripción ');
    IF result <> g OR NOT EXISTS (
        SELECT 1 FROM public.groups WHERE id = g AND name = 'Nuevo nombre'
          AND description = 'Nueva descripción' AND created_by = auth.uid()
          AND created_at = current_setting('planb.manage_created_at')::timestamptz
          AND updated_at >= created_at
    ) THEN RAISE EXCEPTION 'Edición incorrecta o alteración de identidad'; END IF;
    IF NOT EXISTS (SELECT 1 FROM public.group_members WHERE group_id = g AND user_id = auth.uid() AND role = 'owner') THEN
        RAISE EXCEPTION 'Se modificó el owner durante la edición';
    END IF;
    PERFORM public.update_group(g, repeat('n', 100), repeat('d', 1000));
    PERFORM public.update_group(g, 'Descripción vacía', '');
    IF EXISTS (SELECT 1 FROM public.groups WHERE id = g AND description IS NOT NULL) THEN
        RAISE EXCEPTION 'No se limpió la descripción';
    END IF;
    FOREACH value IN ARRAY ARRAY['', '   ', E'\t\n', repeat('n', 101)] LOOP
        BEGIN
            PERFORM public.update_group(g, value, 'Inválida');
            RAISE EXCEPTION 'Se aceptó un nombre inválido';
        EXCEPTION WHEN invalid_parameter_value THEN NULL;
        END;
    END LOOP;
    BEGIN
        PERFORM public.update_group(g, 'Nombre', repeat('d', 1001));
        RAISE EXCEPTION 'Se aceptó descripción demasiado larga';
    EXCEPTION WHEN invalid_parameter_value THEN NULL;
    END;
    IF NOT EXISTS (SELECT 1 FROM public.groups WHERE id = g AND name = 'Descripción vacía' AND description IS NULL) THEN
        RAISE EXCEPTION 'Una edición inválida dejó cambios parciales';
    END IF;
    BEGIN
        PERFORM public.update_group(p_group_id => g, p_name => 'Falso', owner_id => auth.uid());
        RAISE EXCEPTION 'Se aceptó owner_id';
    EXCEPTION WHEN undefined_function THEN NULL;
    END;
    BEGIN
        PERFORM public.delete_group(p_group_id => g, user_id => auth.uid());
        RAISE EXCEPTION 'Se aceptó user_id';
    EXCEPTION WHEN undefined_function THEN NULL;
    END;
END;
$$;

-- Miembro y extraño: mismo rechazo en la base, incluso saltándose Django.
DO $$
DECLARE actor text; g uuid := current_setting('planb.manage_group')::uuid;
BEGIN
    FOREACH actor IN ARRAY ARRAY[current_setting('planb.manage_member'), current_setting('planb.manage_outsider')] LOOP
        PERFORM set_config('request.jwt.claim.sub', actor, true);
        BEGIN
            PERFORM public.update_group(g, 'Ataque', NULL);
            RAISE EXCEPTION 'Un no-owner pudo editar';
        EXCEPTION WHEN SQLSTATE 'PT404' THEN NULL;
        END;
        BEGIN
            PERFORM public.delete_group(g);
            RAISE EXCEPTION 'Un no-owner pudo eliminar';
        EXCEPTION WHEN SQLSTATE 'PT404' THEN NULL;
        END;
    END LOOP;
    PERFORM set_config('request.jwt.claim.sub', '', true);
    BEGIN
        PERFORM public.update_group(g, 'Sin identidad', NULL);
        RAISE EXCEPTION 'Se permitió editar sin identidad';
    EXCEPTION WHEN insufficient_privilege THEN NULL;
    END;
    BEGIN
        PERFORM public.delete_group(g);
        RAISE EXCEPTION 'Se permitió eliminar sin identidad';
    EXCEPTION WHEN insufficient_privilege THEN NULL;
    END;
END;
$$;
RESET ROLE;
SET LOCAL ROLE anon;
DO $$
BEGIN
    BEGIN
        PERFORM public.update_group(current_setting('planb.manage_group')::uuid, 'Anon', NULL);
        RAISE EXCEPTION 'anon pudo ejecutar update_group';
    EXCEPTION WHEN insufficient_privilege THEN NULL;
    END;
    BEGIN
        PERFORM public.delete_group(current_setting('planb.manage_group')::uuid);
        RAISE EXCEPTION 'anon pudo ejecutar delete_group';
    EXCEPTION WHEN insufficient_privilege THEN NULL;
    END;
END;
$$;
RESET ROLE;

-- Fixture administrativa de cambio de owner: NO agrega esta funcionalidad a la app.
-- Demostrar que created_by no otorga permiso por sí mismo.
UPDATE public.group_members SET role = 'member'
WHERE group_id = current_setting('planb.manage_group')::uuid AND role = 'owner';
UPDATE public.group_members SET role = 'owner'
WHERE group_id = current_setting('planb.manage_group')::uuid
  AND user_id = current_setting('planb.manage_member')::uuid;
SELECT set_config('request.jwt.claim.sub', current_setting('planb.manage_owner'), true);
SET LOCAL ROLE authenticated;
DO $$
BEGIN
    BEGIN
        PERFORM public.update_group(current_setting('planb.manage_group')::uuid, 'Ex owner', NULL);
        RAISE EXCEPTION 'created_by conservó permiso sin ser owner';
    EXCEPTION WHEN SQLSTATE 'PT404' THEN NULL;
    END;
    BEGIN
        PERFORM public.delete_group(current_setting('planb.manage_group')::uuid);
        RAISE EXCEPTION 'created_by pudo eliminar sin ser owner';
    EXCEPTION WHEN SQLSTATE 'PT404' THEN NULL;
    END;
END;
$$;
SELECT set_config('request.jwt.claim.sub', current_setting('planb.manage_member'), true);
SELECT public.update_group(current_setting('planb.manage_group')::uuid, 'Owner vigente', NULL);
SELECT public.delete_group(current_setting('planb.manage_group')::uuid);
DO $$
BEGIN
    BEGIN
        PERFORM public.delete_group(current_setting('planb.manage_group')::uuid);
        RAISE EXCEPTION 'Segunda eliminación devolvió éxito';
    EXCEPTION WHEN SQLSTATE 'PT404' THEN NULL;
    END;
END;
$$;
RESET ROLE;

DO $$
DECLARE r text; t text;
BEGIN
    IF EXISTS (SELECT 1 FROM public.groups WHERE id = current_setting('planb.manage_group')::uuid)
       OR EXISTS (SELECT 1 FROM public.group_members WHERE group_id = current_setting('planb.manage_group')::uuid) THEN
        RAISE EXCEPTION 'Grupo o membresías persisten tras eliminar';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM public.groups WHERE id = current_setting('planb.manage_other')::uuid)
       OR NOT EXISTS (SELECT 1 FROM public.group_members WHERE group_id = current_setting('planb.manage_other')::uuid) THEN
        RAISE EXCEPTION 'Se eliminó otro grupo o su membresía';
    END IF;
    FOREACH r IN ARRAY ARRAY['anon', 'authenticated', 'service_role', 'planb_django_runtime'] LOOP
        FOREACH t IN ARRAY ARRAY['public.groups', 'public.group_members'] LOOP
            IF has_table_privilege(r, t, 'UPDATE') OR has_table_privilege(r, t, 'DELETE')
               OR has_any_column_privilege(r, t, 'UPDATE') THEN
                RAISE EXCEPTION 'Permisos DML excesivos para % sobre %', r, t;
            END IF;
        END LOOP;
        IF r <> 'authenticated' AND (
            has_function_privilege(r, 'public.update_group(uuid,text,text)', 'EXECUTE')
            OR has_function_privilege(r, 'public.delete_group(uuid)', 'EXECUTE')
        ) THEN RAISE EXCEPTION 'EXECUTE excesivo para %', r; END IF;
    END LOOP;
    IF NOT (SELECT relrowsecurity FROM pg_class WHERE oid = 'public.groups'::regclass)
       OR NOT (SELECT relrowsecurity FROM pg_class WHERE oid = 'public.group_members'::regclass) THEN
        RAISE EXCEPTION 'Se deshabilitó RLS';
    END IF;
END;
$$;
ROLLBACK;
SELECT 'OK: edición/eliminación exclusivas del owner, validación, grants y CASCADE; fixtures revertidas.' AS resultado;
