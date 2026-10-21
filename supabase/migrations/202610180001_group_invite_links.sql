-- Enlaces bearer para que usuarios autenticados se unan a un grupo.
BEGIN;

CREATE TABLE public.group_invite_links (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    group_id uuid NOT NULL REFERENCES public.groups(id) ON DELETE CASCADE,
    token_hash text NOT NULL UNIQUE CHECK (token_hash ~ '^[0-9a-f]{64}$'),
    created_by uuid NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL,
    revoked_at timestamptz,
    CONSTRAINT invite_link_expiry_valid CHECK (expires_at > created_at),
    CONSTRAINT invite_link_revocation_valid CHECK (revoked_at IS NULL OR revoked_at >= created_at)
);
CREATE INDEX group_invite_links_group_idx ON public.group_invite_links(group_id, created_at DESC);

ALTER TABLE public.group_invite_links ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.group_invite_links
    FROM PUBLIC, anon, authenticated, service_role, planb_django_runtime;

CREATE FUNCTION public.create_group_invite_link(p_group_id uuid, p_token_hash text)
RETURNS timestamptz
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = '' AS $$
DECLARE actor uuid := auth.uid(); target uuid; link_expiry timestamptz;
BEGIN
    IF actor IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = '42501', MESSAGE = 'Authentication required';
    END IF;
    IF p_token_hash IS NULL OR p_token_hash !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'Invalid invite link';
    END IF;
    SELECT g.id INTO target FROM public.groups g
        JOIN public.group_members m ON m.group_id = g.id
        WHERE g.id = p_group_id AND m.user_id = actor AND m.role = 'owner'
        FOR UPDATE OF g;
    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE = 'PT404', MESSAGE = 'Group unavailable';
    END IF;
    UPDATE public.group_invite_links SET revoked_at = statement_timestamp()
        WHERE group_id = target AND revoked_at IS NULL;
    link_expiry := statement_timestamp() + interval '7 days';
    INSERT INTO public.group_invite_links(group_id, token_hash, created_by, expires_at)
        VALUES (target, p_token_hash, actor, link_expiry);
    RETURN link_expiry;
END;
$$;

CREATE FUNCTION public.get_group_from_invite_link(p_token_hash text)
RETURNS TABLE(group_id uuid, group_name text, expires_at timestamptz)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = '' AS $$
DECLARE actor uuid := auth.uid();
BEGIN
    IF actor IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = '42501', MESSAGE = 'Authentication required';
    END IF;
    IF p_token_hash IS NULL OR p_token_hash !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION USING ERRCODE = 'PT404', MESSAGE = 'Invite link unavailable';
    END IF;
    RETURN QUERY SELECT g.id, g.name, l.expires_at
        FROM public.group_invite_links l JOIN public.groups g ON g.id = l.group_id
        WHERE l.token_hash = p_token_hash AND l.revoked_at IS NULL
          AND l.expires_at > statement_timestamp();
    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE = 'PT404', MESSAGE = 'Invite link unavailable';
    END IF;
END;
$$;

CREATE FUNCTION public.join_group_by_invite_link(p_token_hash text)
RETURNS uuid
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = '' AS $$
DECLARE actor uuid := auth.uid(); target uuid;
BEGIN
    IF actor IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = '42501', MESSAGE = 'Authentication required';
    END IF;
    IF p_token_hash IS NULL OR p_token_hash !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION USING ERRCODE = 'PT404', MESSAGE = 'Invite link unavailable';
    END IF;
    SELECT l.group_id INTO target FROM public.group_invite_links l
        WHERE l.token_hash = p_token_hash AND l.revoked_at IS NULL
          AND l.expires_at > statement_timestamp();
    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE = 'PT404', MESSAGE = 'Invite link unavailable';
    END IF;
    -- Serializa el canje con la regeneración del enlace y la eliminación del grupo.
    PERFORM g.id FROM public.groups g WHERE g.id = target FOR UPDATE;
    PERFORM l.id FROM public.group_invite_links l
        WHERE l.token_hash = p_token_hash AND l.group_id = target
          AND l.revoked_at IS NULL AND l.expires_at > statement_timestamp()
        FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE = 'PT404', MESSAGE = 'Invite link unavailable';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM public.group_members m WHERE m.group_id = target AND m.user_id = actor) THEN
        INSERT INTO public.group_members(group_id, user_id, role) VALUES (target, actor, 'member');
    END IF;
    RETURN target;
END;
$$;

REVOKE ALL ON FUNCTION public.create_group_invite_link(uuid, text),
    public.get_group_from_invite_link(text), public.join_group_by_invite_link(text)
    FROM PUBLIC, anon, authenticated, service_role, planb_django_runtime;
GRANT EXECUTE ON FUNCTION public.create_group_invite_link(uuid, text),
    public.get_group_from_invite_link(text), public.join_group_by_invite_link(text) TO authenticated;

COMMENT ON TABLE public.group_invite_links IS
    'Enlaces de invitación con hash SHA-256, vencimiento de 7 días y revocación al regenerar.';
NOTIFY pgrst, 'reload schema';
COMMIT;