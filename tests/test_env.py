"""Tests for the `.env` / `.env.local` parser.

We deliberately don't pull in python-dotenv — the parser is ~10 lines of
custom code. These tests guard the surface area (comments, quoting,
empty-value semantics).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from build_binder import load_env_file


@pytest.fixture
def write_env(tmp_path: Path):
    def _write(content: str) -> Path:
        path = tmp_path / ".env.test"
        path.write_text(content)
        return path

    return _write


def test_missing_file_returns_empty_dict():
    assert load_env_file(Path("/nope/does/not/exist")) == {}


def test_basic_key_value(write_env):
    p = write_env("FOO=bar\nBAZ=qux\n")
    assert load_env_file(p) == {"FOO": "bar", "BAZ": "qux"}


def test_blank_lines_and_comments_ignored(write_env):
    p = write_env("# This is a comment\n\nFOO=bar\n\n  # indented comment\nBAZ=qux\n")
    assert load_env_file(p) == {"FOO": "bar", "BAZ": "qux"}


def test_empty_value_is_treated_as_unset(write_env):
    # A blank value is the .env convention for "documented but not configured"
    # — the parser should NOT include it, so .env.local can override it
    # without the .env's emptiness winning the merge.
    p = write_env("FOO=\nBAR=value\n")
    assert load_env_file(p) == {"BAR": "value"}


def test_quoted_values_stripped(write_env):
    p = write_env(
        'FOO="quoted value"\n'
        "BAR='single quoted'\n"
        'MIXED="unbalanced\n'  # only strips matching surrounding quotes
    )
    parsed = load_env_file(p)
    assert parsed["FOO"] == "quoted value"
    assert parsed["BAR"] == "single quoted"
    assert parsed["MIXED"] == '"unbalanced'  # unbalanced quotes preserved literally


def test_value_with_equals_sign_preserved(write_env):
    p = write_env("URL=https://example.com/path?a=1&b=2\n")
    assert load_env_file(p) == {"URL": "https://example.com/path?a=1&b=2"}


def test_whitespace_around_equals_stripped(write_env):
    p = write_env("  FOO  =  bar  \n")
    assert load_env_file(p) == {"FOO": "bar"}


def test_lines_without_equals_are_skipped(write_env):
    p = write_env("FOO=bar\nnonsense line\nBAR=baz\n")
    assert load_env_file(p) == {"FOO": "bar", "BAR": "baz"}


def test_env_local_can_override_env_defaults(tmp_path: Path):
    # The merge pattern used in load_config(): .env first, .env.local wins.
    env = tmp_path / ".env"
    env.write_text("FUZZY_MATCH_THRESHOLD=0.75\nCHORD_SHEETS_DIR=\n")
    local = tmp_path / ".env.local"
    local.write_text("CHORD_SHEETS_DIR=/Users/me/Music\n")

    merged = {**load_env_file(env), **load_env_file(local)}
    assert merged == {
        "FUZZY_MATCH_THRESHOLD": "0.75",
        "CHORD_SHEETS_DIR": "/Users/me/Music",
    }
