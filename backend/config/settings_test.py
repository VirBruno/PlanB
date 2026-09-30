"""Suite local: sin .env, conexiones externas ni credenciales reales."""
from .settings_base import *  # noqa: F403

SECRET_KEY = "clave-ficticia-exclusiva-de-tests-sin-uso-en-entornos-reales"
DEBUG = False
ALLOWED_HOSTS = ["testserver", "localhost", "127.0.0.1"]
DJANGO_PUBLIC_URL = "http://testserver"
SUPABASE_URL = "https://planb-test.invalid"
SUPABASE_PUBLISHABLE_KEY = "sb_publishable_test_placeholder"
SUPABASE_SECRET_KEY = "sb_secret_test_placeholder"
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
SECURE_SSL_REDIRECT = False
TEST_RUNNER = "config.test_runner.OfflineTestRunner"
