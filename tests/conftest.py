"""What every test shares."""

import math
import sys

import pytest

from sonicache.engine import sources


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
    # macOS keeps it under the home folder, which no variable of its own moves
    if sys.platform == "darwin":
        monkeypatch.setenv("HOME", str(folder))


@pytest.fixture(autouse=True)
def wifi_on_this_computer_only(monkeypatch):
    """The Wi-Fi server in tests listens on 127.0.0.1: on every address Windows would ask about the firewall."""
    from sonicache import subsonic

    monkeypatch.setattr(subsonic, "HOST", "127.0.0.1")


@pytest.fixture(autouse=True)
def russian():
    """The tests read Russian messages; a runner with an English locale must not change that."""
    from sonicache.engine.i18n import set_language

    set_language("ru")
    yield
    set_language("ru")
