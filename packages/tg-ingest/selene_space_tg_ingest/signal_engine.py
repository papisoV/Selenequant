"""Signal engine - orchestrates keyword filtering and LLM refinement for news factor extraction.

Pluggable callback hooks (on_signal_factor, on_tg_event) decouple selene integration
from package internals. Replaces the old tgwatcher.web SSE push with a callable hook
that selene scripts can register (e.g., to write into macro_events.db via EventGuard).
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable

from selene_space_tg_ingest.signal_dedup import SignalDeduper
from selene_space_tg_ingest.signal_filter import KeywordFilter
from selene_space_tg_ingest.signal_llm import SignalLLMClient, LLMRefineError
from selene_space_tg_ingest.storage import Storage

logger = logging.getLogger(__name__)


@dataclass
class BatchResult:
    total: int = 0
    completed: int = 0
    failed: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)


class SignalEngine:
    """Orchestrates keyword pre-filter + LLM refinement.

    Optional callback hooks:
        on_signal_factor(factor_dict): invoked when a message completes processing
            (either LLM-completed or keyword-skipped). Useful for pushing to selene's
            EventGuard or any external sink. Must be exception-safe; errors are logged.
        on_tg_event(event_dict): invoked when a TG message arrives but before LLM
            processing. Reserved for future use (e.g., raw-event deduplication).
    """

    def __init__(
        self,
        storage: Storage,
        keyword_filter: KeywordFilter,
        llm: SignalLLMClient,
        config: dict,
        on_signal_factor: Callable[[dict], None] | None = None,
        on_tg_event: Callable[[dict], None] | None = None,
        deduper: SignalDeduper | None = None,
    ):
        self._storage = storage
        self._filter = keyword_filter
        self._llm = llm
        self._config = config
        self._batch_size = config.get("batch_size", 50)
        self._llm_delay = config.get("llm_delay", 1.0)
        self._llm_batch_size = config.get("llm_batch_size", 15)
        self._factor_version = config.get("factor_version", 2)
        self._on_signal_factor = on_signal_factor
        self._on_tg_event = on_tg_event
        self._deduper = deduper

    def _llm_model_name(self) -> str:
        """Resolve the model name recorded on factors.

        Prefers the config model (legacy flat configs); falls back to the LLM
        client's resolved model for providers-dict configs where the top-level
        `model` key is absent.
        """
        cfg_model = self._config.get("llm", {}).get("model", "")
        if cfg_model:
            return cfg_model
        if self._llm is not None:
            return self._llm.model_name
        return ""

    @staticmethod
    def _compute_is_signal(factor: dict) -> bool:
        """Event-gate semantics (message = event sensor, not direction crystal ball).

        A factor is a real signal when:
        - LLM completed (not failed/skipped)
        - event_type is valid (non-empty)
        - magnitude > 0
        - confidence >= 0.3

        Direction is NOT required: neutral (direction=0) events carry real
        market-impact information (audit 2026-08-13: macro/listing/whale bars
        show significant volume without predictive direction). Direction remains
        a sentiment label, not a gate.

        Used as the gate for downstream event overlay (de-risk) and dedup.
        """
        if factor.get("llm_status") != "completed":
            return False
        try:
            event_type = str(factor.get("event_type") or "").strip()
            magnitude = float(factor.get("magnitude") or 0)
            confidence = float(factor.get("confidence") or 0)
        except (TypeError, ValueError):
            return False
        return event_type != "" and magnitude > 0 and confidence >= 0.3

    def _build_signal_payload(self, factor: dict) -> dict:
        """Enrich a completed/skipped factor into a downstream push payload.

        Adds:
        - is_signal: event-gate semantics (llm completed + event_type valid + mag>0 + conf>=0.3)
        - signal_score: direction * magnitude * confidence * (0.5 + 0.5 * urgency),
          range [-1, 1] — emotional strength only, NOT a tradeable signal.
          Downstream must NOT consume direction (direction alpha falsified 2026-08-13).
        - expires_at: created_at + halflife_min minutes

        Keeps the factor's existing keys — symbols stays a JSON string (the
        downstream EventGuard hook parses it).
        """
        payload = dict(factor)
        payload["is_signal"] = self._compute_is_signal(payload)
        direction = float(payload.get("direction") or 0)
        magnitude = float(payload.get("magnitude") or 0)
        confidence = float(payload.get("confidence") or 0)
        urgency = float(payload.get("urgency") or 0)
        payload["signal_score"] = direction * magnitude * confidence * (0.5 + 0.5 * urgency)
        halflife = int(payload.get("halflife_min") or 60)
        created = payload.get("created_at")
        if created:
            try:
                dt = datetime.fromisoformat(str(created).replace(" ", "T"))
                payload["expires_at"] = (dt + timedelta(minutes=halflife)).isoformat()
            except (ValueError, TypeError):
                pass
        return payload

    def _should_emit(self, payload: dict) -> bool:
        """Dedup gate for real signals.

        Non-signals always emit (backward compatibility — the downstream hook
        filters them itself). Real signals pass through the deduper when one is
        configured; deduper=None means every signal emits.
        """
        if not payload.get("is_signal", True):
            return True
        if self._deduper is None:
            return True
        return self._deduper.should_emit(payload)

    def _save_and_read_back(self, factor_data: dict) -> dict:
        """Save factor, then return the full DB row.

        Callers (on_signal_factor hooks) rely on created_at for downstream
        timestamping; save_signal_factor returns None, so re-read the row to
        enrich the returned factor with DB-populated columns.
        """
        self._storage.save_signal_factor(factor_data)
        full = self._storage.get_signal_factor(
            factor_data["message_id"], factor_data["chat_id"]
        )
        return full or factor_data

    def _emit_signal_factor(self, factor: dict) -> None:
        """Fire on_signal_factor callback if registered. Swallows exceptions."""
        if self._on_signal_factor is None:
            return
        try:
            self._on_signal_factor(factor)
        except Exception as e:
            logger.warning("on_signal_factor callback failed for msg %s: %s", factor.get("message_id"), e)

    def _emit_tg_event(self, msg: dict) -> None:
        """Fire on_tg_event callback if registered. Swallows exceptions."""
        if self._on_tg_event is None:
            return
        try:
            self._on_tg_event(msg)
        except Exception as e:
            logger.warning("on_tg_event callback failed for msg %s: %s", msg.get("message_id"), e)

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
            return self._save_and_read_back(factor_data)

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
                "llm_model": self._llm_model_name(),
                "llm_raw": json.dumps(llm_result, ensure_ascii=False),
            })
            factor_data["is_signal"] = self._compute_is_signal(factor_data)
        except (LLMRefineError, Exception) as e:
            logger.warning("LLM refinement failed for msg %d: %s", message_id, e)
            raw_response = getattr(e, "raw_response", None)
            factor_data.update({
                "llm_status": "failed",
                "llm_error": str(e)[:256],
                "is_signal": False,
            })
            if raw_response:
                factor_data["llm_raw"] = raw_response

        return self._save_and_read_back(factor_data)

    def _process_batch_llm(
        self,
        pending: list[tuple[dict, dict]],
        progress_callback=None,
        base_index: int = 0,
        total: int = 0,
    ) -> tuple[int, int, list[str]]:
        completed = 0
        failed = 0
        errors: list[str] = []

        chunk_size = self._llm_batch_size
        for chunk_start in range(0, len(pending), chunk_size):
            chunk = pending[chunk_start:chunk_start + chunk_size]

            for msg, filter_result in chunk:
                self._storage.save_signal_factor({
                    "message_id": msg["message_id"],
                    "chat_id": msg["chat_id"],
                    "llm_status": "processing",
                    "filter_result": "passed",
                    "matched_keywords": json.dumps(filter_result.matched_keywords, ensure_ascii=False),
                    "keyword_preliminary": json.dumps(filter_result.preliminary_factors, ensure_ascii=False),
                    "factor_version": self._factor_version,
                })

            batch_input = [(msg["text"], filt.preliminary_factors) for msg, filt in chunk]
            batch_results = self._llm.refine_batch(batch_input)

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
                        "llm_model": self._llm_model_name(),
                        "llm_raw": json.dumps(llm_result, ensure_ascii=False),
                    })
                    factor_data["is_signal"] = self._compute_is_signal(factor_data)
                    completed += 1
                else:
                    factor_data.update({
                        "llm_status": "failed",
                        "llm_error": "Batch item validation failed",
                        "is_signal": False,
                    })
                    failed += 1
                    errors.append(f"msg {msg['message_id']}: validation failed in batch")

                self._storage.save_signal_factor(factor_data)

            if progress_callback:
                try:
                    processed = base_index + chunk_start + len(chunk)
                    progress_callback(processed, total, failed)
                except Exception:
                    pass

            if self._llm_delay > 0 and chunk_start + chunk_size < len(pending):
                time.sleep(self._llm_delay)

        return completed, failed, errors

    def process_batch(self, chat_id: int | None = None,
                      date_from: str | None = None, date_to: str | None = None,
                      overwrite: bool = False,
                      progress_callback=None, stop_check=None) -> BatchResult:
        """Process all unprocessed messages in two phases."""
        result = BatchResult()

        reset_count = self._storage.reset_stuck_processing()
        if reset_count > 0:
            logger.info("Reset %d stuck processing rows", reset_count)

        messages = self._storage.get_unprocessed_messages(
            chat_id=chat_id, date_from=date_from, date_to=date_to, overwrite=overwrite
        )
        result.total = len(messages)
        logger.info("Batch processing %d messages (overwrite=%s)", result.total, overwrite)

        min_len = self._config.get("filter", {}).get("min_text_length", 10)

        pending: list[tuple[dict, dict]] = []
        for i, msg in enumerate(messages):
            text = msg.get("text")
            if not text or len(text) < min_len:
                continue

            message_id = msg["message_id"]
            chat_id_msg = msg["chat_id"]
            filter_result = self._filter.filter(text)

            if not filter_result.passed:
                self._storage.save_signal_factor({
                    "message_id": message_id,
                    "chat_id": chat_id_msg,
                    "llm_status": "skipped",
                    "filter_result": "rejected",
                    "matched_keywords": json.dumps(filter_result.matched_keywords, ensure_ascii=False),
                    "keyword_preliminary": json.dumps(filter_result.preliminary_factors, ensure_ascii=False),
                    "factor_version": self._factor_version,
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

        logger.info(
            "Filter phase done: %d passed, %d skipped out of %d",
            len(pending), result.skipped, result.total,
        )

        if pending:
            completed, failed, errors = self._process_batch_llm(
                pending,
                progress_callback=progress_callback,
                base_index=result.total,
                total=result.total,
            )
            result.completed = completed
            result.failed = failed
            result.errors = errors

        logger.info("Batch complete: %d completed, %d failed, %d skipped out of %d",
                     result.completed, result.failed, result.skipped, result.total)
        return result

    def process_new_message(self, msg: dict) -> dict | None:
        """Process a new message from the listener. Wraps LLM call with timeout.

        On failure, sets llm_status='pending' so batch processor can retry later.
        Fires on_signal_factor callback (if registered) when processing completes
        or is skipped — replaces the old tgwatcher.web SSE push.

        Real signals (is_signal=True) pass through the deduper first, so duplicate
        news forwarded across groups (or re-polled from RSS) only pushes downstream
        once. Non-signals always emit for backward compatibility — the downstream
        hook filters them itself.
        """
        self._emit_tg_event(msg)
        try:
            factor = self.process_message(msg)
            if factor and factor.get("llm_status") in ("completed", "skipped"):
                payload = self._build_signal_payload(factor)
                if self._should_emit(payload):
                    self._emit_signal_factor(payload)
            return factor
        except Exception as e:
            logger.error("process_new_message failed for msg %d: %s", msg.get("message_id"), e)
            self._storage.save_signal_factor({
                "message_id": msg["message_id"],
                "chat_id": msg["chat_id"],
                "llm_status": "pending",
            })
            return None
