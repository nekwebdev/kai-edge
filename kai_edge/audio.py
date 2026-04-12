from __future__ import annotations

import logging
import subprocess
import wave
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


class StreamingAudioPlayer:
    def __init__(self, *, playback_device: str | None, logger: logging.Logger) -> None:
        self._playback_device = playback_device
        self._logger = logger
        self._process: subprocess.Popen[bytes] | None = None
        self._mime_type: str | None = None
        self._bytes_written = 0

    def _start(self, *, mime_type: str) -> None:
        device = self._playback_device or "default"
        command = [
            "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            "pipe:0",
            "-f",
            "alsa",
            device,
        ]
        self._logger.info("starting streaming playback (%s) through %s", mime_type, device)
        try:
            self._process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise EdgeRuntimeError("streaming playback failed because 'ffmpeg' is not installed") from exc
        self._mime_type = mime_type

    def write_chunk(self, *, mime_type: str, chunk: bytes) -> None:
        if not chunk:
            return

        if self._process is None:
            self._start(mime_type=mime_type)
        elif self._mime_type != mime_type:
            self._logger.warning(
                "backend stream mime type changed during playback: %s -> %s",
                self._mime_type or "-",
                mime_type,
            )
            self._mime_type = mime_type

        process = self._process
        if process is None or process.stdin is None:
            raise EdgeRuntimeError("streaming playback process is not active")

        try:
            process.stdin.write(chunk)
            process.stdin.flush()
        except BrokenPipeError as exc:
            details = _read_streaming_stderr(process)
            suffix = f": {details}" if details else ""
            raise EdgeRuntimeError(f"streaming audio playback failed while writing audio{suffix}") from exc

        self._bytes_written += len(chunk)

    def close(self) -> bool:
        process = self._process
        self._process = None
        if process is None:
            return False

        if process.stdin is not None:
            process.stdin.close()

        try:
            return_code = process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                raise EdgeRuntimeError("streaming playback process did not exit cleanly")
            raise EdgeRuntimeError("streaming playback timed out while waiting for ffmpeg")

        if return_code != 0:
            details = _read_streaming_stderr(process)
            suffix = f": {details}" if details else ""
            raise EdgeRuntimeError(
                f"streaming audio playback failed with exit code {return_code}{suffix}"
            )

        return self._bytes_written > 0

    def abort(self) -> None:
        process = self._process
        self._process = None
        if process is None:
            return
        if process.stdin is not None:
            try:
                process.stdin.close()
            except OSError:
                pass
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self._logger.warning("streaming playback process did not exit cleanly")


def _read_streaming_stderr(process: subprocess.Popen[bytes]) -> str:
    if process.stderr is None:
        return ""
    try:
        return process.stderr.read().decode("utf-8", errors="replace").strip()
    except OSError:
        return ""


def write_pcm16_mono_wav(*, output_path: Path, sample_rate: int, frames: tuple[bytes, ...]) -> None:
    with wave.open(str(output_path), "wb") as wave_file:
        wave_file.setnchannels(1)
        wave_file.setsampwidth(2)
        wave_file.setframerate(sample_rate)
        wave_file.writeframes(b"".join(frames))

    if not output_path.exists() or output_path.stat().st_size == 0:
        raise EdgeRuntimeError(f"wav render produced an empty file: {output_path}")
