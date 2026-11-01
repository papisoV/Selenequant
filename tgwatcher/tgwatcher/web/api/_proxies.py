"""Auto-poll proxy singletons extracted from `._legacy` (Phase 2C).

These four proxies preserve the original module-level surface area of
`._legacy` for callers that imported them directly:

    _auto_poll_shutdown        — Event-like (set/clear/is_set/wait)
    _auto_poll_stop_requested  — Event-like (set/clear/is_set)
    _auto_poll_state           — Mapping-like over the daemon's _state dict
    _auto_poll_lock            — Context-manager over the daemon's _lock

All four delegate to the `_auto_poll_daemon` singleton (still owned by
`._legacy`). The daemon reference is resolved lazily inside each method
via `from tgwatcher.web.api import _auto_poll_daemon` to avoid a
circular import at module-load time.

Re-exported by `._legacy` so `from ._legacy import _auto_poll_lock`
continues to work for all route modules.
"""


class _AutoPollShutdownProxy:
    __slots__ = ()

    def set(self) -> None:
        from tgwatcher.web.api import _auto_poll_daemon
        _auto_poll_daemon.signal_shutdown()

    def clear(self) -> None:
        from tgwatcher.web.api import _auto_poll_daemon
        _auto_poll_daemon.clear_shutdown()

    def is_set(self) -> bool:
        from tgwatcher.web.api import _auto_poll_daemon
        return _auto_poll_daemon.is_shutdown_set()

    def wait(self, timeout: float | None = None) -> bool:
        from tgwatcher.web.api import _auto_poll_daemon
        return _auto_poll_daemon.wait_shutdown(timeout)


class _AutoPollStopRequestedProxy:
    __slots__ = ()

    def set(self) -> None:
        from tgwatcher.web.api import _auto_poll_daemon
        _auto_poll_daemon.request_stop()

    def clear(self) -> None:
        from tgwatcher.web.api import _auto_poll_daemon
        _auto_poll_daemon.clear_stop()

    def is_set(self) -> bool:
        from tgwatcher.web.api import _auto_poll_daemon
        return _auto_poll_daemon.is_stop_requested()


class _AutoPollStateProxy:
    """Mapping-like proxy over the daemon's _state dict.

    Supports `len()`, `in`, iteration, `.items()`, `.get()`, `.values()`,
    subscript access, and dict-style mutation (`_state[k] = v`, `del _state[k]`,
    `.clear()`). Reads/writes happen on the live backing dict; callers that
    need atomicity should use `with _auto_poll_lock:` (proxied below).
    """

    __slots__ = ("_lock_proxy",)

    def __init__(self) -> None:
        # Bound late — `_auto_poll_lock` is constructed on `_legacy` *after*
        # this class is imported. Read it directly off `_legacy.__dict__`
        # (NOT `from tgwatcher.web.api import _auto_poll_lock`) because the
        # package `__init__.py` is still mid-import at the time `_legacy`
        # instantiates this singleton, so the package-level attribute is
        # not bound yet. `_legacy.py` constructs `_auto_poll_lock` BEFORE
        # `_auto_poll_state`, so `_legacy.__dict__['_auto_poll_lock']` is
        # guaranteed to exist by the time we get here.
        from tgwatcher.web.api import _legacy
        self._lock_proxy = _legacy.__dict__["_auto_poll_lock"]

    def _state(self) -> dict[int, dict]:
        from tgwatcher.web.api import _auto_poll_daemon
        return _auto_poll_daemon.state

    def __len__(self) -> int:
        return len(self._state())

    def __contains__(self, key: object) -> bool:
        return key in self._state()

    def __iter__(self):
        return iter(self._state())

    def __getitem__(self, key: int) -> dict:
        return self._state()[key]

    def __setitem__(self, key: int, value: dict) -> None:
        self._state()[key] = value

    def __delitem__(self, key: int) -> None:
        del self._state()[key]

    def items(self):
        return self._state().items()

    def values(self):
        return self._state().values()

    def keys(self):
        return self._state().keys()

    def get(self, key: int, default=None):
        return self._state().get(key, default)

    def clear(self) -> None:
        self._state().clear()

    def __repr__(self) -> str:
        return repr(self._state())


class _AutoPollLockProxy:
    """Context-manager proxy over the daemon's internal _lock."""

    __slots__ = ()

    def __enter__(self):
        from tgwatcher.web.api import _auto_poll_daemon
        return _auto_poll_daemon.lock.__enter__()

    def __exit__(self, exc_type, exc, tb):
        from tgwatcher.web.api import _auto_poll_daemon
        return _auto_poll_daemon.lock.__exit__(exc_type, exc, tb)


# NOTE: the four singletons (_auto_poll_shutdown, _auto_poll_stop_requested,
# _auto_poll_lock, _auto_poll_state) are instantiated on `._legacy` (not
# here) to keep construction order: `_auto_poll_lock` must exist before
# `_AutoPollStateProxy()` is constructed. `._legacy` imports these classes
# and then instantiates the singletons in the correct order.
