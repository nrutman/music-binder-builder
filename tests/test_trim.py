"""Tests for the trailing-chrome-page trim heuristic.

The heuristic walks pages from the back; a page is "chrome-only" if every
line on it (after normalizing case and digits) also appears on an earlier
page AND the unique residual is under 5 words. We test it against fake
PdfReader objects so we don't need actual PDFs.
"""

from __future__ import annotations

from dataclasses import dataclass

from build_binder import effective_page_count

# ---------------------------------------------------------------------------
# Minimal fakes: effective_page_count only needs `len(reader.pages)` and
# `page.extract_text()` on each page.
# ---------------------------------------------------------------------------


@dataclass
class FakePage:
    text: str

    def extract_text(self) -> str:
        return self.text


class FakeReader:
    def __init__(self, page_texts: list[str]) -> None:
        self.pages = [FakePage(t) for t in page_texts]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_single_page_returns_one_no_trim():
    r = FakeReader(["whatever\nthis page\nis fine"])
    assert effective_page_count(r) == (1, 0)


def test_two_distinct_pages_keeps_both():
    r = FakeReader(
        [
            "Header text\nVerse 1 content here\nThis is the song",
            "Header text\nVerse 2 totally different lyrics\nLast line",
        ]
    )
    assert effective_page_count(r) == (2, 0)


def test_trailing_chrome_page_is_trimmed():
    # Page 2 only repeats header/footer-only content from page 1 plus the
    # word "two" instead of "one" in the page number. Should be trimmed.
    r = FakeReader(
        [
            "Providence Church Page 1 of 2\nHOLY HOLY HOLY\nVerse content here\n© Public Domain\nCCLI #1210714",
            "Providence Church Page 2 of 2\nHOLY HOLY HOLY\n© Public Domain\nCCLI #1210714",
        ]
    )
    eff, trimmed = effective_page_count(r)
    assert eff == 1
    assert trimmed == 1


def test_chrome_trim_normalizes_digits():
    # "Page 1 of 2" vs "Page 2 of 2" only differ in digits. After
    # digit-normalization both become "page # of #" and the trailing page
    # has no unique content.
    r = FakeReader(
        [
            "Title Page 1 of 2\nReal verse content with many words and lines and stuff",
            "Title Page 2 of 2",
        ]
    )
    eff, trimmed = effective_page_count(r)
    assert eff == 1
    assert trimmed == 1


def test_real_content_on_last_page_is_not_trimmed():
    # The last page has substantial unique content; must keep it.
    r = FakeReader(
        [
            "Header\nVerse 1 lyrics here many words distinct content",
            "Header\nVerse 2 different lyrics not seen before more unique content here",
        ]
    )
    assert effective_page_count(r) == (2, 0)


def test_first_page_is_never_trimmed_even_if_only_chrome():
    # Edge case: a 1-page PDF where the page happens to have very little
    # content. We must not return 0 pages — first page is always kept.
    r = FakeReader([""])
    eff, trimmed = effective_page_count(r)
    assert eff == 1
    assert trimmed == 0


def test_multiple_trailing_chrome_pages_all_trimmed():
    # Three pages where pages 2 and 3 are both chrome-only.
    r = FakeReader(
        [
            "Header text\nReal content with lots of unique words and lines",
            "Header text",
            "Header text",
        ]
    )
    eff, trimmed = effective_page_count(r)
    assert eff == 1
    assert trimmed == 2


def test_chrome_trim_threshold_five_words():
    # A trailing page with exactly 5 unique words must be kept; 4 should be
    # trimmed. Documents the boundary.
    base = "Repeating header line shared between pages"
    four_unique = FakeReader([base + "\nverse content here", base + "\none two three four"])
    five_unique = FakeReader([base + "\nverse content here", base + "\none two three four five"])
    assert effective_page_count(four_unique) == (1, 1)
    assert effective_page_count(five_unique) == (2, 0)
