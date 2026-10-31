"""Fragmentos del popup, sin pantalla adicional ni navegación fuera del plan."""
from django.http import Http404, JsonResponse
from django.shortcuts import render
from django.views.decorators.debug import sensitive_variables
from django.views.decorators.http import require_http_methods, require_POST

from apps.groups.services.exceptions import GroupNotFound, GroupUnavailable, InvalidGroup
from apps.users.decorators import private_page, supabase_login_required
from apps.users.services import session_service
from apps.notifications import debts
from .expense_forms import ExpenseForm
from .services import expense_service
from .services.exceptions import PlanNotFound, PlanUnavailable
from .views import _plan_and_group


@private_page
@require_http_methods(["GET", "POST"])
@supabase_login_required
@sensitive_variables()
def expenses(request, plan_id):
    token = session_service.get_access_token(request)
    try:
        plan, _ = _plan_and_group(request, plan_id)
        summary = expense_service.get_summary(token, plan_id=plan_id, actor_id=request.planb_user["id"])
    except (PlanNotFound, GroupNotFound):
        raise Http404("No encontramos ese plan.") from None
    except (PlanUnavailable, GroupUnavailable, InvalidGroup, expense_service.ExpenseUnavailable,
            expense_service.InvalidExpense):
        return JsonResponse({"message": "No pudimos consultar los gastos. Volvé a intentar."}, status=503)
    form = ExpenseForm(request.POST if request.method == "POST" else None, members=summary["members"])
    if request.method == "POST":
        if form.is_valid():
            try:
                expense_service.create_expense(token, plan_id=plan_id, **form.cleaned_data)
            except PlanNotFound:
                raise Http404("No encontramos ese plan.") from None
            except expense_service.InvalidExpense as error:
                form.add_error(None, str(error))
            except expense_service.ExpenseUnavailable:
                # No afirmar que falló: la conexión pudo cortarse después del COMMIT.
                return JsonResponse({"message": "No pudimos confirmar el guardado. Cerrá el formulario y actualizá los gastos antes de volver a cargarlo."}, status=503)
            else:
                # La lectura posterior va en otro GET: un fallo al refrescar no reenvía el gasto.
                return JsonResponse({"saved": True}, status=201)
        return render(request, "plans/expenses/form.html", {"form": form, "plan": plan}, status=400)
    member_ids = {p["id"] for p in summary["members"]}
    for transfer in summary["transfers"]:
        transfer["can_notify"] = (transfer["to_id"] == request.planb_user["id"]
                                  and transfer["from_id"] in member_ids)
    return render(request, "plans/expenses/panel.html", {"summary": summary, "form": form, "plan": plan})


@private_page
@require_POST
@supabase_login_required
@sensitive_variables()
def notify_debt(request, plan_id):
    if set(request.POST) - {"debtor_id", "csrfmiddlewaretoken"} or len(request.POST.getlist("debtor_id")) != 1:
        return JsonResponse({"message": "Enviá únicamente el integrante a notificar."}, status=400)
    token = session_service.get_access_token(request)
    try:
        _plan_and_group(request, plan_id)
        debtor_id = expense_service.canonical_id(request.POST.get("debtor_id"))
        summary = expense_service.get_summary(token, plan_id=plan_id, actor_id=request.planb_user["id"])
        members = {p["id"] for p in summary["members"]}
        if debtor_id not in members or not any(
            t["from_id"] == debtor_id and t["to_id"] == request.planb_user["id"]
            for t in summary["transfers"]
        ):
            return JsonResponse({"message": "Sólo el acreedor de una transferencia pendiente puede notificarla."}, status=403)
        # La RPC revalida bajo locks: la deuda pudo cambiar desde la lectura anterior.
        result = debts.notify(token, plan_id=plan_id, debtor_id=debtor_id)
    except (PlanNotFound, GroupNotFound):
        raise Http404("No encontramos ese plan.") from None
    except (expense_service.InvalidExpense, debts.InvalidDebtReminder):
        return JsonResponse({"message": "La transferencia ya no está disponible. Actualizá los gastos."}, status=409)
    except (PlanUnavailable, GroupUnavailable, InvalidGroup, expense_service.ExpenseUnavailable,
            debts.DebtReminderUnavailable):
        return JsonResponse({"message": "No pudimos confirmar el aviso. Podés reintentar; los pendientes no se duplican."}, status=503)
    name = result["debtor_username"]
    message = (f"Notificación enviada a {name}." if result["created"] else
               f"{name} ya tiene un recordatorio pendiente; se actualizó con el monto actual.")
    return JsonResponse({"message": message, "created": result["created"]})
