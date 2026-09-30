"""Sesiones técnicas: persistencia local y coordinación de refresh/logout."""
import logging
from datetime import datetime, timedelta, timezone as datetime_timezone

from django.conf import settings
from django.db import transaction
from django.middleware.csrf import rotate_token
from django.utils import timezone
from django.views.decorators.debug import sensitive_variables

from ..models import SupabaseSession
from . import auth_service, profile_service
from .exceptions import AuthError, InvalidCredentials, SessionExpired

logger = logging.getLogger("planb.sessions")
REFRESH_MARGIN = timedelta(seconds=60)


def _expiration(value):
    return datetime.fromtimestamp(value, tz=datetime_timezone.utc)


@sensitive_variables()
def establish_session(request, auth_session):
    # La cookie identifica una sesión nueva; la identidad no se copia al ORM.
    with transaction.atomic():
        request.session.flush()
        request.session["planb_authenticated"] = True
        request.session.set_expiry(timezone.now() + timedelta(seconds=settings.SESSION_COOKIE_AGE))
        request.session.save()
        SupabaseSession.objects.create(
            session_id=request.session.session_key,
            access_token=auth_session.access_token,
            refresh_token=auth_session.refresh_token,
            expires_at=_expiration(auth_session.expires_at),
        )
    rotate_token(request)


def _clear(request):
    request.session.flush()  # El borrado ORM de Session elimina su fila técnica.
    rotate_token(request)


@sensitive_variables()
def _active_token(request):
    if not request.session.get("planb_authenticated"):
        return None
    session_key = request.session.session_key
    try:
        with transaction.atomic():
            stored = SupabaseSession.objects.select_for_update().filter(
                session_id=session_key,
                session__expire_date__gt=timezone.now(),
            ).first()
            if stored is None:
                raise SessionExpired()
            if stored.expires_at <= timezone.now() + REFRESH_MARGIN:
                # Releer bajo lock evita renovar otra vez desde un snapshot viejo.
                renewed = auth_service.refresh(stored.refresh_token)
                stored.access_token = renewed.access_token
                stored.refresh_token = renewed.refresh_token
                stored.expires_at = _expiration(renewed.expires_at)
                stored.save(update_fields=["access_token", "refresh_token", "expires_at"])
            return stored.access_token
    except AuthError:
        # Una renovación fallida siempre invalida la sesión local.
        _clear(request)
        return None


@sensitive_variables()
def current_user(request):
    access_token = _active_token(request)
    if access_token is None:
        return None
    try:
        user_id = auth_service.get_user(access_token)
        profile = profile_service.get_profile(access_token, user_id)
        return {"id": profile["id"], "username": profile["username"]}
    except (InvalidCredentials, SessionExpired):
        _clear(request)
        return None


@sensitive_variables()
def logout(request):
    try:
        # Carga/valida la sesión Django antes de tomar su identificador.
        request.session.get("planb_authenticated")
        session_key = request.session.session_key
        with transaction.atomic():
            stored = SupabaseSession.objects.select_for_update().filter(
                session_id=session_key
            ).first()
            if stored is not None:
                try:
                    access_token = stored.access_token
                    if stored.expires_at <= timezone.now():
                        renewed = auth_service.refresh(stored.refresh_token)
                        access_token = renewed.access_token
                    auth_service.sign_out(access_token)
                except Exception:
                    # No registrar excepciones del transporte ni tokens.
                    logger.warning("No se pudo confirmar el cierre remoto de una sesion.")
                finally:
                    stored.delete()
    finally:
        # Incluso si Supabase falla, no queda una sesión utilizable en Django.
        _clear(request)
