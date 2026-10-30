"""Gastos: aritmética exacta, contratos HTTP reales y fragmentos privados del modal."""
from copy import deepcopy
from decimal import Decimal
import json
from unittest.mock import patch
from uuid import UUID

import httpx
from django.conf import settings
from django.test import Client, SimpleTestCase, TestCase

from apps.plans.expense_forms import ExpenseForm
from apps.plans.services import expense_service as service
from apps.plans.services.exceptions import PlanNotFound
from apps.plans.templatetags.expense_money import expense_balance, expense_money
from apps.plans.tests.test_services import GROUP_ID, PLAN_ID, USER_ID, plan_row
from apps.users.services import clients
from apps.users.services.exceptions import SessionExpired

JUAN = "00000000-0000-0000-0000-000000000001"
MARTINA = "00000000-0000-0000-0000-000000000002"
OUTSIDER = "00000000-0000-0000-0000-000000000003"
EXPENSE = "00000000-0000-0000-0000-000000000010"
MEMBERS = [{"id": JUAN, "username": "Juan"}, {"id": MARTINA, "username": "Martina"},
           {"id": USER_ID, "username": "Facundo"}]


def snapshot(*expenses):
    # Oráculo de fixtures del algoritmo anterior, sólo en tests. Producción usa SQL.
    balances = {p["id"]: Decimal("0.00") for p in MEMBERS}
    for row in expenses:
        balances[row["paid_by"]] += Decimal(str(row["amount"]))
        for part in row["participants"]:
            balances[part["user_id"]] -= Decimal(part["share_amount"])
    creditors = [[uid, value] for uid, value in sorted(balances.items()) if value > 0]
    debtors = [[uid, -value] for uid, value in sorted(balances.items()) if value < 0]
    transfers = []
    i = j = 0
    while i < len(debtors) and j < len(creditors):
        value = min(debtors[i][1], creditors[j][1])
        transfers.append({"from_id": debtors[i][0], "to_id": creditors[j][0], "amount": str(value)})
        debtors[i][1] -= value
        creditors[j][1] -= value
        i += debtors[i][1] == 0
        j += creditors[j][1] == 0
    return {"people": deepcopy(MEMBERS), "members": [{"id": p["id"]} for p in MEMBERS],
            "expenses": list(expenses), "transfers": transfers}


def expense(amount="30000.00", payer=JUAN, parts=None, eid=EXPENSE, **kwargs):
    return {"id": eid, "description": "Cena", "amount": amount, "paid_by": payer,
            "created_at": "2026-10-30T21:00:00Z", "participants": [
                {"user_id": uid, "share_amount": value}
                for uid, value in (parts if parts is not None else [(p["id"], "10000.00") for p in MEMBERS])
            ], **kwargs}


def payload(**kwargs):
    return {"description": "Cena", "amount": "30.000", "paid_by": JUAN,
            "participants": [USER_ID, MARTINA], **kwargs}


