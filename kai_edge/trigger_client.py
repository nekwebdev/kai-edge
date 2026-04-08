from __future__ import annotations

import socket
from pathlib import Path

from .errors import EdgeRuntimeError


def send_trigger(*, socket_path: str, timeout_seconds: int) -> str:
    path = Path(socket_path)
    if not path.exists():
        raise EdgeRuntimeError(
            f"trigger socket not found: {socket_path} (is kai-edge.service running?)"
        )

    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(timeout_seconds)
        try:
            client.connect(str(path))
        except OSError as exc:
            raise EdgeRuntimeError(f"failed to connect to trigger socket {socket_path}: {exc}") from exc

        client.sendall(b"trigger\n")
        client.shutdown(socket.SHUT_WR)

        chunks: list[bytes] = []
        while True:
            chunk = client.recv(4096)
            if not chunk:
                break
            chunks.append(chunk)

    response = b"".join(chunks).decode("utf-8", errors="replace").strip()
    if not response:
        raise EdgeRuntimeError("daemon returned an empty trigger response")

    return response
