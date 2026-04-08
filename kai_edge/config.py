from __future__ import annotations

import ast
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from .errors import EdgeConfigError

DEFAULT_ENV_FILE = "/etc/kai/edge.env"
DEFAULT_RECORD_SECONDS = 5
DEFAULT_SAMPLE_RATE = 16000
DEFAULT_TIMEOUT_SECONDS = 60
DEFAULT_TRIGGER_SOCKET_PATH = "/run/kai-edge/trigger.sock"


@dataclass(frozen=True)
class EdgeConfig:
    backend_url: str
    record_seconds: int
    sample_rate: int
    timeout_seconds: int
    record_device: str | None
    playback_device: str | None
    trigger_socket_path: str


def positive_int(raw_value: str, setting_name: str) -> int:
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise EdgeConfigError(f"{setting_name} must be an integer: {raw_value!r}") from exc

    if value <= 0:
        raise EdgeConfigError(f"{setting_name} must be greater than zero: {raw_value!r}")

    return value


def optional_string(value: str) -> str | None:
    stripped = value.strip()
    return stripped or None


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
            raise EdgeConfigError(f"invalid line {line_number} in {path}: {raw_line!r}")

        key, raw_value = line.split("=", 1)
        key = key.strip()
        raw_value = raw_value.strip()

        if not key:
            raise EdgeConfigError(f"invalid key on line {line_number} in {path}")

        if not raw_value:
            values[key] = ""
            continue

        if raw_value[0] in ("'", '"') and raw_value[-1] == raw_value[0]:
            try:
                parsed_value = ast.literal_eval(raw_value)
            except (SyntaxError, ValueError) as exc:
                raise EdgeConfigError(
                    f"invalid quoted value for {key} on line {line_number} in {path}"
                ) from exc
            values[key] = str(parsed_value)
            continue

        values[key] = raw_value

    return values


def _get_setting(
    name: str,
    file_settings: Mapping[str, str],
    defaults: Mapping[str, str],
    overrides: Mapping[str, str] | None = None,
) -> str:
    if overrides and name in overrides:
        return overrides[name]
    if name in os.environ:
        return os.environ[name]
    if name in file_settings:
        return file_settings[name]
    return defaults[name]


def build_edge_config(
    *,
    file_settings: Mapping[str, str],
    overrides: Mapping[str, str] | None = None,
) -> EdgeConfig:
    defaults = {
        "KAI_CORE_BASE_URL": "",
        "KAI_RECORD_SECONDS": str(DEFAULT_RECORD_SECONDS),
        "KAI_AUDIO_SAMPLE_RATE": str(DEFAULT_SAMPLE_RATE),
        "KAI_HTTP_TIMEOUT_SECONDS": str(DEFAULT_TIMEOUT_SECONDS),
        "KAI_RECORD_DEVICE": "",
        "KAI_PLAYBACK_DEVICE": "",
        "KAI_TRIGGER_SOCKET_PATH": DEFAULT_TRIGGER_SOCKET_PATH,
    }

    backend_url = _get_setting("KAI_CORE_BASE_URL", file_settings, defaults, overrides).strip()
    record_seconds = positive_int(
        _get_setting("KAI_RECORD_SECONDS", file_settings, defaults, overrides),
        "KAI_RECORD_SECONDS",
    )
    sample_rate = positive_int(
        _get_setting("KAI_AUDIO_SAMPLE_RATE", file_settings, defaults, overrides),
        "KAI_AUDIO_SAMPLE_RATE",
    )
    timeout_seconds = positive_int(
        _get_setting("KAI_HTTP_TIMEOUT_SECONDS", file_settings, defaults, overrides),
        "KAI_HTTP_TIMEOUT_SECONDS",
    )
    record_device = optional_string(
        _get_setting("KAI_RECORD_DEVICE", file_settings, defaults, overrides)
    )
    playback_device = optional_string(
        _get_setting("KAI_PLAYBACK_DEVICE", file_settings, defaults, overrides)
    )
    trigger_socket_path = _get_setting(
        "KAI_TRIGGER_SOCKET_PATH", file_settings, defaults, overrides
    ).strip()
    if not trigger_socket_path:
        trigger_socket_path = DEFAULT_TRIGGER_SOCKET_PATH

    return EdgeConfig(
        backend_url=backend_url,
        record_seconds=record_seconds,
        sample_rate=sample_rate,
        timeout_seconds=timeout_seconds,
        record_device=record_device,
        playback_device=playback_device,
        trigger_socket_path=trigger_socket_path,
    )


def load_edge_config(
    env_file: str,
    *,
    overrides: Mapping[str, str] | None = None,
) -> EdgeConfig:
    file_settings = load_env_file(env_file)
    return build_edge_config(file_settings=file_settings, overrides=overrides)