class ExpenseCalculationTests(SimpleTestCase):
    def summary(self, *rows):
        return service.summarize(snapshot(*rows), USER_ID)

    def test_empty_has_zero_balance_without_transfers(self):
        result = self.summary()
        self.assertEqual((result["count"], result["total"], result["own_balance"], result["transfers"]),
                         (0, Decimal("0.00"), Decimal("0.00"), []))

    def test_two_parts_and_payer_also_participates(self):
        result = self.summary(expense("20.00", parts=[(JUAN, "10.00"), (USER_ID, "10.00")]))
        self.assertEqual(result["own_balance"], Decimal("-10.00"))
        self.assertEqual(result["transfers"], [{"from_id": USER_ID, "to_id": JUAN,
                         "amount": Decimal("10.00"), "debtor": "Facundo", "creditor": "Juan"}])

    def test_three_parts_with_remaining_cent_preserve_exact_total(self):
        row = expense("100.00", parts=[(JUAN, "33.34"), (MARTINA, "33.33"), (USER_ID, "33.33")])
        result = self.summary(row)
        self.assertEqual(result["total"], Decimal("100.00"))
        self.assertEqual(sum(p["share"] for p in result["people"]), Decimal("100.00"))
        self.assertEqual(sum(p["balance"] for p in result["people"]), Decimal("0.00"))
        self.assertEqual(sum(t["amount"] for t in result["transfers"]), Decimal("66.66"))

    def test_payer_can_be_outside_participants(self):
        result = self.summary(expense("20.00", parts=[(USER_ID, "10.00"), (MARTINA, "10.00")]))
        juan = next(p for p in result["people"] if p["id"] == JUAN)
        self.assertEqual((juan["paid"], juan["share"], juan["balance"]),
                         (Decimal("20.00"), Decimal("0.00"), Decimal("20.00")))
        self.assertEqual(len(result["transfers"]), 2)

    def test_multiple_expenses_paid_totals_and_balances(self):
        result = self.summary(expense(), expense("12000.00", payer=USER_ID,
                              parts=[(JUAN, "6000.00"), (MARTINA, "6000.00")], eid=OUTSIDER))
        self.assertEqual(result["total"], Decimal("42000.00"))
        self.assertEqual(result["own_balance"], Decimal("2000.00"))
        by_id = {p["id"]: p for p in result["people"]}
        self.assertEqual(by_id[JUAN]["paid"], Decimal("30000.00"))
        self.assertEqual(by_id[MARTINA]["paid"], Decimal("0.00"))
        self.assertEqual(sum(p["balance"] for p in result["people"]), 0)

    def test_new_member_does_not_change_historical_shares(self):
        before = snapshot(expense())
        after = deepcopy(before)
        after["people"].append({"id": OUTSIDER, "username": "Pedro"})
        after["members"].append({"id": OUTSIDER})
        original = service.summarize(before, USER_ID)
        changed = service.summarize(after, USER_ID)
        self.assertEqual(original["expenses"], changed["expenses"])
        pedro = next(p for p in changed["people"] if p["id"] == OUTSIDER)
        self.assertEqual(pedro["balance"], Decimal("0.00"))

    def test_departed_member_keeps_history_and_balance(self):
        data = snapshot(expense())
        data["members"] = [p for p in data["members"] if p["id"] != JUAN]
        result = service.summarize(data, USER_ID)
        juan = next(p for p in result["people"] if p["id"] == JUAN)
        self.assertFalse(juan["current_member"])
        self.assertEqual(juan["balance"], Decimal("20000.00"))

    def test_departed_actor_is_rejected_in_snapshot(self):
        data = snapshot()
        data["members"] = [{"id": JUAN}]
        with self.assertRaises(ValueError):
            service.summarize(data, USER_ID)

    def test_multiple_creditors_debtors_settle_deterministically(self):
        balances = {JUAN: Decimal("15000.00"), MARTINA: Decimal("5000.00"),
                    USER_ID: Decimal("-12000.00"), OUTSIDER: Decimal("-8000.00")}
        rows = [{"from_id": OUTSIDER, "to_id": JUAN, "amount": "8000.00"},
                {"from_id": USER_ID, "to_id": JUAN, "amount": "7000.00"},
                {"from_id": USER_ID, "to_id": MARTINA, "amount": "5000.00"}]
        transfers = service.validate_transfers(balances, rows)
        self.assertEqual(transfers, service.validate_transfers(dict(reversed(list(balances.items()))), rows))
        remaining = balances.copy()
        for transfer in transfers:
            remaining[transfer["from_id"]] += transfer["amount"]
            remaining[transfer["to_id"]] -= transfer["amount"]
            self.assertGreater(transfer["amount"], 0)
        self.assertTrue(all(value == 0 for value in remaining.values()))

    def test_settlement_does_not_lose_cents(self):
        balances = {JUAN: Decimal("0.02"), MARTINA: Decimal("0.01"),
                    USER_ID: Decimal("-0.02"), OUTSIDER: Decimal("-0.01")}
        rows = [{"from_id": OUTSIDER, "to_id": JUAN, "amount": "0.01"},
                {"from_id": USER_ID, "to_id": JUAN, "amount": "0.01"},
                {"from_id": USER_ID, "to_id": MARTINA, "amount": "0.01"}]
        self.assertEqual(sum(t["amount"] for t in service.validate_transfers(balances, rows)), Decimal("0.03"))

    def test_zero_part_is_valid_for_one_cent_split_three_ways(self):
        result = self.summary(expense("0.01", parts=[(JUAN, "0.01"), (MARTINA, "0.00"), (USER_ID, "0.00")]))
        self.assertEqual(result["transfers"], [])

    def test_inconsistent_part_totals_are_rejected(self):
        with self.assertRaises(ValueError):
            self.summary(expense("100.00", parts=[(JUAN, "33.33"), (MARTINA, "33.33"), (USER_ID, "33.33")]))

    def test_duplicate_historical_parts_are_rejected(self):
        with self.assertRaises(ValueError):
            self.summary(expense("20.00", parts=[(JUAN, "10.00"), (JUAN, "10.00")]))

    def test_unbalanced_settlement_is_rejected(self):
        with self.assertRaises(ValueError):
            service.validate_transfers({JUAN: Decimal("0.01")}, [])

    def test_money_rejects_invalid_nonfinite_overflow_and_float(self):
        for value in ("NaN", "Infinity", "-Infinity", "0", "-1", "1000000000000", "0.001", "1.000", 0.1, True):
            with self.subTest(value=value), self.assertRaises(service.InvalidExpense):
                service.money(value, positive=True)

    def test_decimal_format_preserves_cents_and_sign(self):
        self.assertEqual(expense_money(Decimal("30000.01")), "$30.000,01")
        self.assertEqual(expense_balance(Decimal("-33.33")), "-$33,33")
        self.assertEqual(expense_balance(Decimal("20.00")), "+$20,00")


