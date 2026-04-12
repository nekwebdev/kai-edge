from __future__ import annotations

import base64
import binascii
import json
import logging
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

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


@dataclass(frozen=True)
class CoreStreamResult:
    text: str
    response: str
    audio_chunks: int


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


def _raise_backend_http_error(exc: urllib.error.HTTPError) -> None:
    detail = exc.read().decode("utf-8", errors="replace").strip()
    if detail:
        raise EdgeRuntimeError(f"backend returned HTTP {exc.code}: {detail}") from exc
    raise EdgeRuntimeError(f"backend returned HTTP {exc.code}: {exc.reason}") from exc


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
        _raise_backend_http_error(exc)
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


def _stream_event_name(event_payload: Mapping[str, Any]) -> str:
    for key in ("event", "type", "kind"):
        raw_value = event_payload.get(key)
        if isinstance(raw_value, str):
            normalized = raw_value.strip().lower()
            if normalized:
                return normalized
    raise EdgeRuntimeError("backend stream event is missing event/type/kind")


def _stream_field(
    event_payload: Mapping[str, Any],
    *keys: str,
) -> str | None:
    for key in keys:
        value = event_payload.get(key)
        if isinstance(value, str):
            return value
    return None


def _collect_text_fields(
    event_payload: Mapping[str, Any],
    *,
    text: str | None,
    response_text: str | None,
) -> tuple[str | None, str | None]:
    next_text = text
    next_response = response_text

    direct_text = _stream_field(event_payload, "text")
    if direct_text is not None:
        next_text = direct_text

    direct_response = _stream_field(event_payload, "response")
    if direct_response is not None:
        next_response = direct_response

    nested_result = event_payload.get("result")
    if isinstance(nested_result, dict):
        nested_text = _stream_field(nested_result, "text")
        if nested_text is not None:
            next_text = nested_text
        nested_response = _stream_field(nested_result, "response")
        if nested_response is not None:
            next_response = nested_response

    return next_text, next_response


def _parse_audio_chunk_payload(
    event_payload: Mapping[str, Any],
    *,
    default_mime_type: str,
) -> CoreAudio:
    audio_payload = event_payload.get("audio")
    if audio_payload is not None:
        parsed_audio = parse_audio_payload(audio_payload)
        if parsed_audio is None:
            raise EdgeRuntimeError("backend stream audio event returned an empty audio payload")
        return parsed_audio

    encoded_chunk = _stream_field(event_payload, "data", "audio_chunk", "chunk")
    if encoded_chunk is None:
        raise EdgeRuntimeError("backend stream audio chunk is missing base64 payload data")

    mime_type = _stream_field(event_payload, "mime_type", "audio_mime_type") or default_mime_type
    try:
        decoded_chunk = base64.b64decode(encoded_chunk, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise EdgeRuntimeError(f"backend stream audio chunk is not valid base64: {exc}") from exc

    if not decoded_chunk:
        raise EdgeRuntimeError("backend stream audio chunk is empty")

    return CoreAudio(mime_type=mime_type, data=decoded_chunk)


def send_audio_stream(
    *,
    audio_path: Path,
    backend_url: str,
    timeout_seconds: int,
    logger: logging.Logger,
    on_audio_chunk: Callable[[CoreAudio], None],
) -> CoreStreamResult:
    endpoint = f"{backend_url.rstrip('/')}/audio/stream"
    body, boundary = build_multipart_body(audio_path)
    headers = {
        "Accept": "application/x-ndjson",
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "Content-Length": str(len(body)),
    }
    request = urllib.request.Request(endpoint, data=body, headers=headers, method="POST")

    logger.info("sending recorded audio to %s", endpoint)
    text: str | None = None
    response_text: str | None = None
    default_mime_type = "audio/wav"
    audio_chunks = 0

    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            status_code = getattr(response, "status", response.getcode())
            if status_code != 200:
                raise EdgeRuntimeError(f"backend returned unexpected HTTP status {status_code}")

            for raw_line in response:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    event_payload = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise EdgeRuntimeError(f"backend stream returned invalid JSON event: {exc}") from exc

                if not isinstance(event_payload, dict):
                    raise EdgeRuntimeError("backend stream event is not a JSON object")

                event_name = _stream_event_name(event_payload)
                text, response_text = _collect_text_fields(
                    event_payload,
                    text=text,
                    response_text=response_text,
                )

                event_mime_type = _stream_field(event_payload, "mime_type", "audio_mime_type")
                if event_mime_type is not None:
                    default_mime_type = event_mime_type

                if event_name == "meta":
                    continue

                if event_name in ("audio_chunk", "audio"):
                    audio = _parse_audio_chunk_payload(
                        event_payload,
                        default_mime_type=default_mime_type,
                    )
                    default_mime_type = audio.mime_type
                    on_audio_chunk(audio)
                    audio_chunks += 1
                    continue

                if event_name == "done":
                    break

                if event_name == "error":
                    detail = _stream_field(event_payload, "detail", "error", "message")
                    if detail:
                        raise EdgeRuntimeError(f"backend stream returned an error event: {detail}")
                    raise EdgeRuntimeError("backend stream returned an error event")

                logger.warning("ignoring unsupported backend stream event: %s", event_name)
    except urllib.error.HTTPError as exc:
        _raise_backend_http_error(exc)
    except urllib.error.URLError as exc:
        raise EdgeRuntimeError(f"backend request failed: {exc.reason}") from exc
    except TimeoutError as exc:
        raise EdgeRuntimeError(f"backend request timed out after {timeout_seconds}s") from exc

    if text is None:
        raise EdgeRuntimeError("backend stream completed without a 'text' field")
    if response_text is None:
        raise EdgeRuntimeError("backend stream completed without a 'response' field")

    return CoreStreamResult(text=text, response=response_text, audio_chunks=audio_chunks)
