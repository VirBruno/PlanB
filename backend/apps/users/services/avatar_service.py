"""Fotografías privadas: operaciones Storage con JWT de usuario y políticas RLS."""
from uuid import UUID
from urllib.parse import quote

from django.conf import settings
from django.views.decorators.debug import sensitive_variables

from . import clients

BUCKET = "profile-avatars"
MAX_BYTES = 2 * 1024 * 1024

class AvatarError(Exception):
    pass


def _path(user_id):
    try:
        return f"{UUID(str(user_id))}/avatar"
    except (ValueError, TypeError, AttributeError):
        raise AvatarError("Identificador de usuario inválido.") from None


def _type(data):
    if data.startswith(b"\xff\xd8\xff") and data.endswith(b"\xff\xd9"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    raise AvatarError("Elegí una imagen JPG, PNG o WebP válida.")


@sensitive_variables()
def _request(access_token, method, path, **kwargs):
    if not access_token:
        raise AvatarError("Tu sesión no está disponible.")
    headers = {
        "apikey": settings.SUPABASE_PUBLISHABLE_KEY,
        "Authorization": f"Bearer {access_token}",
    }
    headers.update(kwargs.pop("headers", {}))
    try:
        with clients.http_client() as client:
            return client.request(method, settings.SUPABASE_URL + "/storage/v1/" + path,
                                  headers=headers, **kwargs)
    except Exception:
        raise AvatarError("No se pudo conectar con el almacenamiento.") from None


@sensitive_variables()
def upload(access_token, user_id, uploaded):
    if uploaded.size > MAX_BYTES or uploaded.size <= 0:
        raise AvatarError("La imagen debe pesar como máximo 2 MB.")
    data = uploaded.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise AvatarError("La imagen debe pesar como máximo 2 MB.")
    mime = _type(data)
    path = quote(_path(user_id), safe="/")
    response = _request(access_token, "POST", f"object/{BUCKET}/{path}",
                        content=data, headers={"Content-Type": mime, "x-upsert": "true", "Cache-Control": "no-cache"})
    if response.status_code not in (200, 201):
        raise AvatarError("No se pudo guardar la foto. Verificá que el bucket y sus políticas estén configurados.")


@sensitive_variables()
def remove(access_token, user_id):
    path = quote(_path(user_id), safe="/")
    response = _request(access_token, "DELETE", f"object/{BUCKET}/{path}")
    if response.status_code not in (200, 204, 404):
        raise AvatarError("No se pudo eliminar la foto.")


@sensitive_variables()
def signed_url(access_token, user_id):
    path = quote(_path(user_id), safe="/")
    response = _request(access_token, "POST", f"object/sign/{BUCKET}/{path}",
                        json={"expiresIn": 300})
    if response.status_code != 200:
        return None
    try:
        value = response.json().get("signedURL") or response.json().get("signedUrl")
        if not isinstance(value, str) or not value.startswith("/") or value.startswith("//"):
            return None
        return settings.SUPABASE_URL + "/storage/v1" + value
    except (ValueError, TypeError, AttributeError):
        return None
