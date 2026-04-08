from __future__ import annotations

import logging
from pathlib import Path

from .errors import EdgeRuntimeError
from .subprocess_utils import run_command


def record_audio(
    *,
    output_path: Path,
    duration_seconds: int,
    sample_rate: int,
    record_device: str | None,
    logger: logging.Logger,
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
    logger.info("recording %ss from %s", duration_seconds, device_label)
    run_command(command, "microphone recording")

    if not output_path.exists() or output_path.stat().st_size == 0:
        raise EdgeRuntimeError(f"microphone recording produced an empty file: {output_path}")


def play_audio(*, audio_path: Path, playback_device: str | None, logger: logging.Logger) -> None:
    command = ["aplay", "-q"]
    if playback_device:
        command.extend(["-D", playback_device])
    command.append(str(audio_path))

    device_label = playback_device or "default playback device"
    logger.info("playing backend audio through %s", device_label)
    run_command(command, "audio playback")
