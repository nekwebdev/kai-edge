from __future__ import annotations

import base64
import binascii
import json
import logging
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import EdgeRuntimeError


@dataclass(frozen=True)
class CoreAudio:
    mime_type: str
    data: bytes


@dataclass(frozen=True)
class CoreResponse:
    text: str
    response: str
    audio: CoreAudio | None


def build_multipart_body(audio_path: Path) -> tuple[bytes, str]:
    boundary = f"kai-edge-{uuid.uuid4().hex}"
    header = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{audio_path.name}"\r\n'
        "Content-Type: audio/wav\r\n"
        "\r\n"
    ).encode("utf-8")
    footer = f"\r\n--{boundary}--\r\n".encode("utf-8")
    body = header + audio_path.read_bytes() + footer
    return body, boundary


def parse_audio_payload(audio_payload: Any) -> CoreAudio | None:
    if audio_payload is None:
        return None

    mime_type = "audio/wav"
    data: str

    if isinstance(audio_payload, str):
        data = audio_payload
    elif isinstance(audio_payload, dict):
        raw_mime_type = audio_payload.get("mime_type")
        raw_data = audio_payload.get("data")

        if raw_mime_type is not None:
            if not isinstance(raw_mime_type, str):
                raise EdgeRuntimeError("backend audio payload has a non-string 'mime_type' field")
            mime_type = raw_mime_type

        if not isinstance(raw_data, str):
            raise EdgeRuntimeError("backend audio payload is missing a string 'data' field")
        data = raw_data
    else:
        raise EdgeRuntimeError("backend response 'audio' field must be null, string, or object")

    try:
        audio_bytes = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise EdgeRuntimeError(f"backend audio payload is not valid base64: {exc}") from exc

    if not audio_bytes:
        raise EdgeRuntimeError("backend audio payload is empty")

    return CoreAudio(mime_type=mime_type, data=audio_bytes)


def parse_response_json(response_json: Any) -> CoreResponse:
    if not isinstance(response_json, dict):
        raise EdgeRuntimeError("backend response is not a JSON object")

    text = response_json.get("text")
    response_text = response_json.get("response")

    if not isinstance(text, str):
        raise EdgeRuntimeError("backend response is missing a string 'text' field")
    if not isinstance(response_text, str):
        raise EdgeRuntimeError("backend response is missing a string 'response' field")

    audio = parse_audio_payload(response_json.get("audio"))

    return CoreResponse(text=text, response=response_text, audio=audio)


def send_audio(*, audio_path: Path, backend_url: str, timeout_seconds: int, logger: logging.Logger) -> CoreResponse:
    endpoint = f"{backend_url.rstrip('/')}/audio"
    body, boundary = build_multipart_body(audio_path)
    headers = {
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "Content-Length": str(len(body)),
    }
    request = urllib.request.Request(endpoint, data=body, headers=headers, method="POST")

    logger.info("sending recorded audio to %s", endpoint)
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            status_code = getattr(response, "status", response.getcode())
            payload = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace").strip()
        if detail:
            raise EdgeRuntimeError(f"backend returned HTTP {exc.code}: {detail}") from exc
        raise EdgeRuntimeError(f"backend returned HTTP {exc.code}: {exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise EdgeRuntimeError(f"backend request failed: {exc.reason}") from exc
    except TimeoutError as exc:
        raise EdgeRuntimeError(f"backend request timed out after {timeout_seconds}s") from exc

    if status_code != 200:
        raise EdgeRuntimeError(f"backend returned unexpected HTTP status {status_code}")

    try:
        response_json = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise EdgeRuntimeError(f"backend returned invalid JSON: {exc}") from exc

    return parse_response_json(response_json)
