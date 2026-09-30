from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect, render
from django.views.decorators.debug import sensitive_variables
from django.views.decorators.http import require_http_methods

from apps.users.decorators import private_page, supabase_login_required
from apps.users.services import session_service
from .forms import GroupForm
from .services import group_service
from .services.exceptions import GroupNotFound, GroupUnavailable, InvalidGroup


@private_page
@require_http_methods(["GET", "POST"])
@supabase_login_required
@sensitive_variables()
def create(request):
    form = GroupForm(request.POST if request.method == "POST" else None)
    status = 200
    if request.method == "POST" and form.is_valid():
        try:
            group_id = group_service.create_group(
                session_service.get_access_token(request),
                name=form.cleaned_data["name"],
                description=form.cleaned_data["description"],
            )
        except InvalidGroup as error:
            form.add_error(None, str(error))
        except GroupUnavailable:
            form.add_error(None, "No pudimos confirmar la creación. Revisá Mis grupos antes de volver a enviar el formulario.")
            status = 503
        else:
            messages.success(request, "¡Tu grupo está listo!")
            return redirect("groups:detail", group_id=group_id)
    return render(request, "groups/create.html", {
        "form": form, "planb_user": request.planb_user,
    }, status=status)


@private_page
@require_http_methods(["GET"])
@supabase_login_required
@sensitive_variables()
def detail(request, group_id):
    try:
        group = group_service.get_group(session_service.get_access_token(request), group_id)
    except GroupNotFound:
        raise Http404("No encontramos ese grupo.") from None
    except (GroupUnavailable, InvalidGroup):
        return render(request, "groups/detail.html", {
            "unavailable": True, "planb_user": request.planb_user,
        }, status=503)
    return render(request, "groups/detail.html", {
        "group": group, "planb_user": request.planb_user,
    })
