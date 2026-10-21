-- INTEGRACIÓN: únicamente Supabase local o proyecto de prueba autorizado.
-- Requiere todas las migraciones hasta 202610180001. No ejecutar en el compartido.
BEGIN;
SELECT set_config('planb.link_owner', gen_random_uuid()::text, true);
SELECT set_config('planb.link_guest', gen_random_uuid()::text, true);
INSERT INTO auth.users(id, aud, role, email, raw_user_meta_data, created_at, updated_at)
SELECT id, 'authenticated', 'authenticated', id::text || '@example.invalid',
       jsonb_build_object('username', 'L_' || substr(replace(id::text, '-', ''), 1, 20)), now(), now()
FROM (VALUES (current_setting('planb.link_owner')::uuid),
             (current_setting('planb.link_guest')::uuid)) fixture(id);

SELECT set_config('request.jwt.claim.sub', current_setting('planb.link_owner'), true);
SET LOCAL ROLE authenticated;
SELECT set_config('planb.link_group', public.create_group('Link group', NULL)::text, true);
SELECT public.create_group_invite_link(current_setting('planb.link_group')::uuid, repeat('a', 64));

SELECT set_config('request.jwt.claim.sub', current_setting('planb.link_guest'), true);
DO $$
DECLARE g uuid := current_setting('planb.link_group')::uuid;
BEGIN
    IF (SELECT count(*) FROM public.get_group_from_invite_link(repeat('a', 64))) <> 1 THEN
        RAISE EXCEPTION 'Authenticated user could not preview valid link';
    END IF;
    IF public.join_group_by_invite_link(repeat('a', 64)) <> g
       OR NOT EXISTS (SELECT 1 FROM public.group_members WHERE group_id = g AND user_id = auth.uid() AND role = 'member') THEN
        RAISE EXCEPTION 'Link did not create member membership';
    END IF;
    IF public.join_group_by_invite_link(repeat('a', 64)) <> g THEN
        RAISE EXCEPTION 'Repeated join was not idempotent';
    END IF;
END;
$$;

SELECT set_config('request.jwt.claim.sub', current_setting('planb.link_owner'), true);
SELECT public.create_group_invite_link(current_setting('planb.link_group')::uuid, repeat('b', 64));
DO $$
BEGIN
    BEGIN
        PERFORM public.get_group_from_invite_link(repeat('a', 64));
        RAISE EXCEPTION 'Regenerating the link did not revoke the previous token';
    EXCEPTION WHEN SQLSTATE 'PT404' THEN NULL; END;
    IF (SELECT count(*) FROM public.get_group_from_invite_link(repeat('b', 64))) <> 1 THEN
        RAISE EXCEPTION 'Replacement link is not active';
    END IF;
END;
$$;

RESET ROLE;
SET LOCAL ROLE anon;
DO $$
BEGIN
    BEGIN
        PERFORM public.get_group_from_invite_link(repeat('b', 64));
        RAISE EXCEPTION 'Anonymous user previewed an invite link';
    EXCEPTION WHEN insufficient_privilege THEN NULL; END;
    BEGIN
        PERFORM public.join_group_by_invite_link(repeat('b', 64));
        RAISE EXCEPTION 'Anonymous user redeemed an invite link';
    EXCEPTION WHEN insufficient_privilege THEN NULL; END;
END;
$$;
RESET ROLE;
ROLLBACK;