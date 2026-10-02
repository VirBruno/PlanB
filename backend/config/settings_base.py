"""Configuración común sin credenciales ni acceso a servicios externos."""
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
INSTALLED_APPS = [
    "django.contrib.sessions", "django.contrib.messages", "django.contrib.staticfiles",
    "apps.users.apps.UsersConfig",
    "apps.groups.apps.GroupsConfig",
    "apps.plans.apps.PlansConfig",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
ROOT_URLCONF = "config.urls"
TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates", "DIRS": [], "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request",
        "django.contrib.messages.context_processors.messages",
    ]},
}]
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"
LANGUAGE_CODE = "es-ar"
TIME_ZONE = "America/Argentina/Buenos_Aires"
USE_I18N = True
USE_TZ = True
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
SESSION_ENGINE = "django.contrib.sessions.backends.db"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_AGE = 60 * 60 * 8
SESSION_SAVE_EVERY_REQUEST = False
CSRF_COOKIE_HTTPONLY = True
CSRF_COOKIE_SAMESITE = "Lax"
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
AUTH_PASSWORD_VALIDATORS = []  # Política canónica en users.validators.

LOGGING = {
    "version": 1, "disable_existing_loggers": False,
    "filters": {"sensitive": {"()": "apps.users.logging_filters.SensitiveDataFilter"}},
    "handlers": {
        "console": {"class": "logging.StreamHandler", "filters": ["sensitive"]},
        "null": {"class": "logging.NullHandler"},
    },
    "loggers": {
        "django": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "django.server": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "planb": {"handlers": ["console"], "level": "INFO", "propagate": False},
        **{name: {"handlers": ["null"], "propagate": False}
           for name in ("httpx", "httpcore", "supabase", "supabase_auth", "postgrest")},
    },
}