class ExpenseFormTests(SimpleTestCase):
    def form(self, **kwargs):
        return ExpenseForm(payload(**kwargs), members=MEMBERS)

    def test_localized_amount_inputs(self):
        for text in ("30.000", "30.000,00", "30000", "30000,00", "30000.00"):
            form = self.form(amount=text)
            self.assertTrue(form.is_valid(), form.errors)
            self.assertEqual(form.cleaned_data["amount"], Decimal("30000"))

    def test_invalid_amount_inputs(self):
        for text in ("", "0", "-2", "NaN", "Infinity", "2,001", "12.34.5", "1000000000000", "1e3"):
            form = self.form(amount=text)
            self.assertFalse(form.is_valid(), text)
            self.assertIn("amount", form.errors)

    def test_concept_required_trimmed_and_limited(self):
        for text in ("", " \t\n ", "x" * 161):
            self.assertFalse(self.form(description=text).is_valid())
        form = self.form(description="  Cena  ")
        self.assertTrue(form.is_valid())
        self.assertEqual(form.cleaned_data["description"], "Cena")

    def test_payer_not_required_to_participate(self):
        self.assertTrue(self.form().is_valid())

    def test_foreign_payer_rejected(self):
        self.assertIn("paid_by", self.form(paid_by=OUTSIDER).errors)

    def test_foreign_participant_rejected(self):
        self.assertIn("participants", self.form(participants=[OUTSIDER]).errors)

    def test_duplicate_participants_rejected(self):
        self.assertIn("participants", self.form(participants=[JUAN, JUAN]).errors)

    def test_at_least_one_participant(self):
        self.assertIn("participants", self.form(participants=[]).errors)

    def test_all_group_is_explicit_flag_without_fake_identity(self):
        form = self.form(participants=[], all_group="on")
        self.assertTrue(form.is_valid(), form.errors)
        self.assertTrue(form.cleaned_data["all_group"])
        self.assertEqual(form.cleaned_data["participants"], [])


