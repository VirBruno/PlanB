"""Avisos de deuda: permisos, SDK, snapshots privados y compatibilidad de bandeja."""
from decimal import Decimal
import json
import re
from unittest.mock import patch
from zoneinfo import ZoneInfo

import httpx
from django.conf import settings
from django.test import Client, SimpleTestCase, TestCase
from django.utils import timezone

from apps.groups.services.invitation_service import InvitationUnavailable
from apps.notifications import debts, services
from apps.notifications.tests.test_services import NOTIFICATION_ID, notification
from apps.notifications.tests.test_plan_confirmed import confirmed
from apps.notifications.tests.test_reminder_inbox import reminder
from apps.plans.services import expense_service
from apps.plans.services.exceptions import PlanNotFound
from apps.plans.tests import test_expenses as expense_tests
from apps.plans.tests.test_expenses import (
    JUAN, MARTINA, OUTSIDER, expense, snapshot,
)
from apps.plans.tests.test_services import PLAN_ID, USER_ID
from apps.users.services import clients
from apps.users.services.exceptions import SessionExpired


def debt_notice(**kwargs):
    return {"id": NOTIFICATION_ID, "type": "expense_debt_reminder",
            "created_at": "2026-10-30T01:22:00Z", "read_at": None,
            "plan_id": PLAN_ID, "plan_name": "Cena del viernes", "plan_available": True,
            "group_id": "9f3bb3a8-50ea-4f9d-a7c5-80e5ed548e8e", "group_name": "Amigos",
            "creditor_id": JUAN, "creditor_username": "Juan", "expense_amount": "10000.00", **kwargs}


