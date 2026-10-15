"""Application global state — encapsulates the 11 module-level globals
previously scattered across ``tgwatcher.web.api``.

Phase 2A full: extracts ``_storage``, ``_crawl_service``, ``_config``,
``_async_loop``, ``_auth_token``, ``_signal_engine``, ``_webhook_dispatcher``,
``_tg_client``, ``_tg_lock``, ``_signal_service``, ``_source_quality_tracker``
into a single ``AppState`` class. The 3 daemon singletons
(``_sse_bus``, ``_auto_poll_daemon``, ``_listener_daemon``) remain in api.py
as before — AppState references them via lazy module lookup to avoid
circular imports.

Conservative pattern (mirrors prior SSEBus/AutoPollDaemon/ListenerDaemon
extractions): route code is unchanged; the 11 names are forwarded back to
``api._app_state`` via PEP 562 module ``__getattr__`` / ``__setattr__``.
"""
from __future__ import annotations

import logging
import os
import secrets
import threading
from pathlib import Path
from typing import TYPE_CHECKING

from tgwatcher.client import TGClient
from tgwatcher.signal_engine import SignalEngine
from tgwatcher.signal_filter import KeywordFilter
from tgwatcher.signal_llm import SignalLLMClient
from tgwatcher.storage import Storage
from tgwatcher.web.crawl_service import CrawlService
from tgwatcher.web.signal_service import SignalService

if TYPE_CHECKING:
    from tgwatcher.web.async_loop import AsyncLoopManager

logger = logging.getLogger(__name__)


