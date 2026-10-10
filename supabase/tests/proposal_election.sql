-- INTEGRACIÓN: sólo Supabase de prueba con 202610100002 aplicado.
-- Ejecutar como administrador; los fixtures y cambios se revierten.
BEGIN;
SELECT set_config('planb.election_owner', gen_random_uuid()::text, true);
SELECT set_config('planb.election_member', gen_random_uuid()::text, true);

INSERT INTO auth.users(id, aud, role, email, raw_user_meta_data, created_at, updated_at)
SELECT id, 'authenticated', 'authenticated', id::text || '@example.invalid',
       jsonb_build_object('username', 'E_' || substr(replace(id::text, '-', ''), 1, 20)), now(), now()
FROM (VALUES (current_setting('planb.election_owner')::uuid),
             (current_setting('planb.election_member')::uuid)) AS fixture(id);

SELECT set_config('request.jwt.claim.sub', current_setting('planb.election_owner'), true);
SET LOCAL ROLE authenticated;
SELECT set_config('planb.election_group',
    public.create_group('Elección de prueba', NULL)::text, true);
WITH created AS (
    INSERT INTO public.plans(name, description, status, group_id, created_by)
    VALUES ('Plan de votos', 'Prueba reversible', true,
            current_setting('planb.election_group')::uuid, auth.uid())
    RETURNING id
)
SELECT set_config('planb.election_votes_plan', id::text, true) FROM created;
WITH created AS (
    INSERT INTO public.plans(name, description, status, group_id, created_by)
    VALUES ('Plan de combate', 'Prueba reversible', true,
            current_setting('planb.election_group')::uuid, auth.uid())
    RETURNING id
)
SELECT set_config('planb.election_combat_plan', id::text, true) FROM created;
WITH created AS (
    INSERT INTO public.proposals(tittle, created_by, plan_id, type, posicion)
    VALUES ('Votos owner', auth.uid(), current_setting('planb.election_votes_plan')::uuid,
            'juntada', 'SRID=4326;POINT(-58.38 -34.6)'::geography)
    RETURNING id
)
SELECT set_config('planb.election_votes_owner_proposal', id::text, true) FROM created;
WITH created AS (
    INSERT INTO public.proposals(tittle, created_by, plan_id, type, posicion)
    VALUES ('Combate owner', auth.uid(), current_setting('planb.election_combat_plan')::uuid,
            'juntada', 'SRID=4326;POINT(-58.38 -34.6)'::geography)
    RETURNING id
)
SELECT set_config('planb.election_combat_owner_proposal', id::text, true) FROM created;
SELECT public.set_proposal_election_method(
    current_setting('planb.election_votes_plan')::uuid, 'votes'
);
RESET ROLE;

INSERT INTO public.group_members(group_id, user_id, role)
VALUES (current_setting('planb.election_group')::uuid,
        current_setting('planb.election_member')::uuid, 'member');

SELECT set_config('request.jwt.claim.sub', current_setting('planb.election_member'), true);
SET LOCAL ROLE authenticated;
WITH created AS (
    INSERT INTO public.proposals(tittle, created_by, plan_id, type, posicion)
    VALUES ('Votos member', auth.uid(), current_setting('planb.election_votes_plan')::uuid,
            'salida', 'SRID=4326;POINT(-58.4 -34.61)'::geography)
    RETURNING id
)
SELECT set_config('planb.election_votes_member_proposal', id::text, true) FROM created;
WITH created AS (
    INSERT INTO public.proposals(tittle, created_by, plan_id, type, posicion)
    VALUES ('Combate member', auth.uid(), current_setting('planb.election_combat_plan')::uuid,
            'salida', 'SRID=4326;POINT(-58.4 -34.61)'::geography)
    RETURNING id
)
SELECT set_config('planb.election_combat_member_proposal', id::text, true) FROM created;
DO $$
DECLARE owner_votes bigint; member_votes bigint;
BEGIN
    PERFORM public.cast_proposal_vote(
        current_setting('planb.election_votes_plan')::uuid,
        current_setting('planb.election_votes_owner_proposal')::bigint
    );
    IF public.proposal_vote_for_user(current_setting('planb.election_votes_plan')::uuid)
       IS DISTINCT FROM current_setting('planb.election_votes_owner_proposal')::bigint THEN
        RAISE EXCEPTION 'El voto del integrante no quedó asociado a su identidad';
    END IF;
    IF public.cast_proposal_vote(
        current_setting('planb.election_votes_plan')::uuid,
        current_setting('planb.election_votes_owner_proposal')::bigint
    ) <> 1 THEN
        RAISE EXCEPTION 'Repetir el mismo voto modificó el conteo';
    END IF;
    PERFORM public.cast_proposal_vote(
        current_setting('planb.election_votes_plan')::uuid,
        current_setting('planb.election_votes_member_proposal')::bigint
    );
    IF public.proposal_vote_for_user(current_setting('planb.election_votes_plan')::uuid)
       IS DISTINCT FROM current_setting('planb.election_votes_member_proposal')::bigint THEN
        RAISE EXCEPTION 'No se pudo cambiar el voto a otra propuesta';
    END IF;
    SELECT proposal.votes INTO owner_votes FROM public.proposals AS proposal
        WHERE proposal.id = current_setting('planb.election_votes_owner_proposal')::bigint;
    SELECT proposal.votes INTO member_votes FROM public.proposals AS proposal
        WHERE proposal.id = current_setting('planb.election_votes_member_proposal')::bigint;
    IF owner_votes <> 0 OR member_votes <> 1 THEN
        RAISE EXCEPTION 'Cambiar el voto no actualizó ambos conteos';
    END IF;
    IF public.remove_proposal_vote(current_setting('planb.election_votes_plan')::uuid)
       IS DISTINCT FROM current_setting('planb.election_votes_member_proposal')::bigint THEN
        RAISE EXCEPTION 'No se eliminó el voto propio';
    END IF;
    IF public.proposal_vote_for_user(current_setting('planb.election_votes_plan')::uuid) IS NOT NULL THEN
        RAISE EXCEPTION 'El usuario todavía tiene un voto después de eliminarlo';
    END IF;
    SELECT proposal.votes INTO member_votes FROM public.proposals AS proposal
        WHERE proposal.id = current_setting('planb.election_votes_member_proposal')::bigint;
    IF member_votes <> 0 THEN RAISE EXCEPTION 'Eliminar el voto no actualizó el conteo'; END IF;
    PERFORM public.cast_proposal_vote(
        current_setting('planb.election_votes_plan')::uuid,
        current_setting('planb.election_votes_owner_proposal')::bigint
    );
    BEGIN
        PERFORM public.set_proposal_election_method(
            current_setting('planb.election_votes_plan')::uuid, 'ideal'
        );
        RAISE EXCEPTION USING ERRCODE = 'P0002', MESSAGE = 'Un miembro cambió el método';
    EXCEPTION WHEN insufficient_privilege THEN NULL;
    END;
