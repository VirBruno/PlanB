-- Comprobación de solo lectura para ejecutar después de la instalación.
-- Las funciones públicas deben existir, pero no estar habilitadas todavía.
DO $$
DECLARE
    signature text;
    routine regprocedure;
    table_name text;
    relation regclass;
BEGIN
    FOREACH signature IN ARRAY ARRAY[
        'public.uno_duel_invite(uuid,bigint,uuid)',
        'public.uno_duel_respond(uuid,boolean,bigint)',
        'public.uno_duel_game(uuid,text,text,text,bigint,bigint)',
        'public.uno_duel_list(uuid)'
    ] LOOP
        routine := to_regprocedure(signature);
        IF routine IS NULL THEN RAISE EXCEPTION 'Falta la función %', signature; END IF;
        IF has_function_privilege('authenticated', routine, 'EXECUTE')
           OR has_function_privilege('anon', routine, 'EXECUTE') THEN
            RAISE EXCEPTION 'La función % ya tiene acceso habilitado', signature;
        END IF;
    END LOOP;
    FOREACH table_name IN ARRAY ARRAY['planb_private.uno_duel_matches', 'planb_private.uno_duel_events'] LOOP
        relation := to_regclass(table_name);
        IF relation IS NULL THEN RAISE EXCEPTION 'Falta la tabla %', table_name; END IF;
        IF has_table_privilege('authenticated', relation, 'SELECT')
           OR has_table_privilege('anon', relation, 'SELECT')
           OR NOT EXISTS (SELECT 1 FROM pg_catalog.pg_class WHERE oid = relation AND relrowsecurity) THEN
            RAISE EXCEPTION 'Revisar permisos o RLS de %', table_name;
        END IF;
    END LOOP;
    IF NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_constraint
        WHERE conrelid = 'public.notifications'::regclass AND conname = 'notification_plan_required'
          AND pg_catalog.pg_get_constraintdef(oid) LIKE '%uno_duel_invitation%'
    ) THEN
        RAISE EXCEPTION 'Falta la regla de notificaciones de UNO';
    END IF;
END;
$$;
SELECT 'OK: UNO multiplayer installed with user access disabled.' AS resultado;
