from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import EdgeConfig


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _clamp_summary_text(value: str | None, *, max_len: int = 160) -> str:
    if value is None:
        return "-"
    compact = re.sub(r"\s+", " ", value).strip()
    if not compact:
        return "-"
    if len(compact) <= max_len:
        return compact
    return f"{compact[: max_len - 3]}..."


class StatusArtifactWriter:
    def __init__(self, *, status_path: Path, logger: logging.Logger) -> None:
        self._status_path = status_path
        self._logger = logger
        self._write_failure_logged = False

    def write(self, payload: dict[str, Any]) -> None:
        parent_dir = self._status_path.parent
        temp_path: Path | None = None
        try:
            parent_dir.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=parent_dir,
                prefix=f".{self._status_path.name}.",
                delete=False,
            ) as temp_file:
                temp_path = Path(temp_file.name)
                json.dump(payload, temp_file, sort_keys=True)
                temp_file.write("\n")
                temp_file.flush()
                os.fsync(temp_file.fileno())

            os.replace(temp_path, self._status_path)
            os.chmod(self._status_path, 0o644)
            self._write_failure_logged = False
        except OSError as exc:
            if not self._write_failure_logged:
                self._logger.warning(
                    "observability status write failed for %s: %s",
                    self._status_path,
                    exc,
                )
                self._write_failure_logged = True
        finally:
            if temp_path is not None and temp_path.exists():
                temp_path.unlink(missing_ok=True)


class EdgeObservability:
    def __init__(
        self,
        *,
        config: EdgeConfig,
        logger: logging.Logger,
        initial_state: str,
    ) -> None:
        self._logger = logger
        self._mode = config.trigger_mode
        self._vad_backend = "n/a"
        self._state = initial_state

        now = _utc_now_iso()
        self._started_at = now
        self._updated_at = now
        self._state_since = now
        self._last_transition: dict[str, str] | None = None

        self._interactions = 0
        self._accepted_utterances = 0
        self._rejected_utterances = 0
        self._error_count = 0
        self._accepted_duration_total_ms = 0
        self._last_accepted_utterance_ms: int | None = None
        self._last_rejection_reason: str | None = None
        self._last_error_summary: str | None = None
        self._rejection_reasons: Counter[str] = Counter()
        self._stop_reasons: Counter[str] = Counter()

        self._summary_interval_seconds = config.obs_summary_interval_seconds
        self._summary_interval_interactions = config.obs_summary_interval_interactions
        self._last_summary_emit_monotonic = time.monotonic()
        self._last_summary_emit_interactions = 0

        if config.obs_status_file_enabled:
            self._status_writer: StatusArtifactWriter | None = StatusArtifactWriter(
                status_path=Path(config.obs_status_file_path),
                logger=logger,
            )
        else:
            self._status_writer = None

        self._publish_status()

    def _touch(self) -> None:
        self._updated_at = _utc_now_iso()

    def _average_accepted_utterance_ms(self) -> int:
        if self._accepted_utterances <= 0:
            return 0
        return int(round(self._accepted_duration_total_ms / self._accepted_utterances))

    def _publish_status(self) -> None:
        if self._status_writer is None:
            return
        self._status_writer.write(self.snapshot())

    def set_vad_backend(self, backend_name: str) -> None:
        self._vad_backend = backend_name or "n/a"
        self._touch()
        self._publish_status()

    def record_state_transition(self, *, old_state: str, new_state: str) -> None:
        if old_state == new_state:
            return
        transition_time = _utc_now_iso()
        self._state = new_state
        self._state_since = transition_time
        self._updated_at = transition_time
        self._last_transition = {
            "from": old_state,
            "to": new_state,
            "at": transition_time,
        }
        self._publish_status()

    def record_interaction_started(self) -> None:
        self._interactions += 1
        self._touch()
        self._publish_status()

    def record_accepted_utterance(self, *, utterance_ms: int, stop_reason: str | None) -> None:
        bounded_duration = max(0, utterance_ms)
        self._accepted_utterances += 1
        self._accepted_duration_total_ms += bounded_duration
        self._last_accepted_utterance_ms = bounded_duration
        if stop_reason:
            self._stop_reasons[stop_reason] += 1
        self._touch()
        self._publish_status()

    def record_rejected_utterance(self, *, reason: str, stop_reason: str | None) -> None:
        bounded_reason = reason or "unknown"
        self._rejected_utterances += 1
        self._rejection_reasons[bounded_reason] += 1
        self._last_rejection_reason = bounded_reason
        if stop_reason:
            self._stop_reasons[stop_reason] += 1
        self._touch()
        self._publish_status()

    def record_error(self, *, summary: str) -> None:
        self._error_count += 1
        self._last_error_summary = _clamp_summary_text(summary)
        self._touch()
        self._publish_status()

    def snapshot(self) -> dict[str, Any]:
        return {
            "started_at": self._started_at,
            "updated_at": self._updated_at,
            "mode": self._mode,
            "state": self._state,
            "state_since": self._state_since,
            "vad_backend": self._vad_backend,
            "last_transition": self._last_transition,
            "counters": {
                "interactions": self._interactions,
                "accepted_utterances": self._accepted_utterances,
                "rejected_utterances": self._rejected_utterances,
                "errors": self._error_count,
                "avg_accepted_utterance_ms": self._average_accepted_utterance_ms(),
                "last_accepted_utterance_ms": self._last_accepted_utterance_ms,
                "last_rejection_reason": self._last_rejection_reason,
                "last_error_summary": self._last_error_summary,
                "rejection_reasons": dict(sorted(self._rejection_reasons.items())),
                "stop_reasons": dict(sorted(self._stop_reasons.items())),
            },
        }

    def _summary_due(self, *, now_monotonic: float) -> bool:
        if self._summary_interval_interactions > 0:
            interactions_delta = self._interactions - self._last_summary_emit_interactions
            if interactions_delta >= self._summary_interval_interactions:
                return True

        if self._summary_interval_seconds > 0:
            elapsed = now_monotonic - self._last_summary_emit_monotonic
            if elapsed >= float(self._summary_interval_seconds):
                return True

        return False

    def emit_summary_if_due(self, *, force: bool = False, trigger: str = "periodic") -> None:
        now_monotonic = time.monotonic()
        if not force and not self._summary_due(now_monotonic=now_monotonic):
            return

        last_accepted_ms = (
            str(self._last_accepted_utterance_ms)
            if self._last_accepted_utterance_ms is not None
            else "-"
        )
        self._logger.info(
            "summary trigger=%s mode=%s backend=%s state=%s interactions=%s accepted=%s rejected=%s errors=%s avg_utterance_ms=%s last_accepted_ms=%s last_rejection=%s last_error=%s",
            trigger,
            self._mode,
            self._vad_backend,
            self._state,
            self._interactions,
            self._accepted_utterances,
            self._rejected_utterances,
            self._error_count,
            self._average_accepted_utterance_ms(),
            last_accepted_ms,
            _clamp_summary_text(self._last_rejection_reason),
            _clamp_summary_text(self._last_error_summary),
        )
        rejection_reasons = dict(sorted(self._rejection_reasons.items()))
        stop_reasons = dict(sorted(self._stop_reasons.items()))
        if rejection_reasons:
            self._logger.info("summary rejection_reasons=%s", rejection_reasons)
        if stop_reasons:
            self._logger.info("summary stop_reasons=%s", stop_reasons)

        self._last_summary_emit_monotonic = now_monotonic
        self._last_summary_emit_interactions = self._interactions
        self._touch()
        self._publish_status()
