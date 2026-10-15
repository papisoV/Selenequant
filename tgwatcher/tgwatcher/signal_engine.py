"""Signal engine - orchestrates keyword filtering and LLM refinement for news factor extraction."""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field

from tgwatcher.signal_filter import KeywordFilter
from tgwatcher.signal_llm import SignalLLMClient, LLMRefineError
from tgwatcher.storage import Storage

logger = logging.getLogger(__name__)


@dataclass
class BatchResult:
    total: int = 0
    completed: int = 0
    failed: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)


class SignalEngine:
    def __init__(self, storage: Storage, keyword_filter: KeywordFilter,
                 llm: SignalLLMClient, config: dict,
                 webhook_dispatcher=None,
                 bot_pusher=None,
                 deduper=None):
        self._storage = storage
        self._filter = keyword_filter
        self._llm = llm
        self._config = config
        self._batch_size = config.get("batch_size", 50)
        self._llm_delay = config.get("llm_delay", 1.0)
        self._llm_batch_size = config.get("llm_batch_size", 15)
        self._factor_version = config.get("factor_version", 2)
        # Webhook dispatcher (optional). None = no webhook dispatch.
        self._webhook = webhook_dispatcher
        # Bot pusher (optional). None = no Telegram Bot push.
        self._bot_pusher = bot_pusher
        # Signal deduper (optional). None = no dedup, all is_signal=True
        # signals are pushed downstream. When set, filters new_signal SSE +
        # webhook dispatch for same-key signals within window_seconds.
        # DB writes (signal_factors) are NOT affected — only push is filtered.
        self._deduper = deduper

    def process_message(self, msg: dict) -> dict | None:
        """Process a single message through keyword filter and LLM. Returns factor dict or None."""
        text = msg.get("text")
        min_len = self._config.get("filter", {}).get("min_text_length", 10)

        if not text or len(text) < min_len:
            return None

        message_id = msg["message_id"]
        chat_id = msg["chat_id"]

        # Step 1: Keyword filter
        filter_result = self._filter.filter(text)

        if not filter_result.passed:
            factor_data = {
                "message_id": message_id,
                "chat_id": chat_id,
                "llm_status": "skipped",
                "filter_result": "rejected",
                "matched_keywords": json.dumps(filter_result.matched_keywords, ensure_ascii=False),
                "keyword_preliminary": json.dumps(filter_result.preliminary_factors, ensure_ascii=False),
                "factor_version": self._factor_version,
                "is_signal": False,
            }
            self._storage.save_signal_factor(factor_data)
            return factor_data

        # Step 2: Save processing state, then run LLM
        factor_data = {
            "message_id": message_id,
            "chat_id": chat_id,
            "llm_status": "processing",
            "filter_result": "passed",
            "matched_keywords": json.dumps(filter_result.matched_keywords, ensure_ascii=False),
            "keyword_preliminary": json.dumps(filter_result.preliminary_factors, ensure_ascii=False),
            "factor_version": self._factor_version,
        }
        self._storage.save_signal_factor(factor_data)

        try:
            llm_result = self._llm.refine(text, filter_result.preliminary_factors)
            # Merge LLM result into factor data
            factor_data.update({
                "direction": llm_result["direction"],
                "magnitude": llm_result["magnitude"],
                "urgency": llm_result["urgency"],
                "confidence": llm_result["confidence"],
                "halflife_min": llm_result["halflife_min"],
                "symbols": json.dumps(llm_result["symbols"], ensure_ascii=False),
                "event_type": llm_result["event_type"],
                "reasoning": llm_result.get("reasoning", ""),
                "llm_status": "completed",
                "llm_model": f"{self._llm.provider}:{self._llm.model_name}",
                "llm_raw": json.dumps(llm_result, ensure_ascii=False),
            })
        except (LLMRefineError, Exception) as e:
            logger.warning("LLM refinement failed for msg %d: %s", message_id, e)
            factor_data.update({
                "llm_status": "failed",
                "llm_error": str(e)[:256],
            })

        # Activate the is_signal flag: only "non-neutral + confidence>=0.3 + completed".
        # Before this change, the column defaulted to True for every saved row, which
        # made it useless as a downstream filter. Now it carries real semantics.
        factor_data["is_signal"] = self._compute_is_signal(factor_data)

        self._storage.save_signal_factor(factor_data)
        return factor_data

    @staticmethod
    def _compute_is_signal(factor_data: dict) -> bool:
        """A factor is a 'signal' if LLM completed, direction is non-zero, and
        confidence is at least 0.3. Used by webhook + new_signal SSE filters."""
        if factor_data.get("llm_status") != "completed":
            return False
        try:
            direction = float(factor_data.get("direction") or 0)
            confidence = float(factor_data.get("confidence") or 0)
        except (TypeError, ValueError):
            return False
        return direction != 0 and confidence >= 0.3

    def _process_batch_llm(
        self,
        pending: list[tuple[dict, dict]],
        progress_callback=None,
        base_index: int = 0,
        total: int = 0,
    ) -> tuple[int, int, list[str]]:
        """
        Process filter-passed messages using batch LLM calls.

        Args:
            pending: List of (msg, filter_result) tuples.
            progress_callback: Optional callback(processed, total, errors).
            base_index: Offset for progress reporting.
            total: Total message count for progress reporting.

        Returns:
            (completed_count, failed_count, errors_list)
        """
        completed = 0
        failed = 0
        errors: list[str] = []

        # Chunk the pending messages into groups of llm_batch_size
        chunk_size = self._llm_batch_size
        for chunk_start in range(0, len(pending), chunk_size):
            chunk = pending[chunk_start:chunk_start + chunk_size]

            # Mark all as processing
            for msg, filter_result in chunk:
                self._storage.save_signal_factor({
                    "message_id": msg["message_id"],
                    "chat_id": msg["chat_id"],
                    "llm_status": "processing",
                    "filter_result": "passed",
                    "is_signal": False,  # avoid transient True (column default) before LLM verdict
                    "matched_keywords": json.dumps(filter_result.matched_keywords, ensure_ascii=False),
                    "keyword_preliminary": json.dumps(filter_result.preliminary_factors, ensure_ascii=False),
                    "factor_version": self._factor_version,
                })

            # Build batch input
            batch_input = [(msg["text"], filt.preliminary_factors) for msg, filt in chunk]

            # Call batch LLM
            batch_results = self._llm.refine_batch(batch_input)

            # Save each result
            for i, (msg, filter_result) in enumerate(chunk):
                llm_result = batch_results[i] if i < len(batch_results) else None
                factor_data = {
                    "message_id": msg["message_id"],
                    "chat_id": msg["chat_id"],
                    "filter_result": "passed",
                    "matched_keywords": json.dumps(filter_result.matched_keywords, ensure_ascii=False),
                    "keyword_preliminary": json.dumps(filter_result.preliminary_factors, ensure_ascii=False),
                    "factor_version": self._factor_version,
                }

                if llm_result is not None:
                    factor_data.update({
                        "direction": llm_result["direction"],
                        "magnitude": llm_result["magnitude"],
                        "urgency": llm_result["urgency"],
                        "confidence": llm_result["confidence"],
                        "halflife_min": llm_result["halflife_min"],
                        "symbols": json.dumps(llm_result["symbols"], ensure_ascii=False),
                        "event_type": llm_result["event_type"],
                        "reasoning": llm_result.get("reasoning", ""),
                        "llm_status": "completed",
                        "llm_model": f"{self._llm.provider}:{self._llm.model_name}",
                        "llm_raw": json.dumps(llm_result, ensure_ascii=False),
                    })
                    completed += 1
                else:
                    # refine_batch returned None for this item — means batch API
                    # failed AND individual fallback also failed (503/JSON parse/
                    # validation). The exact reason is logged in signal_llm.py;
                    # surface a more accurate error text than the old "Batch item
                    # validation failed" which conflated call-failure with
                    # schema-validation-failure.
                    factor_data.update({
                        "llm_status": "failed",
                        "llm_error": "LLM call failed (batch + fallback exhausted; see signal_llm logs for reason)",
                    })
                    failed += 1
                    errors.append(f"msg {msg['message_id']}: LLM call failed (batch + fallback)")

                # Activate is_signal flag consistently with process_message path.
                # Without this, batch-processed rows keep the column's default (True)
                # even when direction=0 or confidence<0.3, breaking downstream filters.
                factor_data["is_signal"] = self._compute_is_signal(factor_data)

                self._storage.save_signal_factor(factor_data)

            # Progress callback after each chunk
            if progress_callback:
                try:
                    processed = base_index + chunk_start + len(chunk)
                    progress_callback(processed, total, failed)
                except Exception:
                    pass

            # Delay between batch API calls
            if self._llm_delay > 0 and chunk_start + chunk_size < len(pending):
                time.sleep(self._llm_delay)

        return completed, failed, errors

    def process_batch(self, chat_id: int | None = None,
                      date_from: str | None = None, date_to: str | None = None,
                      overwrite: bool = False,
                      progress_callback=None, stop_check=None) -> BatchResult:
        """
        Process all unprocessed messages in two phases:
        1. Filter all messages, save rejected/skipped immediately.
        2. Batch LLM calls for filter-passed messages.
        """
        result = BatchResult()

        # Reset stuck processing rows first
        reset_count = self._storage.reset_stuck_processing()
        if reset_count > 0:
            logger.info("Reset %d stuck processing rows", reset_count)

        messages = self._storage.get_unprocessed_messages(
            chat_id=chat_id, date_from=date_from, date_to=date_to, overwrite=overwrite
        )
        result.total = len(messages)
        logger.info("Batch processing %d messages (overwrite=%s)", result.total, overwrite)

        min_len = self._config.get("filter", {}).get("min_text_length", 10)

        # Phase 1: Filter all messages
        pending: list[tuple[dict, dict]] = []
        for i, msg in enumerate(messages):
            text = msg.get("text")
            if not text or len(text) < min_len:
                continue

            message_id = msg["message_id"]
            chat_id_msg = msg["chat_id"]
            filter_result = self._filter.filter(text)

            if not filter_result.passed:
                # Save rejected immediately
                self._storage.save_signal_factor({
                    "message_id": message_id,
                    "chat_id": chat_id_msg,
                    "llm_status": "skipped",
                    "filter_result": "rejected",
                    "matched_keywords": json.dumps(filter_result.matched_keywords, ensure_ascii=False),
                    "keyword_preliminary": json.dumps(filter_result.preliminary_factors, ensure_ascii=False),
                    "factor_version": self._factor_version,
                    "is_signal": False,
                })
                result.skipped += 1
            else:
                pending.append((msg, filter_result))

            if progress_callback:
                try:
                    progress_callback(i + 1, result.total, 0)
                except Exception:
                    pass

            if stop_check and stop_check():
                logger.info("Batch processing stopped during filter phase")
                return result

        # Phase 2: Batch LLM calls
        logger.info(
            "Filter phase done: %d passed, %d skipped out of %d",
            len(pending), result.skipped, result.total,
        )

        if pending:
            completed, failed, errors = self._process_batch_llm(
                pending,
                progress_callback=progress_callback,
                base_index=result.total,  # offset after filter phase
                total=result.total,
            )
            result.completed = completed
            result.failed = failed
            result.errors = errors

        logger.info("Batch complete: %d completed, %d failed, %d skipped out of %d",
                     result.completed, result.failed, result.skipped, result.total)
        return result

    @staticmethod
    def _build_signal_payload(msg: dict, factor: dict) -> dict:
        """Build the new_signal payload — mirrors /api/signals/export JSON row shape.

        Adds two downstream-facing fields:
        - expires_at: date + 2 * halflife_min (when the signal is considered stale)
        - signal_score: normalized [-1,1] score combining direction/magnitude/
          confidence/urgency. Formula chosen to be a single actionable number
          for downstream risk gating; adjust here if formula changes.
        """
        try:
            symbols = json.loads(factor["symbols"]) if factor.get("symbols") else []
        except (json.JSONDecodeError, TypeError):
            symbols = []

        direction = factor.get("direction") or 0
        magnitude = factor.get("magnitude") or 0
        urgency = factor.get("urgency") or 0
        confidence = factor.get("confidence") or 0
        halflife_min = factor.get("halflife_min") or 60

        # Normalized signal score: direction * magnitude * confidence * (0.5 + 0.5 * urgency)
        # Range [-1, 1]. Urgency acts as a weight multiplier (high urgency → full weight,
        # low urgency → half weight). Confidence scales the whole thing down when LLM is unsure.
        try:
            signal_score = round(
                float(direction) * float(magnitude) * float(confidence) * (0.5 + 0.5 * float(urgency)),
                4,
            )
        except (TypeError, ValueError):
            signal_score = 0.0

        # Expiry: 2x halflife = signal decays to 25% strength, considered stale.
        date_dt = msg.get("date")
        expires_at = None
        if date_dt is not None:
            try:
                from datetime import timedelta, timezone
                # Telethon returns aware UTC datetimes, but be defensive —
                # if a naive datetime slips in (test/replay path), stamp UTC
                # so downstream always sees a timezone-suffixed ISO string.
                if date_dt.tzinfo is None:
                    date_dt = date_dt.replace(tzinfo=timezone.utc)
                expires_at = (date_dt + timedelta(minutes=int(halflife_min) * 2)).isoformat()
            except (TypeError, ValueError):
                pass

        return {
            "message_id": msg["message_id"],
            "chat_id": msg["chat_id"],
            "chat_title": msg.get("chat_title"),
            "sender_name": msg.get("sender_name"),
            "text": msg.get("text"),
            "date": msg.get("date"),
            "direction": direction,
            "magnitude": magnitude,
            "urgency": urgency,
            "confidence": confidence,
            "halflife_min": halflife_min,
            "symbols": symbols,
            "event_type": factor.get("event_type"),
            "reasoning": factor.get("reasoning"),
            "signal_score": signal_score,
            "expires_at": expires_at,
        }

    def process_new_message(self, msg: dict) -> dict | None:
        """Process a new message from the listener. Wraps LLM call with timeout.
        On failure, sets llm_status='pending' so batch processor can retry later.
        Pushes SSE event on completion.

        - `signal_factor` SSE: fires on every completed/skipped (existing behavior).
        - `new_signal` SSE: fires only when is_signal=True (downstream-facing).
        - webhook dispatch: same trigger condition as `new_signal` SSE.
        """
        try:
            factor = self.process_message(msg)
            if not factor:
                return None

            # Always push the per-factor SSE (debugging, includes skipped)
            if factor.get("llm_status") in ("completed", "skipped"):
                from tgwatcher.web.api import push_sse_event
                push_sse_event("signal_factor", {
                    "message_id": msg["message_id"],
                    "chat_id": msg["chat_id"],
                    "direction": factor.get("direction"),
                    "event_type": factor.get("event_type"),
                    "magnitude": factor.get("magnitude"),
                    "urgency": factor.get("urgency"),
                })

            # Downstream-facing new_signal + webhook — only for real signals.
            if factor.get("is_signal"):
                from tgwatcher.web.api import push_sse_event
                payload = self._build_signal_payload(msg, factor)

                # Dedup: same (symbols, direction_sign, event_type) within
                # window_seconds. Only filters new_signal SSE + webhook dispatch.
                # signal_factor SSE (debugging) above is unaffected. DB writes
                # via save_signal_factor already happened in process_message.
                if self._deduper and not self._deduper.should_emit(payload):
                    logger.debug(
                        "Signal deduplicated (not pushed downstream): msg %d",
                        msg["message_id"],
                    )
                else:
                    push_sse_event("new_signal", payload)
                    if self._webhook and self._webhook.enabled:
                        try:
                            self._webhook.dispatch(payload)
                        except Exception as wh_err:
                            logger.warning(
                                "Webhook dispatch failed for msg %d: %s",
                                msg["message_id"], wh_err,
                            )
                    if self._bot_pusher and self._bot_pusher.enabled:
                        try:
                            self._bot_pusher.dispatch(payload)
                        except Exception as bp_err:
                            logger.warning(
                                "Bot push failed for msg %d: %s",
                                msg["message_id"], bp_err,
                            )

            return factor
        except Exception as e:
            logger.error("process_new_message failed for msg %d: %s", msg.get("message_id"), e)
            # Set to pending so batch processor can retry; include filter_result
            # so count_pending picks it up (otherwise it's an invisible orphan).
            self._storage.save_signal_factor({
                "message_id": msg["message_id"],
                "chat_id": msg["chat_id"],
                "filter_result": "passed",
                "llm_status": "pending",
            })
            return None