END;
$$;
RESET ROLE;

SELECT set_config('request.jwt.claim.sub', current_setting('planb.election_owner'), true);
SET LOCAL ROLE authenticated;
DO $$
BEGIN
    IF public.set_proposal_election_method(
        current_setting('planb.election_votes_plan')::uuid, 'combat'
    ) <> 'combat' THEN
        RAISE EXCEPTION 'No se pudo cambiar el método con un voto existente';
    END IF;
    IF public.set_proposal_election_method(
        current_setting('planb.election_votes_plan')::uuid, 'votes'
    ) <> 'votes' THEN
        RAISE EXCEPTION 'No se pudo volver a votación';
    END IF;
    IF (SELECT proposal.votes FROM public.proposals AS proposal
        WHERE proposal.id = current_setting('planb.election_votes_owner_proposal')::bigint) <> 1 THEN
        RAISE EXCEPTION 'Cambiar el método borró resultados existentes';
    END IF;
    IF public.set_proposal_election_method(
        current_setting('planb.election_combat_plan')::uuid, 'combat'
    ) <> 'combat' THEN
        RAISE EXCEPTION 'No se guardó el método combate';
    END IF;
END;
$$;
RESET ROLE;

SELECT set_config('request.jwt.claim.sub', current_setting('planb.election_member'), true);
SET LOCAL ROLE authenticated;
DO $$
DECLARE saved bigint;
BEGIN
    saved := public.save_proposal_score(
        current_setting('planb.election_combat_plan')::uuid,
        current_setting('planb.election_combat_member_proposal')::bigint, 12
    );
    IF saved <> 12 THEN RAISE EXCEPTION 'No se guardó el score'; END IF;
    saved := public.save_proposal_score(
        current_setting('planb.election_combat_plan')::uuid,
        current_setting('planb.election_combat_member_proposal')::bigint, 7
    );
    IF saved <> 12 THEN RAISE EXCEPTION 'Un replay redujo el mejor score'; END IF;
    BEGIN
        PERFORM public.save_proposal_score(
            current_setting('planb.election_combat_plan')::uuid,
            current_setting('planb.election_combat_owner_proposal')::bigint, 99
        );
        RAISE EXCEPTION USING ERRCODE = 'P0002', MESSAGE = 'Se guardó score en propuesta ajena';
    EXCEPTION WHEN SQLSTATE '22023' THEN NULL;
    END;
    IF has_column_privilege('authenticated', 'public.proposals', 'votes', 'UPDATE')
       OR has_column_privilege('authenticated', 'public.proposals', 'Score', 'UPDATE') THEN
        RAISE EXCEPTION 'authenticated puede actualizar métricas directamente';
    END IF;
    IF has_table_privilege('authenticated', 'public.proposal_votes', 'SELECT')
       OR has_table_privilege('authenticated', 'public.proposal_votes', 'INSERT') THEN
        RAISE EXCEPTION 'authenticated tiene acceso directo a proposal_votes';
    END IF;
END;
$$;
RESET ROLE;

ROLLBACK;
SELECT 'OK: métodos flexibles, cambio/borrado de voto y score máximo; fixtures revertidas.' AS resultado;