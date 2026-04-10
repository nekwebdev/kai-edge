from __future__ import annotations

import logging
import unittest
from unittest import mock

from kai_edge.config import build_edge_config
from kai_edge.daemon import EdgeDaemon
from kai_edge.errors import EdgeRuntimeError


class EdgeDaemonTests(unittest.TestCase):
    def _make_daemon(
        self,
        *,
        trigger_mode: str = "manual",
        wakeword_backend: str | None = None,
    ) -> EdgeDaemon:
        settings = {
            "KAI_TRIGGER_MODE": trigger_mode,
            "KAI_OBS_STATUS_FILE_ENABLED": "0",
        }
        if wakeword_backend is not None:
            settings["KAI_WAKEWORD_BACKEND"] = wakeword_backend
        if trigger_mode == "wakeword":
            selected_backend = settings.get("KAI_WAKEWORD_BACKEND", "porcupine")
            settings["KAI_WAKEWORD_BACKEND"] = selected_backend
            if selected_backend == "porcupine":
                settings["KAI_WAKEWORD_ACCESS_KEY"] = "test-access-key"
        config = build_edge_config(file_settings=settings)
        logger = logging.getLogger("test-daemon")
        return EdgeDaemon(config=config, logger=logger)

    def test_serve_forever_uses_manual_mode_loop(self) -> None:
        daemon = self._make_daemon(trigger_mode="manual")
        with (
            mock.patch("signal.signal"),
            mock.patch.object(daemon, "_serve_manual_mode", return_value=7) as manual_loop,
            mock.patch.object(daemon, "_serve_vad_mode", return_value=9) as vad_loop,
            mock.patch.object(daemon, "_serve_wakeword_mode", return_value=11) as wakeword_loop,
        ):
            result = daemon.serve_forever()

        self.assertEqual(result, 7)
        manual_loop.assert_called_once_with()
        vad_loop.assert_not_called()
        wakeword_loop.assert_not_called()

    def test_serve_forever_uses_vad_mode_loop(self) -> None:
        daemon = self._make_daemon(trigger_mode="vad")
        with (
            mock.patch("signal.signal"),
            mock.patch.object(daemon, "_serve_manual_mode", return_value=7) as manual_loop,
            mock.patch.object(daemon, "_serve_vad_mode", return_value=9) as vad_loop,
            mock.patch.object(daemon, "_serve_wakeword_mode", return_value=11) as wakeword_loop,
        ):
            result = daemon.serve_forever()

        self.assertEqual(result, 9)
        manual_loop.assert_not_called()
        vad_loop.assert_called_once_with()
        wakeword_loop.assert_not_called()

    def test_serve_forever_uses_wakeword_mode_loop(self) -> None:
        daemon = self._make_daemon(trigger_mode="wakeword")
        with (
            mock.patch("signal.signal"),
            mock.patch.object(daemon, "_serve_manual_mode", return_value=7) as manual_loop,
            mock.patch.object(daemon, "_serve_vad_mode", return_value=9) as vad_loop,
            mock.patch.object(daemon, "_serve_wakeword_mode", return_value=11) as wakeword_loop,
        ):
            result = daemon.serve_forever()

        self.assertEqual(result, 11)
        manual_loop.assert_not_called()
        vad_loop.assert_not_called()
        wakeword_loop.assert_called_once_with()

    def test_serve_forever_uses_wakeword_mode_loop_for_openwakeword_backend(self) -> None:
        daemon = self._make_daemon(
            trigger_mode="wakeword",
            wakeword_backend="openwakeword",
        )
        with (
            mock.patch("signal.signal"),
            mock.patch.object(daemon, "_serve_manual_mode", return_value=7) as manual_loop,
            mock.patch.object(daemon, "_serve_vad_mode", return_value=9) as vad_loop,
            mock.patch.object(daemon, "_serve_wakeword_mode", return_value=11) as wakeword_loop,
        ):
            result = daemon.serve_forever()

        self.assertEqual(result, 11)
        manual_loop.assert_not_called()
        vad_loop.assert_not_called()
        wakeword_loop.assert_called_once_with()

    def test_frame_bytes_for_vad_requires_whole_samples(self) -> None:
        daemon = EdgeDaemon(
            config=build_edge_config(
                file_settings={
                    "KAI_AUDIO_SAMPLE_RATE": "11025",
                    "KAI_VAD_FRAME_MS": "30",
                    "KAI_OBS_STATUS_FILE_ENABLED": "0",
                }
            ),
            logger=logging.getLogger("test-daemon"),
        )

        with self.assertRaises(EdgeRuntimeError):
            daemon._frame_bytes_for_vad()


if __name__ == "__main__":
    unittest.main()
