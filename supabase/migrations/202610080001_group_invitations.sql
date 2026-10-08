-- Aplicar manualmente después de todas las migraciones anteriores. Sin Auth/ORM nuevo.
BEGIN;

CREATE TABLE public.group_invitations (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    group_id uuid NOT NULL REFERENCES public.groups(id) ON DELETE CASCADE,
    invited_user_id uuid NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
    invited_by uuid NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
    status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'accepted', 'rejected')),
    created_at timestamptz NOT NULL DEFAULT now(),
    responded_at timestamptz,
    CONSTRAINT invitation_recipient_key UNIQUE (id, invited_user_id),
    CONSTRAINT invitation_not_self CHECK (invited_user_id <> invited_by),
    CONSTRAINT invitation_response_valid CHECK (
        (status = 'pending' AND responded_at IS NULL)
        OR (status IN ('accepted', 'rejected') AND responded_at IS NOT NULL AND responded_at >= created_at)
    )
);
CREATE UNIQUE INDEX group_invitations_one_pending_idx
    ON public.group_invitations(group_id, invited_user_id) WHERE status = 'pending';
CREATE INDEX group_invitations_recipient_idx ON public.group_invitations(invited_user_id, created_at DESC);

CREATE TABLE public.notifications (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
    type text NOT NULL CHECK (type ~ '^[a-z][a-z0-9_]{0,63}$'),
    payload jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(payload) = 'object'),
    invitation_id uuid UNIQUE,
    created_at timestamptz NOT NULL DEFAULT now(),
    read_at timestamptz,
    CONSTRAINT notification_invitation_recipient_fk FOREIGN KEY (invitation_id, user_id)
        REFERENCES public.group_invitations(id, invited_user_id) ON DELETE CASCADE,
    CONSTRAINT notification_invitation_required CHECK (
        (type = 'group_invitation' AND invitation_id IS NOT NULL)
        OR (type <> 'group_invitation' AND invitation_id IS NULL)
    ),
    CONSTRAINT notification_read_valid CHECK (read_at IS NULL OR read_at >= created_at)
);
CREATE INDEX notifications_user_date_idx ON public.notifications(user_id, created_at DESC, id DESC);
CREATE INDEX notifications_unread_idx ON public.notifications(user_id) WHERE read_at IS NULL;

ALTER TABLE public.group_invitations ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.notifications ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.group_invitations, public.notifications
    FROM PUBLIC, anon, authenticated, service_role, planb_django_runtime;
GRANT SELECT ON public.group_invitations, public.notifications TO authenticated;
CREATE POLICY invitations_select_recipient_or_owner ON public.group_invitations
    FOR SELECT TO authenticated USING (
        invited_user_id = (SELECT auth.uid()) OR EXISTS (
            SELECT 1 FROM public.group_members AS m
            WHERE m.group_id = group_invitations.group_id
              AND m.user_id = (SELECT auth.uid()) AND m.role = 'owner'
        )
    );
CREATE POLICY notifications_select_own ON public.notifications
    FOR SELECT TO authenticated USING (user_id = (SELECT auth.uid()));

-- No se amplía SELECT de profiles ni group_members. RPCs devuelven campos mínimos.
CREATE FUNCTION public.search_group_invitees(p_group_id uuid, p_username text)
RETURNS TABLE(id uuid, username text)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = '' AS $$
DECLARE actor uuid := auth.uid(); clean text := lower(btrim(p_username) COLLATE "C");
BEGIN
    IF actor IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = '42501', MESSAGE = 'Authentication required';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM public.group_members m
                   WHERE m.group_id = p_group_id AND m.user_id = actor AND m.role = 'owner') THEN
        RAISE EXCEPTION USING ERRCODE = 'PT404', MESSAGE = 'Group unavailable';
    END IF;
    IF clean IS NULL OR clean !~ '^[a-z0-9][a-z0-9_.-]{2,29}$' THEN
        RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'Invalid username';
    END IF;
    RETURN QUERY SELECT p.id, p.username FROM public.profiles p
        WHERE p.username_normalized = clean AND p.id <> actor
          AND NOT EXISTS (SELECT 1 FROM public.group_members m
                          WHERE m.group_id = p_group_id AND m.user_id = p.id)
        LIMIT 1;
END;
$$;

