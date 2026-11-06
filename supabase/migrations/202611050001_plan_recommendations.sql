-- Revisión/aplicación MANUAL, después de todas las migraciones existentes.
-- No cambia confirm_plan_proposal ni ejecuta solicitudes externas desde SQL.
BEGIN;

CREATE TABLE public.plan_recommendations (
    plan_id uuid PRIMARY KEY REFERENCES public.plans(id) ON DELETE CASCADE,
    proposal_id bigint NOT NULL,
    confirmed_at timestamptz NOT NULL,
    provider text NOT NULL DEFAULT 'geoapify' CHECK (provider = 'geoapify'),
    status text NOT NULL CHECK (status IN ('pending', 'ready', 'failed')),
    error_code text CHECK (error_code IN (
        'missing_api_key', 'timeout', 'http_error', 'invalid_response',
        'unsupported_intent', 'missing_location', 'location_not_found', 'proposal_unavailable'
    )),
    -- Un único snapshot acotado: reemplazarlo nunca deja un Top 5 parcial.
    items jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (
        CASE WHEN jsonb_typeof(items) = 'array' THEN jsonb_array_length(items) <= 5 ELSE false END
    ),
    base_location jsonb,
    criteria jsonb,
    generation_id uuid NOT NULL DEFAULT gen_random_uuid(),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    generated_at timestamptz,
    CHECK ((status = 'failed' AND error_code IS NOT NULL)
           OR (status IN ('pending', 'ready') AND error_code IS NULL))
);

ALTER TABLE public.plan_recommendations ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.plan_recommendations
    FROM PUBLIC, anon, authenticated, service_role, planb_django_runtime;
GRANT SELECT (plan_id, proposal_id, confirmed_at, provider, status, error_code, items,
              base_location, criteria, created_at, updated_at, generated_at)
    ON public.plan_recommendations TO authenticated;
CREATE POLICY plan_recommendations_select_member ON public.plan_recommendations
FOR SELECT TO authenticated USING (
    EXISTS (
        SELECT 1 FROM public.plans p JOIN public.group_members m ON m.group_id = p.group_id
        WHERE p.id = plan_recommendations.plan_id AND m.user_id = (SELECT auth.uid())
          AND p.status IS TRUE AND p.selected_proposal_id = plan_recommendations.proposal_id
          AND p.confirmed_at = plan_recommendations.confirmed_at
    )
);

CREATE FUNCTION public.claim_plan_recommendations(
    p_plan_id uuid, p_proposal_id bigint, p_confirmed_at timestamptz, p_replace boolean DEFAULT false
)
RETURNS uuid LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = '' AS $$
DECLARE target public.plans%ROWTYPE; current_row public.plan_recommendations%ROWTYPE; ticket uuid;
BEGIN
    -- Orden de locks compartido con confirmación, reapertura y finalización.
    SELECT * INTO target FROM public.plans WHERE id = p_plan_id FOR UPDATE;
    IF NOT FOUND OR target.status IS NOT TRUE OR target.selected_proposal_id IS NULL
       OR target.selected_proposal_id IS DISTINCT FROM p_proposal_id
       OR target.confirmed_at IS DISTINCT FROM p_confirmed_at THEN
        RETURN NULL;
    END IF;
    SELECT * INTO current_row FROM public.plan_recommendations WHERE plan_id = p_plan_id FOR UPDATE;
    IF FOUND THEN
        -- Los clientes web nunca solicitan replace. Un reintento futuro explícito
        -- puede recuperar un pending abandonado tras 2 minutos, sin polling.
        IF p_replace IS NOT TRUE OR
           (current_row.status = 'pending' AND current_row.updated_at > now() - interval '2 minutes') THEN
            RETURN NULL;
        END IF;
        ticket := gen_random_uuid();
        UPDATE public.plan_recommendations SET status = 'pending', error_code = NULL,
            generation_id = ticket, updated_at = now() WHERE plan_id = p_plan_id;
        -- Conservar items/base/criteria/generated_at durante el recálculo.
    ELSE
        INSERT INTO public.plan_recommendations(plan_id, proposal_id, confirmed_at, status)
          VALUES (p_plan_id, p_proposal_id, p_confirmed_at, 'pending')
          RETURNING generation_id INTO ticket;
    END IF;
    RETURN ticket;
