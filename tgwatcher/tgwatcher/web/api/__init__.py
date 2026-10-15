"""Flask REST API package for TGWatcher.

Phase 2B: api.py module converted to a package. The original module lives
on as `._legacy` (history preserved via git mv). New route groups are
extracted into sub-blueprints (routes_auth, routes_stats, ...) and
registered under the parent `api` blueprint defined in `._legacy`.

Backward-compat: `from tgwatcher.web.api import api, init_services, ...`
continues to work because this package re-exports everything from
`._legacy` (via `*` plus an explicit list of names that star-import
would skip — dunder/private names).

PEP 562 note: module-level `__getattr__` works for reads, but module-level
`__setattr__` does NOT fire for plain `module.attr = value` writes —
Python writes those directly to `module.__dict__`. Tests like
`api_mod._storage = None` and `api_mod._auth_token = 't'` need those
writes to land on `_app_state` so `_legacy`'s bare-name lookups
(`_auth_token`, `_storage`, ...) see the updated value. We solve this by
swapping the package module's class for a `ModuleType` subclass whose
`__setattr__` forwards the 11 _APP_STATE_FORWARDED names to `_app_state`
and writes all other names to the module dict.
"""
import sys as _sys
from types import ModuleType

from ._legacy import *  # noqa: F401,F403

from . import _legacy  # noqa: F401  (for _ApiPackage.__setattr__ mirror)

# Explicit re-exports for names that `import *` skips (underscore-prefixed
# and module dunder names). PEP 562 __getattr__ defined on `._legacy`
# forwards reads of the 11 _APP_STATE_FORWARDED names to `_app_state.<attr>`.
from ._legacy import (  # noqa: F401
    _app_state,
    _auto_poll_daemon,
    _auto_poll_lock,
    _auto_poll_loop,
    _auto_poll_shutdown,
    _auto_poll_state,
    _auto_poll_stop_requested,
    _check_rate_limit,
    _disconnect_tg_client,
    _get_tg_client,
    _init_auto_poll,
    _init_listener,
    _init_signal_engine,
    _iso_z,
    _listener_daemon,
    _load_or_create_auth_token,
    _run_coro,
    _run_listener_async,
    _sse_bus,
    _start_listener_thread,
    _stop_listener,
    _tg_client_guard,
    api,
    init_services,
    push_new_message,
    push_sse_event,
    require_auth,
)

from .routes_auth import bp as auth_bp
from .routes_bot import bp as bot_bp
from .routes_bot_plans import bp as bot_plans_bp
from .routes_bot_subs import bp as bot_subs_bp
from .routes_compliance import bp as compliance_bp
from .routes_config import bp as config_bp
from .routes_crawl import bp as crawl_bp
from .routes_digest import bp as digest_bp
from .routes_health import bp as health_bp
from .routes_listen import bp as listen_bp
from .routes_messages import bp as messages_bp
from .routes_signals import bp as signals_bp
from .routes_sse import bp as sse_bp
from .routes_stats import bp as stats_bp
from .routes_webhook import bp as webhook_bp

api.register_blueprint(auth_bp)
api.register_blueprint(bot_bp)
api.register_blueprint(bot_plans_bp)
api.register_blueprint(bot_subs_bp)
api.register_blueprint(compliance_bp)
api.register_blueprint(config_bp)
api.register_blueprint(crawl_bp)
api.register_blueprint(digest_bp)
api.register_blueprint(health_bp)
api.register_blueprint(listen_bp)
api.register_blueprint(messages_bp)
api.register_blueprint(signals_bp)
api.register_blueprint(sse_bp)
api.register_blueprint(stats_bp)
api.register_blueprint(webhook_bp)


_APP_STATE_FORWARDED = frozenset({
    "_storage", "_crawl_service", "_config", "_async_loop", "_auth_token",
    "_tg_client", "_tg_lock", "_signal_engine", "_signal_service",
    "_webhook_dispatcher", "_source_quality_tracker",
})


class _ApiPackage(ModuleType):
    """Custom module class so `package._auth_token = X` writes land on
    `_app_state` (where `_legacy.require_auth` reads them via PEP 562
    `__getattr__`) AND in `_legacy.__dict__` (so bare-name global lookups
    inside `_legacy`'s functions, e.g. `if _auth_token is None`, resolve).

    Background: PEP 562 module `__getattr__` only fires for *attribute*
    access from outside the module — it does NOT fire for bare-name global
    lookups inside the module's own functions (those consult
    `module.__dict__` / `globals()` directly and raise `NameError` if
    absent). Before the package conversion `api_mod._auth_token = X`
    wrote to `_legacy.__dict__` (because `api_mod` WAS `_legacy`), so the
    bare-name lookup worked. After conversion `api_mod` is this package,
    so we must mirror writes into `_legacy.__dict__` to keep bare-name
    lookups working. We also forward to `_app_state` so reads via
    `_app_state.<attr>` (used by routes_auth / routes_stats) stay
    consistent.
    """

    def __getattr__(self, name: str):
        if name in _APP_STATE_FORWARDED:
            return getattr(_app_state, name[1:])
        raise AttributeError(f"module {self.__name__!r} has no attribute {name!r}")

    def __setattr__(self, name: str, value) -> None:
        if name in _APP_STATE_FORWARDED:
            setattr(_app_state, name[1:], value)
            # Mirror into _legacy.__dict__ so bare-name global lookups
            # inside _legacy.py (e.g. `if _auth_token is None` in
            # require_auth) resolve instead of raising NameError.
            _legacy.__dict__[name] = value
        else:
            self.__dict__[name] = value


# Swap this module's class so __getattr__/__setattr__ fire on attribute
# access. Must happen after all top-level names are defined.
_sys.modules[__name__].__class__ = _ApiPackage