class ExpenseSDKTests(SimpleTestCase):
    def setUp(self):
        self.requests, self.responses, self.transports = [], [], []

        def handle(request):
            self.requests.append(request)
            status, body = self.responses.pop(0)
            return httpx.Response(status, json=body)

        def factory():
            transport = httpx.Client(transport=httpx.MockTransport(handle))
            self.transports.append(transport)
            return transport

        patcher = patch.object(clients, "http_client", side_effect=factory)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self.assertFalse(self.responses)
        self.assertTrue(all(t.is_closed for t in self.transports))
        for request in self.requests:
            self.assertEqual(request.headers["authorization"], "Bearer user-jwt")
            self.assertEqual(request.headers["apikey"], settings.SUPABASE_PUBLISHABLE_KEY)

    def create(self, **kwargs):
        return service.create_expense("user-jwt", **{
            "plan_id": PLAN_ID, "description": " Cena ", "amount": Decimal("100.00"),
            "paid_by": JUAN, "participants": [MARTINA, USER_ID], **kwargs})

    def test_create_single_atomic_rpc_with_decimal_string_no_actor(self):
        self.responses = [(200, EXPENSE)]
        self.assertEqual(self.create(), EXPENSE)
        self.assertEqual(len(self.requests), 1)
        request = self.requests[0]
        self.assertEqual(request.url.path, "/rest/v1/rpc/create_plan_expense")
        body = json.loads(request.content)
        self.assertEqual(body["p_amount"], "100.00")
        self.assertEqual(body["p_description"], "Cena")
        self.assertNotIn("created_by", body)
        self.assertFalse(body["p_all_group"])

    def test_all_group_sent_as_flag_for_database_expansion(self):
        self.responses = [(200, EXPENSE)]
        self.create(all_group=True, participants=[])
        body = json.loads(self.requests[0].content)
        self.assertTrue(body["p_all_group"])
        self.assertEqual(body["p_participants"], [])

    def test_read_is_one_rpc_and_uses_persisted_shares(self):
        self.responses = [(200, snapshot(expense()))]
        result = service.get_summary("user-jwt", plan_id=PLAN_ID, actor_id=USER_ID)
        self.assertEqual(result["own_balance"], Decimal("-10000.00"))
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.requests[0].url.path, "/rest/v1/rpc/get_plan_expenses")

    def test_no_token_makes_no_request(self):
        with self.assertRaises(SessionExpired):
            service.get_summary(None, plan_id=PLAN_ID, actor_id=USER_ID)

    def test_malformed_and_duplicate_ids_rejected_before_http(self):
        for kwargs in ({"paid_by": "other"}, {"participants": [JUAN, JUAN]},
                       {"participants": []}, {"all_group": True}, {"amount": 0.1}):
            with self.subTest(kwargs=kwargs), self.assertRaises(service.InvalidExpense):
                self.create(**kwargs)
        self.assertEqual(self.requests, [])

    def test_database_revalidates_membership_and_hides_outsider_plan(self):
        for code, error in (("22023", service.InvalidExpense), ("PT404", PlanNotFound),
                            ("PGRST301", SessionExpired), ("42883", service.ExpenseUnavailable)):
            self.responses = [(400, {"code": code, "message": "Privado", "details": None, "hint": None})]
            with self.subTest(code=code), self.assertRaises(error):
                self.create()

    def test_malformed_summary_fails_closed(self):
        self.responses = [(200, snapshot(expense(amount=30000.0)))]
        with self.assertRaises(service.ExpenseUnavailable):
            service.get_summary("user-jwt", plan_id=PLAN_ID, actor_id=USER_ID)


