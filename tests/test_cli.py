"""Command line arguments that need interpreting."""

import argparse

import pytest

from trackhound.cli import _rate


@pytest.fixture
def parser():
    return argparse.ArgumentParser(prog="trackhound")


class TestRate:
    @pytest.mark.parametrize("given, expected", [
        ("", 0),
        ("500K", 500 * 1024),
        ("2M", 2 * 1024 ** 2),
        ("1.5m", int(1.5 * 1024 ** 2)),
        ("1,5M", int(1.5 * 1024 ** 2)),
        ("800", 800),
        ("2MB/s", 2 * 1024 ** 2),
    ])
    def test_human_speeds(self, given, expected, parser):
        assert _rate(given, parser) == expected

    @pytest.mark.parametrize("given", ["fast", "-2M", "2 megabytes"])
    def test_nonsense_stops_the_program(self, given, parser):
        with pytest.raises(SystemExit):
            _rate(given, parser)
