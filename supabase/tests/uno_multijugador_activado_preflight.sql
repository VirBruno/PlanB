-- Comprobación de solo lectura para ejecutar después de la activación.
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
        IF routine IS NULL OR NOT has_function_privilege('authenticated', routine, 'EXECUTE')
           OR has_function_privilege('anon', routine, 'EXECUTE') THEN
            RAISE EXCEPTION 'Revisar permisos de la función %', signature;
        END IF;
    END LOOP;
    FOREACH table_name IN ARRAY ARRAY['planb_private.uno_duel_matches', 'planb_private.uno_duel_events'] LOOP
        relation := to_regclass(table_name);
        IF relation IS NULL OR has_table_privilege('authenticated', relation, 'SELECT,INSERT,UPDATE,DELETE')
           OR has_table_privilege('anon', relation, 'SELECT,INSERT,UPDATE,DELETE')
           OR NOT EXISTS (SELECT 1 FROM pg_catalog.pg_class WHERE oid = relation AND relrowsecurity) THEN
            RAISE EXCEPTION 'Revisar permisos o RLS de %', table_name;
        END IF;
    END LOOP;
    FOREACH signature IN ARRAY ARRAY[
        'planb_private.uno_duel_action(jsonb,text,text,text,text)',
        'planb_private.uno_duel_visible(jsonb,text)'
    ] LOOP
        routine := to_regprocedure(signature);
        IF routine IS NULL OR has_function_privilege('authenticated', routine, 'EXECUTE')
           OR has_function_privilege('anon', routine, 'EXECUTE') THEN
            RAISE EXCEPTION 'Revisar permisos de la función privada %', signature;
        END IF;
    END LOOP;
END;
$$;
SELECT 'OK: authenticated access enabled and private data protected.' AS resultado;
