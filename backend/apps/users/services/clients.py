"""Clientes efímeros con estado y transportes privados por operación."""

from contextlib import contextmanager

import httpx
from django.conf import settings
from django.views.decorators.debug import sensitive_variables
from supabase import Client
from supabase.lib.client_options import SyncClientOptions

HTTP_TIMEOUT = 10.0


def http_client():
    # El contexto propietario siempre cierra este transporte. No hay pool global.
    return httpx.Client(timeout=HTTP_TIMEOUT, follow_redirects=False)


@sensitive_variables()
def _remove_api_key_bearer(request):
    # SDK 2.31 agrega la API key como Bearer al construir el cliente y al salir.
    # Corregir en el transporte cubre ambos casos sin alterar los JWT de usuario.
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    if scheme.lower() == "bearer" and (
        token == request.headers.get("apikey")
        or token.startswith(("sb_publishable_", "sb_secret_"))
    ):
        request.headers.pop("Authorization", None)


@contextmanager
@sensitive_variables()
def _client(api_key, access_token=None):
    with http_client() as transport:
        transport.event_hooks["request"].append(_remove_api_key_bearer)
        options = SyncClientOptions(
            persist_session=False,
            auto_refresh_token=False,
            httpx_client=transport,
            headers={"Authorization": f"Bearer {access_token}"} if access_token else {},
        )
        # create_client() consulta get_session(); este constructor no restaura nada.
        client = Client(settings.SUPABASE_URL, api_key, options=options)
        yield client


@contextmanager
@sensitive_variables()
def public_client(access_token=None):
    with _client(settings.SUPABASE_PUBLISHABLE_KEY, access_token) as client:
        yield client


@contextmanager
@sensitive_variables()
def administrative_client():
    # Nunca recibe un JWT de usuario ni ejecuta login/refresh.
    with _client(settings.SUPABASE_SECRET_KEY) as client:
        yield client
