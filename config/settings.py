import os
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def env(key: str, default: str = "") -> str:
    return os.getenv(key, default)


SECRET_KEY = env("DJANGO_SECRET_KEY", "insecure-dev-key")
DEBUG = env("DJANGO_DEBUG", "true").lower() in {"1", "true", "yes"}
ALLOWED_HOSTS = [h.strip() for h in env("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h.strip()]

INSTALLED_APPS = [
    "daphne",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "rest_framework_simplejwt.token_blacklist",
    "corsheaders",
    "django_filters",
    "channels",
    "apps.accounts",
    "apps.restaurants",
    "apps.menu",
    "apps.orders",
    "apps.inventory",
    "apps.production",
    "apps.staff",
    "apps.expenses",
    "apps.accounting",
    "apps.todos",
    "apps.customers",
    "apps.loyalty",
    "apps.delivery",
    "apps.billing",
    "apps.notifications",
    "apps.audit",
    "apps.reports",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "apps.restaurants.middleware.RestaurantContextMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

database_url = env("DATABASE_URL", "postgres://cubixmet@localhost:5432/bitemybill")
# postgres://user:pass@host:port/name
from urllib.parse import urlparse

_db = urlparse(database_url)
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": _db.path.lstrip("/"),
        "USER": _db.username or "",
        "PASSWORD": _db.password or "",
        "HOST": _db.hostname or "localhost",
        "PORT": str(_db.port or 5432),
    }
}

AUTH_USER_MODEL = "accounts.User"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 6}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-in"
TIME_ZONE = "Asia/Kolkata"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

CORS_ALLOWED_ORIGINS = [
    o.strip()
    for o in env(
        "CORS_ALLOWED_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173",
    ).split(",")
    if o.strip()
]
# e.g. ^https://[\w-]+-app\.bitemybills\.tech$ for storefront PWAs
CORS_ALLOWED_ORIGIN_REGEXES = [
    r.strip()
    for r in env("CORS_ALLOWED_ORIGIN_REGEXES", "").split(",")
    if r.strip()
]
CORS_ALLOW_CREDENTIALS = True
CORS_ALLOW_HEADERS = [
    "accept",
    "authorization",
    "content-type",
    "origin",
    "x-csrftoken",
    "x-requested-with",
    "x-restaurant-id",
    "idempotency-key",
]

CSRF_TRUSTED_ORIGINS = [
    o.strip()
    for o in env("DJANGO_CSRF_TRUSTED_ORIGINS", ",".join(CORS_ALLOWED_ORIGINS)).split(",")
    if o.strip()
]

FRONTEND_RESTAURANT_URL = env("FRONTEND_RESTAURANT_URL", "http://localhost:5173")
FRONTEND_CUSTOMER_URL = env("FRONTEND_CUSTOMER_URL", "http://localhost:5174")
FRONTEND_ADMIN_URL = env("FRONTEND_ADMIN_URL", "http://localhost:5175")
FRONTEND_BUILDER_URL = env("FRONTEND_BUILDER_URL", "http://localhost:5176")

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": ("rest_framework.permissions.IsAuthenticated",),
    "DEFAULT_FILTER_BACKENDS": (
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ),
    "DEFAULT_PAGINATION_CLASS": None,
    "DEFAULT_THROTTLE_RATES": {
        "login": "10/min",
        "anon": "60/min",
    },
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=int(env("ACCESS_TOKEN_MINUTES", "15"))),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=int(env("REFRESH_TOKEN_DAYS", "14"))),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "AUTH_HEADER_TYPES": ("Bearer",),
    "USER_ID_FIELD": "id",
    "USER_ID_CLAIM": "user_id",
}

REDIS_URL = env("REDIS_URL", "redis://127.0.0.1:6379/0")
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {"hosts": [REDIS_URL]},
    }
}

CELERY_BROKER_URL = REDIS_URL
CELERY_RESULT_BACKEND = REDIS_URL
CELERY_TIMEZONE = TIME_ZONE

EMAIL_BACKEND = env("EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend")
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", "BiteMyBills <noreply@bitemybills.tech>")
EMAIL_HOST = env("EMAIL_HOST")
EMAIL_PORT = int(env("EMAIL_PORT", "587") or "587")
EMAIL_HOST_USER = env("EMAIL_HOST_USER")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD")
EMAIL_USE_TLS = env("EMAIL_USE_TLS", "true").lower() in {"1", "true", "yes"}

RAZORPAY_KEY_ID = env("RAZORPAY_KEY_ID")
RAZORPAY_KEY_SECRET = env("RAZORPAY_KEY_SECRET")

# Shared customer PWA: {slug}{STOREFRONT_SUBDOMAIN_SUFFIX}.{STOREFRONT_BASE_DOMAIN}
# Suffix avoids colliding with shell/admin hosts (e.g. malabar-kitchen-app.bitemybills.tech).
STOREFRONT_BASE_DOMAIN = env("STOREFRONT_BASE_DOMAIN", "bitemybills.tech")
STOREFRONT_SUBDOMAIN_SUFFIX = env("STOREFRONT_SUBDOMAIN_SUFFIX", "-app")
STOREFRONT_PUBLIC_SCHEME = env("STOREFRONT_PUBLIC_SCHEME", "https")
RAZORPAY_WEBHOOK_SECRET = env("RAZORPAY_WEBHOOK_SECRET")

GRACE_MONTHS = 3

# Production hardening when DEBUG is off (override individually via env).
if not DEBUG:
    SECURE_SSL_REDIRECT = env("SECURE_SSL_REDIRECT", "true").lower() in {"1", "true", "yes"}
    SESSION_COOKIE_SECURE = env("SESSION_COOKIE_SECURE", "true").lower() in {"1", "true", "yes"}
    CSRF_COOKIE_SECURE = env("CSRF_COOKIE_SECURE", "true").lower() in {"1", "true", "yes"}
    SECURE_HSTS_SECONDS = int(env("SECURE_HSTS_SECONDS", "31536000") or "0")
    SECURE_HSTS_INCLUDE_SUBDOMAINS = env("SECURE_HSTS_INCLUDE_SUBDOMAINS", "true").lower() in {
        "1",
        "true",
        "yes",
    }
    SECURE_HSTS_PRELOAD = env("SECURE_HSTS_PRELOAD", "true").lower() in {"1", "true", "yes"}
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_REFERRER_POLICY = "same-origin"
    X_FRAME_OPTIONS = "DENY"

_proxy_header = env("SECURE_PROXY_SSL_HEADER", "")
if _proxy_header:
    # Format: HTTP_X_FORWARDED_PROTO,https
    parts = [p.strip() for p in _proxy_header.split(",", 1)]
    if len(parts) == 2 and parts[0] and parts[1]:
        SECURE_PROXY_SSL_HEADER = (parts[0], parts[1])

