-- Solo para ejecutar dentro de una transacción reversible de prueba.
SELECT set_config('planb.duel_owner', gen_random_uuid()::text, true);
SELECT set_config('planb.duel_member', gen_random_uuid()::text, true);
SELECT set_config('planb.duel_outsider', gen_random_uuid()::text, true);
INSERT INTO auth.users(id, aud, role, email, raw_user_meta_data, created_at, updated_at)
SELECT id, 'authenticated', 'authenticated', id::text || '@example.invalid',
       jsonb_build_object('username', 'D_' || substr(replace(id::text, '-', ''), 1, 20)), now(), now()
FROM (VALUES (current_setting('planb.duel_owner')::uuid),
             (current_setting('planb.duel_member')::uuid),
             (current_setting('planb.duel_outsider')::uuid)) AS fixture(id);

SELECT set_config('request.jwt.claim.sub', current_setting('planb.duel_owner'), true);
SET LOCAL ROLE authenticated;
SELECT set_config('planb.duel_group', public.create_group('Duelo UNO de prueba', NULL)::text, true);
WITH created AS (
    INSERT INTO public.plans(name, description, status, group_id, created_by)
    VALUES ('Plan duelo temporal', 'Prueba', true, current_setting('planb.duel_group')::uuid, auth.uid()) RETURNING id
) SELECT set_config('planb.duel_plan', id::text, true) FROM created;
WITH created AS (
    INSERT INTO public.plans(name, description, status, group_id, created_by)
    VALUES ('Otro plan temporal', 'Prueba', true, current_setting('planb.duel_group')::uuid, auth.uid()) RETURNING id
) SELECT set_config('planb.duel_other_plan', id::text, true) FROM created;
WITH created AS (
    INSERT INTO public.proposals(tittle, created_by, plan_id, type, posicion, date_pick)
    VALUES ('Propuesta A', auth.uid(), current_setting('planb.duel_plan')::uuid,
            'juntada', 'SRID=4326;POINT(-58.38 -34.6)', '2035-01-01 18:00:00+00') RETURNING id
) SELECT set_config('planb.duel_proposal', id::text, true) FROM created;
WITH created AS (
    INSERT INTO public.proposals(tittle, created_by, plan_id, type, posicion, date_pick)
    VALUES ('Propuesta de otro plan', auth.uid(), current_setting('planb.duel_other_plan')::uuid,
            'juntada', 'SRID=4326;POINT(-58.38 -34.6)', '2035-01-01 18:00:00+00') RETURNING id
) SELECT set_config('planb.duel_other_proposal', id::text, true) FROM created;
RESET ROLE;
UPDATE public.plans SET election_method = 'combat' WHERE id = current_setting('planb.duel_plan')::uuid;
INSERT INTO public.group_members(group_id, user_id, role)
VALUES (current_setting('planb.duel_group')::uuid, current_setting('planb.duel_member')::uuid, 'member');

DO $$
DECLARE rejected boolean := false;
BEGIN
    BEGIN
        INSERT INTO public.notifications(user_id, type, payload)
        VALUES (current_setting('planb.duel_member')::uuid, 'uno_duel_invitation', '{}'::jsonb);
    EXCEPTION WHEN check_violation THEN rejected := true;
    END;
    IF NOT rejected THEN RAISE EXCEPTION 'Se permitió una invitación UNO sin plan'; END IF;
    rejected := false;
    BEGIN
        INSERT INTO public.notifications(user_id, type, plan_id, payload)
        VALUES (current_setting('planb.duel_member')::uuid, 'otro_tipo',
                current_setting('planb.duel_plan')::uuid, '{}'::jsonb);
    EXCEPTION WHEN check_violation THEN rejected := true;
    END;
    IF NOT rejected THEN RAISE EXCEPTION 'Otro tipo de notificación pudo asociarse a un plan'; END IF;
END;
$$;

-- Concedidos únicamente dentro de la transacción de prueba.
GRANT EXECUTE ON FUNCTION public.uno_duel_invite(uuid,bigint,uuid),
    public.uno_duel_respond(uuid,boolean,bigint) TO authenticated;
SET LOCAL ROLE authenticated;
DO $$
DECLARE rejected boolean;
BEGIN
    rejected := false;
    BEGIN
        PERFORM public.uno_duel_invite(current_setting('planb.duel_plan')::uuid,
            current_setting('planb.duel_proposal')::bigint, current_setting('planb.duel_outsider')::uuid);
    EXCEPTION WHEN insufficient_privilege THEN rejected := true;
    END;
    IF NOT rejected THEN RAISE EXCEPTION 'Se permitió invitar a una persona ajena al grupo'; END IF;
    rejected := false;
    BEGIN
        PERFORM public.uno_duel_invite(current_setting('planb.duel_plan')::uuid,
            current_setting('planb.duel_other_proposal')::bigint, current_setting('planb.duel_member')::uuid);
    EXCEPTION WHEN invalid_parameter_value THEN rejected := true;
    END;
    IF NOT rejected THEN RAISE EXCEPTION 'Se aceptó una propuesta de otro plan'; END IF;
END;
$$;
SELECT set_config('planb.duel_match', public.uno_duel_invite(
    current_setting('planb.duel_plan')::uuid, current_setting('planb.duel_proposal')::bigint,
    current_setting('planb.duel_member')::uuid)::text, true);
