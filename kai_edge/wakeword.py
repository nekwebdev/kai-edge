from __future__ import annotations

import logging
import struct
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from numbers import Real
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


def _coerce_openwakeword_score(value: Any) -> float | None:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, Real):
        return float(value)
    if isinstance(value, Mapping):
        best: float | None = None
        for nested_value in value.values():
            candidate = _coerce_openwakeword_score(nested_value)
            if candidate is None:
                continue
            if best is None or candidate > best:
                best = candidate
        return best
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        best = None
        for nested_value in value:
            candidate = _coerce_openwakeword_score(nested_value)
            if candidate is None:
                continue
            if best is None or candidate > best:
                best = candidate
        return best
    return None


def _create_openwakeword_engine(*, model_class: Any, model_paths: tuple[str, ...]) -> Any:
    if model_paths:
        list_paths = list(model_paths)
        candidate_kwargs: list[dict[str, Any]] = [
            {"wakeword_models": list_paths},
            {"model_paths": list_paths},
            {"wakeword_model_paths": list_paths},
        ]
        if len(list_paths) == 1:
            candidate_kwargs.extend(
                (
                    {"model_path": list_paths[0]},
                    {"wakeword_model_path": list_paths[0]},
                )
            )
    else:
        candidate_kwargs = [{}]

    first_error: Exception | None = None
    for kwargs in candidate_kwargs:
        try:
            return model_class(**kwargs)
        except TypeError:
            continue
        except Exception as exc:
            if first_error is None:
                first_error = exc

    if first_error is not None:
        raise EdgeRuntimeError(
            f"failed to initialize openwakeword wakeword backend: {first_error}"
        ) from first_error

    if model_paths:
        raise EdgeRuntimeError(
            "failed to initialize openwakeword wakeword backend with "
            "KAI_WAKEWORD_OPENWAKEWORD_MODEL_PATHS"
        )
    raise EdgeRuntimeError("failed to initialize openwakeword wakeword backend")


@dataclass(frozen=True)
class OpenWakeWordDetector(WakeWordDetector):
    threshold: float
    model_paths: tuple[str, ...]
    _model_class: Any
    _numpy: Any

    def __post_init__(self) -> None:
        engine = _create_openwakeword_engine(
            model_class=self._model_class,
            model_paths=self.model_paths,
        )

        sample_rate = int(getattr(engine, "sample_rate", 16000))
        if sample_rate <= 0:
            raise EdgeRuntimeError("openwakeword backend returned invalid sample rate")

        frame_length = int(getattr(engine, "frame_length", 1280))
        if frame_length <= 0:
            raise EdgeRuntimeError("openwakeword backend returned invalid frame length")

        object.__setattr__(self, "_engine", engine)
        object.__setattr__(self, "sample_rate", sample_rate)
        object.__setattr__(self, "frame_bytes", frame_length * 2)

    @property
    def backend_name(self) -> str:
        return "openwakeword"

    def process_frame(self, *, frame: bytes) -> bool:
        if len(frame) != self.frame_bytes:
            raise EdgeRuntimeError(
                f"wakeword frame size mismatch: expected {self.frame_bytes} bytes, got {len(frame)}"
            )

        pcm = self._numpy.frombuffer(frame, dtype=self._numpy.int16)
        try:
            prediction = self._engine.predict(pcm)
        except Exception as exc:
            raise EdgeRuntimeError(f"openwakeword frame processing failed: {exc}") from exc

        score = _coerce_openwakeword_score(prediction)
        if score is None:
            return False
        return score >= self.threshold

    def close(self) -> None:
        close = getattr(self._engine, "close", None)
        if callable(close):
            close()


def _format_openwakeword_model_paths(model_paths: tuple[str, ...]) -> str:
    if not model_paths:
        return "-"
    return ",".join(model_paths)


def build_wakeword_detector(*, config: EdgeConfig, logger: logging.Logger) -> WakeWordDetector:
    backend = config.wakeword_backend
    if backend == "porcupine":
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

    if backend == "openwakeword":
        try:
            from openwakeword.model import Model as OpenWakeWordModel  # type: ignore[import-not-found]
        except ImportError as exc:
            raise EdgeRuntimeError(
                "openwakeword is not installed; install runtime dependencies before using "
                "KAI_WAKEWORD_BACKEND=openwakeword"
            ) from exc

        try:
            import numpy as np  # type: ignore[import-not-found]
        except ImportError as exc:
            raise EdgeRuntimeError(
                "numpy is required by openwakeword; install runtime dependencies before "
                "using KAI_WAKEWORD_BACKEND=openwakeword"
            ) from exc

        detector = OpenWakeWordDetector(
            threshold=config.wakeword_openwakeword_threshold,
            model_paths=config.wakeword_openwakeword_model_paths,
            _model_class=OpenWakeWordModel,
            _numpy=np,
        )
        logger.info(
            "wakeword backend active: %s sample_rate=%s frame_bytes=%s model_paths=%s threshold=%.2f",
            detector.backend_name,
            detector.sample_rate,
            detector.frame_bytes,
            _format_openwakeword_model_paths(config.wakeword_openwakeword_model_paths),
            config.wakeword_openwakeword_threshold,
        )
        return detector

    raise EdgeRuntimeError(f"unsupported wakeword backend: {backend}")
