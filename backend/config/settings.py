"""Configuración de ejecución. Los tests utilizan settings_test por separado."""
import os
from dotenv import load_dotenv
from .settings_base import *  # noqa: F403

load_dotenv(BASE_DIR.parent / ".env")
SECRET_KEY = os.environ["DJANGO_SECRET_KEY"]
DEBUG = os.getenv("DJANGO_DEBUG", "False").strip().lower() in {"true", "1", "yes"}
ALLOWED_HOSTS = [host.strip() for host in os.getenv(
    "DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1"
).split(",") if host.strip()]
DJANGO_PUBLIC_URL = os.getenv("DJANGO_PUBLIC_URL", "http://127.0.0.1:8000").rstrip("/")
CSRF_TRUSTED_ORIGINS = [DJANGO_PUBLIC_URL]
SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_PUBLISHABLE_KEY = os.environ["SUPABASE_PUBLISHABLE_KEY"]
SUPABASE_SECRET_KEY = os.environ["SUPABASE_SECRET_KEY"]
DATABASES = {"default": {
    "ENGINE": "django.db.backends.postgresql",
    "NAME": os.environ["DB_NAME"], "USER": os.environ["DB_USER"],
    "PASSWORD": os.environ["DB_PASSWORD"], "HOST": os.environ["DB_HOST"],
    "PORT": os.getenv("DB_PORT", "5432"),
    "OPTIONS": {
        "sslmode": os.getenv("DB_SSLMODE", "prefer"),
        "options": "-c search_path=django_internal",
    },
}}
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SECURE_SSL_REDIRECT = not DEBUG