class ExpenseViewTests(TestCase):
    def setUp(self):
        fixtures = {
            "apps.users.services.session_service.current_user": {"id": USER_ID, "username": "Facundo"},
            "apps.users.services.session_service.get_access_token": "private-jwt",
            "apps.notifications.services.unread_count": 0,
            "apps.plans.views.plan_service.get_plan": plan_row(),
            "apps.plans.views.group_service.get_group": {"id": GROUP_ID, "name": "Amigos", "role": "member"},
            "apps.plans.views.proposal_service.list_proposals": [],
            "apps.plans.views.profile_service.usernames_for_ids": {},
            "apps.plans.expense_views.expense_service.get_summary": service.summarize(snapshot(), USER_ID),
            "apps.plans.expense_views.expense_service.create_expense": EXPENSE,
        }
        self.mocks = {}
        for name, value in fixtures.items():
            patcher = patch(name, return_value=value)
            self.mocks[name.rsplit(".", 1)[1]] = patcher.start()
            self.addCleanup(patcher.stop)
        self.url = f"/planes/{PLAN_ID}/gastos/"

    def test_member_can_open_empty_expenses_fragment(self):
        response = self.client.get(self.url)
        self.assertContains(response, "Aún no hay gastos compartidos en este plan.")
        self.assertContains(response, "+ Agregar primer gasto")
        self.assertContains(response, "$0,00")
        self.assertNotContains(response, "NaN")
        self.assertNotContains(response, "Para saldar")
        self.assertIn("no-store", response.headers["Cache-Control"])

    def test_outsider_cannot_read_or_create(self):
        self.mocks["get_plan"].side_effect = PlanNotFound()
        for method in ("get", "post"):
            self.assertEqual(getattr(self.client, method)(self.url).status_code, 404)
        self.mocks["get_summary"].assert_not_called()
        self.mocks["create_expense"].assert_not_called()

    def test_anonymous_is_redirected_without_expense_reads(self):
        self.mocks["current_user"].return_value = None
        self.assertEqual(self.client.get(self.url).status_code, 302)
        self.mocks["get_summary"].assert_not_called()

    def test_member_creates_without_page_redirect(self):
        response = self.client.post(self.url, payload())
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json(), {"saved": True})
        self.mocks["create_expense"].assert_called_once_with(
            "private-jwt", plan_id=UUID(PLAN_ID), description="Cena",
            amount=Decimal("30000"), paid_by=JUAN, participants=[USER_ID, MARTINA], all_group=False)

    def test_owner_can_create_for_whole_group(self):
        self.mocks["get_group"].return_value["role"] = "owner"
        response = self.client.post(self.url, payload(all_group="on", participants=[]))
        self.assertEqual(response.status_code, 201)
        self.assertTrue(self.mocks["create_expense"].call_args.kwargs["all_group"])

    def test_invalid_member_ids_and_duplicates_never_create(self):
        for data in (payload(paid_by=OUTSIDER), payload(participants=[OUTSIDER]),
                     payload(participants=[JUAN, JUAN]), payload(participants=[])):
            response = self.client.post(self.url, data)
            self.assertEqual(response.status_code, 400)
            self.assertContains(response, "Revisá los datos", status_code=400)
        self.mocks["create_expense"].assert_not_called()

    def test_concurrent_membership_change_returns_form_error(self):
        self.mocks["create_expense"].side_effect = service.InvalidExpense()
        response = self.client.post(self.url, payload())
        self.assertContains(response, "La membresía puede haber cambiado", status_code=400)

    def test_save_then_get_updates_summary_in_same_modal(self):
        self.assertEqual(self.client.post(self.url, payload()).status_code, 201)
        self.mocks["get_summary"].return_value = service.summarize(snapshot(expense()), USER_ID)
        response = self.client.get(self.url)
        self.assertContains(response, "$30.000,00")
        self.assertContains(response, "+$20.000,00")
        self.assertContains(response, "-$10.000,00")
        self.assertContains(response, "Para saldar")
        self.assertContains(response, "Facundo</strong> debe", html=False)
        self.assertNotContains(response, "Notificar")

    def test_unavailable_is_not_misrepresented_as_empty(self):
        self.mocks["get_summary"].side_effect = service.ExpenseUnavailable()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 503)
        self.assertNotContains(response, "Aún no hay gastos", status_code=503)

    def test_uncertain_save_requests_refresh_before_retry(self):
        self.mocks["create_expense"].side_effect = service.ExpenseUnavailable()
        response = self.client.post(self.url, payload())
        self.assertEqual(response.status_code, 503)
        self.assertIn("antes de volver a cargarlo", response.json()["message"])

    def test_csrf_is_required(self):
        self.assertEqual(Client(enforce_csrf_checks=True).post(self.url, payload()).status_code, 403)
        self.mocks["create_expense"].assert_not_called()

    def test_modal_in_plan_detail_with_no_eager_expense_query(self):
        response = self.client.get(f"/planes/{PLAN_ID}/")
        self.assertContains(response, 'data-open-expenses')
        self.assertContains(response, '<dialog id="plan-expenses"')
        self.assertContains(response, "plan-expenses.js")
        self.mocks["get_summary"].assert_not_called()

    def test_all_plan_states_allow_expenses(self):
        for status in (True, False):
            for selected in (None, 47):
                self.mocks["get_plan"].return_value.update(status=status, selected_proposal_id=selected)
                self.assertEqual(self.client.get(self.url).status_code, 200)
                self.assertEqual(self.client.post(self.url, payload()).status_code, 201)

    def test_historical_user_is_visible_but_not_selectable(self):
        data = snapshot(expense())
        data["members"] = [p for p in data["members"] if p["id"] != JUAN]
        self.mocks["get_summary"].return_value = service.summarize(data, USER_ID)
        response = self.client.get(self.url)
        self.assertContains(response, "Exintegrante")
        self.assertNotContains(response, f'<option value="{JUAN}">')

    def test_description_is_escaped(self):
        self.mocks["get_summary"].return_value = service.summarize(
            snapshot(expense(description="<script>alert(1)</script>")), USER_ID)
        response = self.client.get(self.url)
        self.assertContains(response, "&lt;script&gt;")
        self.assertNotContains(response, "<script>alert(1)</script>")
