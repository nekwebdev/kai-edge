from __future__ import annotations

import unittest

from kai_edge.vad_session import UtteranceCollector, milliseconds_to_frames


class VadSessionTests(unittest.TestCase):
    def test_milliseconds_to_frames_rounds_up(self) -> None:
        self.assertEqual(milliseconds_to_frames(1, 30), 1)
        self.assertEqual(milliseconds_to_frames(30, 30), 1)
        self.assertEqual(milliseconds_to_frames(31, 30), 2)

    def test_collector_accepts_after_trailing_silence(self) -> None:
        collector = UtteranceCollector(
            frame_ms=20,
            pre_roll_ms=40,
            min_speech_ms=60,
            trailing_silence_ms=40,
            max_utterance_ms=300,
        )

        sequence = [
            (b"a", False),
            (b"b", True),
            (b"c", True),
            (b"d", True),
            (b"e", False),
            (b"f", False),
        ]

        decision = None
        for frame, is_speech in sequence:
            _, decision = collector.consume_frame(frame=frame, is_speech=is_speech)
            if decision is not None:
                break

        self.assertIsNotNone(decision)
        assert decision is not None
        self.assertTrue(decision.accepted)
        self.assertEqual(decision.reason, "accepted")
        self.assertEqual(decision.stop_reason, "trailing_silence")
        self.assertEqual(decision.speech_ms, 60)
        self.assertEqual(decision.utterance_ms, 120)
        self.assertEqual(decision.frames, (b"a", b"b", b"c", b"d", b"e", b"f"))

    def test_collector_rejects_short_burst(self) -> None:
        collector = UtteranceCollector(
            frame_ms=20,
            pre_roll_ms=0,
            min_speech_ms=60,
            trailing_silence_ms=40,
            max_utterance_ms=300,
        )

        decision = None
        for frame, is_speech in ((b"a", True), (b"b", False), (b"c", False)):
            _, decision = collector.consume_frame(frame=frame, is_speech=is_speech)
            if decision is not None:
                break

        self.assertIsNotNone(decision)
        assert decision is not None
        self.assertFalse(decision.accepted)
        self.assertEqual(decision.reason, "speech_too_short")
        self.assertEqual(decision.stop_reason, "trailing_silence")
        self.assertEqual(decision.speech_ms, 20)
        self.assertEqual(decision.utterance_ms, 60)

    def test_collector_stops_on_max_duration(self) -> None:
        collector = UtteranceCollector(
            frame_ms=20,
            pre_roll_ms=0,
            min_speech_ms=40,
            trailing_silence_ms=200,
            max_utterance_ms=60,
        )

        decision = None
        for frame, is_speech in ((b"a", True), (b"b", True), (b"c", True)):
            _, decision = collector.consume_frame(frame=frame, is_speech=is_speech)
            if decision is not None:
                break

        self.assertIsNotNone(decision)
        assert decision is not None
        self.assertTrue(decision.accepted)
        self.assertEqual(decision.stop_reason, "max_duration")
        self.assertEqual(decision.speech_ms, 60)
        self.assertEqual(decision.utterance_ms, 60)
        self.assertFalse(collector.is_recording)

    def test_collector_can_start_new_segment_after_finish(self) -> None:
        collector = UtteranceCollector(
            frame_ms=20,
            pre_roll_ms=0,
            min_speech_ms=20,
            trailing_silence_ms=20,
            max_utterance_ms=100,
        )

        _, first = collector.consume_frame(frame=b"a", is_speech=True)
        self.assertIsNone(first)
        _, first = collector.consume_frame(frame=b"b", is_speech=False)
        self.assertIsNotNone(first)
        assert first is not None
        self.assertTrue(first.accepted)

        speech_start, second = collector.consume_frame(frame=b"c", is_speech=True)
        self.assertTrue(speech_start)
        self.assertIsNone(second)


if __name__ == "__main__":
    unittest.main()
