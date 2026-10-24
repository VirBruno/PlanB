-- Plan B · calendario y ubicación asociados al plan. Aplicar en Supabase SQL Editor
-- después de las migraciones existentes. No se guardan pronósticos: se consultan en Open-Meteo.
BEGIN;

ALTER TABLE public.plans
  ADD COLUMN IF NOT EXISTS scheduled_at timestamptz,
  ADD COLUMN IF NOT EXISTS weather_province text,
  ADD COLUMN IF NOT EXISTS weather_city_id integer,
  ADD COLUMN IF NOT EXISTS weather_city_name text;

DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conrelid = 'public.plans'::regclass AND conname = 'plans_weather_schedule_complete'
  ) THEN
    ALTER TABLE public.plans ADD CONSTRAINT plans_weather_schedule_complete
    CHECK (
      (scheduled_at IS NULL AND weather_province IS NULL AND weather_city_id IS NULL AND weather_city_name IS NULL)
      OR
      (scheduled_at IS NOT NULL AND weather_province IS NOT NULL
       AND weather_city_id IS NOT NULL AND weather_city_id > 0 AND weather_city_name IS NOT NULL
       AND char_length(weather_city_name) BETWEEN 1 AND 120)
    );
  END IF;
END $$;

-- Los permisos existentes de RLS siguen intactos. Se habilitan sólo las columnas nuevas
-- para las operaciones autenticadas; las policies actuales validan al owner del grupo.
GRANT INSERT (scheduled_at, weather_province, weather_city_id, weather_city_name)
  ON TABLE public.plans TO authenticated;
GRANT UPDATE (scheduled_at, weather_province, weather_city_id, weather_city_name)
  ON TABLE public.plans TO authenticated;

COMMENT ON COLUMN public.plans.scheduled_at IS 'Fecha y hora del plan (timestamptz; UI en horario de Argentina).';
COMMENT ON COLUMN public.plans.weather_province IS 'Código de provincia argentina para Open-Meteo.';
COMMENT ON COLUMN public.plans.weather_city_id IS 'ID de localidad validado mediante geocodificación Open-Meteo.';
COMMENT ON COLUMN public.plans.weather_city_name IS 'Nombre validado de localidad; se presenta en los detalles del plan.';

NOTIFY pgrst, 'reload schema';
COMMIT;
