-- Aplicar después de 202610060001_proposals_security.sql.
-- Resuelve duplicados previos antes de crear el índice único.
BEGIN;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM public.proposals
        WHERE created_by IS NOT NULL
        GROUP BY plan_id, created_by
        HAVING count(*) > 1
    ) THEN
        RAISE EXCEPTION
            'Hay propuestas duplicadas por plan/autor; resuélvelas antes de aplicar esta migración.';
    END IF;
END;
$$;

CREATE UNIQUE INDEX proposals_one_per_user_plan_idx
    ON public.proposals(plan_id, created_by)
    WHERE created_by IS NOT NULL;

COMMENT ON INDEX public.proposals_one_per_user_plan_idx IS
    'Cada usuario puede tener como máximo una propuesta por plan.';
COMMIT;