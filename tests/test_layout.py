"""Tests for the pure layout algorithm — the spread math that decides where
each song lands and when blank pages need to be inserted.

These are the highest-value tests in the repo: a silent off-by-one here
would produce wrong page placements with no obvious error in the script's
output.
"""

from __future__ import annotations

import pytest
from build_binder import LayoutEntry, plan_layout


def test_empty_setlist():
    assert plan_layout([]) == []


def test_single_one_page_song():
    # Page 1 stands alone; the song goes there.
    assert plan_layout([1]) == [LayoutEntry(blank_before=False, binder_start=1, binder_end=1)]


def test_single_two_page_song():
    # A 2-page song can't start on page 1 (which is alone); insert a blank
    # and put the song on the 2-3 spread.
    assert plan_layout([2]) == [LayoutEntry(blank_before=True, binder_start=2, binder_end=3)]


def test_two_one_page_songs_share_a_spread():
    # Song A on page 1 (alone). Song B on page 2 (start of 2-3 spread).
    # No blanks needed.
    assert plan_layout([1, 1]) == [
        LayoutEntry(blank_before=False, binder_start=1, binder_end=1),
        LayoutEntry(blank_before=False, binder_start=2, binder_end=2),
    ]


def test_one_page_then_two_page():
    # Song A on page 1 (alone). Song B is 2 pages; position is now 2 (even),
    # so no blank needed; B lands on 2-3.
    assert plan_layout([1, 2]) == [
        LayoutEntry(blank_before=False, binder_start=1, binder_end=1),
        LayoutEntry(blank_before=False, binder_start=2, binder_end=3),
    ]


def test_two_page_then_one_page():
    # A: 2-page → blank before → on 2-3. Position is 4.
    # B: 1-page → on page 4. Position is 5.
    assert plan_layout([2, 1]) == [
        LayoutEntry(blank_before=True, binder_start=2, binder_end=3),
        LayoutEntry(blank_before=False, binder_start=4, binder_end=4),
    ]


def test_user_example_from_design():
    # From the original requirements: [2-page A, 1-page B, 2-page C, 1-page D, 1-page E]
    # Expected layout:
    #   page 1 blank, pages 2-3 A, page 4 B, page 5 blank, pages 6-7 C, page 8 D, page 9 E
    result = plan_layout([2, 1, 2, 1, 1])
    assert result == [
        LayoutEntry(blank_before=True, binder_start=2, binder_end=3),
        LayoutEntry(blank_before=False, binder_start=4, binder_end=4),
        LayoutEntry(blank_before=True, binder_start=6, binder_end=7),
        LayoutEntry(blank_before=False, binder_start=8, binder_end=8),
        LayoutEntry(blank_before=False, binder_start=9, binder_end=9),
    ]


def test_all_two_page_songs_pack_perfectly():
    # 4 two-page songs with a blank in front of the first one → 9 pages total,
    # one song per spread.
    result = plan_layout([2, 2, 2, 2])
    assert result == [
        LayoutEntry(blank_before=True, binder_start=2, binder_end=3),
        LayoutEntry(blank_before=False, binder_start=4, binder_end=5),
        LayoutEntry(blank_before=False, binder_start=6, binder_end=7),
        LayoutEntry(blank_before=False, binder_start=8, binder_end=9),
    ]


def test_all_one_page_songs_no_blanks_ever():
    # 1-page songs never need a blank.
    result = plan_layout([1] * 5)
    assert all(not entry.blank_before for entry in result)
    assert [(e.binder_start, e.binder_end) for e in result] == [
        (1, 1),
        (2, 2),
        (3, 3),
        (4, 4),
        (5, 5),
    ]


def test_two_page_songs_never_cross_a_spread():
    # Property check: for any input mixing 1s and 2s, every 2-page song's
    # binder_start must be even (so it occupies a single spread).
    inputs = [
        [2, 1, 2, 1, 2],
        [1, 2, 1, 2, 1, 2],
        [2, 2, 1, 1, 2],
        [1, 1, 1, 2],
    ]
    for page_counts in inputs:
        for pc, entry in zip(page_counts, plan_layout(page_counts), strict=True):
            if pc == 2:
                assert entry.binder_start % 2 == 0, (
                    f"two-page song landed on odd page {entry.binder_start} in {page_counts}"
                )
                assert entry.binder_end == entry.binder_start + 1


def test_order_is_preserved():
    # Output entries must correspond 1:1 with input order — never reorder.
    page_counts = [2, 1, 2, 1, 1, 2]
    result = plan_layout(page_counts)
    assert len(result) == len(page_counts)
    # Each entry's page span must match its input's page count.
    for pc, entry in zip(page_counts, result, strict=True):
        assert entry.binder_end - entry.binder_start + 1 == pc


@pytest.mark.parametrize(
    "page_counts,expected_total_binder_pages",
    [
        ([1], 1),
        ([2], 3),  # blank + 2 pages
        ([1, 1], 2),
        ([1, 2], 3),  # no blank needed
        ([2, 1], 4),  # blank + 2 + 1
        ([2, 1, 2, 1, 1], 9),  # user example
    ],
)
def test_total_binder_page_count(page_counts, expected_total_binder_pages):
    result = plan_layout(page_counts)
    assert result[-1].binder_end == expected_total_binder_pages