class DebtActionTests(TestCase):
    def setUp(self):
        expense_tests.ExpenseViewTests.setUp(self)
        self.mocks["get_summary"].return_value = expense_service.summarize(snapshot(expense()), JUAN)
        self.mocks["current_user"].return_value = {"id": JUAN, "username": "Juan"}
        patcher = patch("apps.plans.expense_views.debts.notify", return_value={
            "notification_id": NOTIFICATION_ID, "created": True, "amount": Decimal("10000.00"),
            "debtor_username": "Facundo"})
        self.notify = patcher.start()
        self.addCleanup(patcher.stop)
        self.action = f"/planes/{PLAN_ID}/gastos/notificar/"

    def test_creditor_sees_buttons_for_both_debtors(self):
        response = self.client.get(self.url)
        self.assertContains(response, "Notificar a Facundo")
        self.assertContains(response, "Notificar a Martina")
        forms = re.findall(r'<form[^>]*data-debt-reminder-form>.*?</form>', response.content.decode(), re.S)
        self.assertEqual(len(forms), 2)
        for form in forms:
            self.assertNotIn('name="amount"', form)
            self.assertNotIn('name="creditor_id"', form)

    def test_debtor_does_not_see_buttons(self):
        self.mocks["current_user"].return_value = {"id": USER_ID, "username": "Facundo"}
        self.assertNotContains(self.client.get(self.url), "data-debt-reminder-form")

    def test_third_member_does_not_see_buttons(self):
        self.mocks["current_user"].return_value = {"id": MARTINA, "username": "Martina"}
        self.assertNotContains(self.client.get(self.url), "data-debt-reminder-form")

    def test_creditor_sends_only_plan_and_debtor(self):
        response = self.client.post(self.action, {"debtor_id": USER_ID})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"created": True, "message": "Notificación enviada a Facundo."})
        self.notify.assert_called_once()
        self.assertEqual(set(self.notify.call_args.kwargs), {"plan_id", "debtor_id"})
        self.assertEqual(str(self.notify.call_args.kwargs["plan_id"]), PLAN_ID)
        self.assertEqual(self.notify.call_args.kwargs["debtor_id"], USER_ID)

    def test_repeat_returns_pending_feedback(self):
        self.notify.return_value["created"] = False
        response = self.client.post(self.action, {"debtor_id": USER_ID})
        self.assertIn("ya tiene un recordatorio pendiente", response.json()["message"])

    def test_noncreditor_and_self_notification_are_rejected(self):
        for actor, debtor in ((MARTINA, USER_ID), (USER_ID, USER_ID), (USER_ID, MARTINA)):
            self.mocks["current_user"].return_value = {"id": actor, "username": "Miembro"}
            self.assertEqual(self.client.post(self.action, {"debtor_id": debtor}).status_code, 403)
        self.notify.assert_not_called()

    def test_outsider_never_calls_rpc(self):
        self.mocks["get_plan"].side_effect = PlanNotFound()
        self.assertEqual(self.client.post(self.action, {"debtor_id": USER_ID}).status_code, 404)
        self.notify.assert_not_called()

    def test_foreign_or_creditor_recipient_rejected(self):
        for recipient in (OUTSIDER, JUAN):
            self.assertEqual(self.client.post(self.action, {"debtor_id": recipient}).status_code, 403)
        self.notify.assert_not_called()

    def test_frontend_amount_creditor_and_recipient_fields_are_rejected(self):
        for key in ("amount", "creditor_id", "user_id", "plan_id"):
            self.assertEqual(self.client.post(self.action, {"debtor_id": USER_ID, key: "forged"}).status_code, 400)
        self.notify.assert_not_called()

    def test_repeated_debtor_field_rejected(self):
        self.assertEqual(self.client.post(self.action, {"debtor_id": [USER_ID, MARTINA]}).status_code, 400)
        self.notify.assert_not_called()

    def test_missing_or_invalid_id_rejected(self):
        self.assertEqual(self.client.post(self.action, {}).status_code, 400)
        self.assertEqual(self.client.post(self.action, {"debtor_id": "invalid"}).status_code, 409)

    def test_no_longer_existing_transfer_rejected(self):
        self.mocks["get_summary"].return_value = expense_service.summarize(snapshot(), JUAN)
        self.assertEqual(self.client.post(self.action, {"debtor_id": USER_ID}).status_code, 403)
        self.notify.assert_not_called()

    def test_database_rechecks_after_snapshot_changed(self):
        self.notify.side_effect = debts.InvalidDebtReminder()
        self.assertEqual(self.client.post(self.action, {"debtor_id": USER_ID}).status_code, 409)

    def test_former_debtor_not_notifiable_but_still_in_balances(self):
        data = snapshot(expense())
        data["members"] = [p for p in data["members"] if p["id"] != USER_ID]
        self.mocks["get_summary"].return_value = expense_service.summarize(data, JUAN)
        response = self.client.get(self.url)
        self.assertContains(response, "Exintegrante")
        self.assertNotContains(response, "Notificar a Facundo")
        self.assertContains(response, "Notificar a Martina")
        self.assertEqual(self.client.post(self.action, {"debtor_id": USER_ID}).status_code, 403)

    def test_action_requires_post_and_csrf(self):
        self.assertEqual(self.client.get(self.action).status_code, 405)
        self.assertEqual(Client(enforce_csrf_checks=True).post(self.action, {"debtor_id": USER_ID}).status_code, 403)
        self.notify.assert_not_called()

    def test_deep_link_sets_auto_open_without_bypassing_plan_access(self):
        response = self.client.get(f"/planes/{PLAN_ID}/?open=expenses")
        self.assertContains(response, "data-auto-open")
        self.mocks["get_plan"].side_effect = PlanNotFound()
        self.assertEqual(self.client.get(f"/planes/{PLAN_ID}/?open=expenses").status_code, 404)

    def test_no_auto_open_on_unrelated_query(self):
        self.assertNotContains(self.client.get(f"/planes/{PLAN_ID}/?open=other"), "data-auto-open")

    def test_uncertain_rpc_response_is_retryable_without_success_claim(self):
        self.notify.side_effect = debts.DebtReminderUnavailable()
        response = self.client.post(self.action, {"debtor_id": USER_ID})
        self.assertEqual(response.status_code, 503)
        self.assertIn("no se duplican", response.json()["message"])


