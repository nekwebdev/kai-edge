from __future__ import annotations

import logging
import struct
from dataclasses import dataclass
from typing import Any

from .config import EdgeConfig
from .errors import EdgeRuntimeError


class WakeWordDetector:
    backend_name: str
    sample_rate: int
    frame_bytes: int

    def process_frame(self, *, frame: bytes) -> bool:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError


@dataclass(frozen=True)
class PorcupineWakeWordDetector(WakeWordDetector):
    access_key: str
    sensitivity: float
    keyword_path: str | None
    builtin_keyword: str | None
    model_path: str | None
    _module: Any

    def __post_init__(self) -> None:
        create_kwargs: dict[str, Any] = {
            "access_key": self.access_key,
            "sensitivities": [self.sensitivity],
        }
        if self.model_path:
            create_kwargs["model_path"] = self.model_path

        if self.keyword_path:
            create_kwargs["keyword_paths"] = [self.keyword_path]
        elif self.builtin_keyword:
            create_kwargs["keywords"] = [self.builtin_keyword]
        else:
            raise EdgeRuntimeError(
                "porcupine wakeword requires KAI_WAKEWORD_KEYWORD_PATH or KAI_WAKEWORD_BUILTIN_KEYWORD"
            )

        try:
            engine = self._module.create(**create_kwargs)
        except Exception as exc:
            raise EdgeRuntimeError(f"failed to initialize porcupine wakeword backend: {exc}") from exc

        frame_length = int(engine.frame_length)
        if frame_length <= 0:
            raise EdgeRuntimeError("porcupine wakeword backend returned invalid frame length")
        sample_rate = int(engine.sample_rate)
        if sample_rate <= 0:
            raise EdgeRuntimeError("porcupine wakeword backend returned invalid sample rate")

        object.__setattr__(self, "_engine", engine)
        object.__setattr__(self, "sample_rate", sample_rate)
        object.__setattr__(self, "frame_bytes", frame_length * 2)
        object.__setattr__(self, "_frame_format", "<" + ("h" * frame_length))

    @property
    def backend_name(self) -> str:
        return "porcupine"

    def process_frame(self, *, frame: bytes) -> bool:
        if len(frame) != self.frame_bytes:
            raise EdgeRuntimeError(
                f"wakeword frame size mismatch: expected {self.frame_bytes} bytes, got {len(frame)}"
            )

        pcm = struct.unpack(self._frame_format, frame)
        keyword_index = int(self._engine.process(pcm))
        return keyword_index >= 0

    def close(self) -> None:
        delete = getattr(self._engine, "delete", None)
        if callable(delete):
            delete()


def build_wakeword_detector(*, config: EdgeConfig, logger: logging.Logger) -> WakeWordDetector:
    backend = config.wakeword_backend
    if backend != "porcupine":
        raise EdgeRuntimeError(f"unsupported wakeword backend: {backend}")

    try:
        import pvporcupine  # type: ignore[import-not-found]
    except ImportError as exc:
        raise EdgeRuntimeError(
            "pvporcupine is not installed; install runtime dependencies before using wakeword mode"
        ) from exc

    detector = PorcupineWakeWordDetector(
        access_key=config.wakeword_access_key or "",
        sensitivity=config.wakeword_sensitivity,
        keyword_path=config.wakeword_keyword_path,
        builtin_keyword=config.wakeword_builtin_keyword,
        model_path=config.wakeword_model_path,
        _module=pvporcupine,
    )
    logger.info(
        "wakeword backend active: %s sample_rate=%s frame_bytes=%s keyword=%s keyword_path=%s sensitivity=%.2f",
        detector.backend_name,
        detector.sample_rate,
        detector.frame_bytes,
        config.wakeword_builtin_keyword or "-",
        config.wakeword_keyword_path or "-",
        config.wakeword_sensitivity,
    )
    return detector
