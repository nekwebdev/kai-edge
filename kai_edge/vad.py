from __future__ import annotations

import logging
import math
import struct
from dataclasses import dataclass
from typing import Any

from .config import EdgeConfig

WEBRTC_ALLOWED_SAMPLE_RATES = (8000, 16000, 32000, 48000)


class VadDetector:
    backend_name: str

    def is_speech(self, *, frame: bytes, sample_rate: int) -> bool:
        raise NotImplementedError


@dataclass(frozen=True)
class WebRtcVadDetector(VadDetector):
    aggressiveness: int
    _module: Any

    @property
    def backend_name(self) -> str:
        return "webrtcvad"

    def __post_init__(self) -> None:
        object.__setattr__(self, "_vad", self._module.Vad(self.aggressiveness))

    def is_speech(self, *, frame: bytes, sample_rate: int) -> bool:
        return bool(self._vad.is_speech(frame, sample_rate))


@dataclass(frozen=True)
class EnergyVadDetector(VadDetector):
    threshold: int

    @property
    def backend_name(self) -> str:
        return "energy"

    def is_speech(self, *, frame: bytes, sample_rate: int) -> bool:
        del sample_rate
        return _frame_rms(frame) >= float(self.threshold)


def _frame_rms(frame: bytes) -> float:
    if not frame:
        return 0.0
    if len(frame) % 2 != 0:
        return 0.0

    sample_count = len(frame) // 2
    samples = struct.unpack("<" + "h" * sample_count, frame)
    square_sum = 0.0
    for sample in samples:
        square_sum += float(sample) * float(sample)
    return math.sqrt(square_sum / float(sample_count))


def build_vad_detector(*, config: EdgeConfig, logger: logging.Logger) -> VadDetector:
    try:
        import webrtcvad  # type: ignore[import-not-found]
    except ImportError:
        logger.warning(
            "webrtcvad not installed; falling back to energy detector (threshold=%s)",
            config.vad_energy_threshold,
        )
        return EnergyVadDetector(threshold=config.vad_energy_threshold)

    if config.sample_rate not in WEBRTC_ALLOWED_SAMPLE_RATES:
        logger.warning(
            "sample rate %s is not supported by webrtcvad; falling back to energy detector",
            config.sample_rate,
        )
        return EnergyVadDetector(threshold=config.vad_energy_threshold)

    return WebRtcVadDetector(
        aggressiveness=config.vad_aggressiveness,
        _module=webrtcvad,
    )