class DebtSDKTests(SimpleTestCase):
    def setUp(self):
        expense_tests.ExpenseSDKTests.setUp(self)

    def tearDown(self):
        expense_tests.ExpenseSDKTests.tearDown(self)

    def test_rpc_never_sends_amount_creditor_or_notification_id(self):
        self.responses = [(200, {"notification_id": NOTIFICATION_ID, "created": True,
                                 "amount": "10000.01", "debtor_username": "Facundo"})]
        result = debts.notify("user-jwt", plan_id=PLAN_ID, debtor_id=USER_ID)
        self.assertEqual(result["amount"], Decimal("10000.01"))
        self.assertEqual(json.loads(self.requests[0].content), {"p_plan_id": PLAN_ID, "p_debtor_id": USER_ID})
        self.assertEqual(self.requests[0].url.path, "/rest/v1/rpc/notify_expense_debt")

    def test_pending_update_contract(self):
        self.responses = [(200, {"notification_id": NOTIFICATION_ID, "created": False,
                                 "amount": "12000.00", "debtor_username": "Facundo"})]
        self.assertFalse(debts.notify("user-jwt", plan_id=PLAN_ID, debtor_id=USER_ID)["created"])

    def test_database_invalid_transfer_is_not_success(self):
        for code, error in (("22023", debts.InvalidDebtReminder), ("PT404", PlanNotFound),
                            ("42501", debts.InvalidDebtReminder), ("PGRST301", SessionExpired),
                            ("PGRST202", debts.DebtReminderUnavailable)):
            self.responses = [(400, {"code": code, "message": "Privado", "hint": None, "details": None})]
            with self.subTest(code=code), self.assertRaises(error):
                debts.notify("user-jwt", plan_id=PLAN_ID, debtor_id=USER_ID)

    def test_missing_token_or_bad_ids_never_make_http_request(self):
        with self.assertRaises(SessionExpired):
            debts.notify("", plan_id=PLAN_ID, debtor_id=USER_ID)
        with self.assertRaises(debts.InvalidDebtReminder):
            debts.notify("user-jwt", plan_id=PLAN_ID, debtor_id="bad")
        self.assertFalse(self.requests)

    def test_malformed_monetary_response_fails_closed(self):
        for amount in ("NaN", "0", "-1", 0.1):
            self.responses = [(200, {"notification_id": NOTIFICATION_ID, "created": True,
                                     "amount": amount, "debtor_username": "Facundo"})]
            with self.assertRaises(debts.DebtReminderUnavailable):
                debts.notify("user-jwt", plan_id=PLAN_ID, debtor_id=USER_ID)


