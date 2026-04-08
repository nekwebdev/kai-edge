from __future__ import annotations

import logging
import unittest
from unittest import mock

from kai_edge.config import build_edge_config
from kai_edge.daemon import EdgeDaemon
from kai_edge.errors import EdgeRuntimeError


class EdgeDaemonTests(unittest.TestCase):
    def _make_daemon(self, *, trigger_mode: str = "manual") -> EdgeDaemon:
        config = build_edge_config(file_settings={"KAI_TRIGGER_MODE": trigger_mode})
        logger = logging.getLogger("test-daemon")
        return EdgeDaemon(config=config, logger=logger)

    def test_serve_forever_uses_manual_mode_loop(self) -> None:
        daemon = self._make_daemon(trigger_mode="manual")
        with (
            mock.patch("signal.signal"),
            mock.patch.object(daemon, "_serve_manual_mode", return_value=7) as manual_loop,
            mock.patch.object(daemon, "_serve_vad_mode", return_value=9) as vad_loop,
        ):
            result = daemon.serve_forever()

        self.assertEqual(result, 7)
        manual_loop.assert_called_once_with()
        vad_loop.assert_not_called()

    def test_serve_forever_uses_vad_mode_loop(self) -> None:
        daemon = self._make_daemon(trigger_mode="vad")
        with (
            mock.patch("signal.signal"),
            mock.patch.object(daemon, "_serve_manual_mode", return_value=7) as manual_loop,
            mock.patch.object(daemon, "_serve_vad_mode", return_value=9) as vad_loop,
        ):
            result = daemon.serve_forever()

        self.assertEqual(result, 9)
        manual_loop.assert_not_called()
        vad_loop.assert_called_once_with()

    def test_frame_bytes_for_vad_requires_whole_samples(self) -> None:
        daemon = EdgeDaemon(
            config=build_edge_config(
                file_settings={
                    "KAI_AUDIO_SAMPLE_RATE": "11025",
                    "KAI_VAD_FRAME_MS": "30",
                }
            ),
            logger=logging.getLogger("test-daemon"),
        )

        with self.assertRaises(EdgeRuntimeError):
            daemon._frame_bytes_for_vad()


if __name__ == "__main__":
    unittest.main()
