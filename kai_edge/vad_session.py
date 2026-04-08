from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Deque


def milliseconds_to_frames(duration_ms: int, frame_ms: int) -> int:
    if duration_ms <= 0:
        return 0
    return (duration_ms + frame_ms - 1) // frame_ms


@dataclass(frozen=True)
class UtteranceDecision:
    accepted: bool
    reason: str
    stop_reason: str
    utterance_ms: int
    speech_ms: int
    frames: tuple[bytes, ...]


class UtteranceCollector:
    def __init__(
        self,
        *,
        frame_ms: int,
        pre_roll_ms: int,
        min_speech_ms: int,
        trailing_silence_ms: int,
        max_utterance_ms: int,
    ) -> None:
        self._frame_ms = frame_ms
        self._pre_roll_frames = milliseconds_to_frames(pre_roll_ms, frame_ms)
        self._min_speech_frames = milliseconds_to_frames(min_speech_ms, frame_ms)
        self._trailing_silence_frames = milliseconds_to_frames(trailing_silence_ms, frame_ms)
        self._max_utterance_frames = milliseconds_to_frames(max_utterance_ms, frame_ms)
        self._pre_roll: Deque[tuple[bytes, bool]] = deque(maxlen=max(1, self._pre_roll_frames))
        self._recording = False
        self._segment_frames: list[bytes] = []
        self._speech_frames = 0
        self._silence_frames = 0

    @property
    def is_recording(self) -> bool:
        return self._recording

    def reset(self) -> None:
        self._pre_roll.clear()
        self._recording = False
        self._segment_frames = []
        self._speech_frames = 0
        self._silence_frames = 0

    def consume_frame(self, *, frame: bytes, is_speech: bool) -> tuple[bool, UtteranceDecision | None]:
        speech_start = False

        if self._recording:
            self._segment_frames.append(frame)
            if is_speech:
                self._speech_frames += 1
                self._silence_frames = 0
            else:
                self._silence_frames += 1

            if len(self._segment_frames) >= self._max_utterance_frames:
                return speech_start, self._finish(stop_reason="max_duration")
            if self._silence_frames >= self._trailing_silence_frames:
                return speech_start, self._finish(stop_reason="trailing_silence")
            return speech_start, None

        self._pre_roll.append((frame, is_speech))
        if not is_speech:
            return speech_start, None

        speech_start = True
        self._recording = True
        if self._pre_roll_frames > 0:
            self._segment_frames = [saved_frame for saved_frame, _ in self._pre_roll]
            self._speech_frames = sum(1 for _, saved_is_speech in self._pre_roll if saved_is_speech)
        else:
            self._segment_frames = [frame]
            self._speech_frames = 1
        self._silence_frames = 0

        if len(self._segment_frames) >= self._max_utterance_frames:
            return speech_start, self._finish(stop_reason="max_duration")
        return speech_start, None

    def _finish(self, *, stop_reason: str) -> UtteranceDecision:
        frames = tuple(self._segment_frames)
        utterance_ms = len(frames) * self._frame_ms
        speech_ms = self._speech_frames * self._frame_ms
        accepted = self._speech_frames >= self._min_speech_frames
        reason = "accepted" if accepted else "speech_too_short"
        decision = UtteranceDecision(
            accepted=accepted,
            reason=reason,
            stop_reason=stop_reason,
            utterance_ms=utterance_ms,
            speech_ms=speech_ms,
            frames=frames,
        )
        self.reset()
        return decision
