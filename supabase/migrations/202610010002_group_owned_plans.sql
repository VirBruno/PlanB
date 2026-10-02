-- Aplicar después de 202610010001_plans_security.sql.
-- Los planes pertenecen a un grupo; el owner del grupo administra sus planes.
BEGIN;

-- Reasignar primero cualquier plan existente sin grupo.
ALTER TABLE public.plans ALTER COLUMN group_id SET NOT NULL;

DROP POLICY IF EXISTS plans_select_visible ON public.plans;
DROP POLICY IF EXISTS plans_insert_creator ON public.plans;
DROP POLICY IF EXISTS plans_update_creator ON public.plans;
DROP POLICY IF EXISTS plans_delete_creator ON public.plans;

CREATE POLICY plans_select_group_members ON public.plans
    FOR SELECT TO authenticated USING (
        EXISTS (
            SELECT 1 FROM public.group_members AS membership
            WHERE membership.group_id = plans.group_id
              AND membership.user_id = (SELECT auth.uid())
        )
    );
CREATE POLICY plans_insert_group_owner ON public.plans
    FOR INSERT TO authenticated WITH CHECK (
        created_by = (SELECT auth.uid())
        AND EXISTS (
            SELECT 1 FROM public.group_members AS membership
            WHERE membership.group_id = plans.group_id
              AND membership.user_id = (SELECT auth.uid())
              AND membership.role = 'owner'
        )
    );
CREATE POLICY plans_update_group_owner ON public.plans
    FOR UPDATE TO authenticated USING (
        EXISTS (
            SELECT 1 FROM public.group_members AS membership
            WHERE membership.group_id = plans.group_id
              AND membership.user_id = (SELECT auth.uid())
              AND membership.role = 'owner'
        )
    ) WITH CHECK (
        EXISTS (
            SELECT 1 FROM public.group_members AS membership
            WHERE membership.group_id = plans.group_id
              AND membership.user_id = (SELECT auth.uid())
              AND membership.role = 'owner'
        )
    );
CREATE POLICY plans_delete_group_owner ON public.plans
    FOR DELETE TO authenticated USING (
        EXISTS (
            SELECT 1 FROM public.group_members AS membership
            WHERE membership.group_id = plans.group_id
              AND membership.user_id = (SELECT auth.uid())
              AND membership.role = 'owner'
        )
    );

COMMENT ON TABLE public.plans IS
    'Planes de grupo: sus integrantes pueden leerlos y sólo el owner puede administrarlos.';
NOTIFY pgrst, 'reload schema';
COMMIT;