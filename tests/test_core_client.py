from __future__ import annotations

import base64
import logging
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from kai_edge.core_client import parse_response_json, send_audio_stream
from kai_edge.errors import EdgeRuntimeError


class _FakeStreamingResponse:
    def __init__(self, lines: list[bytes], status: int = 200) -> None:
        self._lines = lines
        self.status = status

    def __enter__(self) -> "_FakeStreamingResponse":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        del exc_type, exc, tb

    def __iter__(self):
        return iter(self._lines)

    def getcode(self) -> int:
        return self.status


class CoreClientTests(unittest.TestCase):
    def test_parse_response_json_with_string_audio(self) -> None:
        wav_bytes = b"RIFF\x00\x00\x00\x00WAVE"
        encoded = base64.b64encode(wav_bytes).decode("ascii")

        parsed = parse_response_json(
            {
                "text": "hello",
                "response": "hi",
                "audio": encoded,
            }
        )

        self.assertIsNotNone(parsed.audio)
        assert parsed.audio is not None
        self.assertEqual(parsed.audio.mime_type, "audio/wav")
        self.assertEqual(parsed.audio.data, wav_bytes)

    def test_parse_response_json_with_object_audio(self) -> None:
        wav_bytes = b"test-bytes"
        encoded = base64.b64encode(wav_bytes).decode("ascii")

        parsed = parse_response_json(
            {
                "text": "hello",
                "response": "hi",
                "audio": {
                    "mime_type": "audio/wav",
                    "data": encoded,
                },
            }
        )

        self.assertIsNotNone(parsed.audio)
        assert parsed.audio is not None
        self.assertEqual(parsed.audio.mime_type, "audio/wav")
        self.assertEqual(parsed.audio.data, wav_bytes)

    def test_parse_response_json_rejects_invalid_audio_shape(self) -> None:
        with self.assertRaises(EdgeRuntimeError):
            parse_response_json(
                {
                    "text": "hello",
                    "response": "hi",
                    "audio": ["not", "valid"],
                }
            )

    def test_send_audio_stream_parses_events_and_emits_audio_chunks(self) -> None:
        payload = base64.b64encode(b"RIFF-chunk").decode("ascii")
        response = _FakeStreamingResponse(
            lines=[
                b'{"event":"meta","mime_type":"audio/wav"}\n',
                f'{{"event":"audio_chunk","data":"{payload}"}}\n'.encode("utf-8"),
                b'{"event":"done","text":"hello","response":"hi"}\n',
            ]
        )
        chunks: list[tuple[str, bytes]] = []

        with tempfile.TemporaryDirectory() as temp_dir_name:
            audio_path = Path(temp_dir_name) / "request.wav"
            audio_path.write_bytes(b"fake")
            with mock.patch("kai_edge.core_client.urllib.request.urlopen", return_value=response):
                result = send_audio_stream(
                    audio_path=audio_path,
                    backend_url="http://example",
                    timeout_seconds=5,
                    logger=logging.getLogger("test-core-client-stream"),
                    on_audio_chunk=lambda audio: chunks.append((audio.mime_type, audio.data)),
                )

        self.assertEqual(result.text, "hello")
        self.assertEqual(result.response, "hi")
        self.assertEqual(result.audio_chunks, 1)
        self.assertEqual(chunks, [("audio/wav", b"RIFF-chunk")])

    def test_send_audio_stream_accepts_done_result_object(self) -> None:
        response = _FakeStreamingResponse(
            lines=[
                b'{"event":"done","result":{"text":"hello","response":"hi"}}\n',
            ]
        )

        with tempfile.TemporaryDirectory() as temp_dir_name:
            audio_path = Path(temp_dir_name) / "request.wav"
            audio_path.write_bytes(b"fake")
            with mock.patch("kai_edge.core_client.urllib.request.urlopen", return_value=response):
                result = send_audio_stream(
                    audio_path=audio_path,
                    backend_url="http://example",
                    timeout_seconds=5,
                    logger=logging.getLogger("test-core-client-stream-result"),
                    on_audio_chunk=lambda _audio: None,
                )

        self.assertEqual(result.text, "hello")
        self.assertEqual(result.response, "hi")
        self.assertEqual(result.audio_chunks, 0)

    def test_send_audio_stream_raises_on_error_event(self) -> None:
        response = _FakeStreamingResponse(
            lines=[
                b'{"event":"error","detail":"stream failed"}\n',
            ]
        )

        with tempfile.TemporaryDirectory() as temp_dir_name:
            audio_path = Path(temp_dir_name) / "request.wav"
            audio_path.write_bytes(b"fake")
            with mock.patch("kai_edge.core_client.urllib.request.urlopen", return_value=response):
                with self.assertRaises(EdgeRuntimeError):
                    send_audio_stream(
                        audio_path=audio_path,
                        backend_url="http://example",
                        timeout_seconds=5,
                        logger=logging.getLogger("test-core-client-stream-error"),
                        on_audio_chunk=lambda _audio: None,
                    )


if __name__ == "__main__":
    unittest.main()
