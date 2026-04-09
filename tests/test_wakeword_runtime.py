from __future__ import annotations

import unittest

from kai_edge.wakeword_runtime import SpeechStartDeadline, WakeWordCooldownGate


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def monotonic(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class WakeWordRuntimeTests(unittest.TestCase):
    def test_cooldown_gate_tracks_remaining_time(self) -> None:
        clock = FakeClock()
        gate = WakeWordCooldownGate(cooldown_ms=1500, monotonic=clock.monotonic)

        self.assertEqual(gate.remaining_seconds(), 0.0)

        gate.mark_detected()
        self.assertAlmostEqual(gate.remaining_seconds(), 1.5, places=3)

        clock.advance(0.4)
        self.assertAlmostEqual(gate.remaining_seconds(), 1.1, places=3)

        clock.advance(1.2)
        self.assertEqual(gate.remaining_seconds(), 0.0)

    def test_speech_start_deadline_expires_after_timeout(self) -> None:
        clock = FakeClock()
        deadline = SpeechStartDeadline(timeout_ms=800, monotonic=clock.monotonic)

        self.assertFalse(deadline.expired())
        clock.advance(0.79)
        self.assertFalse(deadline.expired())
        clock.advance(0.02)
        self.assertTrue(deadline.expired())

    def test_zero_speech_start_timeout_never_expires(self) -> None:
        clock = FakeClock()
        deadline = SpeechStartDeadline(timeout_ms=0, monotonic=clock.monotonic)

        for _ in range(5):
            clock.advance(10.0)
            self.assertFalse(deadline.expired())


if __name__ == "__main__":
    unittest.main()
