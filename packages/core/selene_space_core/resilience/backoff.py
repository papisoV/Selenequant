"""Delay schedule for retry loops: geometric growth, hard cap, optional jitter."""

from __future__ import annotations

import random


class BackoffPolicy:
    """Immutable settings object; call calculate_delay() per attempt.

    delay(attempt) = initial * factor**attempt, clamped to max_delay.
    With jitter the final value is scaled uniformly into [75%, 125%]."""

    __slots__ = ("initial", "max_delay", "factor", "jitter")

    def __init__(
        self,
        initial: float = 1.0,
        max_delay: float = 60.0,
        factor: float = 2.0,
        jitter: bool = True,
    ) -> None:
        self.initial = initial
        self.max_delay = max_delay
        self.factor = factor
        self.jitter = jitter

    def __repr__(self) -> str:
        return (
            f"BackoffPolicy(initial={self.initial}, max_delay={self.max_delay}, "
            f"factor={self.factor}, jitter={self.jitter})"
        )

    def __eq__(self, other) -> bool:
        if not isinstance(other, BackoffPolicy):
            return NotImplemented
        return (
            self.initial == other.initial
            and self.max_delay == other.max_delay
            and self.factor == other.factor
            and self.jitter == other.jitter
        )

    def calculate_delay(self, attempt: int) -> float:
        steps = attempt if attempt > 0 else 0
        grown = float(self.initial) * (float(self.factor) ** steps)
        if grown > self.max_delay:
            grown = float(self.max_delay)
        if self.jitter:
            spread = random.random() * 0.5 + 0.75
            grown *= spread
        return grown
