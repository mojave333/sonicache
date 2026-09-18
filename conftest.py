"""Lets the tests import the package straight from the checkout."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))


@pytest.fixture(autouse=True)
def russian():
    """The tests read Russian messages; a runner with an English locale must not change that."""
    from trackhound.engine.i18n import set_language

    set_language("ru")
    yield
    set_language("ru")
