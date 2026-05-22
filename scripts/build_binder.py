#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "pypdf>=4.0.0",
# ]
# ///
"""
build_binder.py — Build a saddle-stitched chord-sheet binder PDF.

Subcommands
-----------
    resolve TITLE [TITLE ...]
        Fuzzy-match each title against CHORD_SHEETS_DIR (recursively, .doc and
        .docx) and print every candidate file for each title. The script
        NEVER picks a candidate on its own — the calling agent must present
        the list to the user and have them confirm/disambiguate before moving
        on to `build`.

    build [--name NAME] FILE [FILE ...]
        Take a list of explicit .doc/.docx file paths (in setlist order),
        convert each to PDF via LibreOffice, count pages, and produce a single
        merged PDF in OUTPUT_DIR. Songs are laid out so that:
          - page 1 is alone
          - spreads are pages 2-3, 4-5, 6-7, ...
          - no two-page song ever crosses a spread (a blank page is inserted
            before it if needed)
        Stops with an error if any song has more than 2 pages.

Exit codes
----------
    0   success
    2   MISSING_CONFIG          required .env.local value not set
    3   MISSING_DEPENDENCY      LibreOffice (or other required tool) not found
    4   UNEXPECTED_PAGE_COUNT   a song has >2 pages — agent must ask the user
    5   usage error / file not found / conversion failure
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path

from pypdf import PdfReader, PdfWriter


# ---------------------------------------------------------------------------
# Repo paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

def load_env_file(path: Path) -> dict[str, str]:
    """Parse a simple KEY=VALUE env file. Ignores comments and blank lines.
    Strips matching surrounding quotes. Empty values count as unset."""
    if not path.exists():
        return {}
    out: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if (len(value) >= 2) and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if value:
            out[key] = value
    return out


@dataclass
class Config:
    chord_sheets_dir: Path
    output_dir: Path
    soffice_path: Path
    fuzzy_threshold: float


def load_config() -> Config:
    """Merge `.env` then `.env.local` (local wins), validate, return Config.
    Exits with code 2 if any required value is missing or invalid."""
    env_defaults = load_env_file(REPO_ROOT / ".env")
    env_local = load_env_file(REPO_ROOT / ".env.local")
    env = {**env_defaults, **env_local}

    missing: list[str] = []
    invalid: list[str] = []

    chord_dir_raw = env.get("CHORD_SHEETS_DIR", "")
    output_dir_raw = env.get("OUTPUT_DIR", "")

    if not chord_dir_raw:
        missing.append("CHORD_SHEETS_DIR")
    if not output_dir_raw:
        missing.append("OUTPUT_DIR")

    if missing:
        die_missing_config(missing)

    chord_dir = Path(os.path.expanduser(chord_dir_raw))
    output_dir = Path(os.path.expanduser(output_dir_raw))

    if not chord_dir.exists() or not chord_dir.is_dir():
        invalid.append(f"CHORD_SHEETS_DIR={chord_dir} (does not exist or not a directory)")
    if not output_dir.exists() or not output_dir.is_dir():
        invalid.append(f"OUTPUT_DIR={output_dir} (does not exist or not a directory)")

    if invalid:
        print("MISSING_CONFIG: one or more required paths in .env.local are invalid:", file=sys.stderr)
        for item in invalid:
            print(f"  - {item}", file=sys.stderr)
        print("\nFix the values in .env.local and try again.", file=sys.stderr)
        sys.exit(2)

    soffice_path = resolve_soffice(env.get("SOFFICE_PATH", ""))

    threshold_raw = env.get("FUZZY_MATCH_THRESHOLD", "0.75")
    try:
        threshold = float(threshold_raw)
    except ValueError:
        print(f"MISSING_CONFIG: FUZZY_MATCH_THRESHOLD must be a number, got '{threshold_raw}'.", file=sys.stderr)
        sys.exit(2)

    return Config(
        chord_sheets_dir=chord_dir,
        output_dir=output_dir,
        soffice_path=soffice_path,
        fuzzy_threshold=threshold,
    )


def die_missing_config(missing: list[str]) -> None:
    print("MISSING_CONFIG: the following required parameters are not set in .env.local:", file=sys.stderr)
    for key in missing:
        print(f"  - {key}", file=sys.stderr)
    print(
        "\nCreate `.env.local` (gitignored) at the repo root and set those values.\n"
        "See `.env` for documentation on each parameter, or copy `.env.local.example`\n"
        "as a starting point:\n"
        "\n"
        "    cp .env.local.example .env.local\n"
        "    # then edit .env.local with your real paths\n",
        file=sys.stderr,
    )
    sys.exit(2)


# ---------------------------------------------------------------------------
# Dependency checks
# ---------------------------------------------------------------------------

def resolve_soffice(configured: str) -> Path:
    """Find LibreOffice's soffice binary. Order: configured path → macOS app
    bundle → PATH. Exits with code 3 if not found."""
    candidates: list[Path] = []
    if configured:
        candidates.append(Path(os.path.expanduser(configured)))
    candidates.append(Path("/Applications/LibreOffice.app/Contents/MacOS/soffice"))
    which = shutil.which("soffice")
    if which:
        candidates.append(Path(which))

    for c in candidates:
        if c.exists() and os.access(c, os.X_OK):
            return c

    print(
        "MISSING_DEPENDENCY: LibreOffice (`soffice`) was not found.\n"
        "\n"
        "Install it with:\n"
        "    brew install --cask libreoffice\n"
        "\n"
        "If it's installed somewhere unusual, set SOFFICE_PATH in .env.local to\n"
        "the absolute path of the `soffice` binary, e.g.\n"
        "    SOFFICE_PATH=/Applications/LibreOffice.app/Contents/MacOS/soffice\n",
        file=sys.stderr,
    )
    sys.exit(3)


# ---------------------------------------------------------------------------
# Fuzzy matching
# ---------------------------------------------------------------------------

def normalize(s: str) -> str:
    """Lowercase, strip extension, replace punctuation with spaces, collapse
    whitespace."""
    s = s.lower()
    s = re.sub(r"\.(doc|docx)$", "", s)
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def score_filename(query: str, filename: str) -> float:
    """Return the best similarity score for `query` against `filename`.

    Computes three scores and takes the max:
      - SequenceMatcher ratio against the full normalized filename
      - SequenceMatcher ratio against the filename with parentheticals
        (e.g. "(Capo 3)") stripped — so "Amazing Grace" still matches
        "Amazing Grace (Capo 3).docx" cleanly
      - A high fixed score (0.9) when the normalized query is a substring of
        the normalized filename
    """
    qn = normalize(query)
    fn_full = normalize(filename)
    stripped = re.sub(r"\([^)]*\)", "", filename)
    fn_stripped = normalize(stripped)

    s1 = SequenceMatcher(None, qn, fn_full).ratio()
    s2 = SequenceMatcher(None, qn, fn_stripped).ratio() if fn_stripped != fn_full else 0.0
    bonus = 0.9 if qn and qn in fn_full else 0.0

    return max(s1, s2, bonus)


def find_chord_sheet_files(root: Path) -> list[Path]:
    """All .doc and .docx files under `root`, recursively. Sorted for stable
    output. Skips temp/lock files (filenames starting with ~$)."""
    files: list[Path] = []
    for pattern in ("*.doc", "*.docx"):
        files.extend(root.rglob(pattern))
    files = [f for f in files if not f.name.startswith("~$")]
    files.sort()
    return files


@dataclass
class MatchResult:
    query: str
    candidates: list[tuple[Path, float]]  # above threshold, sorted desc
    suggestions: list[tuple[Path, float]]  # below threshold, top 3, sorted desc


def match_title(query: str, files: list[Path], threshold: float) -> MatchResult:
    scored = [(f, score_filename(query, f.name)) for f in files]
    scored.sort(key=lambda x: x[1], reverse=True)
    candidates = [(f, s) for f, s in scored if s >= threshold]
    suggestions = [(f, s) for f, s in scored if s < threshold][:3]
    return MatchResult(query=query, candidates=candidates, suggestions=suggestions)


# ---------------------------------------------------------------------------
# `resolve` subcommand
# ---------------------------------------------------------------------------

def cmd_resolve(args: argparse.Namespace) -> int:
    config = load_config()
    titles: list[str] = args.titles
    if not titles:
        print("usage: build_binder.py resolve TITLE [TITLE ...]", file=sys.stderr)
        return 5

    files = find_chord_sheet_files(config.chord_sheets_dir)
    if not files:
        print(
            f"No .doc or .docx files found under {config.chord_sheets_dir}.\n"
            "Double-check CHORD_SHEETS_DIR in .env.local.",
            file=sys.stderr,
        )
        return 5

    print(f"Searched {len(files)} file(s) under {config.chord_sheets_dir}")
    print(f"Fuzzy match threshold: {config.fuzzy_threshold}")
    print()

    for i, title in enumerate(titles, start=1):
        result = match_title(title, files, config.fuzzy_threshold)
        n = len(result.candidates)
        print(f"[{i}] {title!r} — {n} candidate(s):")
        if result.candidates:
            for path, score in result.candidates:
                print(f"      {score:.2f}  {path}")
        else:
            print(f"      (none above threshold {config.fuzzy_threshold})")
        if not result.candidates and result.suggestions:
            print(f"    Top suggestions (below threshold):")
            for path, score in result.suggestions:
                print(f"      {score:.2f}  {path}")
        print()

    print(
        "Next step: confirm the file list with the user (always — even when there's\n"
        "only one candidate per title), then run:\n"
        "\n"
        "    uv run scripts/build_binder.py build [--name \"...\"] FILE [FILE ...]\n"
    )
    return 0


# ---------------------------------------------------------------------------
# PDF conversion + layout
# ---------------------------------------------------------------------------

def convert_to_pdf(src: Path, out_dir: Path, soffice: Path) -> Path:
    """Convert a .doc/.docx to PDF via headless LibreOffice. Returns the
    resulting PDF path. Raises RuntimeError on failure."""
    result = subprocess.run(
        [
            str(soffice),
            "--headless",
            "--convert-to", "pdf",
            "--outdir", str(out_dir),
            str(src),
        ],
        capture_output=True,
        text=True,
    )
    pdf = out_dir / (src.stem + ".pdf")
    if result.returncode != 0 or not pdf.exists():
        raise RuntimeError(
            f"LibreOffice failed to convert {src.name!r}\n"
            f"stdout: {result.stdout.strip()}\n"
            f"stderr: {result.stderr.strip()}"
        )
    return pdf


@dataclass
class SongPlacement:
    source: Path
    pdf: Path
    pages: int
    binder_start: int  # 1-indexed page in the final binder
    binder_end: int    # 1-indexed inclusive


def merge_into_binder(song_pdfs: list[tuple[Path, Path]], out_path: Path) -> list[SongPlacement]:
    """Merge converted PDFs into the binder, inserting blank pages so two-page
    songs never cross a spread. `song_pdfs` is a list of (source, pdf) tuples
    in setlist order. Returns per-song placement info."""
    writer = PdfWriter()
    placements: list[SongPlacement] = []
    position = 1  # 1-indexed page counter for the final binder

    # Open all readers up front so we can fail fast on a corrupt PDF.
    readers: list[tuple[Path, Path, PdfReader]] = []
    for source, pdf in song_pdfs:
        readers.append((source, pdf, PdfReader(str(pdf))))

    # Validate page counts before writing anything.
    for source, _pdf, reader in readers:
        n = len(reader.pages)
        if n < 1:
            print(f"Conversion produced a 0-page PDF for {source!r}.", file=sys.stderr)
            sys.exit(5)
        if n > 2:
            print(
                f"UNEXPECTED_PAGE_COUNT: {source.name!r} produced a {n}-page PDF.\n"
                "This script expects every chord sheet to be 1 or 2 pages. Stop and\n"
                "ask the user how to proceed (trim the source, exclude the song, or\n"
                "raise the limit).",
                file=sys.stderr,
            )
            sys.exit(4)

    # Lay out and write.
    for source, pdf, reader in readers:
        n = len(reader.pages)
        # Two-page songs must start on an even page (so they fit one spread).
        # Page 1 is alone; the first spread is 2-3.
        if n == 2 and position % 2 == 1:
            first = reader.pages[0]
            writer.add_blank_page(
                width=first.mediabox.width,
                height=first.mediabox.height,
            )
            position += 1

        start = position
        for page in reader.pages:
            writer.add_page(page)
            position += 1
        placements.append(
            SongPlacement(
                source=source,
                pdf=pdf,
                pages=n,
                binder_start=start,
                binder_end=position - 1,
            )
        )

    with open(out_path, "wb") as f:
        writer.write(f)

    return placements


# ---------------------------------------------------------------------------
# `build` subcommand
# ---------------------------------------------------------------------------

def cmd_build(args: argparse.Namespace) -> int:
    config = load_config()

    files: list[Path] = []
    for raw in args.files:
        p = Path(os.path.expanduser(raw)).resolve()
        if not p.exists():
            print(f"File not found: {raw}", file=sys.stderr)
            return 5
        if p.suffix.lower() not in (".doc", ".docx"):
            print(f"Unsupported file type ({p.suffix}): {p}", file=sys.stderr)
            return 5
        files.append(p)

    if not files:
        print("usage: build_binder.py build [--name NAME] FILE [FILE ...]", file=sys.stderr)
        return 5

    binder_name = args.name or f"Binder {date.today().isoformat()}"
    # Strip a trailing .pdf if the agent supplied one; we add it back.
    if binder_name.lower().endswith(".pdf"):
        binder_name = binder_name[:-4]
    out_path = config.output_dir / f"{binder_name}.pdf"

    print(f"Building binder: {out_path}")
    print(f"Songs ({len(files)}):")
    for i, f in enumerate(files, start=1):
        print(f"  {i}. {f.name}")
    print()

    with tempfile.TemporaryDirectory(prefix="music-binder-") as tmp_str:
        tmp = Path(tmp_str)
        print(f"Converting to PDF (temp dir: {tmp})...")
        song_pdfs: list[tuple[Path, Path]] = []
        for f in files:
            try:
                pdf = convert_to_pdf(f, tmp, config.soffice_path)
            except RuntimeError as e:
                print(str(e), file=sys.stderr)
                return 5
            print(f"  ✓ {f.name} → {pdf.name}")
            song_pdfs.append((f, pdf))

        print()
        print("Merging into binder...")
        placements = merge_into_binder(song_pdfs, out_path)

    # tmp is now deleted by TemporaryDirectory's context manager.

    print()
    print(f"Wrote {out_path}")
    print()
    print("Layout:")
    print(f"  {'#':>2}  {'pages':>5}  {'binder':>8}  source")
    for i, p in enumerate(placements, start=1):
        rng = f"{p.binder_start}" if p.binder_start == p.binder_end else f"{p.binder_start}-{p.binder_end}"
        print(f"  {i:>2}  {p.pages:>5}  {rng:>8}  {p.source.name}")
    return 0


# ---------------------------------------------------------------------------
# argparse
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="build_binder.py",
        description="Build a chord-sheet binder PDF.",
    )
    sub = p.add_subparsers(dest="command", required=True)

    p_resolve = sub.add_parser(
        "resolve",
        help="Fuzzy-match song titles to files in CHORD_SHEETS_DIR (does not build).",
    )
    p_resolve.add_argument("titles", nargs="+", help="Song titles to look up.")
    p_resolve.set_defaults(func=cmd_resolve)

    p_build = sub.add_parser(
        "build",
        help="Build a binder PDF from explicit .doc/.docx file paths.",
    )
    p_build.add_argument(
        "--name",
        help="Binder filename (without .pdf). Defaults to 'Binder YYYY-MM-DD'.",
    )
    p_build.add_argument("files", nargs="+", help="Source files in setlist order.")
    p_build.set_defaults(func=cmd_build)

    return p


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
