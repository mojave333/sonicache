"""What every test shares."""

import math

import pytest

from trackhound.engine import sources


@pytest.fixture(autouse=True)
def no_isrc_lookups(monkeypatch):
    """Downloads in tests look up no ISRC on the real Deezer: the lookups rest
    as if it had just failed to answer. The ISRC tests wake them up."""
    monkeypatch.setitem(sources._isrc_clock, "rest_until", math.inf)


@pytest.fixture(autouse=True)
def own_data_dir(tmp_path_factory, monkeypatch):
    """What the program writes for itself (history, the library's cache) goes
    to a folder of the test's own, never to the real one. The places it looks
    are moved, not the lookup, so the tests of the lookup itself still see it."""
    folder = tmp_path_factory.mktemp("data")
    monkeypatch.setenv("LOCALAPPDATA", str(folder))
    monkeypatch.setenv("XDG_DATA_HOME", str(folder))
