"""Valores de sesión: nunca incluyen tokens en su representación."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class AuthSession:
    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
    expires_at: int
    user_id: str
