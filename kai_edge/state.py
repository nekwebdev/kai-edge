from __future__ import annotations

from enum import Enum


class EdgeState(str, Enum):
    IDLE = "idle"
    RECORDING = "recording"
    SENDING = "sending"
    SPEAKING = "speaking"
    ERROR = "error"
