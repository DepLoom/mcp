"""Opt-in system proxy policy for network-using DepLoom MCP tools.

Explicit tool booleans override DEPLOOM_MCP_USE_SYSTEM_PROXY; absent settings
mean False for backward compatibility. Proxy URLs and credentials are never
parsed, logged or forwarded as tool arguments.
"""
from __future__ import annotations

import os
from typing import Mapping

TRUE_VALUES = frozenset({"true", "1", "yes", "on"})
FALSE_VALUES = frozenset({"false", "0", "no", "off"})


class ProxySettingError(ValueError):
    pass


def resolve_system_proxy(
    explicit: bool | None,
    *,
    environ: Mapping[str, str] | None = None,
) -> tuple[bool, str]:
    if explicit is not None:
        if type(explicit) is not bool:
            raise ProxySettingError("use_system_proxy must be true, false or null")
        return explicit, "tool"
    env = os.environ if environ is None else environ
    raw = env.get("DEPLOOM_MCP_USE_SYSTEM_PROXY")
    if raw is None:
        return False, "default"
    if not isinstance(raw, str):
        raise ProxySettingError("DEPLOOM_MCP_USE_SYSTEM_PROXY must be true or false")
    word = raw.strip().lower()
    if word in TRUE_VALUES:
        return True, "environment"
    if word in FALSE_VALUES:
        return False, "environment"
    raise ProxySettingError(
        "DEPLOOM_MCP_USE_SYSTEM_PROXY must be true/false (also accepts 1/0, yes/no, on/off)"
    )


# npm/Yarn native CLIs differ from requests.Session: they may read implicit
# proxy environment variables despite Core's trust_env=False by default.
# Strip only implicit proxy variables when opt-out is effective; explicit
# project-specific npm/Yarn registry configuration remains in effect.
ENV_PROXY_KEYS = frozenset({"http_proxy", "https_proxy", "all_proxy", "ftp_proxy"})


def native_proxy_environment(
    source: Mapping[str, str], *, use_system_proxy: bool,
) -> dict[str, str]:
    if type(use_system_proxy) is not bool:
        raise ProxySettingError("effective use_system_proxy must be a boolean")
    if use_system_proxy:
        return dict(source)
    return {key: value for key, value in source.items()
            if key.lower() not in ENV_PROXY_KEYS}
