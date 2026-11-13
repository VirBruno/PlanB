-- Diagnóstico de solo lectura para revisar compatibilidad antes de UNO.
-- No consulta filas de usuarios, no modifica datos y no aplica migraciones.
WITH required_columns(table_name, column_name) AS (
    VALUES
      ('plans', 'id'), ('plans', 'group_id'), ('plans', 'status'),
      ('plans', 'election_method'), ('plans', 'selected_proposal_id'),
      ('plans', 'scheduled_at'), ('plans', 'confirmed_at'),
      ('plans', 'weather_province'), ('plans', 'weather_city_id'), ('plans', 'weather_city_name'),
      ('proposals', 'id'), ('proposals', 'plan_id'), ('proposals', 'created_by'),
      ('proposals', 'date_pick'), ('proposals', 'posicion'),
      ('group_members', 'group_id'), ('group_members', 'user_id'), ('group_members', 'role'),
      ('groups', 'id'), ('profiles', 'id'),
      ('notifications', 'user_id'), ('notifications', 'type'),
      ('notifications', 'plan_id'), ('notifications', 'payload')
), checks AS (
    SELECT 'columna'::text AS tipo, c.table_name || '.' || c.column_name AS elemento,
           EXISTS (SELECT 1 FROM information_schema.columns AS actual
                   WHERE actual.table_schema = 'public' AND actual.table_name = c.table_name
                     AND actual.column_name = c.column_name) AS disponible
    FROM required_columns AS c
    UNION ALL
    SELECT 'esquema', 'planb_private', to_regnamespace('planb_private') IS NOT NULL
    UNION ALL
    SELECT 'función', 'auth.uid()', to_regprocedure('auth.uid()') IS NOT NULL
    UNION ALL
    SELECT 'función', 'public.confirm_truco_plan_proposal(uuid,bigint)',
           to_regprocedure('public.confirm_truco_plan_proposal(uuid,bigint)') IS NOT NULL
    UNION ALL
    SELECT 'rol', r.name, EXISTS (SELECT 1 FROM pg_catalog.pg_roles AS role WHERE role.rolname = r.name)
    FROM (VALUES ('anon'), ('authenticated'), ('service_role'), ('planb_django_runtime')) AS r(name)
    UNION ALL
    SELECT 'extensión', 'postgis', EXISTS (SELECT 1 FROM pg_catalog.pg_extension WHERE extname = 'postgis')
    UNION ALL
    SELECT 'índice', 'notifications (user_id, plan_id, type; plan_confirmed)',
        EXISTS (SELECT 1 FROM pg_catalog.pg_indexes AS idx
                WHERE idx.schemaname = 'public' AND idx.tablename = 'notifications'
                  AND idx.indexdef ILIKE '%UNIQUE%'
                  AND idx.indexdef ILIKE '%(user_id, plan_id, type)%'
                  AND idx.indexdef ILIKE '%plan_confirmed%')
    UNION ALL
    SELECT 'borrador aplicado', 'planb_private.uno_victories',
           to_regclass('planb_private.uno_victories') IS NOT NULL
    UNION ALL
    SELECT 'borrador aplicado', 'public.uno_bot_game(uuid,bigint,text,text,text)',
           to_regprocedure('public.uno_bot_game(uuid,bigint,text,text,text)') IS NOT NULL
)
SELECT tipo, elemento, CASE WHEN disponible THEN 'sí' ELSE 'no' END AS disponible
FROM checks ORDER BY tipo, elemento;
