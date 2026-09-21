"""What every test shares."""

import math

import pytest

from trackhound.engine import sources


@pytest.fixture(autouse=True)
def no_isrc_lookups(monkeypatch):
    """Downloads in tests look up no ISRC on the real Deezer: the lookups rest
    as if it had just failed to answer. The ISRC tests wake them up."""
    monkeypatch.setitem(sources._isrc_clock, "rest_until", math.inf)
