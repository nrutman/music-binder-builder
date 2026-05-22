"""Tests for the fuzzy title→filename matching used by the local `resolve`
subcommand.

The default threshold is 0.75. Tests pin the behavior at this threshold so
tuning the threshold later doesn't silently break expectations elsewhere.
"""

from __future__ import annotations

import pytest
from build_binder import normalize, score_filename

# ---------------------------------------------------------------------------
# normalize
# ---------------------------------------------------------------------------


def test_normalize_lowercases():
    assert normalize("Amazing Grace") == "amazing grace"


def test_normalize_strips_doc_extension():
    assert normalize("Amazing Grace.doc") == "amazing grace"
    assert normalize("Amazing Grace.docx") == "amazing grace"


def test_normalize_collapses_punctuation_and_whitespace():
    assert normalize("It's   Your-name.docx") == "it s your name"


def test_normalize_preserves_internal_digits():
    # Digits are alphanumeric so they stay (only non-word becomes space).
    assert normalize("Psalm 23.docx") == "psalm 23"


# ---------------------------------------------------------------------------
# score_filename
# ---------------------------------------------------------------------------


def test_exact_match_scores_1_0():
    assert score_filename("Amazing Grace", "Amazing Grace.docx") == pytest.approx(1.0)


def test_capo_variant_is_still_a_strong_match():
    # The whole point of stripping parentheticals before scoring: "Amazing
    # Grace" should still match "Amazing Grace (Capo 3).docx" highly so
    # both candidates surface for the user to disambiguate.
    s = score_filename("Amazing Grace", "Amazing Grace (Capo 3).docx")
    assert s >= 0.9


def test_substring_query_gets_the_0_9_bonus():
    # Querying "Grace" against "Amazing Grace.docx" — short query is fully
    # contained in the filename → substring bonus → at least 0.9.
    s = score_filename("Grace", "Amazing Grace.docx")
    assert s >= 0.9


def test_partial_title_matches_multiple_candidates_above_threshold():
    # "How Great" must match both "How Great Thou Art" and "How Great Is
    # Our God" above the 0.75 default threshold (this is how the agent
    # surfaces ambiguity to the user).
    threshold = 0.75
    assert score_filename("How Great", "How Great Thou Art.docx") >= threshold
    assert score_filename("How Great", "How Great Is Our God.docx") >= threshold


def test_unrelated_song_scores_well_below_threshold():
    # No shared content → score should be far below the 0.75 default.
    assert score_filename("Amazing Grace", "Holy Holy Holy.docx") < 0.5


def test_one_distinct_word_doesnt_pass_threshold():
    # "Amazing Grace" vs "Grace Like Rain" share one word but the overall
    # similarity should land below 0.75 — otherwise we'd surface
    # too-loosely-related songs as candidates.
    assert score_filename("Amazing Grace", "Grace Like Rain.docx") < 0.75


def test_case_and_punctuation_dont_matter():
    a = score_filename("Amazing Grace", "Amazing Grace.docx")
    b = score_filename("amazing grace!!!", "AMAZING-GRACE.docx")
    # Both should be effectively perfect matches.
    assert a >= 0.95
    assert b >= 0.95


def test_extension_independent():
    # Same title with .doc vs .docx should score identically.
    a = score_filename("Amazing Grace", "Amazing Grace.doc")
    b = score_filename("Amazing Grace", "Amazing Grace.docx")
    assert a == b
