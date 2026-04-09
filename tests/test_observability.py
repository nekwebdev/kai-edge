from __future__ import annotations

import json
import logging
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from kai_edge.config import build_edge_config
from kai_edge.observability import EdgeObservability


class ObservabilityTests(unittest.TestCase):
    def _build_config(self, *, extra_settings: dict[str, str] | None = None):
        settings = {
            "KAI_OBS_STATUS_FILE_ENABLED": "0",
            "KAI_OBS_SUMMARY_INTERVAL_SECONDS": "0",
            "KAI_OBS_SUMMARY_INTERVAL_INTERACTIONS": "0",
        }
        if extra_settings:
            settings.update(extra_settings)
        return build_edge_config(file_settings=settings)

    def test_records_counters_and_last_values(self) -> None:
        config = self._build_config()
        logger = logging.getLogger("test-observability-counters")
        obs = EdgeObservability(config=config, logger=logger, initial_state="idle")

        obs.set_vad_backend("webrtcvad")
        obs.set_wake_backend("porcupine")
        obs.record_state_transition(old_state="idle", new_state="listening")
        obs.record_interaction_started()
        obs.record_accepted_utterance(utterance_ms=1200, stop_reason="trailing_silence")
        obs.record_rejected_utterance(
            reason="speech_too_short",
            stop_reason="trailing_silence",
        )
        obs.record_wake_detection()
        obs.record_wake_post_accepted_utterance()
        obs.record_wake_post_timeout()
        obs.record_wake_retrigger_suppressed()
        obs.record_error(summary="network timeout while sending audio")

        snapshot = obs.snapshot()

        self.assertEqual(snapshot["state"], "listening")
        self.assertEqual(snapshot["vad_backend"], "webrtcvad")
        self.assertEqual(snapshot["wake_backend"], "porcupine")
        self.assertEqual(snapshot["counters"]["interactions"], 1)
        self.assertEqual(snapshot["counters"]["accepted_utterances"], 1)
        self.assertEqual(snapshot["counters"]["rejected_utterances"], 1)
        self.assertEqual(snapshot["counters"]["errors"], 1)
        self.assertEqual(snapshot["counters"]["wake_detections"], 1)
        self.assertEqual(snapshot["counters"]["wake_post_accepted_utterances"], 1)
        self.assertEqual(snapshot["counters"]["wake_post_speech_timeouts"], 1)
        self.assertEqual(snapshot["counters"]["wake_retrigger_suppressions"], 1)
        self.assertEqual(snapshot["counters"]["avg_accepted_utterance_ms"], 1200)
        self.assertEqual(snapshot["counters"]["last_accepted_utterance_ms"], 1200)
        self.assertEqual(snapshot["counters"]["last_rejection_reason"], "speech_too_short")
        self.assertEqual(
            snapshot["counters"]["rejection_reasons"],
            {"speech_too_short": 1},
        )
        self.assertEqual(
            snapshot["counters"]["stop_reasons"],
            {"trailing_silence": 2},
        )

    def test_writes_status_artifact_when_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir_name:
            status_path = Path(temp_dir_name) / "status.json"
            config = self._build_config(
                extra_settings={
                    "KAI_OBS_STATUS_FILE_ENABLED": "1",
                    "KAI_OBS_STATUS_FILE_PATH": str(status_path),
                }
            )
            logger = logging.getLogger("test-observability-status-file")
            obs = EdgeObservability(config=config, logger=logger, initial_state="idle")
            obs.record_interaction_started()

            self.assertTrue(status_path.exists())
            payload = json.loads(status_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["mode"], config.trigger_mode)
            self.assertEqual(payload["state"], "idle")
            self.assertEqual(payload["counters"]["interactions"], 1)

    def test_emits_summary_after_interaction_threshold(self) -> None:
        config = self._build_config(
            extra_settings={
                "KAI_OBS_SUMMARY_INTERVAL_SECONDS": "0",
                "KAI_OBS_SUMMARY_INTERVAL_INTERACTIONS": "2",
            }
        )
        logger = mock.Mock(spec=logging.Logger)
        obs = EdgeObservability(config=config, logger=logger, initial_state="idle")

        obs.record_interaction_started()
        obs.emit_summary_if_due()
        self.assertEqual(logger.info.call_count, 0)

        obs.record_interaction_started()
        obs.emit_summary_if_due(trigger="interaction")

        self.assertGreaterEqual(logger.info.call_count, 1)
        first_message = logger.info.call_args_list[0].args[0]
        self.assertIn("summary trigger=%s", first_message)


if __name__ == "__main__":
    unittest.main()
