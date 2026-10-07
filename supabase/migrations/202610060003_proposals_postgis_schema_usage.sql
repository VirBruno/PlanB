-- Aplicar después de las migraciones de seguridad y unicidad de proposals.
-- Data API necesita USAGE para resolver el tipo geography durante los inserts.
BEGIN;
GRANT USAGE ON SCHEMA postgis TO authenticated;
COMMIT;