"""
Master clock and tempo map.

Clock: one anchor (beat N happened at wall time T) plus the current BPM. Every
part thread computes absolute timestamps from the same anchor and sleeps until
them, so parts stay locked together and a tempo change moves all of them at once.

TempoMap: the offline equivalent, used for MIDI / WAV export - converts beats
to seconds given the tempo marks in the piece.
"""
from __future__ import annotations

import threading
import time
from typing import List, Tuple


class Clock:
    POLL = 0.02   # max sleep slice so tempo changes are picked up promptly

    def __init__(self, bpm: float = 120.0):
        self._bpm = float(bpm)
        self._lock = threading.RLock()
        self._ref_beat = 0.0
        self._ref_time = 0.0
        self.running = threading.Event()

    def start(self) -> None:
        with self._lock:
            self._ref_beat, self._ref_time = 0.0, time.perf_counter()
            self.running.set()

    def stop(self) -> None:
        self.running.clear()

    @property
    def bpm(self) -> float:
        return self._bpm

    @bpm.setter
    def bpm(self, value: float) -> None:
        with self._lock:                      # re-anchor at *now* so nothing jumps
            if self.running.is_set():
                self._ref_beat, self._ref_time = self.now_beat(), time.perf_counter()
            self._bpm = float(value)

    def now_beat(self) -> float:
        with self._lock:
            if not self.running.is_set():
                return 0.0
            return self._ref_beat + (time.perf_counter() - self._ref_time) * self._bpm / 60.0

    def time_of(self, beat: float) -> float:
        with self._lock:
            return self._ref_time + (beat - self._ref_beat) * 60.0 / self._bpm

    def wait_until(self, beat: float) -> bool:
        """Block until *beat*. Returns False if the clock was stopped meanwhile."""
        while True:
            if not self.running.is_set():
                return False
            remaining = self.time_of(beat) - time.perf_counter()
            if remaining <= 0:
                return True
            time.sleep(min(remaining, self.POLL))


class TempoMap:
    """Beats -> seconds for a fixed list of (beat, bpm) tempo marks."""

    def __init__(self, initial_bpm: float, marks: List[Tuple[float, float]] = ()):
        segs = {0.0: float(initial_bpm)}
        for beat, bpm in marks:
            segs[float(beat)] = float(bpm)
        self.marks = sorted(segs.items())         # [(beat, bpm), ...] starting at 0

    def seconds_at(self, beat: float) -> float:
        t = 0.0
        for i, (b0, bpm) in enumerate(self.marks):
            b1 = self.marks[i + 1][0] if i + 1 < len(self.marks) else float("inf")
            if beat <= b0:
                break
            t += (min(beat, b1) - b0) * 60.0 / bpm
        return t
