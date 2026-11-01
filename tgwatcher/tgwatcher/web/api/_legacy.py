"""Flask REST API blueprint shim. Routes live in routes_*.py; this module
owns the 4 singletons + `api` Blueprint and re-exports helpers/proxies
so `from ._legacy import X` keeps working. PEP 562 `__getattr__` /
`__setattr__` below forward the 11 `_APP_STATE_FORWARDED` names to
`_app_state` so external reads/writes (tests, metrics) work unchanged.
"""
import sys as _sys

from flask import Blueprint

from tgwatcher.client import TGClient  # noqa: F401  (historical re-export)
from tgwatcher.web.app_state import AppState
from tgwatcher.web.auto_poll_daemon import AutoPollDaemon
from tgwatcher.web.listener_daemon import ListenerDaemon
from tgwatcher.web.sse_bus import SSEBus
from ._helpers import (  # noqa: F401
    _atomic_write_config, _check_rate_limit, _clear_auth_cookie,
    _disconnect_tg_client, _extract_auth_token, _get_auth_token_path,
    _get_listen_groups, _get_tg_client, _iso_z, _rate_limit_store,
    _run_coro, _set_auth_cookie, _tg_client_guard, require_auth,
)
from ._proxies import (  # noqa: F401
    _AutoPollLockProxy, _AutoPollShutdownProxy, _AutoPollStateProxy,
    _AutoPollStopRequestedProxy,
)

api = Blueprint("api", __name__, url_prefix="/api")

# ── Singletons ─────────────────────────────────────────────────────────
_listener_daemon = ListenerDaemon()
_listener_daemon.bind_host(lambda: _sys.modules[__name__])
_sse_bus = SSEBus()
_app_state = AppState()
_auto_poll_daemon = AutoPollDaemon()
# Proxy singletons — lock must exist before state proxy reads it.
_auto_poll_shutdown = _AutoPollShutdownProxy()
_auto_poll_stop_requested = _AutoPollStopRequestedProxy()
_auto_poll_lock = _AutoPollLockProxy()
_auto_poll_state = _AutoPollStateProxy()

# ── Re-exports (bound-method aliases — no wrapper overhead) ────────────
init_services = _app_state.init_services
_load_or_create_auth_token = _app_state.load_or_create_auth_token
_init_signal_engine = _app_state.init_signal_engine
_init_auto_poll = _auto_poll_daemon.init_from_config
_auto_poll_loop = _auto_poll_daemon.run_loop
_init_listener = _listener_daemon.init_from_config
_start_listener_thread = _listener_daemon.start_thread
_run_listener_async = _listener_daemon.run_async
_stop_listener = _listener_daemon.stop
push_sse_event = _sse_bus.push


def push_new_message(msg: dict) -> None:
    _sse_bus.push("new_messages", msg)


# ── PEP 562 forwarding for the 11 AppState globals ─────────────────────
_APP_STATE_FORWARDED = frozenset({
    "_storage", "_crawl_service", "_config", "_async_loop", "_auth_token",
    "_signal_engine", "_webhook_dispatcher", "_tg_client", "_tg_lock",
    "_signal_service", "_source_quality_tracker",
})


def __getattr__(name: str):
    if name in _APP_STATE_FORWARDED:
        return getattr(_app_state, name[1:])
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __setattr__(name: str, value) -> None:
    if name in _APP_STATE_FORWARDED:
        setattr(_app_state, name[1:], value)
    else:
        _sys.modules[__name__].__dict__[name] = value
