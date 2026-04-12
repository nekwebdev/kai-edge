from __future__ import annotations

import logging
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .audio import StreamingAudioPlayer, play_audio, record_audio
from .config import EdgeConfig
from .core_client import CoreAudio, CoreResponse, send_audio, send_audio_stream
from .errors import EdgeRuntimeError


@dataclass(frozen=True)
class InteractionResult:
    text: str
    response: str
    audio_played: bool


def ensure_backend_url(config: EdgeConfig) -> None:
    if config.backend_url:
        return
    raise EdgeRuntimeError(
        "KAI_CORE_BASE_URL is not configured. Set it in /etc/kai/edge.env or pass --backend-url."
    )


def record_request_audio(*, config: EdgeConfig, temp_dir: Path, logger: logging.Logger) -> Path:
    recorded_audio_path = temp_dir / "recorded.wav"
    record_audio(
        output_path=recorded_audio_path,
        duration_seconds=config.record_seconds,
        sample_rate=config.sample_rate,
        record_device=config.record_device,
        logger=logger,
    )
    return recorded_audio_path


def send_request_audio(*, config: EdgeConfig, recorded_audio_path: Path, logger: logging.Logger) -> CoreResponse:
    ensure_backend_url(config)
    return send_audio(
        audio_path=recorded_audio_path,
        backend_url=config.backend_url,
        timeout_seconds=config.timeout_seconds,
        logger=logger,
    )


def send_request_audio_streaming(
    *,
    config: EdgeConfig,
    recorded_audio_path: Path,
    logger: logging.Logger,
    on_audio_chunk: Callable[[CoreAudio], None],
) -> tuple[str, str, int]:
    ensure_backend_url(config)
    stream_result = send_audio_stream(
        audio_path=recorded_audio_path,
        backend_url=config.backend_url,
        timeout_seconds=config.timeout_seconds,
        logger=logger,
        on_audio_chunk=on_audio_chunk,
    )
    return stream_result.text, stream_result.response, stream_result.audio_chunks


def speak_response_audio(
    *,
    config: EdgeConfig,
    core_response: CoreResponse,
    temp_dir: Path,
    logger: logging.Logger,
) -> bool:
    if core_response.audio is None:
        logger.info("backend returned no audio payload")
        return False

    suffix = ".wav" if core_response.audio.mime_type == "audio/wav" else ".bin"
    response_audio_path = temp_dir / f"kai-response{suffix}"
    response_audio_path.write_bytes(core_response.audio.data)
    logger.info("saved backend audio (%s) to %s", core_response.audio.mime_type, response_audio_path)
    play_audio(audio_path=response_audio_path, playback_device=config.playback_device, logger=logger)
    return True


def process_recorded_audio(
    *,
    config: EdgeConfig,
    recorded_audio_path: Path,
    temp_dir: Path,
    logger: logging.Logger,
    on_before_speak: Callable[[], None] | None = None,
) -> InteractionResult:
    if config.audio_stream_enabled:
        audio_player = StreamingAudioPlayer(
            playback_device=config.playback_device,
            logger=logger,
        )
        before_speak_called = False
        audio_chunks = 0

        def _on_audio_chunk(audio: CoreAudio) -> None:
            nonlocal before_speak_called, audio_chunks
            if not before_speak_called and on_before_speak is not None:
                on_before_speak()
                before_speak_called = True
            audio_player.write_chunk(mime_type=audio.mime_type, chunk=audio.data)
            audio_chunks += 1

        try:
            text, response, streamed_chunk_count = send_request_audio_streaming(
                config=config,
                recorded_audio_path=recorded_audio_path,
                logger=logger,
                on_audio_chunk=_on_audio_chunk,
            )
            logger.info("transcribed text: %s", text)
            logger.info("assistant response: %s", response)
            audio_played = audio_player.close()
            if streamed_chunk_count == 0:
                logger.info("backend stream returned no audio chunks")
            return InteractionResult(
                text=text,
                response=response,
                audio_played=audio_played,
            )
        except EdgeRuntimeError as exc:
            audio_player.abort()
            if config.audio_stream_fallback_to_non_stream and audio_chunks == 0:
                logger.warning(
                    "streaming request failed before playback; falling back to /audio: %s",
                    exc,
                )
            else:
                raise

    core_response = send_request_audio(
        config=config,
        recorded_audio_path=recorded_audio_path,
        logger=logger,
    )
    logger.info("transcribed text: %s", core_response.text)
    logger.info("assistant response: %s", core_response.response)
    if core_response.audio is not None and on_before_speak is not None:
        on_before_speak()
    audio_played = speak_response_audio(
        config=config,
        core_response=core_response,
        temp_dir=temp_dir,
        logger=logger,
    )
    return InteractionResult(
        text=core_response.text,
        response=core_response.response,
        audio_played=audio_played,
    )


def run_interaction(*, config: EdgeConfig, logger: logging.Logger) -> InteractionResult:
    with tempfile.TemporaryDirectory(prefix="kai-push-to-talk-") as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        recorded_audio_path = record_request_audio(config=config, temp_dir=temp_dir, logger=logger)
        return process_recorded_audio(
            config=config,
            recorded_audio_path=recorded_audio_path,
            temp_dir=temp_dir,
            logger=logger,
        )
