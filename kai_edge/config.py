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
DEFAULT_TRIGGER_MODE = "manual"
VALID_TRIGGER_MODES = ("manual", "vad")
DEFAULT_VAD_AGGRESSIVENESS = 3
DEFAULT_VAD_FRAME_MS = 30
DEFAULT_VAD_PRE_ROLL_MS = 250
DEFAULT_VAD_MIN_SPEECH_MS = 1200
DEFAULT_VAD_MIN_SPEECH_RUN_MS = 900
DEFAULT_VAD_TRAILING_SILENCE_MS = 700
DEFAULT_VAD_MAX_UTTERANCE_MS = 10000
DEFAULT_VAD_COOLDOWN_MS = 400
DEFAULT_VAD_ENERGY_THRESHOLD = 260
DEFAULT_OBS_SUMMARY_INTERVAL_SECONDS = 300
DEFAULT_OBS_SUMMARY_INTERVAL_INTERACTIONS = 10
DEFAULT_OBS_STATUS_FILE_ENABLED = True
DEFAULT_OBS_STATUS_FILE_PATH = "/run/kai-edge/status.json"


@dataclass(frozen=True)
class EdgeConfig:
    backend_url: str
    record_seconds: int
    sample_rate: int
    timeout_seconds: int
    record_device: str | None
    playback_device: str | None
    trigger_socket_path: str
    trigger_mode: str
    vad_aggressiveness: int
    vad_frame_ms: int
    vad_pre_roll_ms: int
    vad_min_speech_ms: int
    vad_min_speech_run_ms: int
    vad_trailing_silence_ms: int
    vad_max_utterance_ms: int
    vad_cooldown_ms: int
    vad_energy_threshold: int
    obs_summary_interval_seconds: int
    obs_summary_interval_interactions: int
    obs_status_file_enabled: bool
    obs_status_file_path: str


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


def non_negative_int(raw_value: str, setting_name: str) -> int:
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise EdgeConfigError(f"{setting_name} must be an integer: {raw_value!r}") from exc

    if value < 0:
        raise EdgeConfigError(f"{setting_name} must be zero or greater: {raw_value!r}")

    return value


def parse_trigger_mode(raw_value: str) -> str:
    value = raw_value.strip().lower()
    if value not in VALID_TRIGGER_MODES:
        valid_modes = ", ".join(VALID_TRIGGER_MODES)
        raise EdgeConfigError(f"KAI_TRIGGER_MODE must be one of: {valid_modes}")
    return value