class DebtInboxTests(TestCase):
    def setUp(self):
        self.rows = [debt_notice()]
        self.requests, self.transports = [], []
        for name, value in (("current_user", {"id": USER_ID, "username": "Facundo"}),
                            ("get_access_token", "debt-jwt")):
            patcher = patch("apps.users.services.session_service." + name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.object(clients, "http_client", side_effect=self.transport)
        patcher.start()
        self.addCleanup(patcher.stop)

    def transport(self):
        transport = httpx.Client(transport=httpx.MockTransport(self.handle))
        self.transports.append(transport)
        return transport

    def handle(self, request):
        self.requests.append(request)
        self.assertEqual(request.headers["authorization"], "Bearer debt-jwt")
        self.assertEqual(request.headers["apikey"], settings.SUPABASE_PUBLISHABLE_KEY)
        path = request.url.path.rsplit("/", 1)[-1]
        if path == "notifications":
            count = sum(row["read_at"] is None for row in self.rows)
            return httpx.Response(200, headers={"content-range": f"*/{count}"})
        if path == "list_notifications":
            offset = (int(request.url.params["p_page"]) - 1) * 12
            return httpx.Response(200, json=self.rows[offset:offset + 13])
        if path == "mark_notification_read":
            self.rows[0]["read_at"] = "2026-10-30T12:00:00Z"
            return httpx.Response(200, json=NOTIFICATION_ID)
        raise AssertionError(request.url)

    def tearDown(self):
        self.assertTrue(all(t.is_closed for t in self.transports))

    def test_debt_in_inbox_badge_and_deep_link(self):
        response = self.client.get("/notificaciones/")
        self.assertContains(response, "Saldo pendiente · Cena del viernes")
        self.assertContains(response, "$10.000,00")
        self.assertContains(response, "a <strong>Juan</strong>")
        self.assertContains(response, f'href="/planes/{PLAN_ID}/?open=expenses"')
        self.assertContains(response, "Notificaciones: 1 sin leer")
        self.assertIn("no-store", response["Cache-Control"])

    def test_all_four_types_keep_shared_inbox(self):
        self.rows += [confirmed(), reminder(), notification()]
        response = self.client.get("/notificaciones/")
        for text in ("Saldo pendiente", "Plan confirmado", "RECORDATORIO", "Aceptar invitación", "Notificaciones: 4 sin leer"):
            self.assertContains(response, text)

    def test_mark_read_uses_existing_csrf_action_and_badge(self):
        browser = Client(enforce_csrf_checks=True)
        browser.get("/notificaciones/")
        response = browser.post(f"/notificaciones/{NOTIFICATION_ID}/leida/", {
            "csrfmiddlewaretoken": browser.cookies["csrftoken"].value}, follow=True)
        self.assertNotContains(response, "Marcar como leída")
        self.assertNotContains(response, 'class="pb-notification-dot"')
        self.assertIsNotNone(self.rows[0]["read_at"])

    def test_lost_membership_hides_link_but_preserves_notice(self):
        self.rows = [debt_notice(plan_available=False, group_id=None, group_name=None)]
        response = self.client.get("/notificaciones/")
        self.assertContains(response, "Saldo pendiente")
        self.assertContains(response, "Ya no tenés acceso")
        self.assertNotContains(response, f"/planes/{PLAN_ID}/")

    def test_pagination_for_expense_notices(self):
        self.rows = [debt_notice(id=f"00000000-0000-0000-0000-{i:012d}") for i in range(13)]
        self.assertContains(self.client.get("/notificaciones/"), "Saldo pendiente ·", count=12)
        self.assertContains(self.client.get("/notificaciones/"), "?page=2")
        self.assertContains(self.client.get("/notificaciones/?page=2"), "Saldo pendiente ·", count=1)

    def test_history_amount_snapshot_and_names_are_escaped(self):
        self.rows = [debt_notice(creditor_username="<script>Juan</script>", plan_name="<b>Cena</b>", expense_amount="12000.01")]
        response = self.client.get("/notificaciones/")
        self.assertContains(response, "$12.000,01")
        self.assertContains(response, "&lt;script&gt;Juan")
        self.assertNotContains(response, "<script>Juan")
        self.assertContains(response, "Saldo al momento del aviso")

    def test_timestamp_obeys_active_timezone(self):
        with timezone.override(ZoneInfo("America/Argentina/Buenos_Aires")):
            response = self.client.get("/notificaciones/")
        self.assertContains(response, "2026-10-29T22:22:00-03:00")

    def test_payload_is_not_authority_for_references(self):
        self.rows = [debt_notice(payload={"plan_id": "fake", "creditor_id": "fake", "expense_amount": "1"})]
        item = services.list_notifications("debt-jwt")["items"][0]
        self.assertEqual(item["plan_id"], PLAN_ID)
        self.assertEqual(item["creditor_id"], JUAN)
        self.assertEqual(item["expense_amount"], Decimal("10000.00"))
        self.assertNotIn("payload", item)

    def test_invalid_rows_fail_closed(self):
        for change in ({"expense_amount": "NaN"}, {"expense_amount": 0.1}, {"creditor_id": "bad"},
                       {"expense_amount": "0"}, {"creditor_username": None}, {"plan_available": "true"}):
            self.rows = [debt_notice(**change)]
            with self.subTest(change=change), self.assertRaises(InvitationUnavailable):
                services.list_notifications("debt-jwt")


class TransferValidationTests(SimpleTestCase):
    def test_missing_shared_source_is_not_recomputed_locally(self):
        data = snapshot(expense())
        del data["transfers"]
        with self.assertRaises(KeyError):
            expense_service.summarize(data, USER_ID)

    def test_partial_forged_or_overpaid_transfers_are_rejected(self):
        for rows in ([], [{"from_id": USER_ID, "to_id": JUAN, "amount": "30000.00"}],
                     [{"from_id": JUAN, "to_id": USER_ID, "amount": "1.00"}]):
            data = snapshot(expense())
            data["transfers"] = rows
            with self.assertRaises(ValueError):
                expense_service.summarize(data, USER_ID)
