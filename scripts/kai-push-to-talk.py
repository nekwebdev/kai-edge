#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import base64
import binascii
import json
import os
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

DEFAULT_ENV_FILE = "/etc/kai/edge.env"
DEFAULT_RECORD_SECONDS = 5
DEFAULT_SAMPLE_RATE = 16000
DEFAULT_TIMEOUT_SECONDS = 60


def timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log(message: str) -> None:
    print(f"[{timestamp()}] {message}")


def error(message: str) -> None:
    print(f"[{timestamp()}] error: {message}", file=sys.stderr)


def positive_int(raw_value: str, setting_name: str) -> int:
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise RuntimeError(f"{setting_name} must be an integer: {raw_value!r}") from exc

    if value <= 0:
        raise RuntimeError(f"{setting_name} must be greater than zero: {raw_value!r}")

    return value


def load_env_file(path: str) -> dict[str, str]:
    env_path = Path(path)
    values: dict[str, str] = {}

    if not env_path.exists():
        return values

    for line_number, raw_line in enumerate(env_path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        if "=" not in line:
            raise RuntimeError(f"invalid line {line_number} in {path}: {raw_line!r}")

        key, raw_value = line.split("=", 1)
        key = key.strip()
        raw_value = raw_value.strip()

        if not key:
            raise RuntimeError(f"invalid key on line {line_number} in {path}")

        if not raw_value:
            values[key] = ""
            continue

        if raw_value[0] in ("'", '"') and raw_value[-1] == raw_value[0]:
            try:
                parsed_value = ast.literal_eval(raw_value)
            except (SyntaxError, ValueError) as exc:
                raise RuntimeError(
                    f"invalid quoted value for {key} on line {line_number} in {path}"
                ) from exc
            values[key] = str(parsed_value)
            continue

        values[key] = raw_value

    return values


def get_setting(name: str, file_settings: dict[str, str], default: str) -> str:
    return os.environ.get(name, file_settings.get(name, default))


def optional_string(value: str) -> str | None:
    stripped = value.strip()
    return stripped or None


def run_command(command: list[str], action: str) -> None:
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(f"{action} failed because {command[0]!r} is not installed") from exc

    if completed.returncode == 0:
        return

    details = completed.stderr.strip() or completed.stdout.strip() or "no command output"
    raise RuntimeError(f"{action} failed with exit code {completed.returncode}: {details}")


def record_audio(
    output_path: Path,
    duration_seconds: int,
    sample_rate: int,
    record_device: str | None,
) -> None:
    command = [
        "arecord",
        "-q",
        "-d",
        str(duration_seconds),
        "-f",
        "S16_LE",
        "-r",
        str(sample_rate),
        "-c",
        "1",
    ]
    if record_device:
        command.extend(["-D", record_device])
    command.append(str(output_path))

    device_label = record_device or "default capture device"
    log(f"recording {duration_seconds}s from {device_label} to {output_path}")
    run_command(command, "microphone recording")

    if not output_path.exists() or output_path.stat().st_size == 0:
        raise RuntimeError(f"microphone recording produced an empty file: {output_path}")


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


def send_audio(audio_path: Path, backend_url: str, timeout_seconds: int) -> dict[str, Any]:
    endpoint = f"{backend_url.rstrip('/')}/audio"
    body, boundary = build_multipart_body(audio_path)
    headers = {
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "Content-Length": str(len(body)),
    }
    request = urllib.request.Request(endpoint, data=body, headers=headers, method="POST")

    log(f"sending recorded audio to {endpoint}")
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            status_code = getattr(response, "status", response.getcode())
            payload = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace").strip()
        if detail:
            raise RuntimeError(f"backend returned HTTP {exc.code}: {detail}") from exc
        raise RuntimeError(f"backend returned HTTP {exc.code}: {exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"backend request failed: {exc.reason}") from exc
    except TimeoutError as exc:
        raise RuntimeError(f"backend request timed out after {timeout_seconds}s") from exc

    if status_code != 200:
        raise RuntimeError(f"backend returned unexpected HTTP status {status_code}")

    try:
        response_json = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"backend returned invalid JSON: {exc}") from exc

    if not isinstance(response_json, dict):
        raise RuntimeError("backend response is not a JSON object")

    return response_json


def extract_text_fields(response_json: dict[str, Any]) -> tuple[str, str]:
    text = response_json.get("text")
    response_text = response_json.get("response")

    if not isinstance(text, str):
        raise RuntimeError("backend response is missing a string 'text' field")
    if not isinstance(response_text, str):
        raise RuntimeError("backend response is missing a string 'response' field")

    return text, response_text


def save_response_audio(audio_payload: Any, temp_dir: Path) -> Path | None:
    if audio_payload is None:
        return None

    if not isinstance(audio_payload, dict):
        raise RuntimeError("backend response 'audio' field must be an object or null")

    mime_type = audio_payload.get("mime_type")
    data = audio_payload.get("data")
    if not isinstance(mime_type, str):
        raise RuntimeError("backend audio payload is missing a string 'mime_type' field")
    if not isinstance(data, str):
        raise RuntimeError("backend audio payload is missing a string 'data' field")

    try:
        audio_bytes = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise RuntimeError(f"backend audio payload is not valid base64: {exc}") from exc

    if not audio_bytes:
        raise RuntimeError("backend audio payload is empty")

    suffix = ".wav" if mime_type == "audio/wav" else ".bin"
    output_path = temp_dir / f"kai-response{suffix}"
    output_path.write_bytes(audio_bytes)
    log(f"saved backend audio ({mime_type}) to {output_path}")
    return output_path


def play_audio(audio_path: Path, playback_device: str | None) -> None:
    command = ["aplay", "-q"]
    if playback_device:
        command.extend(["-D", playback_device])
    command.append(str(audio_path))

    device_label = playback_device or "default playback device"
    log(f"playing backend audio through {device_label}")
    run_command(command, "audio playback")


def build_parser(file_settings: dict[str, str]) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Record one utterance, POST it to kai-core, and play the audio reply when present.",
    )
    parser.add_argument(
        "--env-file",
        default=os.environ.get("KAI_EDGE_ENV_FILE", DEFAULT_ENV_FILE),
        help=f"environment file to load before applying shell env overrides (default: {DEFAULT_ENV_FILE})",
    )
    parser.add_argument(
        "--backend-url",
        default=get_setting("KAI_CORE_BASE_URL", file_settings, ""),
        help="kai-core base URL; defaults to KAI_CORE_BASE_URL",
    )
    parser.add_argument(
        "--record-seconds",
        type=int,
        default=positive_int(
            get_setting("KAI_RECORD_SECONDS", file_settings, str(DEFAULT_RECORD_SECONDS)),
            "KAI_RECORD_SECONDS",
        ),
        help=f"recording duration in seconds (default: KAI_RECORD_SECONDS or {DEFAULT_RECORD_SECONDS})",
    )
    parser.add_argument(
        "--sample-rate",
        type=int,
        default=positive_int(
            get_setting("KAI_AUDIO_SAMPLE_RATE", file_settings, str(DEFAULT_SAMPLE_RATE)),
            "KAI_AUDIO_SAMPLE_RATE",
        ),
        help=f"WAV sample rate in Hz (default: KAI_AUDIO_SAMPLE_RATE or {DEFAULT_SAMPLE_RATE})",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=positive_int(
            get_setting("KAI_HTTP_TIMEOUT_SECONDS", file_settings, str(DEFAULT_TIMEOUT_SECONDS)),
            "KAI_HTTP_TIMEOUT_SECONDS",
        ),
        help=f"HTTP timeout in seconds (default: KAI_HTTP_TIMEOUT_SECONDS or {DEFAULT_TIMEOUT_SECONDS})",
    )
    parser.add_argument(
        "--record-device",
        default=optional_string(get_setting("KAI_RECORD_DEVICE", file_settings, "")),
        help="optional ALSA capture device passed to arecord -D",
    )
    parser.add_argument(
        "--playback-device",
        default=optional_string(get_setting("KAI_PLAYBACK_DEVICE", file_settings, "")),
        help="optional ALSA playback device passed to aplay -D",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(argv or sys.argv[1:])

    pre_parser = argparse.ArgumentParser(add_help=False)
    pre_parser.add_argument(
        "--env-file",
        default=os.environ.get("KAI_EDGE_ENV_FILE", DEFAULT_ENV_FILE),
    )
    pre_args, _ = pre_parser.parse_known_args(argv)

    try:
        file_settings = load_env_file(pre_args.env_file)
    except RuntimeError as exc:
        error(str(exc))
        return 1

    parser = build_parser(file_settings)

    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code)

    try:
        args.record_seconds = positive_int(str(args.record_seconds), "--record-seconds")
        args.sample_rate = positive_int(str(args.sample_rate), "--sample-rate")
        args.timeout_seconds = positive_int(str(args.timeout_seconds), "--timeout-seconds")
    except RuntimeError as exc:
        error(str(exc))
        return 1

    args.backend_url = args.backend_url.strip()
    if not args.backend_url:
        error(
            "KAI_CORE_BASE_URL is not configured. Set it in "
            f"{args.env_file} or pass --backend-url."
        )
        return 1

    try:
        with tempfile.TemporaryDirectory(prefix="kai-push-to-talk-") as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            recorded_audio_path = temp_dir / "recorded.wav"

            record_audio(
                output_path=recorded_audio_path,
                duration_seconds=args.record_seconds,
                sample_rate=args.sample_rate,
                record_device=args.record_device,
            )
            response_json = send_audio(
                audio_path=recorded_audio_path,
                backend_url=args.backend_url,
                timeout_seconds=args.timeout_seconds,
            )

            transcribed_text, assistant_response = extract_text_fields(response_json)
            log(f"transcribed text: {transcribed_text}")
            log(f"assistant response: {assistant_response}")

            response_audio_path = save_response_audio(response_json.get("audio"), temp_dir)
            if response_audio_path is None:
                log("backend returned no audio payload")
            else:
                play_audio(response_audio_path, args.playback_device)

    except RuntimeError as exc:
        error(str(exc))
        return 1
    except KeyboardInterrupt:
        error("interrupted")
        return 130

    log("completed one-shot request")
    return 0


if __name__ == "__main__":
    sys.exit(main())
