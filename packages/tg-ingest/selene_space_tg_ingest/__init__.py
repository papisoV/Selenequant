"""SeleneQuant TG Ingest — Telegram real-time message → LLM signal → selene EventGuard.

Absorbed from 实验/tgwatcher (2026-07-19). Web UI dropped; core LLM pipeline +
storage + listener retained. Pluggable callback hooks (on_signal_factor /
on_tg_event) decouple selene integration from package internals.
"""

__version__ = "0.1.0"
