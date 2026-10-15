# TG-Ingest Crop Risk Reclassification: stop_event

**Date**: 2026-07-30
**Original risk level**: 2 (medium) — "shared TelegramClient between tg_listen and tg_crawl"
**Reclassified level**: 0 (mislabeled — not a real risk)

## Why this was flagged

Initial audit of `selene-merged-final/packages/tg-ingest` flagged a potential
"shared client" risk: if `tg_listen.py` and `tg_crawl.py` share a single
`TelegramClient` instance, a `stop_event` set by one script could prematurely
halt the other.

## Why it is NOT a risk

Direct source inspection of `scripts/tg_listen.py` and `scripts/tg_crawl.py`
confirms:

- `tg_listen.py` constructs its own `TGClient(config, loop)` and calls
  `connect()` — owns its own `TelegramClient` instance.
- `tg_crawl.py` constructs a separate `TGClient(config, loop)` and calls
  `connect()` — owns its own `TelegramClient` instance.
- The two scripts are designed to run as **independent OS processes**,
  not as threads/tasks within one process. Each has its own Python
  interpreter, its own asyncio loop, its own session file handle.
- A `stop_event` set in `tg_listen.py`'s process cannot affect
  `tg_crawl.py`'s process — they don't share memory.

The session *file* on disk (`sessions/tgwatcher_<phone>`) is shared, but
that's a filesystem resource, not an in-memory client. WAL mode on the
session (see `tg_session.py`) handles concurrent file access correctly.

## Why this matters

Future audits should NOT re-investigate this as a "risk". It is a known
non-issue. Reclassifying it to level 0 saves future audit cycles and
prevents well-meaning engineers from "fixing" a non-problem by adding
cross-process stop_event plumbing (which would add complexity for no
benefit).

## Related hardening (this audit cycle)

- `tg_session.py` (NEW): WAL + busy_timeout on Telethon session — addresses
  the *real* concurrent-access risk (file-level locking), not the
  phantom client-sharing risk.
- `signal_llm.py` (EDIT): GLM refusal detection + raw logging — addresses
  the medium-level risk of silent refusal crop.
- This file (NEW): documents the stop_event reclassification.
