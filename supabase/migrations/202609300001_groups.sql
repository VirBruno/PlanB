-- Ejecutar después de 202609290002_profiles.sql con el rol administrador.
-- Sólo dominio: no concede acceso al rol runtime ni modifica Auth.
BEGIN;

CREATE TABLE public.groups (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name text NOT NULL,
    description text,
    created_by uuid NOT NULL REFERENCES public.profiles(id) ON DELETE RESTRICT,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT groups_name_valid CHECK (
        char_length(name) BETWEEN 1 AND 100 AND name ~ '[^[:space:]]'
        AND name = btrim(name)
    ),
    CONSTRAINT groups_description_valid CHECK (
        description IS NULL OR
        (char_length(description) BETWEEN 1 AND 1000 AND description ~ '[^[:space:]]')
    )
);

CREATE TABLE public.group_members (
    group_id uuid NOT NULL REFERENCES public.groups(id) ON DELETE CASCADE,
    user_id uuid NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
    role text NOT NULL CHECK (role IN ('owner', 'member')),
    joined_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (group_id, user_id)
);

CREATE INDEX group_members_user_group_idx ON public.group_members(user_id, group_id);
CREATE UNIQUE INDEX group_members_one_owner_idx ON public.group_members(group_id)
    WHERE role = 'owner';
CREATE INDEX groups_created_at_id_idx ON public.groups(created_at DESC, id DESC);

CREATE FUNCTION planb_private.touch_group_updated_at()
RETURNS trigger LANGUAGE plpgsql SET search_path = '' AS $$
BEGIN
    NEW.updated_at := statement_timestamp();
    RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION planb_private.touch_group_updated_at()
    FROM PUBLIC, anon, authenticated, service_role;
CREATE TRIGGER planb_group_updated_at BEFORE UPDATE ON public.groups
    FOR EACH ROW EXECUTE FUNCTION planb_private.touch_group_updated_at();

ALTER TABLE public.groups ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.group_members ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.groups, public.group_members
    FROM PUBLIC, anon, authenticated, service_role, planb_django_runtime;
GRANT SELECT ON TABLE public.groups, public.group_members TO authenticated;

-- Sin ciclo: memberships sólo consulta auth.uid(); groups consulta memberships.
CREATE POLICY group_members_select_own ON public.group_members
    FOR SELECT TO authenticated USING (user_id = (SELECT auth.uid()));
CREATE POLICY groups_select_member ON public.groups
    FOR SELECT TO authenticated USING (
        EXISTS (SELECT 1 FROM public.group_members AS membership
                WHERE membership.group_id = groups.id
                  AND membership.user_id = (SELECT auth.uid()))
    );

-- Única puerta de escritura. No acepta owner/role/UUID proporcionados por el cliente.
-- SECURITY DEFINER es deliberado: authenticated carece de INSERT directo.
CREATE FUNCTION public.create_group(p_name text, p_description text DEFAULT NULL)
RETURNS uuid
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = '' AS $$
DECLARE
    actor uuid := auth.uid();
    new_id uuid;
    clean_name text := btrim(p_name);
    clean_description text := nullif(btrim(p_description), '');
BEGIN
    IF actor IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = '42501', MESSAGE = 'Authentication required';
    END IF;
    IF clean_name IS NULL OR char_length(clean_name) NOT BETWEEN 1 AND 100
       OR clean_name !~ '[^[:space:]]'
       OR (clean_description IS NOT NULL AND
           (char_length(clean_description) > 1000 OR clean_description !~ '[^[:space:]]')) THEN
        RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'Invalid group fields';
    END IF;
    INSERT INTO public.groups(name, description, created_by)
        VALUES (clean_name, clean_description, actor) RETURNING id INTO new_id;
    INSERT INTO public.group_members(group_id, user_id, role)
        VALUES (new_id, actor, 'owner');
    RETURN new_id;
END;
$$;

REVOKE ALL ON FUNCTION public.create_group(text, text)
    FROM PUBLIC, anon, authenticated, service_role, planb_django_runtime;
GRANT EXECUTE ON FUNCTION public.create_group(text, text) TO authenticated;
COMMENT ON FUNCTION public.create_group(text, text) IS
    'Operación atómica autenticada: grupo y owner; identidad obtenida de auth.uid().';
COMMENT ON TABLE public.groups IS 'Grupos de Plan B. El creador no puede eliminarse mientras posea grupos.';
COMMENT ON TABLE public.group_members IS 'Membresías: en esta US sólo se crea el owner mediante create_group.';
NOTIFY pgrst, 'reload schema';
COMMIT;
