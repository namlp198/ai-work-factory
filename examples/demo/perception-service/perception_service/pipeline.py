"""Camera pipeline model used by the demo."""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class CameraPipeline:
    name: str
    expected_fps: float = 30.0
    running: bool = False
    frame_timestamps: list[float] = field(default_factory=list)

    def start(self) -> None:
        self.running = True

    def stop(self) -> None:
        self.running = False

    def on_frame(self, timestamp: float | None = None) -> None:
        self.frame_timestamps.append(time.monotonic() if timestamp is None else timestamp)
        self.frame_timestamps = self.frame_timestamps[-120:]

    def measured_fps(self) -> float:
        if len(self.frame_timestamps) < 2:
            return 0.0
        span = self.frame_timestamps[-1] - self.frame_timestamps[0]
        return (len(self.frame_timestamps) - 1) / span if span > 0 else 0.0
