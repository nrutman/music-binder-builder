"""Tests for the --pick argument parser used by pco-resolve / pco-build."""

from __future__ import annotations

import pytest
from build_binder import _parse_picks


def test_no_picks_returns_empty_dict():
    assert _parse_picks(None) == {}
    assert _parse_picks([]) == {}


def test_single_pick():
    assert _parse_picks(["123=456"]) == {"123": "456"}


def test_multiple_picks():
    assert _parse_picks(["123=456", "789=012"]) == {"123": "456", "789": "012"}


def test_whitespace_around_values_stripped():
    assert _parse_picks(["  123  =  456  "]) == {"123": "456"}


def test_later_pick_overrides_earlier_for_same_song():
    assert _parse_picks(["123=456", "123=999"]) == {"123": "999"}


def test_bad_format_exits():
    with pytest.raises(SystemExit) as exc:
        _parse_picks(["not_a_pick"])
    assert exc.value.code == 5


def test_empty_side_exits():
    with pytest.raises(SystemExit) as exc:
        _parse_picks(["=456"])
    assert exc.value.code == 5

    with pytest.raises(SystemExit) as exc:
        _parse_picks(["123="])
    assert exc.value.code == 5
