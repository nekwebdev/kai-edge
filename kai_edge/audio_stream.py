from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass

from .errors import EdgeRuntimeError


@dataclass
class ArecordFrameSource:
    sample_rate: int
    frame_bytes: int
    record_device: str | None
    logger: logging.Logger

    def __post_init__(self) -> None:
        self._process: subprocess.Popen[bytes] | None = None

    def __enter__(self) -> "ArecordFrameSource":
        command = [
            "arecord",
            "-q",
            "-t",
            "raw",
            "-f",
            "S16_LE",
            "-r",
            str(self.sample_rate),
            "-c",
            "1",
        ]
        if self.record_device:
            command.extend(["-D", self.record_device])

        self.logger.info("arming microphone stream")
        self._process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        del exc_type, exc, tb
        if self._process is None:
            return

        process = self._process
        self._process = None

        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.logger.warning("arecord did not exit cleanly after kill")

    def read_frame(self) -> bytes:
        if self._process is None or self._process.stdout is None:
            raise EdgeRuntimeError("audio frame source is not active")

        frame = self._process.stdout.read(self.frame_bytes)
        if not frame:
            details = _read_stderr(self._process)
            suffix = f": {details}" if details else ""
            raise EdgeRuntimeError(f"microphone stream ended unexpectedly{suffix}")

        if len(frame) != self.frame_bytes:
            raise EdgeRuntimeError(
                f"microphone stream returned partial frame: expected {self.frame_bytes} bytes, got {len(frame)}"
            )

        return frame


def _read_stderr(process: subprocess.Popen[bytes]) -> str:
    if process.stderr is None:
        return ""
    try:
        content = process.stderr.read().decode("utf-8", errors="replace").strip()
    except OSError:
        return ""
    return content
