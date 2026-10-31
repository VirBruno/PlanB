"""Recordatorios manuales con JWT; sólo plan y deudor viajan a la RPC."""
from uuid import UUID

from django.views.decorators.debug import sensitive_variables
from postgrest.exceptions import APIError

from apps.plans.services.expense_service import derived_money
from apps.plans.services.exceptions import PlanNotFound
from apps.users.services import clients
from apps.users.services.exceptions import SessionExpired


class InvalidDebtReminder(Exception):
    pass


class DebtReminderUnavailable(Exception):
    pass


@sensitive_variables()
def notify(token, *, plan_id, debtor_id):
    if not token:
        raise SessionExpired()
    try:
        params = {"p_plan_id": str(UUID(str(plan_id))), "p_debtor_id": str(UUID(str(debtor_id)))}
    except (TypeError, ValueError, AttributeError):
        raise InvalidDebtReminder() from None
    try:
        with clients.public_client(access_token=token) as client:
            result = client.rpc("notify_expense_debt", params).execute().data
        if type(result["created"]) is not bool or not isinstance(result["debtor_username"], str):
            raise ValueError
        return {"notification_id": str(UUID(result["notification_id"])), "created": result["created"],
                "amount": derived_money(result["amount"]), "debtor_username": result["debtor_username"]}
    except Exception as error:
        if isinstance(error, APIError):
            if error.code in ("PGRST301", "PGRST303"):
                raise SessionExpired() from None
            if error.code == "PT404":
                raise PlanNotFound() from None
            if error.code in ("22023", "42501"):
                raise InvalidDebtReminder() from None
        raise DebtReminderUnavailable() from None
