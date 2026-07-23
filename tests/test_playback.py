"""PlaybackClock tests -- pure timing, no display, microseconds to run."""

from __future__ import annotations

from giflite.core.playback import PlaybackClock


def advance(clock: PlaybackClock, dt_ms: float, steps: int) -> list[int]:
    """Tick `steps` times and record the index after each."""
    return [clock.tick(dt_ms) for _ in range(steps)]


class TestForward:
    def test_stays_put_until_a_frame_elapses(self):
        clock = PlaybackClock([100, 100, 100])
        assert clock.tick(50) == 0
        assert clock.tick(40) == 0  # 90ms total, still frame 0
        assert clock.tick(20) == 1  # 110ms, crossed into frame 1

    def test_advances_one_frame_per_duration(self):
        clock = PlaybackClock([100, 100, 100, 100])
        assert advance(clock, 100, 3) == [1, 2, 3]

    def test_respects_per_frame_durations(self):
        # frame 0 is long, frame 1 is short
        clock = PlaybackClock([300, 50, 50])
        assert clock.tick(100) == 0
        assert clock.tick(100) == 0
        assert clock.tick(100) == 1  # 300ms crossed
        assert clock.tick(50) == 2  # 50ms frame

    def test_a_large_dt_crosses_multiple_frames_at_once(self):
        clock = PlaybackClock([100, 100, 100, 100, 100])
        # 250ms from a standing start -> frames 0,1 consumed, land on 2
        assert clock.tick(250) == 2

    def test_fractional_time_carries_between_ticks(self):
        clock = PlaybackClock([100, 100, 100])
        assert clock.tick(60) == 0
        assert clock.tick(60) == 1  # 120ms: 100 into frame1, 20 carried
        assert clock.tick(85) == 2  # 20+85=105 >= 100


class TestLooping:
    def test_loop_zero_wraps_forever(self):
        clock = PlaybackClock([100, 100], loop=0)
        seen = advance(clock, 100, 6)
        assert seen == [1, 0, 1, 0, 1, 0]
        assert not clock.finished

    def test_play_once_stops_on_the_last_frame(self):
        clock = PlaybackClock([100, 100, 100], loop=1)
        # advance well past the end
        result = advance(clock, 100, 6)
        assert clock.index == 2
        assert clock.finished
        assert result[-1] == 2

    def test_finite_loop_count_plays_that_many_times(self):
        clock = PlaybackClock([100, 100], loop=2)
        # 2 loops of 2 frames = 4 advances, then stop
        advance(clock, 100, 10)
        assert clock.finished
        assert clock.index == 1

    def test_a_finished_clock_ignores_further_ticks(self):
        clock = PlaybackClock([100, 100], loop=1)
        advance(clock, 100, 5)
        assert clock.finished
        idx = clock.index
        assert clock.tick(1000) == idx

    def test_restart_revives_a_finished_clock(self):
        clock = PlaybackClock([100, 100], loop=1)
        advance(clock, 100, 5)
        clock.restart()
        assert not clock.finished
        assert clock.index == 0
        assert clock.tick(100) == 1


class TestSpeed:
    def test_double_speed_advances_twice_as_fast(self):
        clock = PlaybackClock([100, 100, 100], speed=2.0)
        assert clock.tick(50) == 1  # 50ms * 2 = 100ms

    def test_zero_speed_freezes(self):
        clock = PlaybackClock([100, 100], speed=0.0)
        assert advance(clock, 1000, 3) == [0, 0, 0]

    def test_speed_can_change_mid_playback(self):
        clock = PlaybackClock([100, 100, 100, 100])
        assert clock.tick(100) == 1
        clock.set_speed(2.0)
        assert clock.tick(100) == 3  # 200ms of travel


class TestSeek:
    def test_seek_moves_the_playhead_and_clears_partial_time(self):
        clock = PlaybackClock([100, 100, 100])
        clock.tick(80)  # 80ms into frame 0
        clock.seek(2)
        assert clock.index == 2
        assert clock.tick(80) == 2  # partial time was cleared, not carried

    def test_seek_clamps(self):
        clock = PlaybackClock([100, 100])
        assert clock.seek(99) == 1
        assert clock.seek(-5) == 0


class TestSetDurations:
    def test_adopts_new_timing(self):
        clock = PlaybackClock([100, 100])
        clock.set_durations([50, 50, 50, 50])
        assert clock.tick(50) == 1

    def test_clamps_index_when_the_list_shrinks(self):
        clock = PlaybackClock([100, 100, 100, 100])
        clock.seek(3)
        clock.set_durations([100, 100])  # doc got shorter
        assert clock.index == 1

    def test_revives_a_finished_clock(self):
        clock = PlaybackClock([100], loop=1)
        clock.tick(100)
        clock.set_durations([100, 100])
        assert not clock.finished
        assert clock.tick(100) == 1


class TestDegenerate:
    def test_single_frame_never_advances(self):
        clock = PlaybackClock([100])
        assert advance(clock, 1000, 3) == [0, 0, 0]

    def test_empty_is_harmless(self):
        clock = PlaybackClock([])
        assert clock.tick(100) == 0
        assert clock.index == 0

    def test_zero_duration_is_floored_not_infinite_loop(self):
        # If a 0 slipped past the model, the clock must not spin forever.
        clock = PlaybackClock([0, 0, 0], loop=1)
        clock.tick(100)  # returns rather than hanging
        assert clock.finished