END;
$$;

CREATE FUNCTION public.finish_plan_recommendations(
    p_plan_id uuid, p_generation_id uuid, p_items jsonb,
    p_error_code text DEFAULT NULL, p_base_location jsonb DEFAULT NULL, p_criteria jsonb DEFAULT NULL
)
RETURNS boolean LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = '' AS $$
DECLARE target public.plans%ROWTYPE; current_row public.plan_recommendations%ROWTYPE;
BEGIN
    SELECT * INTO target FROM public.plans WHERE id = p_plan_id FOR UPDATE;
    IF NOT FOUND OR target.status IS NOT TRUE OR target.selected_proposal_id IS NULL THEN RETURN false; END IF;
    SELECT * INTO current_row FROM public.plan_recommendations WHERE plan_id = p_plan_id FOR UPDATE;
    IF NOT FOUND OR current_row.generation_id IS DISTINCT FROM p_generation_id
       OR current_row.status <> 'pending'
       OR current_row.proposal_id IS DISTINCT FROM target.selected_proposal_id
       OR current_row.confirmed_at IS DISTINCT FROM target.confirmed_at THEN RETURN false; END IF;
    IF p_error_code IS NULL THEN
        IF p_items IS NULL OR jsonb_typeof(p_items) <> 'array' THEN
            RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'Invalid recommendation snapshot';
        END IF;
        -- CHECK acota el array; el backend entrega únicamente candidatos normalizados.
        UPDATE public.plan_recommendations SET status = 'ready', error_code = NULL,
            items = p_items, base_location = p_base_location, criteria = p_criteria,
            generated_at = now(), updated_at = now() WHERE plan_id = p_plan_id;
    ELSE
        -- Un fallo al recalcular nunca borra un snapshot previo válido.
        UPDATE public.plan_recommendations SET status = 'failed', error_code = p_error_code,
            updated_at = now() WHERE plan_id = p_plan_id;
    END IF;
    RETURN true;
END;
$$;

CREATE FUNCTION public.invalidate_plan_recommendations()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
BEGIN
    -- También cubre reabrir y reconfirmar la MISMA propuesta con datos nuevos.
    IF NEW.selected_proposal_id IS DISTINCT FROM OLD.selected_proposal_id
       OR NEW.confirmed_at IS DISTINCT FROM OLD.confirmed_at THEN
        DELETE FROM public.plan_recommendations WHERE plan_id = NEW.id;
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER invalidate_plan_recommendations AFTER UPDATE OF selected_proposal_id, confirmed_at
ON public.plans FOR EACH ROW EXECUTE FUNCTION public.invalidate_plan_recommendations();

REVOKE ALL ON FUNCTION public.claim_plan_recommendations(uuid, bigint, timestamptz, boolean),
    public.finish_plan_recommendations(uuid, uuid, jsonb, text, jsonb, jsonb),
    public.invalidate_plan_recommendations()
    FROM PUBLIC, anon, authenticated, service_role, planb_django_runtime;
GRANT EXECUTE ON FUNCTION public.claim_plan_recommendations(uuid, bigint, timestamptz, boolean),
    public.finish_plan_recommendations(uuid, uuid, jsonb, text, jsonb, jsonb) TO service_role;
COMMENT ON TABLE public.plan_recommendations IS
    'Snapshot Geoapify compartido por decisión. Lectura JWT/RLS; escritura exclusiva por RPC backend.';
NOTIFY pgrst, 'reload schema';
COMMIT;
