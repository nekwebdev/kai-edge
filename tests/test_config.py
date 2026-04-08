from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

from kai_edge.config import (
    DEFAULT_TRIGGER_MODE,
    DEFAULT_TRIGGER_SOCKET_PATH,
    build_edge_config,
    load_env_file,
)
from kai_edge.errors import EdgeConfigError


class ConfigTests(unittest.TestCase):
    def test_load_env_file_supports_quotes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir_name:
            env_path = Path(temp_dir_name) / "edge.env"
            env_path.write_text(
                textwrap.dedent(
                    """
                    # comment
                    KAI_CORE_BASE_URL="http://kai-core.local:8000"
                    KAI_RECORD_SECONDS='6'
                    KAI_RECORD_DEVICE=plughw:CARD=Mic,DEV=0
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            values = load_env_file(str(env_path))

        self.assertEqual(values["KAI_CORE_BASE_URL"], "http://kai-core.local:8000")
        self.assertEqual(values["KAI_RECORD_SECONDS"], "6")
        self.assertEqual(values["KAI_RECORD_DEVICE"], "plughw:CARD=Mic,DEV=0")

    def test_build_edge_config_prefers_overrides_over_env_and_file(self) -> None:
        file_settings = {
            "KAI_CORE_BASE_URL": "http://file.example",
            "KAI_RECORD_SECONDS": "5",
        }
        with mock.patch.dict("os.environ", {"KAI_CORE_BASE_URL": "http://env.example"}, clear=False):
            config = build_edge_config(
                file_settings=file_settings,
                overrides={
                    "KAI_CORE_BASE_URL": "http://override.example",
                    "KAI_RECORD_SECONDS": "8",
                },
            )

        self.assertEqual(config.backend_url, "http://override.example")
        self.assertEqual(config.record_seconds, 8)

    def test_build_edge_config_rejects_non_positive_values(self) -> None:
        with self.assertRaises(EdgeConfigError):
            build_edge_config(file_settings={"KAI_RECORD_SECONDS": "0"})

    def test_build_edge_config_uses_default_trigger_socket_when_blank(self) -> None:
        config = build_edge_config(file_settings={"KAI_TRIGGER_SOCKET_PATH": "   "})
        self.assertEqual(config.trigger_socket_path, DEFAULT_TRIGGER_SOCKET_PATH)

    def test_build_edge_config_defaults_to_manual_trigger_mode(self) -> None:
        config = build_edge_config(file_settings={})
        self.assertEqual(config.trigger_mode, DEFAULT_TRIGGER_MODE)

    def test_build_edge_config_accepts_vad_trigger_mode(self) -> None:
        config = build_edge_config(file_settings={"KAI_TRIGGER_MODE": "vad"})
        self.assertEqual(config.trigger_mode, "vad")

    def test_build_edge_config_rejects_invalid_trigger_mode(self) -> None:
        with self.assertRaises(EdgeConfigError):
            build_edge_config(file_settings={"KAI_TRIGGER_MODE": "voice"})

    def test_build_edge_config_rejects_invalid_vad_frame_size(self) -> None:
        with self.assertRaises(EdgeConfigError):
            build_edge_config(file_settings={"KAI_VAD_FRAME_MS": "25"})

    def test_build_edge_config_requires_vad_max_utterance_greater_than_min_speech(self) -> None:
        with self.assertRaises(EdgeConfigError):
            build_edge_config(
                file_settings={
                    "KAI_VAD_MIN_SPEECH_MS": "1000",
                    "KAI_VAD_MAX_UTTERANCE_MS": "1000",
                }
            )

    def test_build_edge_config_requires_vad_max_utterance_greater_than_min_speech_run(self) -> None:
        with self.assertRaises(EdgeConfigError):
            build_edge_config(
                file_settings={
                    "KAI_VAD_MIN_SPEECH_RUN_MS": "1000",
                    "KAI_VAD_MAX_UTTERANCE_MS": "1000",
                }
            )


if __name__ == "__main__":
    unittest.main()
