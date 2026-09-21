"""A track's progress as one smooth number, and the time it still needs."""

import pytest

from trackhound.engine.progress import Clock, Estimates


class Time:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now

    def pass_(self, seconds):
        self.now += seconds


@pytest.fixture
def time():
    return Time()


def clock(time, duration=240):
    return Clock(duration, estimates=Estimates(), now=time)


def test_the_silent_steps_move_the_bar_too(time):
    """yt-dlp preparing the stream reports nothing, and used to hold the bar at 0."""
    track = clock(time)
    time.pass_(1)
    track.enter("prepare", "song")
    shown = []
    for _ in range(6):
        time.pass_(1)
        shown.append(track.percent())
    assert shown == sorted(shown) and shown[-1] > shown[0] > 0


def test_a_fast_download_does_not_leap_to_the_end(time):
    """The bytes come in under a second; the conversion after them is most of the work."""
    track = clock(time)
    track.enter("prepare", "song")
    time.pass_(6)
    track.enter("download")
    time.pass_(0.5)
    track.report(1.0, eta=0)
    assert track.percent() < 60


def test_the_conversion_counts_by_ffmpegs_own_report(time):
    track = clock(time)
    track.enter("prepare", "song")
    time.pass_(6)
    track.enter("download")
    time.pass_(1)
    track.enter("convert")
    time.pass_(5)
    track.report(0.5)
    assert track.remaining() == pytest.approx(5 + 0.6, abs=0.01)  # as long again, then the tags
    halfway = track.percent()
    time.pass_(4)
    track.report(0.9)
    assert track.percent() > halfway


def test_an_overdue_step_still_creeps_and_never_goes_back(time):
    track = clock(time)
    track.enter("prepare", "song")
    before = None
    for _ in range(40):  # far longer than the six seconds expected
        time.pass_(1)
        now = track.percent()
        assert before is None or now >= before
        before = now
    assert before < 100 and track.remaining() > 0


def test_what_a_step_took_is_learned(time):
    estimates = Estimates()
    first = Clock(240, estimates=estimates, now=time)
    first.enter("prepare", "song")
    time.pass_(2)
    first.enter("download")
    assert estimates.get("prepare:song") < 6  # moved towards the two seconds it took
    time.pass_(1)
    first.enter("convert")
    time.pass_(24)
    first.enter("finish")
    assert estimates.get("encode") > 0.05  # 24 s for 240 s of audio: slower than the first guess


def test_done_is_a_hundred_and_nothing_left(time):
    track = clock(time)
    track.done()
    assert (track.percent(), track.remaining()) == (100, 0)
