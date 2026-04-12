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
DEFAULT_AUDIO_STREAM_ENABLED = False
DEFAULT_AUDIO_STREAM_FALLBACK_TO_NON_STREAM = True
DEFAULT_TRIGGER_SOCKET_PATH = "/run/kai-edge/trigger.sock"
DEFAULT_TRIGGER_MODE = "manual"
VALID_TRIGGER_MODES = ("manual", "vad", "wakeword")
DEFAULT_WAKEWORD_BACKEND = "openwakeword"
VALID_WAKEWORD_BACKENDS = ("porcupine", "openwakeword")
DEFAULT_WAKEWORD_BUILTIN_KEYWORD = "porcupine"
DEFAULT_WAKEWORD_SENSITIVITY = 0.5
DEFAULT_WAKEWORD_OPENWAKEWORD_THRESHOLD = 0.5
DEFAULT_WAKEWORD_DETECTION_COOLDOWN_MS = 1500
DEFAULT_WAKEWORD_POST_WAKE_SPEECH_TIMEOUT_MS = 3000
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
    audio_stream_enabled: bool
    audio_stream_fallback_to_non_stream: bool
    record_device: str | None
    playback_device: str | None
    trigger_socket_path: str
    trigger_mode: str
    wakeword_backend: str
    wakeword_access_key: str | None
    wakeword_builtin_keyword: str | None
    wakeword_keyword_path: str | None
    wakeword_model_path: str | None
    wakeword_sensitivity: float
    wakeword_openwakeword_model_paths: tuple[str, ...]
    wakeword_openwakeword_threshold: float
    wakeword_detection_cooldown_ms: int
    wakeword_post_wake_speech_timeout_ms: int
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


def parse_wakeword_backend(raw_value: str) -> str:
    value = raw_value.strip().lower()
    if value not in VALID_WAKEWORD_BACKENDS:
        valid_backends = ", ".join(VALID_WAKEWORD_BACKENDS)
        raise EdgeConfigError(f"KAI_WAKEWORD_BACKEND must be one of: {valid_backends}")
    return value


def parse_absolute_path_list(raw_value: str, *, setting_name: str) -> tuple[str, ...]:
    stripped = raw_value.strip()
    if not stripped:
        return ()

    values: list[str] = []
    for raw_item in stripped.split(","):
        item = raw_item.strip()
        if not item:
            continue
        if not item.startswith("/"):
            raise EdgeConfigError(f"{setting_name} must contain only absolute paths")
        values.append(item)

    return tuple(values)


