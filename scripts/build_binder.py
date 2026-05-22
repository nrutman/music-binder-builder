#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "pypdf>=4.0.0",
#     "httpx>=0.27.0",
# ]
# ///
"""
build_binder.py — Build a saddle-stitched chord-sheet binder PDF.

Subcommands
-----------
    resolve TITLE [TITLE ...]
        Local mode. Fuzzy-match each title against CHORD_SHEETS_DIR
        (recursively, .doc and .docx) and print every candidate file for each
        title. NEVER picks for you — agent presents the list to the user.

    build [--name NAME] FILE [FILE ...]
        Local mode. Take explicit .doc/.docx file paths (in setlist order),
        convert each to PDF via LibreOffice, count pages, lay songs out across
        spreads, and write the merged PDF to OUTPUT_DIR.

    pco-resolve --date YYYY-MM-DD [--service-type ID] [--plan-id ID]
                [--pick SONG_ID=ATTACHMENT_ID ...]
        Planning Center mode. Look up the service plan for the given date,
        list its songs in plan order, and propose a chord-sheet attachment for
        each (filter: filename contains "chord" + .doc/.docx). Exits with
        PCO_AMBIGUOUS_ATTACHMENT if any song has 0 or 2+ chord matches — the
        agent then resolves them with --pick.

    pco-build --date YYYY-MM-DD [--service-type ID] [--plan-id ID]
              [--pick SONG_ID=ATTACHMENT_ID ...] [--name NAME]
        Planning Center mode. Same resolution as pco-resolve, then downloads
        each attachment and runs the local build pipeline.

Layout (applies to both modes)
------------------------------
    - page 1 is alone
    - spreads are pages 2-3, 4-5, 6-7, ...
    - no two-page song ever crosses a spread (a blank page is inserted before
      it if needed)
    - trailing pages containing only recurring header/footer chrome are
      auto-trimmed before layout (with a printed warning per song)
    - a song with >2 effective pages aborts with exit code 4

Exit codes
----------
    0   success
    2   MISSING_CONFIG          required .env.local value not set
    3   MISSING_DEPENDENCY      LibreOffice (or other required tool) not found
    4   UNEXPECTED_PAGE_COUNT   a song has >2 pages — agent must ask the user
    5   usage error / file not found / conversion failure
    6   PCO_*                   PCO ambiguity or not-found (see error message)
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
from datetime import date, datetime
from difflib import SequenceMatcher
from pathlib import Path

# Make sibling modules (pco.py) importable when run via `uv run scripts/...`.
sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from pypdf import PdfReader, PdfWriter
except ModuleNotFoundError as e:
    if e.name == "pypdf":
        sys.stderr.write(
            "MISSING_DEPENDENCY: pypdf was not installed automatically.\n"
            "\n"
            "This usually means your `uv` is too old to read PEP 723 inline script\n"
            "dependencies (need uv >= 0.4.4). Check and upgrade:\n"
            "\n"
            "    uv --version\n"
            "    brew upgrade uv\n"
            "\n"
            "Then re-run. (Also run `bash scripts/check_deps.sh` to verify.)\n"
        )
        sys.exit(3)
    raise

# PCO module is imported lazily inside the pco-* subcommands so the local
# `resolve` / `build` flow doesn't pay the httpx import cost or surface PCO
# errors when the user isn't using PCO.


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
        print(
            "MISSING_CONFIG: one or more required paths in .env.local are invalid:", file=sys.stderr
        )
        for item in invalid:
            print(f"  - {item}", file=sys.stderr)
        print("\nFix the values in .env.local and try again.", file=sys.stderr)
        sys.exit(2)

    soffice_path = resolve_soffice(env.get("SOFFICE_PATH", ""))

    threshold_raw = env.get("FUZZY_MATCH_THRESHOLD", "0.75")
    try:
        threshold = float(threshold_raw)
    except ValueError:
        print(
            f"MISSING_CONFIG: FUZZY_MATCH_THRESHOLD must be a number, got '{threshold_raw}'.",
            file=sys.stderr,
        )
        sys.exit(2)

    return Config(
        chord_sheets_dir=chord_dir,
        output_dir=output_dir,
        soffice_path=soffice_path,
        fuzzy_threshold=threshold,
    )


def die_missing_config(missing: list[str]) -> None:
    print(
        "MISSING_CONFIG: the following required parameters are not set in .env.local:",
        file=sys.stderr,
    )
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
            print("    Top suggestions (below threshold):")
            for path, score in result.suggestions:
                print(f"      {score:.2f}  {path}")
        print()

    print(
        "Next step: confirm the file list with the user (always — even when there's\n"
        "only one candidate per title), then run:\n"
        "\n"
        '    uv run scripts/build_binder.py build [--name "..."] FILE [FILE ...]\n'
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
            "--convert-to",
            "pdf",
            "--outdir",
            str(out_dir),
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


@dataclass(frozen=True)
class LayoutEntry:
    """Where one song lands in the binder (pure layout math, no PDF state)."""

    blank_before: bool  # True if a blank page was inserted before this song
    binder_start: int  # 1-indexed page in the binder
    binder_end: int  # 1-indexed inclusive


def plan_layout(effective_page_counts: list[int]) -> list[LayoutEntry]:
    """Pure layout calculation. Given per-song *effective* page counts (each
    must be 1 or 2 — callers validate first), return a placement entry per
    song in setlist order.

    Rules:
      - page 1 is alone
      - spreads are 2-3, 4-5, 6-7, …
      - a 2-page song that would land at an odd position gets a blank page
        inserted before it so it occupies a single spread
      - song order is preserved
    """
    position = 1
    entries: list[LayoutEntry] = []
    for pages in effective_page_counts:
        blank = pages == 2 and position % 2 == 1
        if blank:
            position += 1
        start = position
        position += pages
        entries.append(LayoutEntry(blank_before=blank, binder_start=start, binder_end=position - 1))
    return entries


@dataclass
class SongPlacement:
    source: Path
    pdf: Path
    pages: int  # effective page count after trimming
    raw_pages: int  # original page count from conversion
    trimmed: int  # how many trailing chrome-only pages were dropped
    binder_start: int  # 1-indexed page in the final binder
    binder_end: int  # 1-indexed inclusive


_TRIM_MIN_UNIQUE_WORDS = 5


def _normalize_line(line: str) -> str:
    """Normalize a line for cross-page comparison: lowercase, collapse digits
    (so 'Page 1 of 2' and 'Page 2 of 2' match), collapse whitespace."""
    line = line.lower().strip()
    line = re.sub(r"\d+", "#", line)
    line = re.sub(r"\s+", " ", line)
    return line


def _page_lines(page) -> list[str]:
    text = page.extract_text() or ""
    return [ln.strip() for ln in text.splitlines() if ln.strip()]


def effective_page_count(reader: PdfReader) -> tuple[int, int]:
    """Return (effective_pages, trimmed_count).

    Walks from the last page backwards. A page is “chrome-only” (and trimmed)
    when every one of its lines, after normalization, also appears on an
    earlier page — i.e. the page contains only recurring header/footer
    boilerplate (page-number header, song title, copyright, CCLI #). Stops at
    the first non-chrome page, and never trims the first page.
    """
    total = len(reader.pages)
    if total <= 1:
        return total, 0

    prior_norms: set[str] = set()
    for i in range(total - 1):
        for line in _page_lines(reader.pages[i]):
            prior_norms.add(_normalize_line(line))

    trimmed = 0
    # Walk backwards from the last page, trimming chrome-only pages.
    for i in range(total - 1, 0, -1):
        lines = _page_lines(reader.pages[i])
        unique = [ln for ln in lines if _normalize_line(ln) not in prior_norms]
        unique_words = sum(len(ln.split()) for ln in unique)
        if unique_words < _TRIM_MIN_UNIQUE_WORDS:
            trimmed += 1
            # When trimming page i, recompute prior_norms to exclude it so an
            # earlier page can still trim against pages before it.
            prior_norms = set()
            for j in range(i - 1):
                for line in _page_lines(reader.pages[j]):
                    prior_norms.add(_normalize_line(line))
        else:
            break

    return total - trimmed, trimmed


def merge_into_binder(song_pdfs: list[tuple[Path, Path]], out_path: Path) -> list[SongPlacement]:
    """Merge converted PDFs into the binder, inserting blank pages so two-page
    songs never cross a spread. `song_pdfs` is a list of (source, pdf) tuples
    in setlist order. Returns per-song placement info."""
    writer = PdfWriter()
    placements: list[SongPlacement] = []

    # Open all readers up front so we can fail fast on a corrupt PDF, and
    # compute effective page counts (after trimming trailing chrome-only
    # pages) so layout decisions use post-trim counts.
    prepared: list[tuple[Path, Path, PdfReader, int, int]] = []
    for source, pdf in song_pdfs:
        reader = PdfReader(str(pdf))
        effective, trimmed = effective_page_count(reader)
        if trimmed:
            noun = "page" if trimmed == 1 else "pages"
            print(
                f"  ⚠ {source.name}: trimmed {trimmed} trailing chrome-only "
                f"{noun} (only header/footer content)"
            )
        prepared.append((source, pdf, reader, effective, trimmed))

    # Validate effective page counts before writing anything.
    for source, _pdf, _reader, effective, _trimmed in prepared:
        if effective < 1:
            print(f"Conversion produced a 0-page PDF for {source!r}.", file=sys.stderr)
            sys.exit(5)
        if effective > 2:
            print(
                f"UNEXPECTED_PAGE_COUNT: {source.name!r} produced a {effective}-page PDF "
                f"(after trimming chrome).\n"
                "This script expects every chord sheet to be 1 or 2 pages. Stop and\n"
                "ask the user how to proceed (trim the source, exclude the song, or\n"
                "raise the limit).",
                file=sys.stderr,
            )
            sys.exit(4)

    # Compute the placement up front using the pure layout helper, then walk
    # the prepared list to actually write pages + blanks. Keeping the math
    # and the writing separate makes plan_layout unit-testable.
    layout = plan_layout([eff for _src, _pdf, _r, eff, _t in prepared])
    for entry, (source, pdf, reader, effective, trimmed) in zip(layout, prepared, strict=True):
        if entry.blank_before:
            first = reader.pages[0]
            writer.add_blank_page(
                width=first.mediabox.width,
                height=first.mediabox.height,
            )
        for i in range(effective):
            writer.add_page(reader.pages[i])
        placements.append(
            SongPlacement(
                source=source,
                pdf=pdf,
                pages=effective,
                raw_pages=effective + trimmed,
                trimmed=trimmed,
                binder_start=entry.binder_start,
                binder_end=entry.binder_end,
            )
        )

    with open(out_path, "wb") as f:
        writer.write(f)

    return placements


# ---------------------------------------------------------------------------
# `build` subcommand
# ---------------------------------------------------------------------------


def run_build_pipeline(
    files: list[Path],
    binder_name: str,
    config: Config,
) -> int:
    """Shared build pipeline used by both `build` and `pco-build`. Takes a
    list of local .doc/.docx file paths (in setlist order) and produces a
    binder PDF in `config.output_dir`. The caller owns the lifetime of the
    input files — this function only creates a temp dir for converted PDFs."""
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

    print()
    print(f"Wrote {out_path}")
    print()
    print("Layout:")
    print(f"  {'#':>2}  {'pages':>5}  {'binder':>8}  source")
    for i, p in enumerate(placements, start=1):
        rng = (
            f"{p.binder_start}"
            if p.binder_start == p.binder_end
            else f"{p.binder_start}-{p.binder_end}"
        )
        pages_label = f"{p.pages}"
        if p.trimmed:
            pages_label = f"{p.pages} (-{p.trimmed})"
        print(f"  {i:>2}  {pages_label:>5}  {rng:>8}  {p.source.name}")
    return 0


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
    return run_build_pipeline(files, binder_name, config)


# ---------------------------------------------------------------------------
# Planning Center mode
# ---------------------------------------------------------------------------


def _parse_iso_date(s: str) -> date:
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        print(f"Bad --date value (expected YYYY-MM-DD): {s!r}", file=sys.stderr)
        sys.exit(5)


def _parse_picks(pick_args: list[str] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in pick_args or []:
        if "=" not in raw:
            print(
                f"Bad --pick value (expected SONG_ID=ATTACHMENT_ID): {raw!r}",
                file=sys.stderr,
            )
            sys.exit(5)
        k, v = raw.split("=", 1)
        k, v = k.strip(), v.strip()
        if not k or not v:
            print(
                f"Bad --pick value (empty side): {raw!r}",
                file=sys.stderr,
            )
            sys.exit(5)
        out[k] = v
    return out


def _print_pco_resolutions(
    resolutions: list,
    plan: dict,
    service_type_name: str,
) -> bool:
    """Pretty-print the resolution table. Returns True iff every song is
    picked unambiguously."""
    plan_attrs = plan["attributes"]
    plan_title = plan_attrs.get("title") or "(untitled)"
    plan_date = (plan_attrs.get("sort_date") or "")[:10] or "?"
    print(f"Service type: {service_type_name}")
    print(f"Plan:         {plan_title}  (id={plan['id']}, sort_date={plan_date})")
    print(f"Songs:        {len(resolutions)}")
    print()
    all_resolved = True
    for r in resolutions:
        scope_bits = []
        if getattr(r, "arrangement_id", None):
            scope_bits.append(f"arrangement={r.arrangement_id}")
        if getattr(r, "key_id", None):
            scope_bits.append(f"key={r.key_id}")
        scope = ("  [" + ", ".join(scope_bits) + "]") if scope_bits else ""
        print(f"[{r.sequence}] {r.song_title}  (song_id={r.song_id}){scope}")
        if r.picked:
            tag = "auto" if r.pick_source == "auto" else "user pick"
            src = f", from {r.chord_source}" if getattr(r, "chord_source", None) else ""
            f = r.picked["attributes"].get("filename") or "(no filename)"
            print(f"      ✓ {f}  (attachment_id={r.picked['id']}, {tag}{src})")
        else:
            all_resolved = False
            if r.chord_candidates:
                src = f" on {r.chord_source}" if getattr(r, "chord_source", None) else ""
                print(f"      ⚠ {len(r.chord_candidates)} chord-named attachments{src} — pick one:")
                for a in r.chord_candidates:
                    fname = a["attributes"].get("filename") or "(no filename)"
                    print(f"          attachment_id={a['id']}  {fname}")
            elif r.doc_candidates:
                print(
                    "      ⚠ no chord-named attachments. Other .doc/.docx anywhere on this song/arrangement/key:"
                )
                for a in r.doc_candidates:
                    fname = a["attributes"].get("filename") or "(no filename)"
                    attachable = (a.get("relationships") or {}).get("attachable", {}).get(
                        "data"
                    ) or {}
                    where = f"{attachable.get('type')}:{attachable.get('id')}"
                    print(f"          attachment_id={a['id']}  {fname}  ({where})")
            else:
                print("      ⚠ no .doc/.docx attachments anywhere on this song/arrangement/key.")
        print()
    return all_resolved


def _pco_load_env() -> dict[str, str]:
    """Merged .env + .env.local for PCO config consumption."""
    return {
        **load_env_file(REPO_ROOT / ".env"),
        **load_env_file(REPO_ROOT / ".env.local"),
    }


def _pco_resolve_plan(
    pco_client,
    args: argparse.Namespace,
) -> tuple[dict, str, str, list]:
    """Shared by pco-resolve and pco-build: resolves the plan + the song
    list. Returns (plan, service_type_id, service_type_name, resolutions)."""
    import pco as pcomod  # local import; module already validated httpx

    if args.service_type:
        st = pcomod.get_service_type(pco_client, args.service_type)
    else:
        st = pcomod.find_service_type(pco_client, None)
    service_type_id = st["id"]
    service_type_name = st["attributes"]["name"]

    if args.plan_id:
        plan = pco_client.get(f"/services/v2/service_types/{service_type_id}/plans/{args.plan_id}")[
            "data"
        ]
    else:
        target_date = _parse_iso_date(args.date)
        plan = pcomod.find_plan_for_date(pco_client, service_type_id, target_date)

    picks = _parse_picks(args.pick)
    resolutions = pcomod.resolve_plan_songs(pco_client, service_type_id, plan["id"], picks)
    return plan, service_type_id, service_type_name, resolutions


def cmd_pco_resolve(args: argparse.Namespace) -> int:
    config = load_config()  # validates local config too, so build can follow
    _ = config  # not used here, but we want the same MISSING_CONFIG semantics
    import pco as pcomod

    pco_config = pcomod.load_pco_config(_pco_load_env())
    with pcomod.PCOClient(pco_config) as client:
        plan, _stid, st_name, resolutions = _pco_resolve_plan(client, args)
        all_resolved = _print_pco_resolutions(resolutions, plan, st_name)

    sys.stdout.flush()
    if not all_resolved:
        sys.stderr.write(
            "\nPCO_AMBIGUOUS_ATTACHMENT: some songs need disambiguation. Show the\n"
            "list above to the user. For each unresolved song, re-run with\n"
            "--pick SONG_ID=ATTACHMENT_ID (one --pick per song).\n"
        )
        return 6

    print("Next step: confirm the list with the user, then run pco-build with the same")
    print('arguments plus --name "..." to produce the binder.')
    return 0


def cmd_pco_doctor(args: argparse.Namespace) -> int:
    """Connectivity + shape diagnostic. Walks the same path the resolver uses
    (service types → plans → items → song attachments → open action) and
    prints structural info only — never credentials. Use to validate that
    a freshly-configured `.env.local` works and that the API responses
    match what this codebase expects."""
    config = load_config()
    _ = config
    import pco as pcomod

    issues: list[str] = []

    def check(label: str, ok: bool, hint: str = "") -> None:
        if ok:
            print(f"  ✓ {label}")
        else:
            print(f"  ✗ {label}" + (f" — {hint}" if hint else ""))
            issues.append(label)

    pco_config = pcomod.load_pco_config(_pco_load_env())
    print("PCO doctor — verifying API connectivity and resource shapes\n")

    with pcomod.PCOClient(pco_config) as client:
        # Step 1: service types (smallest endpoint, confirms auth)
        print("Step 1: GET /services/v2/service_types")
        try:
            payload = client.get("/services/v2/service_types", params={"per_page": 25})
        except pcomod.PCOError as e:
            print(f"  ✗ request failed: {e}")
            return 6
        types = payload.get("data") or []
        print(f"  ✓ HTTP 200, {len(types)} service type(s) returned")
        check("response['data'] is a list", isinstance(payload.get("data"), list))
        for t in types:
            attrs = t.get("attributes", {})
            archived = " [archived]" if attrs.get("archived_at") else ""
            print(f"      id={t['id']}  name={attrs.get('name')!r}{archived}")
        active_types = [t for t in types if not t["attributes"].get("archived_at")]
        if not active_types:
            print("\n⚠ no active service types in this org; further shape checks skipped.")
            return 6 if issues else 0

        # If the user has set a default service type, use it; otherwise pick the first active one.
        if pco_config.default_service_type_id:
            st_match = [t for t in active_types if t["id"] == pco_config.default_service_type_id]
            st = st_match[0] if st_match else active_types[0]
            if not st_match:
                print(
                    f"  ⚠ PCO_DEFAULT_SERVICE_TYPE_ID={pco_config.default_service_type_id} "
                    "not found among active types; falling back to first active."
                )
        else:
            st = active_types[0]
        st_id = st["id"]
        print(
            f"\nUsing service type id={st_id} name={st['attributes'].get('name')!r} for further checks"
        )

        # Step 2: plans listing for this service type. Past plans only —
        # future plans in this org are auto-templated with no songs filled in
        # yet, so checking past ones is more reliable for shape verification.
        print(f"\nStep 2: GET /services/v2/service_types/{st_id}/plans  (5 most recent PAST plans)")
        try:
            payload = client.get(
                f"/services/v2/service_types/{st_id}/plans",
                params={"order": "-sort_date", "per_page": 5, "filter": "past"},
            )
        except pcomod.PCOError as e:
            print(f"  ✗ request failed: {e}")
            return 6
        plans = payload.get("data") or []
        print(f"  ✓ HTTP 200, {len(plans)} plan(s) returned")
        for p in plans:
            attrs = p.get("attributes", {})
            print(
                f"      id={p['id']}  sort_date={attrs.get('sort_date')!r}  "
                f"items_count={attrs.get('items_count')}  title={attrs.get('title')!r}"
            )
        if not plans:
            print("\n⚠ no past plans for this service type; further shape checks skipped.")
            return 6 if issues else 0
        check(
            "plan[0] has 'sort_date'",
            "sort_date" in plans[0]["attributes"],
            "needed for date-based plan lookup",
        )
        check(
            "sort_date starts with YYYY-MM-DD",
            bool(re.match(r"^\d{4}-\d{2}-\d{2}", plans[0]["attributes"].get("sort_date") or "")),
            "client-side date match assumes leading ISO date",
        )
        check("pagination 'links' object present", isinstance(payload.get("links"), dict))

        # Step 3: scan plans (most recent first) for one that actually has
        # song items. items_count includes headers/media too, so we have to
        # fetch items to know for sure.
        chosen = None
        items = []
        included = []
        for candidate in plans:
            cid = candidate["id"]
            try:
                pl = client.get(
                    f"/services/v2/service_types/{st_id}/plans/{cid}/items",
                    params={"include": "song", "order": "sequence", "per_page": 100},
                )
            except pcomod.PCOError as e:
                print(f"  ✗ items fetch for plan {cid} failed: {e}")
                return 6
            cand_items = pl.get("data") or []
            if any(it["attributes"].get("item_type") == "song" for it in cand_items):
                chosen = candidate
                items = cand_items
                included = pl.get("included") or []
                break
        if chosen is None:
            print(
                "\n⚠ none of the recent past plans contain song items; further shape checks skipped."
            )
            return 6 if issues else 0
        plan_id = chosen["id"]
        print(
            f"\nStep 3: GET /services/v2/service_types/{st_id}/plans/{plan_id}/items?include=song&order=sequence"
        )
        songs_inc = [i for i in included if i.get("type") == "Song"]
        song_items = [it for it in items if it["attributes"].get("item_type") == "song"]
        print(
            f"  ✓ HTTP 200, {len(items)} item(s), {len(songs_inc)} included Song(s), {len(song_items)} song item(s)"
        )
        for it in song_items[:5]:
            attrs = it["attributes"]
            song_rel = (it.get("relationships") or {}).get("song", {}).get("data")
            print(
                f"      item_id={it['id']}  seq={attrs.get('sequence')}  "
                f"title={attrs.get('title')!r}  song_id={song_rel and song_rel.get('id')}"
            )
        check("response['included'] is a list", isinstance(payload.get("included"), list))
        check(
            "every item has attributes.item_type",
            all("item_type" in it["attributes"] for it in items),
        )
        check(
            "every item has attributes.sequence",
            all("sequence" in it["attributes"] for it in items),
        )
        check(
            "every song item has relationships.song.data",
            all((it.get("relationships") or {}).get("song", {}).get("data") for it in song_items),
        )
        check(
            "item_type 'song' actually appears in this plan",
            len(song_items) > 0,
            "need at least one song to verify attachment shape",
        )

        if not song_items:
            print("\n⚠ no song items in this plan; attachment shape checks skipped.")
            return 6 if issues else 0

        # Step 4: probe every place attachments could live for a song in a plan.
        # PCO's attachment model is polymorphic — `attachable_type` can be
        # Song, Arrangement, Item, Plan, Media, Key, ServiceType. Different
        # orgs use different conventions, so we look at all of them.
        print("\nStep 4: probe every attachment location for songs in this plan")
        sample_att = None
        sample_att_parent_path = None  # the URL prefix for the /open action

        def _print_atts(label: str, atts: list) -> None:
            print(f"  {label}: {len(atts)} attachment(s)")
            for a in atts:
                attrs = a.get("attributes", {})
                attachable = (a.get("relationships") or {}).get("attachable", {}).get("data") or {}
                a_type = attachable.get("type") or attrs.get("attachable_type") or "?"
                a_id = attachable.get("id") or "?"
                print(
                    f"      attachment_id={a['id']}  filename={attrs.get('filename')!r}  "
                    f"content_type={attrs.get('content_type')!r}  "
                    f"attachable={a_type}:{a_id}"
                )

        # 4a — plan-level all_attachments (sweeps Song, Arrangement, Item, Plan, ...)
        print(f"\n  4a. GET /services/v2/service_types/{st_id}/plans/{plan_id}/all_attachments")
        try:
            payload = client.get(
                f"/services/v2/service_types/{st_id}/plans/{plan_id}/all_attachments",
                params={"per_page": 100},
            )
        except pcomod.PCOError as e:
            print(f"    ✗ request failed: {e}")
            payload = {"data": []}
        plan_all = payload.get("data") or []
        _print_atts("all_attachments", plan_all)
        by_type: dict[str, int] = {}
        for a in plan_all:
            attachable = (a.get("relationships") or {}).get("attachable", {}).get("data") or {}
            by_type[attachable.get("type", "?")] = by_type.get(attachable.get("type", "?"), 0) + 1
        if by_type:
            print(f"    → by attachable_type: {by_type}")

        # 4b — per-song probe: Song attachments + Arrangement(s) + their attachments + Item selected_attachment
        for it in song_items:
            sid = it["relationships"]["song"]["data"]["id"]
            item_id = it["id"]
            song_title = next(
                (s["attributes"]["title"] for s in songs_inc if s["id"] == sid),
                it["attributes"].get("title") or "(untitled)",
            )
            print(f"\n  song {sid} {song_title!r}  (item {item_id})")

            # song-level attachments
            try:
                song_atts = pcomod.list_song_attachments(client, sid)
            except pcomod.PCOError as e:
                print(f"    ✗ GET /songs/{sid}/attachments failed: {e}")
                song_atts = []
            _print_atts(f"    /songs/{sid}/attachments", song_atts)

            # arrangements
            try:
                arr_payload = client.get(
                    f"/services/v2/songs/{sid}/arrangements",
                    params={"per_page": 25},
                )
            except pcomod.PCOError as e:
                print(f"    ✗ GET /songs/{sid}/arrangements failed: {e}")
                arr_payload = {"data": []}
            arrangements = arr_payload.get("data") or []
            print(f"    /songs/{sid}/arrangements: {len(arrangements)} arrangement(s)")
            for arr in arrangements:
                arr_id = arr["id"]
                arr_name = (
                    arr["attributes"].get("name")
                    or arr["attributes"].get("print_margin")
                    or "(unnamed)"
                )
                print(f"      arrangement_id={arr_id}  name={arr_name!r}")
                try:
                    arr_atts_payload = client.get(
                        f"/services/v2/songs/{sid}/arrangements/{arr_id}/attachments",
                        params={"per_page": 100},
                    )
                    arr_atts = arr_atts_payload.get("data") or []
                except pcomod.PCOError as e:
                    print(f"        ✗ GET attachments for arrangement {arr_id}: {e}")
                    arr_atts = []
                _print_atts(f"        arrangement {arr_id} attachments", arr_atts)
                if sample_att is None:
                    chords = [a for a in arr_atts if pcomod.is_chord_doc_attachment(a)]
                    if chords:
                        sample_att = chords[0]
                        sample_att_parent_path = f"/services/v2/songs/{sid}/arrangements/{arr_id}"

            # selected_attachment on this plan's Item
            try:
                sel = client.get(
                    f"/services/v2/service_types/{st_id}/plans/{plan_id}/items/{item_id}/selected_attachment"
                )
                sel_data = sel.get("data")
            except pcomod.PCOError as e:
                print(f"    ✗ GET item/selected_attachment failed: {e}")
                sel_data = None
            if sel_data:
                _print_atts(f"    items/{item_id}/selected_attachment", [sel_data])
            else:
                print(f"    items/{item_id}/selected_attachment: (none)")

            # selected Key on this plan's Item (and its attachments, if any)
            try:
                key_resp = client.get(
                    f"/services/v2/service_types/{st_id}/plans/{plan_id}/items/{item_id}/key"
                )
                key_data = key_resp.get("data")
            except pcomod.PCOError as e:
                print(f"    ✗ GET item/key failed: {e}")
                key_data = None
            if key_data:
                key_id = key_data["id"]
                key_name = key_data.get("attributes", {}).get("name") or "(unnamed)"
                print(f"    items/{item_id}/key: key_id={key_id} name={key_name!r}")
                # Key attachments live under the arrangement that the key belongs to.
                # We probe both possible paths.
                arr_rel = (key_data.get("relationships") or {}).get("arrangement", {}).get("data")
                if arr_rel:
                    arr_id_for_key = arr_rel["id"]
                    try:
                        key_atts_payload = client.get(
                            f"/services/v2/songs/{sid}/arrangements/{arr_id_for_key}/keys/{key_id}/attachments",
                            params={"per_page": 100},
                        )
                        key_atts = key_atts_payload.get("data") or []
                    except pcomod.PCOError as e:
                        print(f"      ✗ GET key attachments: {e}")
                        key_atts = []
                    _print_atts(f"      key {key_id} attachments", key_atts)
                    if sample_att is None:
                        chords = [a for a in key_atts if pcomod.is_chord_doc_attachment(a)]
                        if chords:
                            sample_att = chords[0]
                            sample_att_parent_path = f"/services/v2/songs/{sid}/arrangements/{arr_id_for_key}/keys/{key_id}"
            else:
                print(f"    items/{item_id}/key: (none)")

            if sample_att is None and song_atts:
                chords = [a for a in song_atts if pcomod.is_chord_doc_attachment(a)]
                if chords:
                    sample_att = chords[0]
                    sample_att_parent_path = f"/services/v2/songs/{sid}"

        # Step 5: exercise the open action so we know download will work.
        if sample_att is None:
            print(
                "\n⚠ no chord-named .doc/.docx attachments found at any of the probed "
                "locations; open-action check skipped."
            )
        else:
            print(f"\nStep 5: POST {sample_att_parent_path}/attachments/{sample_att['id']}/open")
            try:
                resp = client.post(f"{sample_att_parent_path}/attachments/{sample_att['id']}/open")
            except pcomod.PCOError as e:
                print(f"  ✗ request failed: {e}")
                return 6
            data = (resp or {}).get("data") or {}
            attrs = data.get("attributes") or {}
            url = attrs.get("attachment_url") or ""
            host = re.sub(r"^https?://([^/?#]+).*", r"\1", url) if url else ""
            print("  ✓ HTTP 200, AttachmentActivity received")
            check("data.attributes.attachment_url is present", bool(url), "needed for download")
            check(
                "attachment_url uses https://",
                url.startswith("https://"),
                "signed CDN URL expected",
            )
            if host:
                print(f"      attachment_url host: {host}")

    print()
    if issues:
        print(f"FAIL: {len(issues)} shape issue(s) found:")
        for s in issues:
            print(f"  - {s}")
        return 6
    print("OK: all shape checks passed.")
    return 0


def cmd_pco_build(args: argparse.Namespace) -> int:
    config = load_config()
    import pco as pcomod

    pco_config = pcomod.load_pco_config(_pco_load_env())
    with pcomod.PCOClient(pco_config) as client:
        plan, _stid, st_name, resolutions = _pco_resolve_plan(client, args)
        all_resolved = _print_pco_resolutions(resolutions, plan, st_name)
        sys.stdout.flush()
        if not all_resolved:
            sys.stderr.write(
                "\nPCO_AMBIGUOUS_ATTACHMENT: cannot build until every song is picked.\n"
                "Re-run pco-resolve first and add --pick SONG_ID=ATTACHMENT_ID for\n"
                "each unresolved song.\n"
            )
            return 6

        target_date = _parse_iso_date(args.date)
        binder_name = args.name or f"Binder {target_date.isoformat()}"

        with tempfile.TemporaryDirectory(prefix="music-binder-pco-dl-") as dl_str:
            dl = Path(dl_str)
            print(f"Downloading {len(resolutions)} attachment(s) from PCO...")
            try:
                files = pcomod.download_resolved_attachments(client, resolutions, dl)
            except pcomod.PCOError as e:
                sys.stderr.write(f"\nPCO download failed: {e}\n")
                return 5
            print()
            return run_build_pipeline(files, binder_name, config)


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

    def _add_pco_common_args(parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--date",
            required=True,
            help="Service date in YYYY-MM-DD. The plan with a sort_date on this day is used.",
        )
        parser.add_argument(
            "--service-type",
            help="PCO Service Type ID. Overrides PCO_DEFAULT_SERVICE_TYPE_ID for this call.",
        )
        parser.add_argument(
            "--plan-id",
            help="PCO Plan ID. Use when multiple plans share the same date.",
        )
        parser.add_argument(
            "--pick",
            action="append",
            metavar="SONG_ID=ATTACHMENT_ID",
            help=(
                "Resolve a specific song to a specific attachment. Repeatable. "
                "Use when a song has 0 or 2+ chord-named attachments."
            ),
        )

    p_pco_resolve = sub.add_parser(
        "pco-resolve",
        help="Resolve a Planning Center plan by date and print the proposed song list.",
    )
    _add_pco_common_args(p_pco_resolve)
    p_pco_resolve.set_defaults(func=cmd_pco_resolve)

    p_pco_build = sub.add_parser(
        "pco-build",
        help="Resolve a PCO plan, download its chord sheets, and build the binder.",
    )
    _add_pco_common_args(p_pco_build)
    p_pco_build.add_argument(
        "--name",
        help="Binder filename (without .pdf). Defaults to 'Binder YYYY-MM-DD'.",
    )
    p_pco_build.set_defaults(func=cmd_pco_build)

    p_pco_doctor = sub.add_parser(
        "pco-doctor",
        help="Verify PCO connectivity and resource shapes (prints structural info only — no credentials).",
    )
    p_pco_doctor.set_defaults(func=cmd_pco_doctor)

    return p


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
