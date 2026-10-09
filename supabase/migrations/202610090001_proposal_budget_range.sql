-- Aplicar manualmente después de las migraciones existentes de proposals.
-- Presupuesto opcional, sin moneda ni cambios de RLS, identidad o PostGIS.
BEGIN;

ALTER TABLE public.proposals
    ADD COLUMN budget_min numeric(12,2) NULL,
    ADD COLUMN budget_max numeric(12,2) NULL;

ALTER TABLE public.proposals
    ADD CONSTRAINT proposals_budget_min_nonnegative
        CHECK (budget_min IS NULL OR (budget_min >= 0 AND budget_min <> 'NaN'::numeric)),
    ADD CONSTRAINT proposals_budget_max_nonnegative
        CHECK (budget_max IS NULL OR (budget_max >= 0 AND budget_max <> 'NaN'::numeric)),
    ADD CONSTRAINT proposals_budget_range_valid
        CHECK (budget_min IS NULL OR budget_max IS NULL OR budget_min <= budget_max);

GRANT INSERT (budget_min, budget_max)
    ON TABLE public.proposals TO authenticated;
GRANT UPDATE (budget_min, budget_max)
    ON TABLE public.proposals TO authenticated;

COMMENT ON COLUMN public.proposals.budget_min IS
    'Límite inferior opcional del presupuesto informado; sin moneda ni uso en cálculo del plan ideal.';
COMMENT ON COLUMN public.proposals.budget_max IS
    'Límite superior opcional del presupuesto informado; sin moneda ni uso en cálculo del plan ideal.';

NOTIFY pgrst, 'reload schema';
COMMIT;
