"""How far a track is, and how long it still needs, as one smooth number.

A track goes through steps of very different lengths that report very
differently: the search; yt-dlp preparing the stream, several silent seconds;
the download, usually under a second, counted in bytes; the conversion, the
longest, counted by ffmpeg; the tags and lyrics. Counting only the bytes made
the bar stand at 0, leap to 99 and stand there again.

Here each step's remaining time is estimated, from its own count where it
has one and from how long the same step took on the tracks before where it
has none, and the progress is the time spent over the time spent and left.
The estimates are learned as tracks finish, so they fit this computer and
this connection after the first few.
"""

from __future__ import annotations

import threading
import time
from typing import Callable

STEPS = ("search", "prepare", "download", "convert", "finish")
# How fast a new measurement moves an estimate: recent tracks count most
LEARNING = 0.4
# An overdue silent step is still given this share of its estimate, so the bar
# keeps creeping instead of standing still or claiming the step is over
OVERDUE_SHARE = 0.15
# How far into a step its own count is trusted over the estimate
TRUST_FROM = 0.25


class Estimates:
    """What earlier tracks taught, shared by every track of the session:
    seconds for the steps that report nothing, and the conversion's seconds
    per second of audio, re-encoding or only copying the stream."""

    def __init__(self):
        self._lock = threading.Lock()
        self._values = {
            "search": 1.5,
            "prepare:song": 6.0, "prepare:video": 6.0, "prepare:soundcloud": 2.0, "prepare:web": 3.0,
            "download": 1.5,
            "encode": 0.05,  # mp3 320 from YouTube's Opus runs at about twenty times real time
            "copy": 0.003,
            "finish": 0.6,
        }

    def get(self, key: str) -> float:
        with self._lock:
            return self._values.get(key, 3.0)

    def learn(self, key: str, value: float) -> None:
        if value <= 0:
            return
        with self._lock:
            old = self._values.get(key, value)
            self._values[key] = old + LEARNING * (value - old)


ESTIMATES = Estimates()


class Clock:
    """One track's way from the search to its tags."""

    def __init__(self, duration: float, estimates: Estimates = ESTIMATES,
                 now: Callable[[], float] = time.monotonic):
        self._now = now
        self._estimates = estimates
        self.duration = max(0.0, duration or 0.0)
        self.started = now()
        self.step = "search"
        self.step_started = self.started
        self.source = "song"
        self.encode = True  # the usual case until the stream says otherwise
        self._fraction = 0.0  # of the step, where the step counts itself
        self._eta: float | None = None
        self._shown = 0

    def enter(self, step: str, source: str | None = None) -> None:
        """The track moves on; what the step it leaves took is learned."""
        if step == self.step:
            return
        self._learn()
        if source:
            self.source = source
        self.step = step
        self.step_started = self._now()
        self._fraction, self._eta = 0.0, None

    def report(self, fraction: float, eta: float | None = None) -> None:
        """The step's own count: bytes for the download, time for the conversion."""
        self._fraction = min(1.0, max(0.0, fraction))
        self._eta = eta

    def done(self) -> None:
        self._learn()
        self.step = "done"

    def remaining(self) -> float:
        """Seconds this track still needs, by the best guess there is."""
        if self.step == "done":
            return 0.0
        index = STEPS.index(self.step)
        left = self._step_left()
        for step in STEPS[index + 1:]:
            left += self._expected(step)
        return left

    def percent(self) -> int:
        """0–99 while under way; never less than was shown before."""
        if self.step == "done":
            return 100
        spent = self._now() - self.started
        left = self.remaining()
        share = spent / (spent + left) if spent + left > 0 else 0.0
        self._shown = max(self._shown, min(99, int(share * 100)))
        return self._shown

    def _expected(self, step: str) -> float:
        if step == "prepare":
            return self._estimates.get(f"prepare:{self.source}")
        if step == "convert":
            return self.duration * self._estimates.get("encode" if self.encode else "copy") + 0.3
        return self._estimates.get(step)

    def _step_left(self) -> float:
        spent = self._now() - self.step_started
        expected = self._expected(self.step)
        guess = max(expected - spent, expected * OVERDUE_SHARE, 0.2)
        if self.step == "download" and self._eta is not None:
            return max(0.0, self._eta)
        if self.step not in ("download", "convert") or self._fraction <= 0.02:
            return guess
        # A step's first reports run slow (ffmpeg starting up), so its own count
        # takes over from the estimate gradually, fully from a quarter of the way
        counted = spent * (1 - self._fraction) / self._fraction
        trust = min(1.0, self._fraction / TRUST_FROM)
        return trust * counted + (1 - trust) * guess

    def _learn(self) -> None:
        spent = self._now() - self.step_started
        if self.step == "prepare":
            self._estimates.learn(f"prepare:{self.source}", spent)
        elif self.step == "convert":
            if self.duration:
                self._estimates.learn("encode" if self.encode else "copy", spent / self.duration)
        elif self.step in ("search", "download", "finish"):
            self._estimates.learn(self.step, spent)
