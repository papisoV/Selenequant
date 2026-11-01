"""Telling a complete streamed answer from a clipped one.

A stream can stop for reasons that look identical to the caller: the model
finished, the model hit its output ceiling, or the connection died. Only the
first of those is a usable answer. ``chat_stream`` yields bare strings, which
have nowhere to record that distinction, so every consumer would have to
re-derive it -- and in practice none did: a truncated reply was handed back as
if it were whole.

These helpers keep the decision in one place. Providers report what the wire
said (``finish_reason`` for OpenAI-shaped streams, ``done_reason`` for
Ollama); ``settle_stream`` turns that into either a clean finish or an
exception at the end of the iteration.
"""

from __future__ import annotations

from selene_space_llm.base import (
    LLMError,
    LLMIncompleteStreamError,
    LLMTruncatedError,
)

#: Reason values with which a completion stops because it ran out of room.
#: The model returned the opening of an answer and stopped mid-thought; the
#: text is real but cannot be treated as the reply.
TRUNCATION_REASONS = frozenset(
    {
        # OpenAI-shaped endpoints
        "length",
        "max_tokens",
        "max_output_tokens",
        # Ollama's done_reason
        "num_predict",
    }
)


def is_truncated(finish_reason: str | None) -> bool:
    """Whether ``finish_reason`` means "stopped before the answer was done"."""
    return (finish_reason or "").strip().lower() in TRUNCATION_REASONS


def describe_transport_failure(exc: BaseException) -> str:
    """Render a transport error so the reason is never blank.

    ``httpx`` raises some failures (``ReadError`` on a dropped connection) with
    an empty message, which used to render as ``"Stream failed: "`` -- an
    error that tells the caller nothing about why their answer stopped. The
    sentence loses its point if the reason is blank, so fall back to the
    exception type.
    """
    reason = str(exc).strip()
    return reason or type(exc).__name__


def settle_stream(
    finish_reason: str | None,
    content_seen: bool,
    *,
    ended_explicitly: bool = False,
) -> None:
    """Raise if a finished stream cannot be presented as a complete answer.

    Called once the stream has ended and its chunks have all been handed out,
    so a truncated reply's text has already reached the consumer -- raising
    here stops the caller from treating it as final without withholding what
    was received.

    Args:
        finish_reason: Last reason the endpoint reported, ``None`` if it
            never said.
        content_seen: Whether any non-empty content chunk was yielded.
        ended_explicitly: The endpoint sent its end-of-stream sentinel (SSE
            ``[DONE]``). A body that simply stops is *not* the same event: one
            is the server saying it finished, the other is the connection
            going away. Both end the loop, so callers that only watch the loop
            cannot tell them apart.

    Raises:
        LLMError: The stream ended without carrying any content at all --
            checked first, because "raise your token budget" is useless advice
            when nothing came back to begin with.
        LLMTruncatedError: The endpoint said it stopped for want of room.
        LLMIncompleteStreamError: The stream never signalled an ending, so the
            reply may be missing its tail.
    """
    if not content_seen:
        # Nothing arrived, so "raise your budget" is not the headline -- but
        # the reason still has to survive: a reasoning model that burned its
        # whole ceiling without emitting text is fixed *only* by raising it,
        # and dropping the signal would leave that caller with no lead.
        detail = f" (finish_reason={finish_reason!r})" if finish_reason else ""
        raise LLMError(f"LLM stream completed without returning any content.{detail}")
    if is_truncated(finish_reason):
        raise LLMTruncatedError(
            f"LLM output hit its token ceiling (finish_reason={finish_reason!r}); "
            "the reply is incomplete. Raise the output token budget and retry."
        )
    if not finish_reason and not ended_explicitly:
        raise LLMIncompleteStreamError(
            "LLM stream ended without a finish reason; the reply may have been "
            "cut off. Treat the partial text as incomplete and retry."
        )


__all__ = ["TRUNCATION_REASONS", "is_truncated", "settle_stream"]