def bounded_float(
    raw_value: str,
    setting_name: str,
    *,
    minimum: float,
    maximum: float,
) -> float:
    try:
        value = float(raw_value)
    except ValueError as exc:
        raise EdgeConfigError(f"{setting_name} must be a float: {raw_value!r}") from exc

    if value < minimum or value > maximum:
        raise EdgeConfigError(
            f"{setting_name} must be between {minimum} and {maximum}: {raw_value!r}"
        )

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
        "KAI_AUDIO_STREAM_ENABLED": "1" if DEFAULT_AUDIO_STREAM_ENABLED else "0",
        "KAI_AUDIO_STREAM_FALLBACK_TO_NON_STREAM": "1"
        if DEFAULT_AUDIO_STREAM_FALLBACK_TO_NON_STREAM
        else "0",
        "KAI_RECORD_DEVICE": "",
        "KAI_PLAYBACK_DEVICE": "",
        "KAI_TRIGGER_SOCKET_PATH": DEFAULT_TRIGGER_SOCKET_PATH,
        "KAI_TRIGGER_MODE": DEFAULT_TRIGGER_MODE,
        "KAI_WAKEWORD_BACKEND": DEFAULT_WAKEWORD_BACKEND,
        "KAI_WAKEWORD_ACCESS_KEY": "",
        "KAI_WAKEWORD_BUILTIN_KEYWORD": DEFAULT_WAKEWORD_BUILTIN_KEYWORD,
        "KAI_WAKEWORD_KEYWORD_PATH": "",
        "KAI_WAKEWORD_MODEL_PATH": "",
        "KAI_WAKEWORD_SENSITIVITY": str(DEFAULT_WAKEWORD_SENSITIVITY),
        "KAI_WAKEWORD_OPENWAKEWORD_MODEL_PATHS": "",
        "KAI_WAKEWORD_OPENWAKEWORD_THRESHOLD": str(DEFAULT_WAKEWORD_OPENWAKEWORD_THRESHOLD),
        "KAI_WAKEWORD_DETECTION_COOLDOWN_MS": str(DEFAULT_WAKEWORD_DETECTION_COOLDOWN_MS),
        "KAI_WAKEWORD_POST_WAKE_SPEECH_TIMEOUT_MS": str(
            DEFAULT_WAKEWORD_POST_WAKE_SPEECH_TIMEOUT_MS
        ),
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
    audio_stream_enabled = parse_bool(
        _get_setting("KAI_AUDIO_STREAM_ENABLED", file_settings, defaults, overrides),
        "KAI_AUDIO_STREAM_ENABLED",
    )
    audio_stream_fallback_to_non_stream = parse_bool(
        _get_setting(
            "KAI_AUDIO_STREAM_FALLBACK_TO_NON_STREAM",
            file_settings,
            defaults,
            overrides,
        ),
        "KAI_AUDIO_STREAM_FALLBACK_TO_NON_STREAM",
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
    wakeword_backend = parse_wakeword_backend(
        _get_setting("KAI_WAKEWORD_BACKEND", file_settings, defaults, overrides)
    )
    wakeword_access_key = optional_string(
        _get_setting("KAI_WAKEWORD_ACCESS_KEY", file_settings, defaults, overrides)
    )
    wakeword_builtin_keyword = optional_string(
        _get_setting("KAI_WAKEWORD_BUILTIN_KEYWORD", file_settings, defaults, overrides)
    )
    wakeword_keyword_path = optional_string(
        _get_setting("KAI_WAKEWORD_KEYWORD_PATH", file_settings, defaults, overrides)
    )
    wakeword_model_path = optional_string(
        _get_setting("KAI_WAKEWORD_MODEL_PATH", file_settings, defaults, overrides)
    )
    wakeword_sensitivity = bounded_float(
        _get_setting("KAI_WAKEWORD_SENSITIVITY", file_settings, defaults, overrides),
        "KAI_WAKEWORD_SENSITIVITY",
        minimum=0.0,
        maximum=1.0,
    )
    wakeword_openwakeword_model_paths = parse_absolute_path_list(
        _get_setting("KAI_WAKEWORD_OPENWAKEWORD_MODEL_PATHS", file_settings, defaults, overrides),
        setting_name="KAI_WAKEWORD_OPENWAKEWORD_MODEL_PATHS",
    )
    wakeword_openwakeword_threshold = bounded_float(
        _get_setting("KAI_WAKEWORD_OPENWAKEWORD_THRESHOLD", file_settings, defaults, overrides),
        "KAI_WAKEWORD_OPENWAKEWORD_THRESHOLD",
        minimum=0.0,
        maximum=1.0,
    )
    wakeword_detection_cooldown_ms = non_negative_int(
        _get_setting("KAI_WAKEWORD_DETECTION_COOLDOWN_MS", file_settings, defaults, overrides),
        "KAI_WAKEWORD_DETECTION_COOLDOWN_MS",
    )
    wakeword_post_wake_speech_timeout_ms = non_negative_int(
        _get_setting(
            "KAI_WAKEWORD_POST_WAKE_SPEECH_TIMEOUT_MS",
            file_settings,
            defaults,
            overrides,
        ),
        "KAI_WAKEWORD_POST_WAKE_SPEECH_TIMEOUT_MS",
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
    if wakeword_keyword_path is not None and not wakeword_keyword_path.startswith("/"):
        raise EdgeConfigError("KAI_WAKEWORD_KEYWORD_PATH must be an absolute path")
    if wakeword_model_path is not None and not wakeword_model_path.startswith("/"):
        raise EdgeConfigError("KAI_WAKEWORD_MODEL_PATH must be an absolute path")
    if trigger_mode == "wakeword":
        if wakeword_backend == "porcupine":
            if not wakeword_access_key:
                raise EdgeConfigError(
                    "KAI_WAKEWORD_ACCESS_KEY must be set when "
                    "KAI_TRIGGER_MODE=wakeword and KAI_WAKEWORD_BACKEND=porcupine"
                )
            if wakeword_keyword_path is None and wakeword_builtin_keyword is None:
                raise EdgeConfigError(
                    "set KAI_WAKEWORD_BUILTIN_KEYWORD or KAI_WAKEWORD_KEYWORD_PATH for "
                    "porcupine wakeword mode"
                )
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
        audio_stream_enabled=audio_stream_enabled,
        audio_stream_fallback_to_non_stream=audio_stream_fallback_to_non_stream,
        record_device=record_device,
        playback_device=playback_device,
        trigger_socket_path=trigger_socket_path,
        trigger_mode=trigger_mode,
        wakeword_backend=wakeword_backend,
        wakeword_access_key=wakeword_access_key,
        wakeword_builtin_keyword=wakeword_builtin_keyword,
        wakeword_keyword_path=wakeword_keyword_path,
        wakeword_model_path=wakeword_model_path,
        wakeword_sensitivity=wakeword_sensitivity,
        wakeword_openwakeword_model_paths=wakeword_openwakeword_model_paths,
        wakeword_openwakeword_threshold=wakeword_openwakeword_threshold,
        wakeword_detection_cooldown_ms=wakeword_detection_cooldown_ms,
        wakeword_post_wake_speech_timeout_ms=wakeword_post_wake_speech_timeout_ms,
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