CREATE FUNCTION public.list_group_members(p_group_id uuid)
RETURNS TABLE(id uuid, username text, role text)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = '' AS $$
DECLARE actor uuid := auth.uid();
BEGIN
    IF actor IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = '42501', MESSAGE = 'Authentication required';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM public.group_members m WHERE m.group_id = p_group_id AND m.user_id = actor) THEN
        RAISE EXCEPTION USING ERRCODE = 'PT404', MESSAGE = 'Group unavailable';
    END IF;
    RETURN QUERY SELECT p.id, p.username, m.role FROM public.group_members m
        JOIN public.profiles p ON p.id = m.user_id WHERE m.group_id = p_group_id
        ORDER BY (m.role = 'owner') DESC, p.username_normalized;
END;
$$;

CREATE FUNCTION public.list_pending_group_invitations(p_group_id uuid)
RETURNS TABLE(id uuid, invited_user_id uuid, username text, created_at timestamptz)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = '' AS $$
DECLARE actor uuid := auth.uid();
BEGIN
    IF actor IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = '42501', MESSAGE = 'Authentication required';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM public.group_members m
                   WHERE m.group_id = p_group_id AND m.user_id = actor AND m.role = 'owner') THEN
        RAISE EXCEPTION USING ERRCODE = 'PT404', MESSAGE = 'Group unavailable';
    END IF;
    RETURN QUERY SELECT i.id, i.invited_user_id, p.username, i.created_at
        FROM public.group_invitations i JOIN public.profiles p ON p.id = i.invited_user_id
        WHERE i.group_id = p_group_id AND i.status = 'pending' ORDER BY i.created_at, i.id;
END;
$$;

CREATE FUNCTION public.create_group_invitation(p_group_id uuid, p_invited_user_id uuid)
RETURNS uuid LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = '' AS $$
DECLARE actor uuid := auth.uid(); new_id uuid; group_name text; actor_name text;
BEGIN
    IF actor IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = '42501', MESSAGE = 'Authentication required';
    END IF;
    -- Mismo orden de locks que update/delete_group; serializa invitaciones y aceptación.
    SELECT g.name INTO group_name FROM public.groups g JOIN public.group_members m ON m.group_id = g.id
        WHERE g.id = p_group_id AND m.user_id = actor AND m.role = 'owner' FOR UPDATE OF g, m;
    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE = 'PT404', MESSAGE = 'Group unavailable';
    END IF;
    IF p_invited_user_id IS NULL OR p_invited_user_id = actor
       OR NOT EXISTS (SELECT 1 FROM public.profiles p WHERE p.id = p_invited_user_id)
       OR EXISTS (SELECT 1 FROM public.group_members m WHERE m.group_id = p_group_id AND m.user_id = p_invited_user_id) THEN
        RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'Invalid invitee';
    END IF;
    INSERT INTO public.group_invitations(group_id, invited_user_id, invited_by)
        VALUES (p_group_id, p_invited_user_id, actor) RETURNING id INTO new_id;
    SELECT p.username INTO actor_name FROM public.profiles p WHERE p.id = actor;
    INSERT INTO public.notifications(user_id, type, invitation_id, payload)
        VALUES (p_invited_user_id, 'group_invitation', new_id,
                jsonb_build_object('group_name', group_name, 'inviter_username', actor_name));
    RETURN new_id;
EXCEPTION WHEN unique_violation THEN
    RAISE EXCEPTION USING ERRCODE = 'PT409', MESSAGE = 'Invitation already pending';
END;
$$;

CREATE FUNCTION public.accept_group_invitation(p_invitation_id uuid)
RETURNS uuid LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = '' AS $$
DECLARE actor uuid := auth.uid(); invitation public.group_invitations%ROWTYPE; target uuid;
BEGIN
    IF actor IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = '42501', MESSAGE = 'Authentication required';
    END IF;
    SELECT i.group_id INTO target FROM public.group_invitations i
        WHERE i.id = p_invitation_id AND i.invited_user_id = actor;
    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE = 'PT404', MESSAGE = 'Invitation unavailable';
    END IF;
    -- Grupo antes de invitación evita deadlocks con delete_group y el CASCADE.
    PERFORM g.id FROM public.groups g WHERE g.id = target FOR UPDATE;
    SELECT i.* INTO invitation FROM public.group_invitations i
        WHERE i.id = p_invitation_id AND i.invited_user_id = actor FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE = 'PT404', MESSAGE = 'Invitation unavailable';
    END IF;
    IF invitation.status <> 'pending' THEN
        RAISE EXCEPTION USING ERRCODE = 'PT409', MESSAGE = 'Invitation already resolved';
    END IF;
    IF EXISTS (SELECT 1 FROM public.group_members m WHERE m.group_id = invitation.group_id AND m.user_id = actor) THEN
        RAISE EXCEPTION USING ERRCODE = 'PT409', MESSAGE = 'Already a member';
    END IF;
    INSERT INTO public.group_members(group_id, user_id, role) VALUES (invitation.group_id, actor, 'member');
    UPDATE public.group_invitations SET status = 'accepted', responded_at = now() WHERE id = invitation.id;
    UPDATE public.notifications SET read_at = coalesce(read_at, now())
        WHERE invitation_id = invitation.id AND user_id = actor;
    RETURN invitation.group_id;
