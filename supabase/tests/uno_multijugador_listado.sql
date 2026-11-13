-- Continúa la prueba de partidas y usa sus usuarios y planes ficticios.
GRANT EXECUTE ON FUNCTION public.uno_duel_list(uuid) TO authenticated;
SELECT set_config('request.jwt.claim.sub', current_setting('planb.duel_owner'), true);
SET LOCAL ROLE authenticated;
DO $$
DECLARE matches jsonb;
BEGIN
    matches := public.uno_duel_list(current_setting('planb.duel_plan')::uuid);
    IF jsonb_typeof(matches) <> 'array' OR jsonb_array_length(matches) < 2
       OR matches::text LIKE '%"deck"%' OR matches::text LIKE '%"hand"%'
       OR matches::text LIKE '%"secret"%' THEN
        RAISE EXCEPTION 'El listado expone datos de juego o no incluye las partidas';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM jsonb_array_elements(matches) AS m
        WHERE m->>'id' = current_setting('planb.duel_forfeit_match')
          AND m->>'role' = 'inviter' AND m->>'status' = 'finished'
          AND m->>'winner' = 'opponent' AND m->>'finish_reason' = 'forfeit') THEN
        RAISE EXCEPTION 'El listado no informa correctamente la partida y su resultado';
    END IF;
END;
$$;
RESET ROLE;
SELECT set_config('request.jwt.claim.sub', current_setting('planb.duel_outsider'), true);
SET LOCAL ROLE authenticated;
DO $$
DECLARE rejected boolean := false;
BEGIN
    BEGIN
        PERFORM public.uno_duel_list(current_setting('planb.duel_plan')::uuid);
    EXCEPTION WHEN insufficient_privilege THEN rejected := true;
    END;
    IF NOT rejected THEN RAISE EXCEPTION 'Una persona ajena pudo listar las partidas'; END IF;
END;
$$;
RESET ROLE;