class AppState:
    """Holds the 11 cross-domain api.py module globals + the 5 functions
    that mutate them.

    Plain class (not dataclass) for simplicity and to mirror the prior
    daemon extractions. Attribute names match the original globals with
    the leading underscore stripped (``self.storage`` ← ``_storage``).
    """

    def __init__(self) -> None:
        # 11 attrs — initialized to None except tg_lock (threading.Lock()).
        self.storage: Storage | None = None
        self.crawl_service: CrawlService | None = None
        self.config: dict | None = None
        self.async_loop: "AsyncLoopManager | None" = None
        self.auth_token: str | None = None
        self.tg_client: TGClient | None = None
        self.tg_lock = threading.Lock()
        self.signal_service: SignalService | None = None
        self.signal_engine: SignalEngine | None = None
        self.webhook_dispatcher = None
        self.bot_pusher = None
        self.source_quality_tracker = None

    # ── _load_or_create_auth_token (api.py:99) ──────────────────────────
    def load_or_create_auth_token(self) -> str:
        """Load auth token from disk or generate+persist a new one.

        Verbatim copy of the original ``_load_or_create_auth_token``;
        ``global _auth_token`` → ``self.auth_token``.
        """
        token_path = self._get_auth_token_path()
        if token_path.exists():
            self.auth_token = token_path.read_text().strip()
            if self.auth_token:
                return self.auth_token
        self.auth_token = secrets.token_hex(32)
        token_path.write_text(self.auth_token)
        logger.info("=" * 60)
        logger.info("Generated new auth token (also saved to %s)", token_path)
        logger.info("Token: %s", self.auth_token)
        logger.info("=" * 60)
        return self.auth_token

    @staticmethod
    def _get_auth_token_path() -> Path:
        config_dir = Path(os.environ.get("TGWATCHER_CONFIG", str(Path.cwd() / "config.yaml"))).parent
        return config_dir / ".tgwatcher_auth"

    # ── init_services (api.py:158) ───────────────────────────────────────
    def init_services(self, config, async_loop=None) -> None:
        """Initialize all services. Verbatim copy of the original
        ``init_services`` body; ``global _storage, ...`` removed; each
        ``_x`` → ``self.x``. Module singletons (_auto_poll_daemon,
        _init_auto_poll, _auto_poll_shutdown, _init_listener,
        push_sse_event, _get_tg_client) are looked up via the api module
        at call time to avoid circular imports.
        """
        from tgwatcher.web import api as _api  # lazy: avoid circular import

        self.config = config
        self.async_loop = async_loop
        db_path = config["storage"]["db_path"]
        self.storage = Storage(db_path)
        self.storage.init_db()

        def _on_status_change(status: dict):
            _api.push_sse_event("crawl_status", status)

        self.crawl_service = CrawlService(
            config, async_loop=async_loop, storage=self.storage,
            on_status_change=_on_status_change,
            get_tg_client=_api._get_tg_client, tg_lock=self.tg_lock,
        )
        self.load_or_create_auth_token()

        # Initialize signal engine if enabled
        signal_cfg = config.get("signal", {})
        if signal_cfg.get("enabled", False):
            self.init_signal_engine(config)
        else:
            self.signal_service = None
            self.signal_engine = None

        # Webhook dispatcher — initialized independently of signal engine so
        # downstream-facing output works even if LLM api_key is missing.
        from tgwatcher.webhook import WebhookDispatcher
        self.webhook_dispatcher = WebhookDispatcher(config)

        # Bot pusher — Telegram Bot API push for new signals.
        from tgwatcher.bot_push import BotPusher
        self.bot_pusher = BotPusher(config.get("signal", {}))
        # Wire storage reference after DB init so BotPusher can query subscriptions.
        self.bot_pusher.set_storage(self.storage)

        # Data retention cleaner — deletes old data per compliance config.
        from tgwatcher.compliance import DataRetentionCleaner
        compliance_cfg = config.get("compliance", {})
        retention_days = int(compliance_cfg.get("data_retention_days", 365))
        if retention_days > 0:
            self.retention_cleaner = DataRetentionCleaner(
                self.storage, retention_days=retention_days,
            )
            threading.Thread(
                target=self.retention_cleaner.run_loop,
                daemon=True,
                name="data-retention",
            ).start()
            logger.info("Data retention cleaner started (retention_days=%d)", retention_days)
        else:
            self.retention_cleaner = None
            logger.info("Data retention cleaner disabled (retention_days=0)")

        # Subscription expiry checker — marks expired subscriptions.
        from tgwatcher.subscription_checker import SubscriptionChecker
        self.subscription_checker = SubscriptionChecker(self.storage)
        threading.Thread(
            target=self.subscription_checker.run_loop,
            daemon=True,
            name="subscription-checker",
        ).start()
        logger.info("Subscription expiry checker started")

        # Auto-catchup on startup
        catchup_cfg = config.get("catchup", {})
        if catchup_cfg.get("enabled", True):
            catchup_groups = [g for g in config.get("groups", []) if g.get("auto_catchup", False)]
            if catchup_groups:
                def _delayed_catchup():
                    import time
                    time.sleep(5)
                    logger.info("Auto-catchup: starting for %d groups", len(catchup_groups))
                    self.crawl_service.start(mode="catchup")
                threading.Thread(target=_delayed_catchup, daemon=True).start()

        # Auto-poll daemon — per-group periodic incremental crawl
        _api._auto_poll_daemon.set_crawl_service(self.crawl_service)
        _api._auto_poll_daemon.set_sse_push_callback(_api.push_sse_event)
        _api._init_auto_poll(config)
        threading.Thread(target=_api._auto_poll_loop, daemon=True, name="auto-poll").start()

        # Auto-LLM daemon — chains LLM batch + digest after each crawl tick.
        # Started only if signal.enabled (LLM client exists) and signal.auto_llm
        # is not explicitly False (default True when signal.enabled).
        if self.signal_engine is not None and signal_cfg.get("auto_llm", True):
            from tgwatcher.web.auto_llm_daemon import AutoLlmDaemon
            self.auto_llm_daemon = AutoLlmDaemon(
                storage=self.storage,
                signal_engine=self.signal_engine,
                push_sse_event=_api.push_sse_event,
                digest_interval_minutes=signal_cfg.get("auto_digest_interval_minutes", 60),
                min_signals=signal_cfg.get("auto_llm_min_signals", 5),
            )
            _api._auto_poll_daemon.set_auto_llm_daemon(self.auto_llm_daemon)
            threading.Thread(
                target=self.auto_llm_daemon.run_loop,
                daemon=True,
                name="auto-llm",
            ).start()
            logger.info(
                "Auto-LLM daemon started (digest_interval=%dmin, min_signals=%d)",
                signal_cfg.get("auto_digest_interval_minutes", 60),
                signal_cfg.get("auto_llm_min_signals", 5),
            )
        else:
            self.auto_llm_daemon = None

        # Register process-lifecycle shutdown for the auto-poll daemon.
        # atexit covers normal interpreter exit (Ctrl+C, sys.exit). SIGTERM covers
        # container/production signals. signal.signal must be in the main thread —
        # under gunicorn workers it raises ValueError, which we swallow and rely
        # on atexit instead.
        import atexit
        import signal as _signal

        def _shutdown_daemons(*_):
            _api._auto_poll_shutdown.set()
            if self.auto_llm_daemon is not None:
                self.auto_llm_daemon.signal_shutdown()
            if self.retention_cleaner is not None:
                self.retention_cleaner.signal_shutdown()
            if self.subscription_checker is not None:
                self.subscription_checker.signal_shutdown()

        atexit.register(_shutdown_daemons)
        try:
            _signal.signal(_signal.SIGTERM, _shutdown_daemons)
        except (ValueError, OSError):
            logger.info("SIGTERM handler not registered (non-main thread); relying on atexit")

        # Real-time listener — per-group Telethon NewMessage handler
        _api._init_listener(config)

    # ── _init_signal_engine (api.py:391) ─────────────────────────────────
    def init_signal_engine(self, config: dict) -> None:
        """Initialize signal engine, LLM client, and service.

        Verbatim copy of the original ``_init_signal_engine`` body;
        ``global _signal_service, _signal_engine`` → ``self.signal_service
        / self.signal_engine``; reads of ``_storage`` / ``_webhook_dispatcher``
        → ``self.storage`` / ``self.webhook_dispatcher``.

        Webhook dispatcher is initialized separately in init_services (not
        gated on signal.enabled or api_key presence).
        """
        signal_cfg = config.get("signal", {})
        llm_cfg = signal_cfg.get("llm", {})

        # Build LLMConfig via factory — handles provider routing, legacy compat,
        # env override, and validation. Raises ValueError on missing/unknown provider.
        try:
            from tgwatcher.signal_llm import LLMConfig
            llm_config = LLMConfig.from_dict(llm_cfg)
        except ValueError as e:
            logger.warning("Signal enabled but LLM config invalid: %s. Disabling signal processing.", e)
            signal_cfg["enabled"] = False
            self.signal_service = None
            self.signal_engine = None
            return

        keyword_filter = KeywordFilter(signal_cfg)
        llm_client = SignalLLMClient(llm_config)

        # Signal deduper for downstream-facing push. In-memory cache; lost on
        # restart (acceptable — worst case a few dup pushes in first minutes).
        # Set to None when disabled so SignalEngine skips the should_emit call.
        dedup_cfg = signal_cfg.get("dedup", {})
        deduper = None
        if dedup_cfg.get("enabled", True):
            from tgwatcher.signal_dedup import SignalDeduper
            window = int(dedup_cfg.get("window_seconds", 300))
            deduper = SignalDeduper(window_seconds=window)
            logger.info("Signal deduper enabled (window=%ds)", window)

        self.signal_engine = SignalEngine(
            self.storage, keyword_filter, llm_client, signal_cfg,
            webhook_dispatcher=self.webhook_dispatcher,
            bot_pusher=self.bot_pusher,
            deduper=deduper,
        )

        from tgwatcher.web import api as _api  # lazy: avoid circular import

        def _on_signal_status(status: dict):
            _api.push_sse_event("signal_process_status", status)

        self.signal_service = SignalService(self.signal_engine, signal_cfg, on_status_change=_on_signal_status)
        # Source quality tracker — accumulates outcome feedback per chat.
        # Skeleton: stats stay 0/empty until Selene starts reporting outcomes
        # via POST /api/signals/<id>/outcome. In-memory only; lost on restart.
        from tgwatcher.source_quality import SourceQualityTracker
        self.source_quality_tracker = SourceQualityTracker()
        logger.info(
            "Signal engine initialized (provider=%s, model=%s, webhook=%s)",
            llm_config.provider,
            llm_config.model,
            "enabled" if (self.webhook_dispatcher and self.webhook_dispatcher.enabled) else "disabled",
        )

    # ── _get_tg_client (api.py:462) ─────────────────────────────────────
    def get_tg_client(self) -> TGClient:
        """Get or create the shared TGClient singleton.

        All API endpoints must use this instead of creating their own TelegramClient,
        because Telethon's SQLite session file cannot be opened by multiple clients
        simultaneously (causes 'database is locked' errors).
        """
        if self.tg_client is not None:
            return self.tg_client
        loop = self.async_loop.get_loop() if self.async_loop else None
        self.tg_client = TGClient(self.config, loop=loop)
        return self.tg_client

    # ── _disconnect_tg_client (api.py:477) ───────────────────────────────
    def disconnect_tg_client(self) -> None:
        """Disconnect and discard the shared TGClient so the next call gets a fresh one."""
        if self.tg_client is None:
            return
        try:
            if self.async_loop:
                self.async_loop.run_coroutine(self.tg_client.disconnect())
            else:
                import asyncio
                loop = asyncio.new_event_loop()
                try:
                    loop.run_until_complete(self.tg_client.disconnect())
                finally:
                    loop.close()
        except Exception:
            pass
        self.tg_client = None
