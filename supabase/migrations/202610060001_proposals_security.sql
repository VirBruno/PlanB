-- Aplicar después de las migraciones de grupos y planes; proposals ya debe existir.
-- Los miembros leen/crean; cada autor administra únicamente sus propuestas.
BEGIN;

CREATE INDEX IF NOT EXISTS proposals_plan_created_idx
    ON public.proposals(plan_id, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS proposals_created_by_idx
    ON public.proposals(created_by);

ALTER TABLE public.proposals ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.proposals
    FROM PUBLIC, anon, authenticated, service_role, planb_django_runtime;
GRANT SELECT, DELETE ON TABLE public.proposals TO authenticated;
GRANT INSERT (tittle, description, date_pick, created_by, plan_id, type, posicion)
    ON TABLE public.proposals TO authenticated;
GRANT UPDATE (tittle, description, date_pick, type, posicion)
    ON TABLE public.proposals TO authenticated;

-- Restrictive policies also narrow any older permissive policy on this table.
CREATE POLICY proposals_member_scope ON public.proposals
    AS RESTRICTIVE FOR ALL TO authenticated
    USING (EXISTS (
        SELECT 1
        FROM public.plans AS plan
        JOIN public.group_members AS membership ON membership.group_id = plan.group_id
        WHERE plan.id = proposals.plan_id AND membership.user_id = (SELECT auth.uid())
    ))
    WITH CHECK (EXISTS (
        SELECT 1
        FROM public.plans AS plan
        JOIN public.group_members AS membership ON membership.group_id = plan.group_id
        WHERE plan.id = proposals.plan_id AND membership.user_id = (SELECT auth.uid())
    ));
CREATE POLICY proposals_author_insert_scope ON public.proposals
    AS RESTRICTIVE FOR INSERT TO authenticated
    WITH CHECK (created_by = (SELECT auth.uid()));
CREATE POLICY proposals_author_update_scope ON public.proposals
    AS RESTRICTIVE FOR UPDATE TO authenticated
    USING (created_by = (SELECT auth.uid()))
    WITH CHECK (created_by = (SELECT auth.uid()));
CREATE POLICY proposals_author_delete_scope ON public.proposals
    AS RESTRICTIVE FOR DELETE TO authenticated
    USING (created_by = (SELECT auth.uid()));

CREATE POLICY proposals_select_group_members ON public.proposals
    FOR SELECT TO authenticated USING (true);
CREATE POLICY proposals_insert_group_members ON public.proposals
    FOR INSERT TO authenticated WITH CHECK (true);
CREATE POLICY proposals_update_author ON public.proposals
    FOR UPDATE TO authenticated USING (true) WITH CHECK (true);
CREATE POLICY proposals_delete_author ON public.proposals
    FOR DELETE TO authenticated USING (true);

COMMENT ON TABLE public.proposals IS
    'Propuestas geográficas de planes: los miembros leen/crean y cada autor administra las propias.';
NOTIFY pgrst, 'reload schema';
COMMIT;