-- Reconciliación de policies de plans con el modelo actual:
-- - Todos los miembros del grupo pueden leer y crear planes.
-- - Sólo el owner actual del grupo puede editar/eliminar.
BEGIN;

-- Policies antiguas / posibles nombres históricos.
DROP POLICY IF EXISTS plans_select_policy ON public.plans;
DROP POLICY IF EXISTS plans_insert_policy ON public.plans;
DROP POLICY IF EXISTS plans_update_policy ON public.plans;
DROP POLICY IF EXISTS plans_delete_policy ON public.plans;

DROP POLICY IF EXISTS plans_insert_group_owner ON public.plans;
DROP POLICY IF EXISTS plans_insert_group_member ON public.plans;

DROP POLICY IF EXISTS plans_select_group_member ON public.plans;
DROP POLICY IF EXISTS plans_update_group_owner ON public.plans;
DROP POLICY IF EXISTS plans_delete_group_owner ON public.plans;

-- Cualquier miembro actual del grupo puede leer sus planes.
CREATE POLICY plans_select_group_member
ON public.plans
FOR SELECT
TO authenticated
USING (
    EXISTS (
        SELECT 1
        FROM public.group_members AS m
        WHERE m.group_id = plans.group_id
          AND m.user_id = (SELECT auth.uid())
    )
);

-- Cualquier miembro actual del grupo puede crear un plan.
-- created_by siempre debe ser el usuario autenticado.
CREATE POLICY plans_insert_group_member
ON public.plans
FOR INSERT
TO authenticated
WITH CHECK (
    created_by = (SELECT auth.uid())
    AND EXISTS (
        SELECT 1
        FROM public.group_members AS m
        WHERE m.group_id = plans.group_id
          AND m.user_id = (SELECT auth.uid())
    )
);

-- Sólo el owner ACTUAL del grupo puede modificar planes.
CREATE POLICY plans_update_group_owner
ON public.plans
FOR UPDATE
TO authenticated
USING (
    EXISTS (
        SELECT 1
        FROM public.group_members AS m
        WHERE m.group_id = plans.group_id
          AND m.user_id = (SELECT auth.uid())
          AND m.role = 'owner'
    )
)
WITH CHECK (
    EXISTS (
        SELECT 1
        FROM public.group_members AS m
        WHERE m.group_id = plans.group_id
          AND m.user_id = (SELECT auth.uid())
          AND m.role = 'owner'
    )
);

-- Sólo el owner ACTUAL del grupo puede eliminar planes.
CREATE POLICY plans_delete_group_owner
ON public.plans
FOR DELETE
TO authenticated
USING (
    EXISTS (
        SELECT 1
        FROM public.group_members AS m
        WHERE m.group_id = plans.group_id
          AND m.user_id = (SELECT auth.uid())
          AND m.role = 'owner'
    )
);

COMMENT ON TABLE public.plans IS
    'Miembros del grupo leen y crean planes; sólo el owner actual del grupo edita/elimina.';

NOTIFY pgrst, 'reload schema';

COMMIT;