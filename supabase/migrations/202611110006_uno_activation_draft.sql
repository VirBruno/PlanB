-- Activa los RPC autenticados después de revisar y probar las migraciones UNO.
-- La ruta de Django sigue desactivada hasta habilitar UNO_SUPABASE_ENABLED.
BEGIN;
GRANT EXECUTE ON FUNCTION public.uno_bot_game(uuid,bigint,text,text,text),
    public.uno_proposal_scores(uuid), public.confirm_uno_plan_proposal(uuid,bigint)
    TO authenticated;
NOTIFY pgrst, 'reload schema';
COMMIT;
SELECT 'OK: acceso autenticado a UNO activado.' AS resultado;
