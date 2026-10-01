-- Aplicar una sola vez como administrador, después de 202609300001_groups.sql.
-- Mantiene RLS y los grants de lectura existentes. Sin DML directo para la app.
BEGIN;

CREATE FUNCTION public.update_group(p_group_id uuid, p_name text, p_description text DEFAULT NULL)
RETURNS uuid
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = '' AS $$
DECLARE
    actor uuid := auth.uid();
    clean_name text := btrim(p_name);
    clean_description text := nullif(btrim(p_description), '');
BEGIN
    IF actor IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = '42501', MESSAGE = 'Authentication required';
    END IF;
    -- La membresía (no created_by) determina el permiso. Bloquear ambas filas
    -- evita que el owner cambie entre la comprobación y la escritura.
    PERFORM g.id FROM public.groups AS g
        JOIN public.group_members AS m ON m.group_id = g.id
        WHERE g.id = p_group_id AND m.user_id = actor AND m.role = 'owner'
        FOR UPDATE OF g, m;
    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE = 'PT404', MESSAGE = 'Group unavailable';
    END IF;
    -- Mismas reglas que create_group; no se altera la RPC existente.
    IF clean_name IS NULL OR char_length(clean_name) NOT BETWEEN 1 AND 100
       OR clean_name !~ '[^[:space:]]'
       OR (clean_description IS NOT NULL AND
           (char_length(clean_description) > 1000 OR clean_description !~ '[^[:space:]]')) THEN
        RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'Invalid group fields';
    END IF;
    UPDATE public.groups
        SET name = clean_name, description = clean_description
        WHERE id = p_group_id;
    -- El trigger existente actualiza updated_at.
    RETURN p_group_id;
END;
$$;

CREATE FUNCTION public.delete_group(p_group_id uuid)
RETURNS uuid
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = '' AS $$
DECLARE actor uuid := auth.uid();
BEGIN
    IF actor IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = '42501', MESSAGE = 'Authentication required';
    END IF;
    PERFORM g.id FROM public.groups AS g
        JOIN public.group_members AS m ON m.group_id = g.id
        WHERE g.id = p_group_id AND m.user_id = actor AND m.role = 'owner'
        FOR UPDATE OF g, m;
    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE = 'PT404', MESSAGE = 'Group unavailable';
    END IF;
    DELETE FROM public.groups WHERE id = p_group_id;
    -- Las FK existentes eliminan todas sus membresías mediante CASCADE.
    RETURN p_group_id;
END;
$$;

REVOKE ALL ON FUNCTION public.update_group(uuid, text, text), public.delete_group(uuid)
    FROM PUBLIC, anon, authenticated, service_role, planb_django_runtime;
GRANT EXECUTE ON FUNCTION public.update_group(uuid, text, text), public.delete_group(uuid)
    TO authenticated;

COMMENT ON FUNCTION public.update_group(uuid, text, text) IS
    'Sólo owner según auth.uid(); modifica name/description y conserva identidad y membresías.';
COMMENT ON FUNCTION public.delete_group(uuid) IS
    'Sólo owner según auth.uid(); elimina grupo y membresías en una transacción.';
NOTIFY pgrst, 'reload schema';
COMMIT;