def parse_bool(raw_value: str, setting_name: str) -> bool:
    value = raw_value.strip().lower()
    if value in ("1", "true", "yes", "on"):
        return True
    if value in ("0", "false", "no", "off"):
        return False
    raise EdgeConfigError(
        f"{setting_name} must be one of: 1, 0, true, false, yes, no, on, off"
    )


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
        "KAI_TRIGGER_MODE": DEFAULT_TRIGGER_MODE,
        "KAI_VAD_AGGRESSIVENESS": str(DEFAULT_VAD_AGGRESSIVENESS),
        "KAI_VAD_FRAME_MS": str(DEFAULT_VAD_FRAME_MS),
        "KAI_VAD_PRE_ROLL_MS": str(DEFAULT_VAD_PRE_ROLL_MS),
        "KAI_VAD_MIN_SPEECH_MS": str(DEFAULT_VAD_MIN_SPEECH_MS),
        "KAI_VAD_MIN_SPEECH_RUN_MS": str(DEFAULT_VAD_MIN_SPEECH_RUN_MS),
        "KAI_VAD_TRAILING_SILENCE_MS": str(DEFAULT_VAD_TRAILING_SILENCE_MS),
        "KAI_VAD_MAX_UTTERANCE_MS": str(DEFAULT_VAD_MAX_UTTERANCE_MS),
        "KAI_VAD_COOLDOWN_MS": str(DEFAULT_VAD_COOLDOWN_MS),
        "KAI_VAD_ENERGY_THRESHOLD": str(DEFAULT_VAD_ENERGY_THRESHOLD),
        "KAI_OBS_SUMMARY_INTERVAL_SECONDS": str(DEFAULT_OBS_SUMMARY_INTERVAL_SECONDS),
        "KAI_OBS_SUMMARY_INTERVAL_INTERACTIONS": str(DEFAULT_OBS_SUMMARY_INTERVAL_INTERACTIONS),
        "KAI_OBS_STATUS_FILE_ENABLED": "1" if DEFAULT_OBS_STATUS_FILE_ENABLED else "0",
        "KAI_OBS_STATUS_FILE_PATH": DEFAULT_OBS_STATUS_FILE_PATH,
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
    trigger_mode = parse_trigger_mode(
        _get_setting("KAI_TRIGGER_MODE", file_settings, defaults, overrides)
    )
    vad_aggressiveness = non_negative_int(
        _get_setting("KAI_VAD_AGGRESSIVENESS", file_settings, defaults, overrides),
        "KAI_VAD_AGGRESSIVENESS",
    )
    if vad_aggressiveness > 3:
        raise EdgeConfigError("KAI_VAD_AGGRESSIVENESS must be between 0 and 3")

    vad_frame_ms = positive_int(
        _get_setting("KAI_VAD_FRAME_MS", file_settings, defaults, overrides),
        "KAI_VAD_FRAME_MS",
    )
    if vad_frame_ms not in (10, 20, 30):
        raise EdgeConfigError("KAI_VAD_FRAME_MS must be one of: 10, 20, 30")

    vad_pre_roll_ms = non_negative_int(
        _get_setting("KAI_VAD_PRE_ROLL_MS", file_settings, defaults, overrides),
        "KAI_VAD_PRE_ROLL_MS",
    )
    vad_min_speech_ms = positive_int(
        _get_setting("KAI_VAD_MIN_SPEECH_MS", file_settings, defaults, overrides),
        "KAI_VAD_MIN_SPEECH_MS",
    )
    vad_min_speech_run_ms = positive_int(
        _get_setting("KAI_VAD_MIN_SPEECH_RUN_MS", file_settings, defaults, overrides),
        "KAI_VAD_MIN_SPEECH_RUN_MS",
    )
    vad_trailing_silence_ms = positive_int(
        _get_setting("KAI_VAD_TRAILING_SILENCE_MS", file_settings, defaults, overrides),
        "KAI_VAD_TRAILING_SILENCE_MS",
    )
    vad_max_utterance_ms = positive_int(
        _get_setting("KAI_VAD_MAX_UTTERANCE_MS", file_settings, defaults, overrides),
        "KAI_VAD_MAX_UTTERANCE_MS",
    )
    vad_cooldown_ms = non_negative_int(
        _get_setting("KAI_VAD_COOLDOWN_MS", file_settings, defaults, overrides),
        "KAI_VAD_COOLDOWN_MS",
    )
    vad_energy_threshold = positive_int(
        _get_setting("KAI_VAD_ENERGY_THRESHOLD", file_settings, defaults, overrides),
        "KAI_VAD_ENERGY_THRESHOLD",
    )
    obs_summary_interval_seconds = non_negative_int(
        _get_setting("KAI_OBS_SUMMARY_INTERVAL_SECONDS", file_settings, defaults, overrides),
        "KAI_OBS_SUMMARY_INTERVAL_SECONDS",
    )
    obs_summary_interval_interactions = non_negative_int(
        _get_setting("KAI_OBS_SUMMARY_INTERVAL_INTERACTIONS", file_settings, defaults, overrides),
        "KAI_OBS_SUMMARY_INTERVAL_INTERACTIONS",
    )
    obs_status_file_enabled = parse_bool(
        _get_setting("KAI_OBS_STATUS_FILE_ENABLED", file_settings, defaults, overrides),
        "KAI_OBS_STATUS_FILE_ENABLED",
    )
    obs_status_file_path = _get_setting(
        "KAI_OBS_STATUS_FILE_PATH", file_settings, defaults, overrides
    ).strip()
    if not obs_status_file_path:
        obs_status_file_path = DEFAULT_OBS_STATUS_FILE_PATH
    if not obs_status_file_path.startswith("/"):
        raise EdgeConfigError("KAI_OBS_STATUS_FILE_PATH must be an absolute path")
    if vad_max_utterance_ms <= vad_min_speech_ms:
        raise EdgeConfigError("KAI_VAD_MAX_UTTERANCE_MS must be greater than KAI_VAD_MIN_SPEECH_MS")
    if vad_max_utterance_ms <= vad_min_speech_run_ms:
        raise EdgeConfigError(
            "KAI_VAD_MAX_UTTERANCE_MS must be greater than KAI_VAD_MIN_SPEECH_RUN_MS"
        )

    return EdgeConfig(
        backend_url=backend_url,
        record_seconds=record_seconds,
        sample_rate=sample_rate,
        timeout_seconds=timeout_seconds,
        record_device=record_device,
        playback_device=playback_device,
        trigger_socket_path=trigger_socket_path,
        trigger_mode=trigger_mode,
        vad_aggressiveness=vad_aggressiveness,
        vad_frame_ms=vad_frame_ms,
        vad_pre_roll_ms=vad_pre_roll_ms,
        vad_min_speech_ms=vad_min_speech_ms,
        vad_min_speech_run_ms=vad_min_speech_run_ms,
        vad_trailing_silence_ms=vad_trailing_silence_ms,
        vad_max_utterance_ms=vad_max_utterance_ms,
        vad_cooldown_ms=vad_cooldown_ms,
        vad_energy_threshold=vad_energy_threshold,
        obs_summary_interval_seconds=obs_summary_interval_seconds,
        obs_summary_interval_interactions=obs_summary_interval_interactions,
        obs_status_file_enabled=obs_status_file_enabled,
        obs_status_file_path=obs_status_file_path,
    )


def load_edge_config(
    env_file: str,
    *,
    overrides: Mapping[str, str] | None = None,
) -> EdgeConfig:
    file_settings = load_env_file(env_file)
    return build_edge_config(file_settings=file_settings, overrides=overrides)
