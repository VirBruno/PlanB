-- Aplicar una sola vez después de crear public.plans y las tablas de grupos.
-- Protege planes propios y planes visibles por membresía; sólo su creador escribe.
BEGIN;

CREATE INDEX plans_created_at_id_idx ON public.plans(created_at DESC, id DESC);
CREATE INDEX plans_group_id_idx ON public.plans(group_id);
CREATE INDEX plans_created_by_idx ON public.plans(created_by);

ALTER TABLE public.plans ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.plans
    FROM PUBLIC, anon, authenticated, service_role, planb_django_runtime;
GRANT SELECT, DELETE ON TABLE public.plans TO authenticated;
GRANT INSERT (name, description, status, group_id, created_by)
    ON TABLE public.plans TO authenticated;
GRANT UPDATE (name, description, status, group_id)
    ON TABLE public.plans TO authenticated;

CREATE POLICY plans_select_visible ON public.plans
    FOR SELECT TO authenticated USING (
        created_by = (SELECT auth.uid())
        OR EXISTS (
            SELECT 1 FROM public.group_members AS membership
            WHERE membership.group_id = plans.group_id
              AND membership.user_id = (SELECT auth.uid())
        )
    );
CREATE POLICY plans_insert_creator ON public.plans
    FOR INSERT TO authenticated WITH CHECK (
        created_by = (SELECT auth.uid())
        AND (group_id IS NULL OR EXISTS (
            SELECT 1 FROM public.group_members AS membership
            WHERE membership.group_id = plans.group_id
              AND membership.user_id = (SELECT auth.uid())
        ))
    );
CREATE POLICY plans_update_creator ON public.plans
    FOR UPDATE TO authenticated USING (created_by = (SELECT auth.uid()))
    WITH CHECK (
        created_by = (SELECT auth.uid())
        AND (group_id IS NULL OR EXISTS (
            SELECT 1 FROM public.group_members AS membership
            WHERE membership.group_id = plans.group_id
              AND membership.user_id = (SELECT auth.uid())
        ))
    );
CREATE POLICY plans_delete_creator ON public.plans
    FOR DELETE TO authenticated USING (created_by = (SELECT auth.uid()));

COMMENT ON TABLE public.plans IS
    'Planes visibles a su creador y a integrantes del grupo; las mutaciones pertenecen al creador.';
NOTIFY pgrst, 'reload schema';
COMMIT;