"""Gastos históricos con JWT/RLS; dinero decimal y saldos derivados en memoria."""
from decimal import Decimal, InvalidOperation
from uuid import UUID

from django.utils.dateparse import parse_datetime
from django.views.decorators.debug import sensitive_variables
from postgrest.exceptions import APIError

from apps.users.services import clients
from apps.users.services.exceptions import SessionExpired
from .exceptions import PlanNotFound

CENT = Decimal("0.01")
ZERO = Decimal("0.00")
MAX_AMOUNT = Decimal("999999999999.99")  # numeric(14, 2)


class InvalidExpense(Exception):
    def __init__(self):
        super().__init__("Revisá el concepto, el monto y los integrantes. La membresía puede haber cambiado.")


class ExpenseUnavailable(Exception):
    def __init__(self):
        super().__init__("No pudimos consultar los gastos. Volvé a intentar en unos minutos.")


def canonical_id(value):
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        raise InvalidExpense() from None


def money(value, *, positive=False):
    # No aceptar floats, ni siquiera si parecen tener dos decimales.
    if not isinstance(value, (str, Decimal, int)) or isinstance(value, bool):
        raise InvalidExpense()
    try:
        amount = Decimal(value)
        if (not amount.is_finite() or amount < 0 or amount > MAX_AMOUNT
                or (positive and amount == 0) or amount.as_tuple().exponent < -2
                or amount != amount.quantize(CENT)):
            raise InvalidExpense()
        return amount.quantize(CENT)
    except (InvalidOperation, ValueError):
        raise InvalidExpense() from None


def settle_balances(balances):
    """UUID ascendente para ambos lados; cada transferencia agota al menos un saldo."""
    if sum(balances.values(), ZERO) != ZERO:
        raise ValueError("Los balances no cierran en cero.")
    creditors = [[uid, amount] for uid, amount in sorted(balances.items()) if amount > 0]
    debtors = [[uid, -amount] for uid, amount in sorted(balances.items()) if amount < 0]
    transfers = []
    i = j = 0
    while i < len(debtors) and j < len(creditors):
        amount = min(debtors[i][1], creditors[j][1])
        transfers.append({"from_id": debtors[i][0], "to_id": creditors[j][0], "amount": amount})
        debtors[i][1] -= amount
        creditors[j][1] -= amount
        i += debtors[i][1] == ZERO
        j += creditors[j][1] == ZERO
    return transfers


def summarize(snapshot, actor_id):
    """Valida la respuesta y usa las partes persistidas, nunca membresías futuras."""
    people = {}
    for row in snapshot["people"]:
        uid = canonical_id(row["id"])
        if uid in people or not isinstance(row["username"], str):
            raise ValueError
        people[uid] = {"id": uid, "username": row["username"], "paid": ZERO, "share": ZERO}
    members = []
    member_ids = set()
    for row in snapshot["members"]:
        uid = canonical_id(row["id"])
        if uid in member_ids or uid not in people:
            raise ValueError
        member_ids.add(uid)
        members.append({"id": uid, "username": people[uid]["username"]})
    if actor_id not in member_ids:
        raise ValueError
    total = ZERO
    expenses = []
    expense_ids = set()
    for row in snapshot["expenses"]:
        eid = canonical_id(row["id"])
        payer = canonical_id(row["paid_by"])
        amount = money(row["amount"], positive=True)
        created_at = parse_datetime(row["created_at"])
        description = row["description"]
        if (eid in expense_ids or created_at is None or not isinstance(description, str)
                or not 1 <= len(description.strip()) <= 160):
            raise ValueError
        expense_ids.add(eid)
        shares = []
        seen = set()
        for part in row["participants"]:
            uid = canonical_id(part["user_id"])
            if uid in seen:
                raise ValueError
            seen.add(uid)
            share = money(part["share_amount"])
            people[uid]["share"] += share
            shares.append({"username": people[uid]["username"], "share_amount": share})
        if not seen or sum((part["share_amount"] for part in shares), ZERO) != amount:
            raise ValueError
        people[payer]["paid"] += amount
        total += amount
        expenses.append({"id": eid, "description": description, "amount": amount,
                         "payer": people[payer]["username"], "created_at": created_at,
                         "participants": shares})
    balances = {}
    for uid, person in people.items():
        person["current_member"] = uid in member_ids
        person["balance"] = person["paid"] - person["share"]
        balances[uid] = person["balance"]
    transfers = settle_balances(balances)
    for transfer in transfers:
        transfer["debtor"] = people[transfer["from_id"]]["username"]
        transfer["creditor"] = people[transfer["to_id"]]["username"]
    return {"total": total, "count": len(expenses), "expenses": expenses, "members": members,
            "people": sorted(people.values(), key=lambda p: (p["username"].lower(), p["id"])),
            "own_balance": balances[actor_id], "transfers": transfers}


def _translate(error):
    if isinstance(error, APIError):
        if error.code in ("PGRST301", "PGRST303"):
            raise SessionExpired() from None
        if error.code == "PT404":
            raise PlanNotFound() from None
        if error.code in ("22023", "23514", "42501"):
            raise InvalidExpense() from None
    raise ExpenseUnavailable() from None


@sensitive_variables()
def get_summary(token, *, plan_id, actor_id):
    if not token:
        raise SessionExpired()
    try:
        with clients.public_client(access_token=token) as client:
            snapshot = client.rpc("get_plan_expenses", {"p_plan_id": canonical_id(plan_id)}).execute().data
        return summarize(snapshot, canonical_id(actor_id))
    except Exception as error:
        _translate(error)


@sensitive_variables()
def create_expense(token, *, plan_id, description, amount, paid_by, participants, all_group=False):
    if not token:
        raise SessionExpired()
    if (not isinstance(description, str) or not 1 <= len(description.strip()) <= 160
            or type(all_group) is not bool or not isinstance(participants, (list, tuple))):
        raise InvalidExpense()
    ids = [canonical_id(uid) for uid in participants]
    if len(set(ids)) != len(ids) or (not all_group and not ids) or (all_group and ids):
        raise InvalidExpense()
    params = {"p_plan_id": canonical_id(plan_id), "p_description": description.strip(),
              "p_amount": str(money(amount, positive=True)), "p_paid_by": canonical_id(paid_by),
              "p_participants": ids, "p_all_group": all_group}
    try:
        with clients.public_client(access_token=token) as client:
            result = client.rpc("create_plan_expense", params).execute().data
        return canonical_id(result)
    except Exception as error:
        _translate(error)
