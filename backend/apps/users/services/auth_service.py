"""Auth de Supabase: entrada y salida controladas, sin estado entre requests."""

from uuid import UUID

import httpx
from django.conf import settings
from django.views.decorators.debug import sensitive_variables

from . import clients, profile_service
from .exceptions import (
    AuthError,
    ConfirmationInvalid,
    InvalidCredentials,
    RegistrationRejected,
    ServiceUnavailable,
    SessionExpired,
    UsernameUnavailable,
)
from .types import AuthSession


def _status(error):
    try:
        return int(getattr(error, "status", 0))
    except (TypeError, ValueError):
        return 0


def _temporary(error):
    status = _status(error)
    return isinstance(error, httpx.HTTPError) or status in (408, 429) or status >= 500


@sensitive_variables()
def _session(response):
    try:
        session = response.session
        if not session or not session.access_token or not session.refresh_token:
            raise ValueError
        return AuthSession(
            access_token=session.access_token,
            refresh_token=session.refresh_token,
            expires_at=int(session.expires_at),
            user_id=str(UUID(session.user.id)),
        )
    except (AttributeError, ValueError, TypeError):
        raise ServiceUnavailable() from None


@sensitive_variables()
def sign_up(username, email, password):
    if not profile_service.username_available(username):
        raise UsernameUnavailable()
    try:
        with clients.public_client() as client:
            response = client.auth.sign_up({
                "email": email.strip().lower(),
                "password": password,
                "options": {
                    "data": {"username": username.strip()},
                    "email_redirect_to": (
                        settings.DJANGO_PUBLIC_URL.rstrip("/") + "/auth/confirmar-email/"
                    ),
                },
            })
    except Exception as error:
        code = getattr(error, "code", None)
        if code in ("user_already_exists", "email_exists"):
            # La pantalla de correo no afirma si se creó una nueva identidad.
            return None
        if code in ("unexpected_failure", "database_error", "23505"):
            # El trigger puede resolver una carrera que la consulta previa no vio.
            if not profile_service.username_available(username):
                raise UsernameUnavailable() from None
        if _temporary(error) or not _status(error):
            raise ServiceUnavailable() from None
        raise RegistrationRejected() from None
    # Una respuesta ofuscada por email existente también puede no tener sesión.
    return _session(response) if response and response.session else None


@sensitive_variables()
def sign_in(identifier, password):
    identifier = identifier.strip()
    email = identifier.lower() if "@" in identifier else profile_service.resolve_email(identifier)
    if not email:
        raise InvalidCredentials()
    try:
        with clients.public_client() as client:
            response = client.auth.sign_in_with_password({"email": email, "password": password})
    except Exception as error:
        if _temporary(error) or not _status(error):
            raise ServiceUnavailable() from None
        raise InvalidCredentials() from None
    if not response or not response.session:
        raise InvalidCredentials()
    return _session(response)


@sensitive_variables()
def get_user(access_token):
    if not access_token:
        raise SessionExpired()
    try:
        with clients.public_client() as client:
            response = client.auth.get_user(jwt=access_token)
        if not response or not response.user:
            raise SessionExpired()
        return str(UUID(response.user.id))
    except AuthError:
        raise
    except Exception as error:
        if _temporary(error) or not _status(error):
            raise ServiceUnavailable() from None
        raise SessionExpired() from None


@sensitive_variables()
def refresh(refresh_token):
    if not refresh_token:
        raise SessionExpired()
    try:
        with clients.public_client() as client:
            response = client.auth.refresh_session(refresh_token=refresh_token)
    except Exception as error:
        if _temporary(error) or not _status(error):
            raise ServiceUnavailable() from None
        raise SessionExpired() from None
    if not response or not response.session:
        raise SessionExpired()
    return _session(response)


@sensitive_variables()
def sign_out(access_token):
    """Revoca sólo esta sesión usando los privilegios del propio usuario."""
    if not access_token:
        raise SessionExpired()
    try:
        with clients.http_client() as transport:
            response = transport.post(
                settings.SUPABASE_URL.rstrip("/") + "/auth/v1/logout",
                params={"scope": "local"},
                headers={
                    "apikey": settings.SUPABASE_PUBLISHABLE_KEY,
                    "Authorization": f"Bearer {access_token}",
                },
            )
        if response.status_code in (401, 403):
            raise SessionExpired()
        if not 200 <= response.status_code < 300:
            raise ServiceUnavailable()
    except httpx.HTTPError:
        raise ServiceUnavailable() from None


@sensitive_variables()
def verify_email(token_hash):
    try:
        with clients.public_client() as client:
            response = client.auth.verify_otp({"token_hash": token_hash, "type": "email"})
    except Exception as error:
        if _temporary(error) or not _status(error):
            raise ServiceUnavailable() from None
        raise ConfirmationInvalid() from None
    if not response or not response.user:
        raise ConfirmationInvalid()
    # Confirmar no inicia una sesión local; descartar y revocar la sesión emitida.
    if response.session:
        try:
            sign_out(response.session.access_token)
        except AuthError:
            pass
