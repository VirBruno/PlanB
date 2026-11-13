-- Preparación aislada para resultados verificados de UNO.
-- Esta migración NO habilita un RPC para escribir ni modifica las propuestas.
-- La escritura se habilitará únicamente junto con una partida validada en el servidor.
BEGIN;

CREATE TABLE planb_private.uno_victories (
    match_id uuid PRIMARY KEY,
    proposal_id bigint NOT NULL REFERENCES public.proposals(id) ON DELETE CASCADE,
    player_id uuid NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX uno_victories_proposal_idx ON planb_private.uno_victories(proposal_id);
ALTER TABLE planb_private.uno_victories ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE planb_private.uno_victories
    FROM PUBLIC, anon, authenticated, service_role, planb_django_runtime;

COMMENT ON TABLE planb_private.uno_victories IS
    'Reserva privada para victorias verificadas de UNO; sin acceso ni escritura desde clientes.';
COMMENT ON COLUMN planb_private.uno_victories.match_id IS
    'Identificador único de la partida; una victoria solo puede registrarse una vez.';

COMMIT;
