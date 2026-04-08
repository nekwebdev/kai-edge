from __future__ import annotations

import argparse
import os
import sys

from ..config import DEFAULT_ENV_FILE, EdgeConfig, build_edge_config, load_env_file
from ..errors import EdgeConfigError, EdgeRuntimeError
from ..interaction import run_interaction
from ..logging_config import configure_logging


def build_parser(default_config: EdgeConfig) -> argparse.ArgumentParser:
    env_file_default = os.environ.get("KAI_EDGE_ENV_FILE", DEFAULT_ENV_FILE)
    parser = argparse.ArgumentParser(
        description="Record one utterance, POST it to kai-core, and play the audio reply when present.",
    )
    parser.add_argument(
        "--env-file",
        default=env_file_default,
        help=f"environment file to load before applying shell env overrides (default: {env_file_default})",
    )
    parser.add_argument(
        "--backend-url",
        default=default_config.backend_url,
        help="kai-core base URL; defaults to KAI_CORE_BASE_URL",
    )
    parser.add_argument(
        "--record-seconds",
        type=int,
        default=default_config.record_seconds,
        help="recording duration in seconds",
    )
    parser.add_argument(
        "--sample-rate",
        type=int,
        default=default_config.sample_rate,
        help="WAV sample rate in Hz",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=default_config.timeout_seconds,
        help="HTTP timeout in seconds",
    )
    parser.add_argument(
        "--record-device",
        default=default_config.record_device,
        help="optional ALSA capture device passed to arecord -D",
    )
    parser.add_argument(
        "--playback-device",
        default=default_config.playback_device,
        help="optional ALSA playback device passed to aplay -D",
    )
    return parser


def _load_file_settings(argv: list[str]) -> tuple[str, dict[str, str]]:
    env_file_default = os.environ.get("KAI_EDGE_ENV_FILE", DEFAULT_ENV_FILE)
    pre_parser = argparse.ArgumentParser(add_help=False)
    pre_parser.add_argument("--env-file", default=env_file_default)
    pre_args, _ = pre_parser.parse_known_args(argv)
    return pre_args.env_file, load_env_file(pre_args.env_file)


def main(argv: list[str] | None = None) -> int:
    logger = configure_logging()
    args_list = list(argv or sys.argv[1:])

    try:
        env_file, file_settings = _load_file_settings(args_list)
        default_config = build_edge_config(file_settings=file_settings)
    except EdgeConfigError as exc:
        logger.error("%s", exc)
        return 1

    parser = build_parser(default_config)
    parsed_args = parser.parse_args(args_list)

    overrides = {
        "KAI_CORE_BASE_URL": parsed_args.backend_url,
        "KAI_RECORD_SECONDS": str(parsed_args.record_seconds),
        "KAI_AUDIO_SAMPLE_RATE": str(parsed_args.sample_rate),
        "KAI_HTTP_TIMEOUT_SECONDS": str(parsed_args.timeout_seconds),
        "KAI_RECORD_DEVICE": parsed_args.record_device or "",
        "KAI_PLAYBACK_DEVICE": parsed_args.playback_device or "",
    }

    try:
        config = build_edge_config(file_settings=file_settings, overrides=overrides)
    except EdgeConfigError as exc:
        logger.error("%s", exc)
        return 1

    if not config.backend_url:
        logger.error(
            "KAI_CORE_BASE_URL is not configured. Set it in %s or pass --backend-url.",
            env_file,
        )
        return 1

    try:
        run_interaction(config=config, logger=logger)
    except EdgeRuntimeError as exc:
        logger.error("%s", exc)
        return 1
    except KeyboardInterrupt:
        logger.error("interrupted")
        return 130

    logger.info("completed one-shot request")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
