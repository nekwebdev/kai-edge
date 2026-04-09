from __future__ import annotations

import time
from collections.abc import Callable


class WakeWordCooldownGate:
    def __init__(
        self,
        *,
        cooldown_ms: int,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._cooldown_seconds = max(0.0, float(cooldown_ms) / 1000.0)
        self._monotonic = monotonic
        self._cooldown_until = 0.0

    def mark_detected(self) -> None:
        if self._cooldown_seconds <= 0:
            return
        self._cooldown_until = self._monotonic() + self._cooldown_seconds

    def remaining_seconds(self) -> float:
        remaining = self._cooldown_until - self._monotonic()
        if remaining <= 0:
            return 0.0
        return remaining


class SpeechStartDeadline:
    def __init__(
        self,
        *,
        timeout_ms: int,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._timeout_seconds = max(0.0, float(timeout_ms) / 1000.0)
        self._monotonic = monotonic
        self._started_at = self._monotonic()

    def expired(self) -> bool:
        if self._timeout_seconds <= 0:
            return False
        return (self._monotonic() - self._started_at) >= self._timeout_seconds
