from __future__ import annotations

import logging
import os
import signal
import socket
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from .audio import write_pcm16_mono_wav
from .audio_stream import ArecordFrameSource
from .config import EdgeConfig
from .errors import EdgeRuntimeError
from .interaction import process_recorded_audio, record_request_audio
from .observability import EdgeObservability
from .state import EdgeState
from .vad import VadDetector, build_vad_detector
from .vad_session import UtteranceCollector, UtteranceDecision
from .wakeword import WakeWordDetector, build_wakeword_detector
from .wakeword_runtime import SpeechStartDeadline, WakeWordCooldownGate


@dataclass(frozen=True)
class VadCaptureResult:
    decision: UtteranceDecision | None
    speech_start_timed_out: bool


class EdgeDaemon:
    def __init__(self, *, config: EdgeConfig, logger: logging.Logger) -> None:
        self._config = config
        self._logger = logger
        self._state = EdgeState.IDLE
        self._stop_requested = False
        self._observability = EdgeObservability(
            config=config,
            logger=logger,
            initial_state=self._state.value,
        )

    @property
    def state(self) -> EdgeState:
        return self._state

    def _transition(self, new_state: EdgeState) -> None:
        old_state = self._state
        self._state = new_state
        if old_state == new_state:
            return
        self._logger.info("state %s -> %s", old_state.value, new_state.value)
        self._observability.record_state_transition(
            old_state=old_state.value,
            new_state=new_state.value,
        )

    def _on_signal(self, signum: int, _frame: object | None) -> None:
        signal_name = signal.Signals(signum).name
        self._logger.info("received %s, shutting down", signal_name)
        self._stop_requested = True

    def _prepare_socket(self) -> socket.socket:
        socket_path = Path(self._config.trigger_socket_path)
        socket_path.parent.mkdir(parents=True, exist_ok=True)

        if socket_path.exists():
            if socket_path.is_socket():
                socket_path.unlink()
            else:
                raise EdgeRuntimeError(
                    f"trigger socket path exists and is not a socket: {socket_path}"
                )

        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(socket_path))
        os.chmod(socket_path, 0o660)
        server.listen(8)
        server.settimeout(1.0)
        return server

    def _cleanup_socket_path(self) -> None:
        socket_path = Path(self._config.trigger_socket_path)
        if socket_path.exists() and socket_path.is_socket():
            socket_path.unlink()

    def _read_request(self, connection: socket.socket) -> str:
        chunks: list[bytes] = []
        while True:
            try:
                chunk = connection.recv(1024)
            except socket.timeout as exc:
                raise EdgeRuntimeError("trigger client timed out before sending a command") from exc
            if not chunk:
                break
            chunks.append(chunk)
            if b"\n" in chunk:
                break

        request = b"".join(chunks).decode("utf-8", errors="replace").strip().lower()
        return request or "trigger"

    def _run_interaction_for_recorded_path(
        self,
        *,
        recorded_audio_path: Path,
        temp_dir: Path,
    ) -> tuple[bool, str]:
        self._observability.record_interaction_started()
        try:
            self._transition(EdgeState.SENDING)
            process_recorded_audio(
                config=self._config,
                recorded_audio_path=recorded_audio_path,
                temp_dir=temp_dir,
                logger=self._logger,
                on_before_speak=lambda: self._transition(EdgeState.SPEAKING),
            )
            self._logger.info("interaction complete")
            return True, "ok"
        except Exception as exc:
            self._transition(EdgeState.ERROR)
            self._logger.error("interaction failed: %s", exc)
            self._observability.record_error(summary=f"interaction failed: {exc}")
            return False, str(exc)
        finally:
            self._transition(EdgeState.IDLE)
            self._observability.emit_summary_if_due()

    def _run_one_manual_interaction(self) -> tuple[bool, str]:
        try:
            with tempfile.TemporaryDirectory(prefix="kai-edge-daemon-") as temp_dir_name:
                temp_dir = Path(temp_dir_name)
                self._transition(EdgeState.RECORDING)
                recorded_audio_path = record_request_audio(
                    config=self._config,
                    temp_dir=temp_dir,
                    logger=self._logger,
                )
                self._observability.record_accepted_utterance(
                    utterance_ms=self._config.record_seconds * 1000,
                    stop_reason="manual_fixed_duration",
                )
                return self._run_interaction_for_recorded_path(
                    recorded_audio_path=recorded_audio_path,
                    temp_dir=temp_dir,
                )
        except Exception as exc:
            self._transition(EdgeState.ERROR)
            self._logger.error("interaction failed: %s", exc)
            self._observability.record_error(summary=f"interaction failed: {exc}")
            self._transition(EdgeState.IDLE)
            self._observability.emit_summary_if_due()
            return False, str(exc)

    def _handle_connection(self, connection: socket.socket) -> str:
        try:
            request = self._read_request(connection)
        except EdgeRuntimeError as exc:
            self._logger.warning("invalid trigger request: %s", exc)
            return f"error: {exc}"

        if request not in ("trigger", "run"):
            self._logger.warning("rejected trigger request: %s", request)
            return "error: unsupported command"

        if self._state != EdgeState.IDLE:
            self._logger.warning("trigger rejected while busy in state %s", self._state.value)
            return "busy"

        self._logger.info("trigger received")
        ok, message = self._run_one_manual_interaction()
        if ok:
            return "ok"
        return f"error: {message}"

    def _serve_manual_mode(self) -> int:
        server = self._prepare_socket()
        socket_path = self._config.trigger_socket_path
        self._logger.info("listening for trigger commands on %s", socket_path)

        try:
            while not self._stop_requested:
                try:
                    connection, _ = server.accept()
                except socket.timeout:
                    self._observability.emit_summary_if_due()
                    continue

                with connection:
                    connection.settimeout(5.0)
                    response = self._handle_connection(connection)
                    try:
                        connection.sendall(f"{response}\n".encode("utf-8"))
                    except OSError as exc:
                        self._logger.warning("failed to send trigger response: %s", exc)
                        self._observability.record_error(
                            summary=f"trigger response send failed: {exc}"
                        )
                self._observability.emit_summary_if_due(trigger="interaction")
        finally:
            server.close()
            self._cleanup_socket_path()

        self._transition(EdgeState.IDLE)
        return 0

    def _frame_bytes_for_vad(self) -> int:
        samples_product = self._config.sample_rate * self._config.vad_frame_ms
        if samples_product % 1000 != 0:
            raise EdgeRuntimeError(
                "sample rate and KAI_VAD_FRAME_MS do not produce whole-frame samples"
            )
        sample_count = samples_product // 1000
        frame_bytes = sample_count * 2
        if frame_bytes <= 0:
            raise EdgeRuntimeError("computed VAD frame size is zero")
        return frame_bytes

    def _build_vad_collector(self) -> UtteranceCollector:
        return UtteranceCollector(
            frame_ms=self._config.vad_frame_ms,
            pre_roll_ms=self._config.vad_pre_roll_ms,
            min_speech_ms=self._config.vad_min_speech_ms,
            min_speech_run_ms=self._config.vad_min_speech_run_ms,
            trailing_silence_ms=self._config.vad_trailing_silence_ms,
            max_utterance_ms=self._config.vad_max_utterance_ms,
        )

    def _capture_vad_utterance_from_source(
        self,
        *,
        detector: VadDetector,
        collector: UtteranceCollector,
        frame_source: ArecordFrameSource,
        speech_start_timeout_ms: int = 0,
    ) -> VadCaptureResult:
        timeout = SpeechStartDeadline(timeout_ms=speech_start_timeout_ms)
        while not self._stop_requested:
            self._observability.emit_summary_if_due()
            if not collector.is_recording and timeout.expired():
                return VadCaptureResult(decision=None, speech_start_timed_out=True)

            frame = frame_source.read_frame()
            is_speech = detector.is_speech(frame=frame, sample_rate=self._config.sample_rate)
            speech_start, decision = collector.consume_frame(frame=frame, is_speech=is_speech)
            if speech_start:
                self._logger.info("speech start detected")
                self._transition(EdgeState.RECORDING)
            if decision is not None:
                self._logger.info("speech end detected (%s)", decision.stop_reason)
                return VadCaptureResult(decision=decision, speech_start_timed_out=False)

        return VadCaptureResult(decision=None, speech_start_timed_out=False)

    def _capture_vad_utterance(
        self,
        *,
        detector: VadDetector,
        collector: UtteranceCollector,
        frame_bytes: int,
        speech_start_timeout_ms: int = 0,
    ) -> VadCaptureResult:
        with ArecordFrameSource(
            sample_rate=self._config.sample_rate,
            frame_bytes=frame_bytes,
            record_device=self._config.record_device,
            logger=self._logger,
        ) as frame_source:
            return self._capture_vad_utterance_from_source(
                detector=detector,
                collector=collector,
                frame_source=frame_source,
                speech_start_timeout_ms=speech_start_timeout_ms,
            )

    def _wait_for_wakeword(self, *, detector: WakeWordDetector) -> bool:
        with ArecordFrameSource(
            sample_rate=detector.sample_rate,
            frame_bytes=detector.frame_bytes,
            record_device=self._config.record_device,
            logger=self._logger,
        ) as frame_source:
            while not self._stop_requested:
                self._observability.emit_summary_if_due()
                frame = frame_source.read_frame()
                if detector.process_frame(frame=frame):
                    return True
        return False

    def _apply_vad_cooldown(self, *, context: str = "re-arming VAD") -> None:
        if self._stop_requested or self._config.vad_cooldown_ms <= 0:
            return
        duration_seconds = self._config.vad_cooldown_ms / 1000.0
        self._logger.info("cooldown %.2fs before %s", duration_seconds, context)
        time.sleep(duration_seconds)

    def _run_vad_interaction_from_decision(
        self,
        *,
        decision: UtteranceDecision,
        temp_prefix: str,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix=temp_prefix) as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            recorded_audio_path = temp_dir / "recorded.wav"
            write_pcm16_mono_wav(
                output_path=recorded_audio_path,
                sample_rate=self._config.sample_rate,
                frames=decision.frames,
            )
            self._run_interaction_for_recorded_path(
                recorded_audio_path=recorded_audio_path,
                temp_dir=temp_dir,
            )

    def _serve_vad_mode(self) -> int:
        detector = build_vad_detector(config=self._config, logger=self._logger)
        self._observability.set_vad_backend(detector.backend_name)
        frame_bytes = self._frame_bytes_for_vad()
        self._logger.info(
            "VAD armed: backend=%s frame_ms=%s min_speech_ms=%s min_speech_run_ms=%s trailing_silence_ms=%s max_utterance_ms=%s",
            detector.backend_name,
            self._config.vad_frame_ms,
            self._config.vad_min_speech_ms,
            self._config.vad_min_speech_run_ms,
            self._config.vad_trailing_silence_ms,
            self._config.vad_max_utterance_ms,
        )

        while not self._stop_requested:
            self._transition(EdgeState.LISTENING)
            collector = self._build_vad_collector()
            try:
                capture_result = self._capture_vad_utterance(
                    detector=detector,
                    collector=collector,
                    frame_bytes=frame_bytes,
                )
            except Exception as exc:
                self._transition(EdgeState.ERROR)
                self._logger.error("VAD capture failed: %s", exc)
                self._observability.record_error(summary=f"VAD capture failed: {exc}")
                self._transition(EdgeState.IDLE)
                self._apply_vad_cooldown()
                self._observability.emit_summary_if_due()
                continue

            if capture_result.speech_start_timed_out:
                self._logger.warning("unexpected speech-start timeout in VAD mode")
                self._transition(EdgeState.IDLE)
                self._apply_vad_cooldown()
                self._observability.emit_summary_if_due(trigger="interaction")
                continue

            decision = capture_result.decision
            if decision is None:
                break

            if not decision.accepted:
                self._logger.info(
                    "utterance rejected: reason=%s stop_reason=%s speech_ms=%s speech_run_ms=%s utterance_ms=%s",
                    decision.reason,
                    decision.stop_reason,
                    decision.speech_ms,
                    decision.longest_speech_run_ms,
                    decision.utterance_ms,
                )
                self._observability.record_rejected_utterance(
                    reason=decision.reason,
                    stop_reason=decision.stop_reason,
                )
                self._transition(EdgeState.IDLE)
                self._apply_vad_cooldown()
                self._observability.emit_summary_if_due(trigger="interaction")
                continue

            self._logger.info(
                "utterance accepted: stop_reason=%s speech_ms=%s speech_run_ms=%s utterance_ms=%s",
                decision.stop_reason,
                decision.speech_ms,
                decision.longest_speech_run_ms,
                decision.utterance_ms,
            )
            self._observability.record_accepted_utterance(
                utterance_ms=decision.utterance_ms,
                stop_reason=decision.stop_reason,
            )
            try:
                self._run_vad_interaction_from_decision(
                    decision=decision,
                    temp_prefix="kai-edge-daemon-vad-",
                )
            except Exception as exc:
                self._transition(EdgeState.ERROR)
                self._logger.error("VAD interaction failed: %s", exc)
                self._observability.record_error(summary=f"VAD interaction failed: {exc}")
                self._transition(EdgeState.IDLE)

            self._apply_vad_cooldown()
            self._observability.emit_summary_if_due(trigger="interaction")

        self._transition(EdgeState.IDLE)
        return 0

    def _serve_wakeword_mode(self) -> int:
        vad_detector = build_vad_detector(config=self._config, logger=self._logger)
        wakeword_detector = build_wakeword_detector(config=self._config, logger=self._logger)
        self._observability.set_vad_backend(vad_detector.backend_name)
        self._observability.set_wake_backend(wakeword_detector.backend_name)

        if self._config.sample_rate != wakeword_detector.sample_rate:
            raise EdgeRuntimeError(
                "KAI_AUDIO_SAMPLE_RATE must match wakeword backend sample rate in wakeword mode: "
                f"configured={self._config.sample_rate} required={wakeword_detector.sample_rate}"
            )

        frame_bytes = self._frame_bytes_for_vad()
        cooldown_gate = WakeWordCooldownGate(
            cooldown_ms=self._config.wakeword_detection_cooldown_ms
        )
        self._logger.info(
            "wakeword armed: backend=%s speech_timeout_ms=%s cooldown_ms=%s vad_backend=%s",
            wakeword_detector.backend_name,
            self._config.wakeword_post_wake_speech_timeout_ms,
            self._config.wakeword_detection_cooldown_ms,
            vad_detector.backend_name,
        )

        try:
            while not self._stop_requested:
                cooldown_remaining = cooldown_gate.remaining_seconds()
                if cooldown_remaining > 0:
                    self._observability.record_wake_retrigger_suppressed()
                    self._logger.info(
                        "wakeword cooldown %.2fs before re-arming listener",
                        cooldown_remaining,
                    )
                    time.sleep(cooldown_remaining)
                    if self._stop_requested:
                        break

                self._transition(EdgeState.LISTENING)
                self._logger.info("wakeword listener armed")
                try:
                    wakeword_detected = self._wait_for_wakeword(detector=wakeword_detector)
                except Exception as exc:
                    self._transition(EdgeState.ERROR)
                    self._logger.error("wakeword listener failed: %s", exc)
                    self._observability.record_error(summary=f"wakeword listener failed: {exc}")
                    self._transition(EdgeState.IDLE)
                    self._observability.emit_summary_if_due()
                    continue

                if not wakeword_detected:
                    break

                self._logger.info("wakeword detected")
                self._observability.record_wake_detection()
                cooldown_gate.mark_detected()

                collector = self._build_vad_collector()
                try:
                    capture_result = self._capture_vad_utterance(
                        detector=vad_detector,
                        collector=collector,
                        frame_bytes=frame_bytes,
                        speech_start_timeout_ms=self._config.wakeword_post_wake_speech_timeout_ms,
                    )
                except Exception as exc:
                    self._transition(EdgeState.ERROR)
                    self._logger.error("post-wake capture failed: %s", exc)
                    self._observability.record_error(summary=f"post-wake capture failed: {exc}")
                    self._transition(EdgeState.IDLE)
                    self._apply_vad_cooldown(context="re-arming wakeword listener")
                    self._observability.emit_summary_if_due(trigger="interaction")
                    continue

                if capture_result.speech_start_timed_out:
                    self._logger.info(
                        "timeout waiting for post-wake speech (%sms)",
                        self._config.wakeword_post_wake_speech_timeout_ms,
                    )
                    self._observability.record_wake_post_timeout()
                    self._observability.record_rejected_utterance(
                        reason="wakeword_speech_timeout",
                        stop_reason="speech_start_timeout",
                    )
                    self._transition(EdgeState.IDLE)
                    self._apply_vad_cooldown(context="re-arming wakeword listener")
                    self._observability.emit_summary_if_due(trigger="interaction")
                    continue

                decision = capture_result.decision
                if decision is None:
                    break

                if not decision.accepted:
                    self._logger.info(
                        "post-wake utterance rejected: reason=%s stop_reason=%s speech_ms=%s speech_run_ms=%s utterance_ms=%s",
                        decision.reason,
                        decision.stop_reason,
                        decision.speech_ms,
                        decision.longest_speech_run_ms,
                        decision.utterance_ms,
                    )
                    self._observability.record_rejected_utterance(
                        reason=decision.reason,
                        stop_reason=decision.stop_reason,
                    )
                    self._transition(EdgeState.IDLE)
                    self._apply_vad_cooldown(context="re-arming wakeword listener")
                    self._observability.emit_summary_if_due(trigger="interaction")
                    continue

                self._logger.info(
                    "post-wake utterance accepted: stop_reason=%s speech_ms=%s speech_run_ms=%s utterance_ms=%s",
                    decision.stop_reason,
                    decision.speech_ms,
                    decision.longest_speech_run_ms,
                    decision.utterance_ms,
                )
                self._observability.record_accepted_utterance(
                    utterance_ms=decision.utterance_ms,
                    stop_reason=decision.stop_reason,
                )
                self._observability.record_wake_post_accepted_utterance()

                try:
                    self._run_vad_interaction_from_decision(
                        decision=decision,
                        temp_prefix="kai-edge-daemon-wakeword-",
                    )
                except Exception as exc:
                    self._transition(EdgeState.ERROR)
                    self._logger.error("wakeword interaction failed: %s", exc)
                    self._observability.record_error(summary=f"wakeword interaction failed: {exc}")
                    self._transition(EdgeState.IDLE)

                self._apply_vad_cooldown(context="re-arming wakeword listener")
                self._observability.emit_summary_if_due(trigger="interaction")
        finally:
            wakeword_detector.close()

        self._transition(EdgeState.IDLE)
        return 0

    def serve_forever(self) -> int:
        signal.signal(signal.SIGINT, self._on_signal)
        signal.signal(signal.SIGTERM, self._on_signal)

        mode = self._config.trigger_mode
        if mode == "manual":
            self._observability.set_vad_backend("n/a")
            self._observability.set_wake_backend("n/a")
        elif mode == "vad":
            self._observability.set_wake_backend("n/a")
        self._logger.info("trigger mode selected: %s", mode)
        self._observability.emit_summary_if_due(force=True, trigger="startup")

        if mode == "manual":
            result = self._serve_manual_mode()
        elif mode == "vad":
            result = self._serve_vad_mode()
        elif mode == "wakeword":
            result = self._serve_wakeword_mode()
        else:
            raise EdgeRuntimeError(f"unsupported trigger mode: {mode}")

        self._observability.emit_summary_if_due(force=True, trigger="shutdown")
        self._logger.info("daemon stopped")
        return result
