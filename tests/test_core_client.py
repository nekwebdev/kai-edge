from __future__ import annotations

import base64
import unittest

from kai_edge.core_client import parse_response_json
from kai_edge.errors import EdgeRuntimeError


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


if __name__ == "__main__":
    unittest.main()
