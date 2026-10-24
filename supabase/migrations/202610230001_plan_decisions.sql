-- Plan B · Etapa 8 · Decisión explícita del encuentro.
-- Ejecutar DESPUÉS de 202610210001_plan_schedule_weather.sql y de las migraciones de votación.
-- Se ejecuta desde SQL Editor por un administrador. Conserva RLS, datos y políticas existentes.
BEGIN;

ALTER TABLE public.plans
    ADD COLUMN IF NOT EXISTS selected_proposal_id bigint REFERENCES public.proposals(id) ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED,
    ADD COLUMN IF NOT EXISTS confirmed_at timestamptz;

-- La fecha introducida al CREAR el plan sigue siendo tentativa. Al CONFIRMAR una
-- propuesta se copia su date_pick; la localidad tentativa se limpia para que no
-- aparezca como si fuera la ubicación elegida (la ubicación real está en posicion).
ALTER TABLE public.plans DROP CONSTRAINT IF EXISTS plans_weather_schedule_complete;
ALTER TABLE public.plans ADD CONSTRAINT plans_weather_schedule_complete CHECK (
    (selected_proposal_id IS NOT NULL AND scheduled_at IS NOT NULL
     AND weather_province IS NULL AND weather_city_id IS NULL AND weather_city_name IS NULL)
    OR
    (selected_proposal_id IS NULL AND scheduled_at IS NULL
     AND weather_province IS NULL AND weather_city_id IS NULL AND weather_city_name IS NULL)
    OR
    (selected_proposal_id IS NULL AND scheduled_at IS NOT NULL
     AND weather_province IS NOT NULL AND weather_city_id IS NOT NULL AND weather_city_id > 0
     AND weather_city_name IS NOT NULL AND char_length(weather_city_name) BETWEEN 1 AND 120)
);

ALTER TABLE public.plans DROP CONSTRAINT IF EXISTS plans_confirmed_together;
ALTER TABLE public.plans ADD CONSTRAINT plans_confirmed_together CHECK (
    (selected_proposal_id IS NULL AND confirmed_at IS NULL)
    OR (selected_proposal_id IS NOT NULL AND confirmed_at IS NOT NULL)
);

-- La definición permite confirmar SOLO una alternativa del propio plan, con fecha.
-- Votación/combate: debe ser líder único, con resultado positivo.
CREATE OR REPLACE FUNCTION public.confirm_plan_proposal(p_plan_id uuid, p_proposal_id bigint)
RETURNS bigint LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = '' AS $$
DECLARE
    actor uuid := auth.uid();
    chosen public.proposals%ROWTYPE;
    target public.plans%ROWTYPE;
    top_score bigint;
    tied_count bigint;
BEGIN
    IF actor IS NULL OR p_plan_id IS NULL OR p_proposal_id IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'Invalid plan or proposal';
    END IF;
    SELECT * INTO target FROM public.plans WHERE id = p_plan_id FOR UPDATE;
    IF NOT FOUND OR target.selected_proposal_id IS NOT NULL OR target.status IS NOT TRUE THEN
        RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'Plan is unavailable or already confirmed';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM public.group_members m WHERE m.group_id = target.group_id
          AND m.user_id = actor AND m.role = 'owner'
    ) THEN
        RAISE EXCEPTION USING ERRCODE = '42501', MESSAGE = 'Only group owner may confirm';
    END IF;
    SELECT * INTO chosen FROM public.proposals
      WHERE id = p_proposal_id AND plan_id = p_plan_id FOR UPDATE;
    IF NOT FOUND OR chosen.date_pick IS NULL OR chosen.posicion IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'Proposal needs date and location';
    END IF;
    IF target.election_method IN ('votes', 'combat') THEN
        IF target.election_method = 'votes' THEN
            SELECT MAX(votes) INTO top_score FROM public.proposals WHERE plan_id = p_plan_id;
            SELECT count(*) INTO tied_count FROM public.proposals WHERE plan_id = p_plan_id AND votes = top_score;
            IF top_score IS NULL OR top_score <= 0 OR chosen.votes != top_score OR tied_count != 1 THEN
                RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'Voting has no unique winner';
            END IF;
        ELSE
            SELECT MAX("Score") INTO top_score FROM public.proposals WHERE plan_id = p_plan_id;
            SELECT count(*) INTO tied_count FROM public.proposals WHERE plan_id = p_plan_id AND "Score" = top_score;
            IF top_score IS NULL OR top_score <= 0 OR chosen."Score" != top_score OR tied_count != 1 THEN
                RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'Combat has no unique winner';
            END IF;
        END IF;
    END IF;
    UPDATE public.plans SET
        selected_proposal_id = chosen.id,
        scheduled_at = chosen.date_pick,
        confirmed_at = now(),
        weather_province = NULL, weather_city_id = NULL, weather_city_name = NULL
    WHERE id = p_plan_id;
    RETURN chosen.id;
