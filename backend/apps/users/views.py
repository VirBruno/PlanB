"""Flujos HTML: las operaciones con Supabase quedan en servicios."""

import re
import time
from functools import wraps
from urllib.parse import unquote

from django.contrib import messages
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters, sensitive_variables
from django.views.decorators.http import require_http_methods, require_POST

from .decorators import supabase_login_required
from .forms import LoginForm, RegisterForm
from .services import auth_service, session_service
from .services.exceptions import (
    ConfirmationInvalid,
    InvalidCredentials,
    RegistrationRejected,
    ServiceUnavailable,
    SessionExpired,
    UsernameUnavailable,
)


PENDING_CONFIRMATION = "pending_email_confirmation"
CONFIRMATION_TTL = 600
HASH_FORMAT = re.compile(r"[A-Za-z0-9_-]{16,256}\Z")
LOGIN_ERROR = "Usuario/email o contraseña incorrectos."
UNAVAILABLE_ERROR = "No pudimos conectar en este momento. Esperá unos minutos y volvé a intentar."


def private_page(view):
    @wraps(view)
    @never_cache
    def wrapped(request, *args, **kwargs):
        response = view(request, *args, **kwargs)
        response["Referrer-Policy"] = "no-referrer"
        return response
    return wrapped


def safe_next(value):
    """Aceptar únicamente rutas locales, también al decodificar caracteres."""
    fallback = reverse("users:dashboard")
    if not isinstance(value, str):
        return fallback
    decoded = value
    for _ in range(3):
        if (
            not decoded.startswith("/")
            or decoded.startswith("//")
            or "\\" in decoded
            or any(ord(char) < 32 for char in decoded)
            or not url_has_allowed_host_and_scheme(decoded, allowed_hosts=set())
        ):
            return fallback
        next_decoded = unquote(decoded)
        if next_decoded == decoded:
            return value
        decoded = next_decoded
    return fallback


@private_page
@require_http_methods(["GET", "POST"])
@sensitive_post_parameters("password", "password_confirm")
@sensitive_variables()
def register(request):
    form = RegisterForm(request.POST if request.method == "POST" else None)
    status = 200
    if request.method == "POST" and form.is_valid():
        try:
            auth_session = auth_service.sign_up(
                username=form.cleaned_data["username"],
                email=form.cleaned_data["email"],
                password=form.cleaned_data["password"],
            )
            if auth_session is None:
                messages.info(request, "Revisá tu correo para continuar con el registro.")
                return redirect("users:email_confirmation")
            session_service.establish_session(request, auth_session)
        except UsernameUnavailable:
            form.add_error("username", "Ese nombre de usuario ya está en uso. Probá con otro.")
        except RegistrationRejected:
            form.add_error(None, "No pudimos completar el registro. Revisá los datos o intentá iniciar sesión.")
        except (ServiceUnavailable, SessionExpired):
            form.add_error(None, UNAVAILABLE_ERROR)
            status = 503
        else:
            messages.success(request, "¡Tu cuenta está lista! Te damos la bienvenida a Plan B.")
            return redirect("users:dashboard")
    return render(request, "users/register.html", {"form": form}, status=status)


@private_page
@require_http_methods(["GET", "POST"])
@sensitive_post_parameters("password")
@sensitive_variables()
def login(request):
    form = LoginForm(request.POST if request.method == "POST" else None)
    destination = safe_next(request.POST.get("next", request.GET.get("next", "")))
    status = 200
    if request.method == "POST" and form.is_valid():
        try:
            auth_session = auth_service.sign_in(
                identifier=form.cleaned_data["identifier"],
                password=form.cleaned_data["password"],
            )
            session_service.establish_session(request, auth_session)
        except (InvalidCredentials, SessionExpired):
            form.add_error(None, LOGIN_ERROR)
        except ServiceUnavailable:
            form.add_error(None, UNAVAILABLE_ERROR)
            status = 503
        else:
            return redirect(destination)
    return render(request, "users/login.html", {"form": form, "next": destination}, status=status)


@private_page
@require_http_methods(["GET", "POST"])
@sensitive_variables()
def email_confirmation(request):
    if request.method == "GET" and "token_hash" in request.GET:
        token_hash = request.GET.get("token_hash", "")
        request.session.pop(PENDING_CONFIRMATION, None)
        if HASH_FORMAT.fullmatch(token_hash):
            request.session[PENDING_CONFIRMATION] = {
                "token_hash": token_hash, "expires_at": time.time() + CONFIRMATION_TTL,
            }
        else:
            messages.error(request, "El enlace de confirmación no es válido.")
        return redirect("users:email_confirmation")

    if request.method == "POST":
        pending = request.session.pop(PENDING_CONFIRMATION, None)
        # Consumir antes de llamar al proveedor, también si luego hay un error.
        request.session.save()
        if not pending or pending.get("expires_at", 0) <= time.time():
            messages.error(request, "El enlace venció o ya fue utilizado. Volvé a abrir el enlace de tu correo.")
            return redirect("users:email_confirmation")
        try:
            auth_service.verify_email(pending["token_hash"])
        except ConfirmationInvalid:
            messages.error(request, "El enlace venció o ya fue utilizado. Volvé a abrir el enlace de tu correo.")
            return redirect("users:email_confirmation")
        except ServiceUnavailable:
            messages.error(request, "No pudimos verificar el email. Volvé a abrir el enlace de tu correo e intentá nuevamente.")
            return redirect("users:email_confirmation")
        messages.success(request, "Email confirmado. Ya podés iniciar sesión.")
        return redirect("users:login")

    pending = request.session.get(PENDING_CONFIRMATION)
    can_confirm = bool(pending and pending.get("expires_at", 0) > time.time())
    if pending and not can_confirm:
        request.session.pop(PENDING_CONFIRMATION, None)
    return render(request, "users/email_confirmation.html", {"can_confirm": can_confirm})


@private_page
@require_http_methods(["GET"])
@supabase_login_required
def dashboard(request):
    return render(request, "users/dashboard.html", {"planb_user": request.planb_user})


@private_page
@require_POST
def logout(request):
    session_service.logout(request)
    messages.success(request, "Cerraste tu sesión en este dispositivo.")
    return redirect("users:login")
