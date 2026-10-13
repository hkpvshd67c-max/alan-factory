from __future__ import annotations

from urllib.parse import urlsplit

from alan import default_profile

LOOPBACK_HOSTNAMES = frozenset({"localhost", "127.0.0.1", "::1"})


def configured_prep_chat_origin() -> str | None:
    return default_profile.resolved().profile.prep_chat_origin


def _configured_hostname() -> str | None:
    origin = configured_prep_chat_origin()
    if origin is None:
        return None
    try:
        return urlsplit(origin).hostname
    except ValueError:
        return None


def allowed_hostnames() -> frozenset[str]:
    hostname = _configured_hostname()
    if hostname is None:
        return LOOPBACK_HOSTNAMES
    return LOOPBACK_HOSTNAMES | {hostname.lower()}