END;
$$;

CREATE FUNCTION public.reject_group_invitation(p_invitation_id uuid)
RETURNS uuid LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = '' AS $$
DECLARE actor uuid := auth.uid(); invitation public.group_invitations%ROWTYPE;
BEGIN
    IF actor IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = '42501', MESSAGE = 'Authentication required';
    END IF;
    SELECT i.* INTO invitation FROM public.group_invitations i
        WHERE i.id = p_invitation_id AND i.invited_user_id = actor FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE = 'PT404', MESSAGE = 'Invitation unavailable';
    END IF;
    IF invitation.status <> 'pending' THEN
        RAISE EXCEPTION USING ERRCODE = 'PT409', MESSAGE = 'Invitation already resolved';
    END IF;
    UPDATE public.group_invitations SET status = 'rejected', responded_at = now() WHERE id = invitation.id;
    UPDATE public.notifications SET read_at = coalesce(read_at, now())
        WHERE invitation_id = invitation.id AND user_id = actor;
    RETURN invitation.id;
END;
$$;

CREATE FUNCTION public.list_notifications(p_page integer DEFAULT 1)
RETURNS TABLE(id uuid, type text, created_at timestamptz, read_at timestamptz,
              invitation_id uuid, group_id uuid, group_name text, inviter_username text, status text)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = '' AS $$
DECLARE actor uuid := auth.uid();
BEGIN
    IF actor IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = '42501', MESSAGE = 'Authentication required';
    END IF;
    IF p_page IS NULL OR p_page NOT BETWEEN 1 AND 10000 THEN
        RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'Invalid page';
    END IF;
    -- El join verifica destinatario: payload no decide identidad, grupo ni estado.
    RETURN QUERY SELECT n.id, n.type, n.created_at, n.read_at, i.id, i.group_id,
        g.name, p.username, i.status
        FROM public.notifications n
        LEFT JOIN public.group_invitations i ON i.id = n.invitation_id AND i.invited_user_id = actor
        LEFT JOIN public.groups g ON g.id = i.group_id
        LEFT JOIN public.profiles p ON p.id = i.invited_by
        WHERE n.user_id = actor ORDER BY n.created_at DESC, n.id DESC
        LIMIT 13 OFFSET ((p_page - 1) * 12);
END;
$$;

CREATE FUNCTION public.mark_notification_read(p_notification_id uuid)
RETURNS uuid LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = '' AS $$
DECLARE actor uuid := auth.uid(); result uuid;
BEGIN
    IF actor IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = '42501', MESSAGE = 'Authentication required';
    END IF;
    UPDATE public.notifications SET read_at = coalesce(read_at, now())
        WHERE id = p_notification_id AND user_id = actor RETURNING id INTO result;
    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE = 'PT404', MESSAGE = 'Notification unavailable';
    END IF;
    RETURN result;
END;
$$;

REVOKE ALL ON FUNCTION public.search_group_invitees(uuid, text), public.list_group_members(uuid),
    public.list_pending_group_invitations(uuid), public.create_group_invitation(uuid, uuid),
    public.accept_group_invitation(uuid), public.reject_group_invitation(uuid),
    public.list_notifications(integer), public.mark_notification_read(uuid)
    FROM PUBLIC, anon, authenticated, service_role, planb_django_runtime;
GRANT EXECUTE ON FUNCTION public.search_group_invitees(uuid, text), public.list_group_members(uuid),
    public.list_pending_group_invitations(uuid), public.create_group_invitation(uuid, uuid),
    public.accept_group_invitation(uuid), public.reject_group_invitation(uuid),
    public.list_notifications(integer), public.mark_notification_read(uuid) TO authenticated;

COMMENT ON TABLE public.group_invitations IS 'Fuente de verdad de invitaciones; historial hasta eliminación del grupo/perfil.';
COMMENT ON TABLE public.notifications IS 'Comunicación privada y extensible; estado de negocio consultado desde la invitación.';
NOTIFY pgrst, 'reload schema';
COMMIT;
