-- Ejecutar únicamente después de instalar y verificar los objetos de UNO
-- multijugador. Las tablas y funciones privadas continúan sin acceso directo.
BEGIN;
GRANT EXECUTE ON FUNCTION public.uno_duel_invite(uuid,bigint,uuid),
    public.uno_duel_respond(uuid,boolean,bigint),
    public.uno_duel_game(uuid,text,text,text,bigint,bigint),
    public.uno_duel_list(uuid) TO authenticated;
NOTIFY pgrst, 'reload schema';
COMMIT;
SELECT 'OK: authenticated access to UNO multiplayer enabled.' AS resultado;