END;
$$;

CREATE OR REPLACE FUNCTION public.reopen_plan_decision(p_plan_id uuid)
RETURNS boolean LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = '' AS $$
DECLARE
    actor uuid := auth.uid();
    target public.plans%ROWTYPE;
BEGIN
    IF actor IS NULL OR p_plan_id IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'Invalid plan';
    END IF;
    SELECT * INTO target FROM public.plans WHERE id = p_plan_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'Plan not found';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM public.group_members m WHERE m.group_id = target.group_id
          AND m.user_id = actor AND m.role = 'owner'
    ) THEN
        RAISE EXCEPTION USING ERRCODE = '42501', MESSAGE = 'Only group owner may reopen';
    END IF;
    IF target.selected_proposal_id IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'Plan is not confirmed';
    END IF;
    UPDATE public.plans SET
        selected_proposal_id = NULL, confirmed_at = NULL, scheduled_at = NULL,
        weather_province = NULL, weather_city_id = NULL, weather_city_name = NULL
    WHERE id = p_plan_id;
    RETURN true;
END;
$$;

-- Bloquea la edición y eliminación de alternativas durante la confirmación.
-- Se verifica también desde DB, ya que ocultar botones HTML no sería suficiente.
CREATE OR REPLACE FUNCTION public.guard_confirmed_proposals()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
DECLARE
    target_plan_id uuid;
BEGIN
    IF TG_OP = 'DELETE' THEN
        target_plan_id := OLD.plan_id;
    ELSE
        target_plan_id := NEW.plan_id;
    END IF;
    IF EXISTS (SELECT 1 FROM public.plans p
               WHERE p.id = target_plan_id AND p.selected_proposal_id IS NOT NULL) THEN
        IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'Reopen plan before changing proposals';
        ELSIF TG_OP = 'INSERT' THEN
            RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'Reopen plan before adding proposals';
        ELSIF NEW.tittle IS DISTINCT FROM OLD.tittle
           OR NEW.description IS DISTINCT FROM OLD.description
           OR NEW.date_pick IS DISTINCT FROM OLD.date_pick
           OR NEW.type IS DISTINCT FROM OLD.type
           OR NEW.posicion IS DISTINCT FROM OLD.posicion
           OR NEW.budget_min IS DISTINCT FROM OLD.budget_min
           OR NEW.budget_max IS DISTINCT FROM OLD.budget_max
           OR NEW.votes IS DISTINCT FROM OLD.votes
           OR NEW."Score" IS DISTINCT FROM OLD."Score" THEN
            RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'Reopen plan before changing proposals';
        END IF;
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS guard_confirmed_proposals ON public.proposals;
CREATE TRIGGER guard_confirmed_proposals
BEFORE INSERT OR UPDATE OR DELETE ON public.proposals
FOR EACH ROW EXECUTE FUNCTION public.guard_confirmed_proposals();

-- Congela el método una vez confirmada una alternativa.
CREATE OR REPLACE FUNCTION public.guard_confirmed_plan_method()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
BEGIN
    IF OLD.selected_proposal_id IS NOT NULL
       AND NEW.selected_proposal_id IS NOT NULL
       AND (NEW.election_method IS DISTINCT FROM OLD.election_method
            OR NEW.scheduled_at IS DISTINCT FROM OLD.scheduled_at
            OR NEW.weather_province IS DISTINCT FROM OLD.weather_province
            OR NEW.weather_city_id IS DISTINCT FROM OLD.weather_city_id
            OR NEW.weather_city_name IS DISTINCT FROM OLD.weather_city_name) THEN
        RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'Reopen plan before changing confirmed schedule';
    END IF;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS guard_confirmed_plan_method ON public.plans;
CREATE TRIGGER guard_confirmed_plan_method
BEFORE UPDATE ON public.plans FOR EACH ROW EXECUTE FUNCTION public.guard_confirmed_plan_method();

-- Nada de UPDATE/INSERT directo a selected_proposal_id: solamente RPC validada.
REVOKE ALL ON FUNCTION public.confirm_plan_proposal(uuid, bigint) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.reopen_plan_decision(uuid) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.confirm_plan_proposal(uuid, bigint) TO authenticated;
GRANT EXECUTE ON FUNCTION public.reopen_plan_decision(uuid) TO authenticated;
COMMENT ON COLUMN public.plans.selected_proposal_id IS 'Propuesta confirmada por el owner; NULL mientras se organiza.';
NOTIFY pgrst, 'reload schema';
COMMIT;
