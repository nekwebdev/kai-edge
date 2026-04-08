from __future__ import annotations

import argparse
import os
import sys

from ..config import DEFAULT_ENV_FILE, build_edge_config, load_env_file
from ..daemon import EdgeDaemon
from ..errors import EdgeConfigError, EdgeRuntimeError
from ..logging_config import configure_logging


def build_parser() -> argparse.ArgumentParser:
    env_file_default = os.environ.get("KAI_EDGE_ENV_FILE", DEFAULT_ENV_FILE)
    parser = argparse.ArgumentParser(
        description="Run the kai edge daemon in manual trigger or VAD armed-listening mode.",
    )
    parser.add_argument(
        "--env-file",
        default=env_file_default,
        help=f"environment file to load before applying shell env overrides (default: {env_file_default})",
    )
    parser.add_argument(
        "--trigger-socket",
        default=None,
        help="optional unix socket path override for manual trigger mode",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    logger = configure_logging()
    parser = build_parser()
    args = parser.parse_args(argv or sys.argv[1:])

    try:
        file_settings = load_env_file(args.env_file)
        overrides = {}
        if args.trigger_socket:
            overrides["KAI_TRIGGER_SOCKET_PATH"] = args.trigger_socket
        config = build_edge_config(file_settings=file_settings, overrides=overrides)
    except EdgeConfigError as exc:
        logger.error("%s", exc)
        return 1

    daemon = EdgeDaemon(config=config, logger=logger)
    try:
        return daemon.serve_forever()
    except EdgeRuntimeError as exc:
        logger.error("%s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
