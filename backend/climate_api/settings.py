"""Django settings for the Climate Hazard Trend Analyzer backend.

Database connection values are read from environment variables (or a .env file
next to manage.py) so credentials never live in source control.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _env_list(name, default=""):
    return [item.strip() for item in os.getenv(name, default).split(",") if item.strip()]


SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "dev-only-insecure-key-change-me")
DEBUG = os.getenv("DJANGO_DEBUG", "1") == "1"
ALLOWED_HOSTS = _env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")
# Render sets this automatically for web services, so the public hostname is always allowed.
if os.getenv("RENDER_EXTERNAL_HOSTNAME"):
    ALLOWED_HOSTS.append(os.environ["RENDER_EXTERNAL_HOSTNAME"])

if not DEBUG and SECRET_KEY.startswith("dev-only"):
    raise RuntimeError("Set DJANGO_SECRET_KEY when DJANGO_DEBUG is off.")

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "corsheaders",
    "rest_framework",
    "hazards",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.common.CommonMiddleware",
]

ROOT_URLCONF = "climate_api.urls"
TEMPLATES = []
WSGI_APPLICATION = "climate_api.wsgi.application"

def _mysql_ssl_options():
    """Hosted MySQL (TiDB Cloud, Aiven) needs TLS.

    DB_SSL=1 turns TLS on using the system CA store. DB_SSL_CA=<path> also pins a CA file.
    """
    ca_path = os.getenv("DB_SSL_CA", "")
    if ca_path:
        return {"ssl": {"ca": ca_path}}
    if os.getenv("DB_SSL", "0") == "1":
        return {"ssl": {}}
    return {}


# MySQL connection, driven by the DB_* variables from the .env file.
DB_ENGINE = os.getenv("DB_CONNECTION", "mysql")
if DB_ENGINE != "mysql":
    raise RuntimeError("This project is configured for MySQL (DB_CONNECTION=mysql).")

import pymysql  # noqa: E402

pymysql.install_as_MySQLdb()

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.mysql",
        "NAME": os.getenv("DB_DATABASE", ""),
        "USER": os.getenv("DB_USERNAME", "root"),
        "PASSWORD": os.getenv("DB_PASSWORD", ""),
        "HOST": os.getenv("DB_HOST", "127.0.0.1"),
        "PORT": os.getenv("DB_PORT", "3306"),
        "OPTIONS": {"charset": "utf8mb4", **_mysql_ssl_options()},
    }
}

# Cache for API responses. Locmem is fine for a single process; swap for Redis if needed.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "climate-hazard",
        "TIMEOUT": int(os.getenv("API_CACHE_SECONDS", "3600")),
    }
}

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = False
USE_TZ = True
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser"],
    "DEFAULT_THROTTLE_CLASSES": ["rest_framework.throttling.AnonRateThrottle"],
    "DEFAULT_THROTTLE_RATES": {
        "anon": os.getenv("RATE_LIMIT_ANON", "60/hour"),
        "analysis": os.getenv("RATE_LIMIT_ANALYSIS", "20/hour"),
    },
    "UNAUTHENTICATED_USER": None,
}

CORS_ALLOWED_ORIGINS = _env_list(
    "CORS_ALLOWED_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
)

# Open-Meteo settings (no API key needed).
OPEN_METEO_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
OPEN_METEO_GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
OPEN_METEO_TIMEOUT_SECONDS = 30