DO $$
DECLARE rejected boolean := false;
BEGIN
    BEGIN
        PERFORM public.uno_duel_invite(current_setting('planb.duel_plan')::uuid,
            current_setting('planb.duel_proposal')::bigint, current_setting('planb.duel_member')::uuid);
    EXCEPTION WHEN unique_violation THEN rejected := true;
    END;
    IF NOT rejected THEN RAISE EXCEPTION 'Se permitió duplicar una invitación activa'; END IF;
    rejected := false;
    BEGIN
        PERFORM public.uno_duel_respond(current_setting('planb.duel_match')::uuid, true,
            current_setting('planb.duel_proposal')::bigint);
    EXCEPTION WHEN insufficient_privilege THEN rejected := true;
    END;
    IF NOT rejected THEN RAISE EXCEPTION 'El invitador pudo aceptar su propia invitación'; END IF;
END;
$$;
RESET ROLE;
DO $$
BEGIN
    IF (SELECT count(*) FROM public.notifications WHERE user_id = current_setting('planb.duel_member')::uuid
        AND type = 'uno_duel_invitation' AND payload->>'match_id' = current_setting('planb.duel_match')) <> 1 THEN
        RAISE EXCEPTION 'La invitación no generó exactamente una notificación';
    END IF;
END;
$$;

SELECT set_config('request.jwt.claim.sub', current_setting('planb.duel_member'), true);
SET LOCAL ROLE authenticated;
DO $$
DECLARE rejected boolean := false;
BEGIN
    BEGIN
        PERFORM public.uno_duel_respond(current_setting('planb.duel_match')::uuid, true,
            current_setting('planb.duel_other_proposal')::bigint);
    EXCEPTION WHEN invalid_parameter_value THEN rejected := true;
    END;
    IF NOT rejected THEN RAISE EXCEPTION 'El invitado pudo elegir una propuesta de otro plan'; END IF;
END;
$$;
SELECT public.uno_duel_respond(current_setting('planb.duel_match')::uuid, true,
    current_setting('planb.duel_proposal')::bigint);
DO $$
DECLARE rejected boolean := false;
BEGIN
    BEGIN
        PERFORM public.uno_duel_respond(current_setting('planb.duel_match')::uuid, true,
            current_setting('planb.duel_proposal')::bigint);
    EXCEPTION WHEN invalid_parameter_value THEN rejected := true;
    END;
    IF NOT rejected THEN RAISE EXCEPTION 'Se permitió responder dos veces'; END IF;
    rejected := false;
    BEGIN
        PERFORM public.uno_duel_invite(current_setting('planb.duel_plan')::uuid,
            current_setting('planb.duel_proposal')::bigint, current_setting('planb.duel_owner')::uuid);
    EXCEPTION WHEN unique_violation THEN rejected := true;
    END;
    IF NOT rejected THEN RAISE EXCEPTION 'Se permitió duplicar la partida con los jugadores invertidos'; END IF;
END;
$$;
RESET ROLE;
DO $$
DECLARE match_row planb_private.uno_duel_matches%ROWTYPE;
BEGIN
    SELECT * INTO match_row FROM planb_private.uno_duel_matches
      WHERE match_id = current_setting('planb.duel_match')::uuid;
    IF match_row.status <> 'playing' OR match_row.invitee_proposal_id <> current_setting('planb.duel_proposal')::bigint
       OR jsonb_array_length(match_row.game->'player') <> 7 OR jsonb_array_length(match_row.game->'bot') <> 7
       OR match_row.game->>'id' <> match_row.match_id::text THEN
        RAISE EXCEPTION 'La aceptación no inició correctamente la partida compartida';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM public.notifications WHERE user_id = match_row.invitee_id
        AND payload->>'match_id' = match_row.match_id::text AND read_at IS NOT NULL) THEN
        RAISE EXCEPTION 'La notificación de la invitación no se marcó como leída';
    END IF;
END;
$$;

SELECT set_config('request.jwt.claim.sub', current_setting('planb.duel_outsider'), true);
SET LOCAL ROLE authenticated;
DO $$
DECLARE rejected boolean := false;
BEGIN
    BEGIN
        PERFORM public.uno_duel_respond(current_setting('planb.duel_match')::uuid, false, NULL);
    EXCEPTION WHEN insufficient_privilege THEN rejected := true;
    END;
    IF NOT rejected THEN RAISE EXCEPTION 'Una persona ajena pudo responder la invitación'; END IF;
END;
$$;
RESET ROLE;

-- Se cierra la primera partida ficticia para comprobar el rechazo de otra.
UPDATE planb_private.uno_duel_matches SET status = 'finished',
    winner_id = inviter_id, finish_reason = 'forfeit', updated_at = now()
WHERE match_id = current_setting('planb.duel_match')::uuid;
SELECT set_config('request.jwt.claim.sub', current_setting('planb.duel_owner'), true);
SET LOCAL ROLE authenticated;
SELECT set_config('planb.duel_rejected', public.uno_duel_invite(
    current_setting('planb.duel_plan')::uuid, current_setting('planb.duel_proposal')::bigint,
    current_setting('planb.duel_member')::uuid)::text, true);
RESET ROLE;
SELECT set_config('request.jwt.claim.sub', current_setting('planb.duel_member'), true);
SET LOCAL ROLE authenticated;
SELECT public.uno_duel_respond(current_setting('planb.duel_rejected')::uuid, false, NULL);
RESET ROLE;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM planb_private.uno_duel_matches
        WHERE match_id = current_setting('planb.duel_rejected')::uuid
          AND status = 'rejected' AND game IS NULL AND winner_id IS NULL) THEN
        RAISE EXCEPTION 'Rechazar la invitación no conservó el estado correcto';
    END IF;
END;
$$;
