from __future__ import annotations

import logging
import os
import signal
import socket
import tempfile
from pathlib import Path

from .config import EdgeConfig
from .errors import EdgeRuntimeError
from .interaction import record_request_audio, send_request_audio, speak_response_audio
from .state import EdgeState


class EdgeDaemon:
    def __init__(self, *, config: EdgeConfig, logger: logging.Logger) -> None:
        self._config = config
        self._logger = logger
        self._state = EdgeState.IDLE
        self._stop_requested = False

    @property
    def state(self) -> EdgeState:
        return self._state

    def _transition(self, new_state: EdgeState) -> None:
        old_state = self._state
        self._state = new_state
        if old_state == new_state:
            return
        self._logger.info("state %s -> %s", old_state.value, new_state.value)

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

    def _run_one_interaction(self) -> tuple[bool, str]:
        try:
            with tempfile.TemporaryDirectory(prefix="kai-edge-daemon-") as temp_dir_name:
                temp_dir = Path(temp_dir_name)
                self._transition(EdgeState.RECORDING)
                recorded_audio_path = record_request_audio(
                    config=self._config,
                    temp_dir=temp_dir,
                    logger=self._logger,
                )

                self._transition(EdgeState.SENDING)
                core_response = send_request_audio(
                    config=self._config,
                    recorded_audio_path=recorded_audio_path,
                    logger=self._logger,
                )
                self._logger.info("transcribed text: %s", core_response.text)
                self._logger.info("assistant response: %s", core_response.response)

                if core_response.audio is not None:
                    self._transition(EdgeState.SPEAKING)
                    speak_response_audio(
                        config=self._config,
                        core_response=core_response,
                        temp_dir=temp_dir,
                        logger=self._logger,
                    )
                else:
                    self._logger.info("backend returned no audio payload")

            self._logger.info("interaction complete")
            return True, "ok"
        except Exception as exc:
            self._transition(EdgeState.ERROR)
            self._logger.error("interaction failed: %s", exc)
            return False, str(exc)
        finally:
            self._transition(EdgeState.IDLE)

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
        ok, message = self._run_one_interaction()
        if ok:
            return "ok"
        return f"error: {message}"

    def serve_forever(self) -> int:
        signal.signal(signal.SIGINT, self._on_signal)
        signal.signal(signal.SIGTERM, self._on_signal)

        server = self._prepare_socket()
        socket_path = self._config.trigger_socket_path
        self._logger.info("listening for trigger commands on %s", socket_path)

        try:
            while not self._stop_requested:
                try:
                    connection, _ = server.accept()
                except socket.timeout:
                    continue

                with connection:
                    connection.settimeout(5.0)
                    response = self._handle_connection(connection)
                    try:
                        connection.sendall(f"{response}\n".encode("utf-8"))
                    except OSError as exc:
                        self._logger.warning("failed to send trigger response: %s", exc)
        finally:
            server.close()
            self._cleanup_socket_path()

        self._logger.info("daemon stopped")
        return 0
