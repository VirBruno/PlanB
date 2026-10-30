"""Integración opcional con dos conexiones PostgreSQL reales, sólo loopback.

Requiere una base local preparada y un rol administrador, no usada por otros tests.
Configurar PLANB_LOCAL_TEST_DSN fuera de .env. Ejemplo de ejecución desde raíz:
python supabase/tests/expense_debt_concurrency.py
No se ejecuta con manage.py test. Crea fixtures temporales y las elimina al final.
"""
from concurrent.futures import ThreadPoolExecutor
import os
from threading import Barrier
from uuid import uuid4

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.types.json import Jsonb


def main():
    dsn = os.environ.get("PLANB_LOCAL_TEST_DSN")
    if not dsn:
        raise SystemExit("Falta PLANB_LOCAL_TEST_DSN para una base LOCAL autorizada.")
    params = conninfo_to_dict(dsn)
    host = params.get("host")
    if host not in ("127.0.0.1", "localhost", "::1") or "service" in params:
        raise SystemExit("Esta prueba sólo permite PostgreSQL local, sin service ni hosts remotos.")
    # Forzar la IP evita que PGHOSTADDR heredado o un DNS redirija a una base remota.
    params["hostaddr"] = "::1" if host == "::1" else "127.0.0.1"
    params["connect_timeout"] = "5"
    owner, debtor = uuid4(), uuid4()
    group = plan = None

    def actor(connection, uid):
        connection.execute("SELECT set_config('request.jwt.claim.sub', %s, true)", (str(uid),))
        connection.execute("SET LOCAL ROLE authenticated")

    with psycopg.connect(**params, autocommit=True) as admin:
        try:
            with admin.transaction():
                for uid in (owner, debtor):
                    admin.execute("""INSERT INTO auth.users
                        (id, aud, role, email, raw_user_meta_data, created_at, updated_at)
                        VALUES (%s, 'authenticated', 'authenticated', %s, %s, now(), now())""",
                        (uid, f"{uid}@example.invalid", Jsonb({"username": "Concurrent_" + uid.hex[:18]})))
                actor(admin, owner)
                group = admin.execute("SELECT public.create_group('Concurrencia de deuda', NULL)").fetchone()[0]
                plan = admin.execute("""INSERT INTO public.plans(name, description, status, group_id, created_by)
                    VALUES ('Plan de concurrencia', 'Fixture local', true, %s, %s) RETURNING id""",
                    (group, owner)).fetchone()[0]
                admin.execute("RESET ROLE")
                admin.execute("INSERT INTO public.group_members(group_id,user_id,role) VALUES (%s,%s,'member')", (group, debtor))
                actor(admin, owner)
                admin.execute("SELECT public.create_plan_expense(%s, 'Consumo', 100.01, %s, ARRAY[%s]::uuid[])", (plan, owner, debtor))

            def parallel_pair():
                gate = Barrier(2)

                def send(_):
                    with psycopg.connect(**params) as connection:
                        connection.execute("SET LOCAL lock_timeout = '10s'")
                        actor(connection, owner)
                        gate.wait(timeout=10)
                        return connection.execute("SELECT public.notify_expense_debt(%s,%s)", (plan, debtor)).fetchone()[0]

                with ThreadPoolExecutor(max_workers=2) as pool:
                    results = list(pool.map(send, range(2)))
                assert len({r["notification_id"] for r in results}) == 1, results
                assert sorted(r["created"] for r in results) == [False, True], results
                assert all(r["amount"] == "100.01" for r in results), results
                return results[0]["notification_id"]

            first = parallel_pair()
            pending = admin.execute("""SELECT count(*) FROM public.notifications
                WHERE plan_id=%s AND type='expense_debt_reminder' AND read_at IS NULL""", (plan,)).fetchone()[0]
            assert pending == 1
            with admin.transaction():
                actor(admin, debtor)
                admin.execute("SELECT public.mark_notification_read(%s)", (first,))
            second = parallel_pair()
            assert second != first
            counts = admin.execute("""SELECT count(*), count(*) FILTER (WHERE read_at IS NULL)
                FROM public.notifications WHERE plan_id=%s AND type='expense_debt_reminder'""", (plan,)).fetchone()
            assert counts == (2, 1), counts
            print("OK: dos solicitudes concurrentes comparten un pendiente; después de leer crean otro único.")
        finally:
            # IDs aleatorios exclusivos de esta prueba. No elimina datos preexistentes.
            with admin.transaction():
                admin.execute("RESET ROLE")
                if plan is not None:
                    admin.execute("DELETE FROM public.plans WHERE id=%s", (plan,))
                if group is not None:
                    admin.execute("DELETE FROM public.groups WHERE id=%s", (group,))
                admin.execute("DELETE FROM auth.users WHERE id IN (%s,%s)", (owner, debtor))


if __name__ == "__main__":
    main()
