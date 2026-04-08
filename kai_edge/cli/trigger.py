from __future__ import annotations

import argparse
import os
import sys

from ..config import DEFAULT_ENV_FILE, build_edge_config, load_env_file, positive_int
from ..errors import EdgeConfigError, EdgeRuntimeError
from ..trigger_client import send_trigger


def build_parser() -> argparse.ArgumentParser:
    env_file_default = os.environ.get("KAI_EDGE_ENV_FILE", DEFAULT_ENV_FILE)
    parser = argparse.ArgumentParser(
        description="Trigger one push-to-talk interaction on the running kai-edge daemon.",
    )
    parser.add_argument(
        "--env-file",
        default=env_file_default,
        help=f"environment file to load before applying shell env overrides (default: {env_file_default})",
    )
    parser.add_argument(
        "--trigger-socket",
        default=None,
        help="optional unix socket path override",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=180,
        help="trigger request timeout in seconds",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv or sys.argv[1:])

    try:
        timeout_seconds = positive_int(str(args.timeout_seconds), "--timeout-seconds")
        file_settings = load_env_file(args.env_file)
        overrides = {}
        if args.trigger_socket:
            overrides["KAI_TRIGGER_SOCKET_PATH"] = args.trigger_socket
        config = build_edge_config(file_settings=file_settings, overrides=overrides)
    except EdgeConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if config.trigger_mode != "manual":
        print(
            (
                "error: KAI_TRIGGER_MODE is set to "
                f"{config.trigger_mode!r}; kai-edge-trigger only works in manual mode"
            ),
            file=sys.stderr,
        )
        return 1

    try:
        response = send_trigger(
            socket_path=config.trigger_socket_path,
            timeout_seconds=timeout_seconds,
        )
    except EdgeRuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(response)

    if response == "ok":
        return 0
    if response == "busy":
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
