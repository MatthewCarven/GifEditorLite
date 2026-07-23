"""PlaybackClock -- the timing logic, with no timer in it.

The frontend owns the actual timer (Tk's `after`, Qt's `QTimer`, a test's
synthetic loop) and feeds this class the elapsed milliseconds. The clock
decides which frame that lands on. Keeping it dt-driven rather than
fixed-step means a slow machine or a dropped timer tick advances by real
elapsed time instead of falling behind (ARCHITECTURE.md 10).

M1 is forward + loop only. Ping-pong is a Mode enum's worth of work reserved
for M4; speed is here because it's a single multiplier on dt and the
controller already promised `set_speed`.
"""

from __future__ import annotations

from typing import Sequence

# A frame can never take zero time or the boundary-crossing loop below would
# spin forever. The model floors durations at 20ms, but set_durations is
# public, so guard here too.
_MIN_FRAME_MS = 1

# After a stall (window dragged, machine asleep) dt can be enormous. Advancing
# through thousands of frames in one tick is never what anyone wants, so the
# frontend caps dt before calling -- this constant documents the intent and is
# used by the frontend.
MAX_TICK_MS = 250


class PlaybackClock:
    def __init__(
        self,
        durations: Sequence[int] = (),
        loop: int = 0,
        speed: float = 1.0,
    ) -> None:
        self._durations: list[int] = [max(d, _MIN_FRAME_MS) for d in durations]
        self.loop = loop  # 0 == forever; N == play the sequence N times
        self._speed = max(float(speed), 0.0)
        self._index = 0
        self._accum_ms = 0.0
        self._loops_done = 0
        self._finished = False
        self.pingpong = False
        self._direction = 1  # +1 forward, -1 backward (only used in pingpong)

    # ---- configuration ---------------------------------------------------

    def set_durations(self, durations: Sequence[int]) -> None:
        """Adopt a new timing list, e.g. after an edit changed the frames.

        Called by the controller on every doc_changed. The playhead is kept in
        range and the partial-frame accumulator is reset -- the frame under the
        playhead may now have a different length, so carrying the old fraction
        forward would be meaningless.
        """
        self._durations = [max(d, _MIN_FRAME_MS) for d in durations]
        self._accum_ms = 0.0
        self._finished = False
        self._loops_done = 0
        self._direction = 1
        self._index = self._clamp(self._index)

    def set_pingpong(self, enabled: bool) -> None:
        """Bounce back and forth instead of looping. Resets direction so a
        toggle mid-play starts cleanly forward."""
        self.pingpong = bool(enabled)
        self._direction = 1

    @property
    def speed(self) -> float:
        return self._speed

    def set_speed(self, factor: float) -> None:
        self._speed = max(float(factor), 0.0)

    @property
    def index(self) -> int:
        return self._index

    @property
    def finished(self) -> bool:
        """True once a finite loop count has played out. Forever-loops never
        finish; a play() after finishing restarts from the top."""
        return self._finished

    # ---- position --------------------------------------------------------

    def seek(self, index: int) -> int:
        """Jump the playhead (scrubbing). Clears any partial-frame time."""
        self._index = self._clamp(index)
        self._accum_ms = 0.0
        self._finished = False
        return self._index

    def restart(self) -> None:
        self._index = 0
        self._accum_ms = 0.0
        self._loops_done = 0
        self._finished = False

    def tick(self, dt_ms: float) -> int:
        """Advance by dt milliseconds and return the resulting frame index.

        Crosses as many frame boundaries as the elapsed time covers, so a big
        dt is handled correctly rather than skipping at most one frame.
        """
        if self._finished or len(self._durations) <= 1:
            # One frame (or none) has nowhere to advance to. A single-frame GIF
            # just sits on frame 0, which is correct.
            return self._index

        self._accum_ms += dt_ms * self._speed
        n = len(self._durations)

        # Guard against a pathological speed/dt producing an unbounded loop:
        # the accumulator strictly decreases each iteration because every
        # duration is >= _MIN_FRAME_MS.
        while self._accum_ms >= self._durations[self._index]:
            self._accum_ms -= self._durations[self._index]
            if self.pingpong:
                self._index = self._step_pingpong(n)
            else:
                nxt = self._index + 1
                if nxt >= n:
                    self._loops_done += 1
                    if self.loop != 0 and self._loops_done >= self.loop:
                        self._index = n - 1
                        self._accum_ms = 0.0
                        self._finished = True
                        break
                    nxt = 0
                self._index = nxt

        return self._index

    def _step_pingpong(self, n: int) -> int:
        """One frame's advance in bounce mode, reflecting off either end.

        Pingpong plays forever (loop count is a forward-mode concept), so it
        never sets `finished`.
        """
        nxt = self._index + self._direction
        if nxt >= n:  # bounced off the end
            self._direction = -1
            nxt = n - 2
        elif nxt < 0:  # bounced off the start
            self._direction = 1
            nxt = 1
        return nxt

    # ---- internals -------------------------------------------------------

    def _clamp(self, index: int) -> int:
        if not self._durations:
            return 0
        return max(0, min(int(index), len(self._durations) - 1))
