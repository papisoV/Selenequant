"""SeleneQuant CLI command modules."""

from __future__ import annotations

_COMMAND_MODULE_NAMES = [
    "setup",
    "status",
    "market",
    "backtest",
    "paper",
    "doctor_cmd",
]

ENTRYPOINT_ALIASES: dict[str, str] = {
    "setup": "setup",
    "status": "status",
    "market": "market",
    "backtest": "backtest",
    "paper": "paper",
    "doctor_cmd": "doctor",
}
