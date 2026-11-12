"""Public customer-app host helpers (shared PWA subdomain + custom domains)."""

from django.conf import settings


def subdomain_suffix() -> str:
    return getattr(settings, "STOREFRONT_SUBDOMAIN_SUFFIX", "-app") or "-app"


def base_domain() -> str:
    return getattr(settings, "STOREFRONT_BASE_DOMAIN", "bitemybills.tech") or "bitemybills.tech"


def public_scheme() -> str:
    return getattr(settings, "STOREFRONT_PUBLIC_SCHEME", "https") or "https"


def storefront_subdomain(slug: str) -> str:
    """Hostname label only, e.g. malabar-kitchen-app."""
    return f"{slug}{subdomain_suffix()}"


def storefront_host(slug: str) -> str:
    """Full host, e.g. malabar-kitchen-app.bitemybills.tech."""
    return f"{storefront_subdomain(slug)}.{base_domain()}"


def storefront_url(slug: str) -> str:
    return f"{public_scheme()}://{storefront_host(slug)}"


def slug_from_host(host: str) -> str | None:
    """Parse restaurant slug from a PWA subdomain host."""
    host = (host or "").split(":")[0].lower().strip()
    suffix = subdomain_suffix()
    base = base_domain().lower()
    if host.endswith("." + base):
        label = host[: -(len(base) + 1)]
        if label.endswith(suffix):
            return label[: -len(suffix)] or None
    # bare label like malabar-kitchen-app (local / preview)
    if host.endswith(suffix) and "." not in host:
        return host[: -len(suffix)] or None
    return None
