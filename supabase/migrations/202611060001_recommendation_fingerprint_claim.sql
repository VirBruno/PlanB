-- Aplicación MANUAL. Sólo evoluciona claim; no modifica tablas, RLS ni finish.
BEGIN;

-- Sin defaults en la firma nueva: PostgREST distingue las llamadas de 4 y 5 campos.
CREATE FUNCTION public.claim_plan_recommendations(
    p_plan_id uuid, p_proposal_id bigint, p_confirmed_at timestamptz,
    p_replace boolean, p_input_fingerprint text
)
RETURNS uuid LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = '' AS $$
DECLARE
    target public.plans%ROWTYPE;
    current_row public.plan_recommendations%ROWTYPE;
    ticket uuid;
    saved_criteria jsonb;
BEGIN
    IF p_input_fingerprint IS NOT NULL
       AND p_input_fingerprint !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'Invalid input fingerprint';
    END IF;

    -- Mismo orden de locks que confirmación, reapertura y finish.
    SELECT * INTO target FROM public.plans WHERE id = p_plan_id FOR UPDATE;
    IF NOT FOUND OR target.status IS NOT TRUE OR target.selected_proposal_id IS NULL
       OR target.selected_proposal_id IS DISTINCT FROM p_proposal_id
       OR target.confirmed_at IS DISTINCT FROM p_confirmed_at THEN
        RETURN NULL;
    END IF;
    SELECT * INTO current_row FROM public.plan_recommendations
        WHERE plan_id = p_plan_id FOR UPDATE;
    IF FOUND THEN
        -- Nunca superponer trabajo con una reserva reciente, aun para otro hash.
        IF current_row.status = 'pending'
           AND current_row.updated_at > now() - interval '2 minutes' THEN
            RETURN NULL;
        END IF;
        IF p_input_fingerprint IS NULL THEN
            -- Compatibilidad exacta con el contrato anterior de cuatro argumentos.
            IF p_replace IS NOT TRUE THEN RETURN NULL; END IF;
            saved_criteria := current_row.criteria;
        ELSE
            saved_criteria := CASE WHEN jsonb_typeof(current_row.criteria) = 'object'
                THEN current_row.criteria ELSE '{}'::jsonb END;
            -- input_fingerprint describe el snapshot válido, nunca el intento.
            -- Los ready legacy sin fingerprint siguen vigentes, sin ola de requests.
            IF saved_criteria ->> 'input_fingerprint' = p_input_fingerprint
               OR (current_row.status = 'ready'
                   AND saved_criteria ->> 'input_fingerprint' IS NULL
                   AND p_replace IS NOT TRUE) THEN
                RETURN NULL;
            END IF;
            -- También cubre una solicitud demorada cuyo competidor ya falló.
            -- Sólo un reintento EXPLÍCITO permite repetir el mismo fingerprint.
            IF saved_criteria ->> 'attempted_input_fingerprint' = p_input_fingerprint
               AND p_replace IS NOT TRUE THEN
                RETURN NULL;
            END IF;
            saved_criteria := saved_criteria || jsonb_build_object(
                'attempted_input_fingerprint', p_input_fingerprint);
        END IF;
        ticket := gen_random_uuid();
        UPDATE public.plan_recommendations SET status = 'pending', error_code = NULL,
            generation_id = ticket, updated_at = now(), criteria = saved_criteria
            WHERE plan_id = p_plan_id;
        -- No modificar items, base_location, generated_at ni criterios del snapshot.
    ELSE
        INSERT INTO public.plan_recommendations(
            plan_id, proposal_id, confirmed_at, status, criteria
        ) VALUES (
            p_plan_id, p_proposal_id, p_confirmed_at, 'pending',
            CASE WHEN p_input_fingerprint IS NULL THEN NULL ELSE jsonb_build_object(
                'attempted_input_fingerprint', p_input_fingerprint) END
        ) RETURNING generation_id INTO ticket;
    END IF;
    RETURN ticket;
END;
$$;

-- La firma anterior mantiene defaults y ACL; delega sin duplicar la lógica.
CREATE OR REPLACE FUNCTION public.claim_plan_recommendations(
    p_plan_id uuid, p_proposal_id bigint, p_confirmed_at timestamptz,
    p_replace boolean DEFAULT false
)
RETURNS uuid LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = '' AS $$
BEGIN
    RETURN public.claim_plan_recommendations(
        p_plan_id, p_proposal_id, p_confirmed_at, p_replace, NULL::text);
END;
$$;

-- La firma nueva necesita su propio ACL; no ampliar acceso respecto de la anterior.
REVOKE ALL ON FUNCTION public.claim_plan_recommendations(uuid, bigint, timestamptz, boolean, text)
    FROM PUBLIC, anon, authenticated, service_role, planb_django_runtime;
GRANT EXECUTE ON FUNCTION public.claim_plan_recommendations(uuid, bigint, timestamptz, boolean, text)
    TO service_role;
NOTIFY pgrst, 'reload schema';
COMMIT;
