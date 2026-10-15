"""Terminal color helpers — raw ANSI, zero dependencies.

Colors are emitted unconditionally; Windows 10+ terminals handle VT
sequences natively and older consoles degrade to visible escapes, which
the doctor report tolerates.
"""

from __future__ import annotations

from typing import Callable

_PREFIX = "\x1b["


def _seq(code: int) -> str:
    return f"{_PREFIX}{code}m"


RESET = _seq(0)
BOLD = _seq(1)
DIM = _seq(2)
RED = _seq(31)
GREEN = _seq(32)
YELLOW = _seq(33)
CYAN = _seq(36)


def _wrap(color: str) -> Callable[[str], str]:
    def apply(text: str, _color: str = color) -> str:
        return f"{_color}{text}{RESET}"

    return apply


ok = _wrap(GREEN)
warn = _wrap(YELLOW)
err = _wrap(RED)
info = _wrap(CYAN)
dim = _wrap(DIM)
