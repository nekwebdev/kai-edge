from __future__ import annotations

import logging
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from kai_edge.config import build_edge_config
from kai_edge.core_client import CoreAudio, CoreResponse
from kai_edge.errors import EdgeRuntimeError
from kai_edge.interaction import process_recorded_audio


class InteractionTests(unittest.TestCase):
    def _build_config(self, *, extra_settings: dict[str, str] | None = None):
        settings = {
            "KAI_CORE_BASE_URL": "http://kai-core.local:8000",
        }
        if extra_settings:
            settings.update(extra_settings)
        return build_edge_config(file_settings=settings)

    def test_process_recorded_audio_uses_streaming_path_when_enabled(self) -> None:
        config = self._build_config(extra_settings={"KAI_AUDIO_STREAM_ENABLED": "1"})
        logger = logging.getLogger("test-interaction-stream")
        before_speak = mock.Mock()

        with tempfile.TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            recorded_audio_path = temp_dir / "recorded.wav"
            recorded_audio_path.write_bytes(b"wav")

            player = mock.Mock()
            player.close.return_value = True

            def fake_stream(**kwargs):
                kwargs["on_audio_chunk"](CoreAudio(mime_type="audio/wav", data=b"chunk"))
                return ("hello", "hi", 1)

            with (
                mock.patch("kai_edge.interaction.StreamingAudioPlayer", return_value=player),
                mock.patch("kai_edge.interaction.send_request_audio_streaming", side_effect=fake_stream),
                mock.patch("kai_edge.interaction.send_request_audio") as send_audio_mock,
            ):
                result = process_recorded_audio(
                    config=config,
                    recorded_audio_path=recorded_audio_path,
                    temp_dir=temp_dir,
                    logger=logger,
                    on_before_speak=before_speak,
                )

        self.assertEqual(result.text, "hello")
        self.assertEqual(result.response, "hi")
        self.assertTrue(result.audio_played)
        before_speak.assert_called_once_with()
        player.write_chunk.assert_called_once_with(mime_type="audio/wav", chunk=b"chunk")
        player.close.assert_called_once_with()
        player.abort.assert_not_called()
        send_audio_mock.assert_not_called()

    def test_process_recorded_audio_streaming_falls_back_to_non_stream(self) -> None:
        config = self._build_config(
            extra_settings={
                "KAI_AUDIO_STREAM_ENABLED": "1",
                "KAI_AUDIO_STREAM_FALLBACK_TO_NON_STREAM": "1",
            }
        )
        logger = logging.getLogger("test-interaction-fallback")

        with tempfile.TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            recorded_audio_path = temp_dir / "recorded.wav"
            recorded_audio_path.write_bytes(b"wav")

            player = mock.Mock()
            fallback_response = CoreResponse(
                text="fallback text",
                response="fallback reply",
                audio=None,
            )

            with (
                mock.patch("kai_edge.interaction.StreamingAudioPlayer", return_value=player),
                mock.patch(
                    "kai_edge.interaction.send_request_audio_streaming",
                    side_effect=EdgeRuntimeError("stream unavailable"),
                ),
                mock.patch(
                    "kai_edge.interaction.send_request_audio",
                    return_value=fallback_response,
                ) as send_audio_mock,
                mock.patch(
                    "kai_edge.interaction.speak_response_audio",
                    return_value=False,
                ) as speak_mock,
            ):
                result = process_recorded_audio(
                    config=config,
                    recorded_audio_path=recorded_audio_path,
                    temp_dir=temp_dir,
                    logger=logger,
                )

        self.assertEqual(result.text, "fallback text")
        self.assertEqual(result.response, "fallback reply")
        self.assertFalse(result.audio_played)
        player.abort.assert_called_once_with()
        send_audio_mock.assert_called_once_with(
            config=config,
            recorded_audio_path=recorded_audio_path,
            logger=logger,
        )
        speak_mock.assert_called_once()

    def test_process_recorded_audio_streaming_no_fallback_when_disabled(self) -> None:
        config = self._build_config(
            extra_settings={
                "KAI_AUDIO_STREAM_ENABLED": "1",
                "KAI_AUDIO_STREAM_FALLBACK_TO_NON_STREAM": "0",
            }
        )
        logger = logging.getLogger("test-interaction-no-fallback")

        with tempfile.TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            recorded_audio_path = temp_dir / "recorded.wav"
            recorded_audio_path.write_bytes(b"wav")

            player = mock.Mock()
            with (
                mock.patch("kai_edge.interaction.StreamingAudioPlayer", return_value=player),
                mock.patch(
                    "kai_edge.interaction.send_request_audio_streaming",
                    side_effect=EdgeRuntimeError("stream unavailable"),
                ),
                mock.patch("kai_edge.interaction.send_request_audio") as send_audio_mock,
            ):
                with self.assertRaises(EdgeRuntimeError):
                    process_recorded_audio(
                        config=config,
                        recorded_audio_path=recorded_audio_path,
                        temp_dir=temp_dir,
                        logger=logger,
                    )

        player.abort.assert_called_once_with()
        send_audio_mock.assert_not_called()

    def test_process_recorded_audio_streaming_no_fallback_after_audio_started(self) -> None:
        config = self._build_config(
            extra_settings={
                "KAI_AUDIO_STREAM_ENABLED": "1",
                "KAI_AUDIO_STREAM_FALLBACK_TO_NON_STREAM": "1",
            }
        )
        logger = logging.getLogger("test-interaction-no-fallback-after-audio")

        with tempfile.TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            recorded_audio_path = temp_dir / "recorded.wav"
            recorded_audio_path.write_bytes(b"wav")

            player = mock.Mock()

            def stream_then_fail(**kwargs):
                kwargs["on_audio_chunk"](CoreAudio(mime_type="audio/wav", data=b"chunk"))
                raise EdgeRuntimeError("stream failed mid-response")

            with (
                mock.patch("kai_edge.interaction.StreamingAudioPlayer", return_value=player),
                mock.patch(
                    "kai_edge.interaction.send_request_audio_streaming",
                    side_effect=stream_then_fail,
                ),
                mock.patch("kai_edge.interaction.send_request_audio") as send_audio_mock,
            ):
                with self.assertRaises(EdgeRuntimeError):
                    process_recorded_audio(
                        config=config,
                        recorded_audio_path=recorded_audio_path,
                        temp_dir=temp_dir,
                        logger=logger,
                    )

        player.abort.assert_called_once_with()
        send_audio_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
