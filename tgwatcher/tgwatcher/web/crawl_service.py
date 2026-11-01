"""Crawl service - runs Telethon crawl in a background thread, controllable via start/stop/status."""
from __future__ import annotations

import asyncio
import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Callable

from tgwatcher.client import TGClient
from tgwatcher.tz_utils import utc_now, local_to_utc

logger = logging.getLogger(__name__)


class CrawlService:
    def __init__(self, config: dict, async_loop=None, storage=None, on_status_change: Callable | None = None,
                 get_tg_client: Callable | None = None, tg_lock: threading.Lock | None = None):
        self.config = config
        self._async_loop = async_loop
        self._storage = storage
        self._on_status_change = on_status_change
        self._get_tg_client = get_tg_client
        self._tg_lock = tg_lock or threading.Lock()
        self._owns_client = False
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._status = {
            "running": False,
            "mode": "idle",
            "current_group": None,
            "current_group_index": 0,
            "total_groups": 0,
            "completed_groups": 0,
            "current_group_fetched": 0,
            "current_group_saved": 0,
            "total_fetched": 0,
            "total_saved": 0,
            "started_at": None,
            "last_crawl_at": None,
            "error": None,
            "speed": 0,
            "elapsed_seconds": 0,
            "eta_seconds": 0,
        }

    @property
    def status(self) -> dict:
        with self._lock:
            return dict(self._status)

    def _update_status(self, **kwargs) -> None:
        with self._lock:
            self._status.update(kwargs)
            snapshot = dict(self._status)
        if self._on_status_change:
            try:
                self._on_status_change(snapshot)
            except Exception:
                pass

    def _read_status(self, key):
        with self._lock:
            return self._status[key]

    def start(self, mode: str = "incremental", offset_date: str | None = None,
              until_date: str | None = None, group_id: int | None = None) -> bool:
        with self._lock:
            if self._status["running"]:
                return False
        self._stop_event.clear()
        self._crawl_offset_date = offset_date
        self._crawl_until_date = until_date
        self._crawl_group_id = group_id

        # For catchup mode, pre-filter groups with auto_catchup enabled
        if mode == "catchup":
            catchup_groups = [g for g in self.config.get("groups", []) if g.get("auto_catchup", False)]
            if not catchup_groups:
                return False
            self._catchup_groups = catchup_groups
            total = len(catchup_groups)
        elif group_id is not None:
            # Single-group crawl (auto-poll)
            self._catchup_groups = [g for g in self.config.get("groups", []) if g.get("id") == group_id]
            if not self._catchup_groups:
                return False
            total = 1
        else:
            self._catchup_groups = None
            total = len(self.config.get("groups", []))

        self._update_status(
            running=True,
            mode=mode,
            current_group=None,
            current_group_index=0,
            total_groups=total,
            completed_groups=0,
            current_group_fetched=0,
            current_group_saved=0,
            total_fetched=0,
            total_saved=0,
            started_at=utc_now().isoformat(),
            last_crawl_at=None,
            error=None,
            speed=0,
            elapsed_seconds=0,
            eta_seconds=0,
        )
        thread = threading.Thread(target=self._run_loop, args=(mode,), daemon=True)
        thread.start()
        logger.info("Crawl service started", extra={"mode": mode, "action": "crawl_start"})
        return True

    def stop(self) -> bool:
        with self._lock:
            if not self._status["running"]:
                return False
        self._stop_event.set()
        self._update_status(current_group="正在停止...")
        logger.info("Crawl service stop requested")
        return True

    def _run_loop(self, mode: str) -> None:
        try:
            if self._async_loop:
                loop = self._async_loop.get_loop()
                future = asyncio.run_coroutine_threadsafe(self._crawl(loop, mode), loop)
                future.result(timeout=3600)  # 1h hard timeout
            else:
                loop = asyncio.new_event_loop()
                try:
                    loop.run_until_complete(self._crawl(loop, mode))
                finally:
                    loop.close()
        except TimeoutError:
            logger.error("Crawl timed out after 1 hour", extra={"mode": mode, "action": "crawl_timeout"})
            self._update_status(error="爬取超时 (1小时)")
        except Exception as e:
            logger.error("Crawl service error", extra={"mode": mode, "error": str(e), "action": "crawl_error"}, exc_info=True)
            self._update_status(error=str(e))
        finally:
            self._stop_event.set()
            self._update_status(running=False, current_group=None)

    async def _crawl(self, loop: asyncio.AbstractEventLoop, mode: str) -> None:
        # For catchup mode, use pre-filtered groups; otherwise use all configured groups
        if mode == "catchup" and getattr(self, "_catchup_groups", None):
            groups = self._catchup_groups
        else:
            groups = self.config.get("groups", [])
        if not groups:
            self._update_status(error="No groups configured")
            return

        crawl_cfg = self.config.get("crawl", {})
        limit = crawl_cfg.get("limit", 100)
        interval = crawl_cfg.get("interval_minutes", 30)

        # Prefer dates passed from API call, fall back to config
        offset_date = None
        until_date = None
        if mode == "date_range":
            raw_offset = getattr(self, "_crawl_offset_date", None) or crawl_cfg.get("offset_date")
            raw_until = getattr(self, "_crawl_until_date", None) or crawl_cfg.get("until_date")
            if raw_offset:
                offset_date = local_to_utc(datetime.fromisoformat(raw_offset)).replace(tzinfo=timezone.utc)
            if raw_until:
                until_local = datetime.fromisoformat(raw_until).replace(hour=23, minute=59, second=59, microsecond=999999)
                until_date = local_to_utc(until_local).replace(tzinfo=timezone.utc)

        storage = self._storage

        # Use shared TGClient if available, otherwise create our own.
        # Hold _tg_lock for the entire crawl — not just connect — because
        # Telethon's session file writes (pts/qts/update_state/auth_key)
        # happen on every iter_messages call, and concurrent _tg_client_guard
        # callers (login_status, dialogs) writing the same session file cause
        # "database is locked" errors. PRAGMA busy_timeout cannot resolve
        # same-connection cursor races, so the lock must serialize all access.
        _lock_acquired = False
        if self._get_tg_client:
            self._tg_lock.acquire()
            _lock_acquired = True
            try:
                tg = self._get_tg_client()
                if tg.client is None or not tg.client.is_connected():
                    await tg.connect()
            except Exception:
                self._tg_lock.release()
                _lock_acquired = False
                raise
            self._owns_client = False
        else:
            loop = self._async_loop.get_loop() if self._async_loop else None
            tg = TGClient(self.config, loop=loop)
            await tg.connect()
            self._owns_client = True

        try:
            iteration = 0
            while not self._stop_event.is_set():
                iteration += 1
                completed = 0
                crawl_start = utc_now()
                for group_idx, group in enumerate(groups):
                    if self._stop_event.is_set():
                        break

                    chat_id = group.get("id") or group.get("username")
                    group_name = group.get("name", chat_id)
                    if not chat_id:
                        continue

                    self._update_status(
                        current_group=group_name,
                        current_group_index=group_idx + 1,
                        completed_groups=completed,
                        current_group_fetched=0,
                        current_group_saved=0,
                    )

                    if mode == "full":
                        min_id = 0
                        msg_limit = limit
                    elif mode == "date_range":
                        min_id = 0
                        msg_limit = crawl_cfg.get("date_range_limit", 1000)
                    elif mode == "catchup":
                        min_id = 0
                        msg_limit = self.config.get("catchup", {}).get("limit", 1000)
                    else:
                        last_id = storage.get_last_message_id(
                            chat_id if isinstance(chat_id, int) else 0
                        )
                        min_id = last_id if last_id else 0
                        msg_limit = limit

                    # For catchup mode, compute per-group offset_date from last message date
                    group_offset_date = None
                    group_until_date = None
                    if mode == "catchup":
                        chat_id_int = chat_id if isinstance(chat_id, int) else 0
                        last_date = storage.get_last_message_date(chat_id_int)
                        if last_date:
                            group_offset_date = last_date.replace(tzinfo=timezone.utc)
                        else:
                            group_offset_date = datetime.now(timezone.utc) - timedelta(days=7)
                        group_until_date = datetime.now(timezone.utc)

                    try:
                        messages = await self._fetch_with_stop_check(
                            tg, chat_id=chat_id, limit=msg_limit, min_id=min_id,
                            offset_date=group_offset_date if mode == "catchup" else (offset_date if mode == "date_range" else None),
                            until_date=group_until_date if mode == "catchup" else (until_date if mode == "date_range" else None),
                        )
                        fetched = len(messages)
                        total_fetched = self._read_status("total_fetched") + fetched
                        self._update_status(
                            current_group_fetched=fetched,
                            total_fetched=total_fetched,
                        )
                        if messages:
                            saved = storage.save_messages(messages)
                            total_saved = self._read_status("total_saved") + saved
                            self._update_status(
                                current_group_saved=saved,
                                total_saved=total_saved,
                            )
                            logger.info("Saved %d/%d messages from %s", saved, len(messages), group_name)
                        else:
                            logger.info("No new messages from %s", group_name)
                    except Exception as e:
                        logger.error("Error crawling %s: %s", group_name, e)
                        self._update_status(error=f"{group_name}: {e}")

                    if self._stop_event.is_set():
                        break

                    # Calculate speed & ETA after each group
                    elapsed = (utc_now() - crawl_start).total_seconds()
                    total_fetched = self._read_status("total_fetched")
                    speed = round(total_fetched / max(elapsed, 1) * 60, 1) if elapsed > 0 else 0
                    remaining_groups = len(groups) - completed - 1
                    avg_per_group = elapsed / max(completed + 1, 1)
                    eta = round(avg_per_group * remaining_groups)
                    self._update_status(
                        speed=speed,
                        elapsed_seconds=round(elapsed),
                        eta_seconds=eta,
                    )

                    completed += 1

                self._update_status(
                    last_crawl_at=utc_now().isoformat(),
                    completed_groups=completed,
                )

                if mode != "incremental":
                    break

                if self._stop_event.is_set():
                    break

                for _ in range(interval * 12):
                    if self._stop_event.is_set():
                        break
                    await asyncio.sleep(5)
        finally:
            if self._owns_client:
                await tg.disconnect()
                self._owns_client = False
            # Release _tg_lock only if we acquired it for shared-client crawl.
            if _lock_acquired:
                try:
                    self._tg_lock.release()
                except RuntimeError:
                    pass

    async def _fetch_with_stop_check(self, tg, **kwargs) -> list:
        """Fetch messages but check stop_event between individual message fetches.

        Telethon's iter_messages yields one at a time, so we wrap it to
        check _stop_event after each message and break early if stopped.
        """
        if not tg.client:
            return []

        chat_id = kwargs["chat_id"]
        limit = kwargs.get("limit", 100)
        min_id = kwargs.get("min_id", 0)
        offset_date = kwargs.get("offset_date")
        until_date = kwargs.get("until_date")

        entity = await tg.client.get_entity(chat_id)

        from tgwatcher.parsers import parse_telethon_message
        from tgwatcher.schemas import ParsedMessage

        messages: list[ParsedMessage] = []
        async for msg in tg.client.iter_messages(
            entity, limit=limit, min_id=min_id, offset_date=offset_date, reverse=True
        ):
            if self._stop_event.is_set():
                logger.info("Stop requested during fetch, returning %d messages so far", len(messages))
                break

            if msg.text is None and msg.media is None:
                continue

            if until_date and msg.date:
                msg_dt = msg.date.replace(tzinfo=None) if msg.date.tzinfo else msg.date
                until_dt = until_date.replace(tzinfo=None) if until_date.tzinfo else until_date
                if msg_dt > until_dt:
                    continue

            parsed = parse_telethon_message(msg, entity)
            messages.append(parsed)

            # Per-message progress update
            self._update_status(current_group_fetched=len(messages))

            import random
            delay = random.uniform(tg.min_delay, tg.max_delay)
            await asyncio.sleep(delay)

        logger.info("Fetched messages", extra={
            "group_id": getattr(entity, "id", chat_id),
            "group_name": getattr(entity, "title", None) or str(chat_id),
            "fetched": len(messages),
            "action": "fetch_complete",
        })
        return messages
