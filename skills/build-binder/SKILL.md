---
name: build-binder
description: >
  Build a printable PDF "binder" of chord sheets for live performance from a list
  of song titles. Fuzzy-matches each title against the user's chord-sheet folder,
  shows all candidates to the user for confirmation (including capo variants),
  converts the chosen .doc/.docx files to PDF via LibreOffice, and merges them so
  page 1 is alone and every two-page song lands on a single spread (2-3, 4-5,
  6-7, …) without crossing.
  TRIGGER when: the user asks to build a binder, setlist PDF, chord sheet binder,
  worship binder, or similar — and supplies (or is willing to supply) a list of
  song titles. Phrases like "build a binder for Sunday", "make me a chord sheet
  PDF with these songs", "binder this setlist".
  DO NOT TRIGGER when: the user wants to author or edit a chord sheet (that's a
  different repo / workflow), wants to merge already-PDF files (this skill is for
  .doc/.docx source files), or is asking general questions about the repo.
user-invocable: true
---

# Build Binder

**Goal**: turn a list of song titles into a single, print-ready binder PDF where every chord sheet falls on a clean spread.

## Iron rules

1. **Never guess a song match.** Always run `resolve` first and show the user every candidate file for every title (including single-candidate matches). Wait for explicit confirmation before running `build`.
2. **Never auto-resolve capo variants.** If a title matches both "Song.docx" and "Song (Capo 3).docx", the user picks.
3. **Never proceed past a >2-page song.** Stop and ask the user how to handle it.
4. **Never leave temp files behind.** The script's `TemporaryDirectory` handles this — don't disable it.

## Required global dependencies

This skill catalogs every global tool it needs. If any are missing, **stop and ask the user to install them** — surface the exact install command. Do not try to work around a missing dependency.

| Tool          | Used for                                       | Install command                       |
| ------------- | ---------------------------------------------- | ------------------------------------- |
| `uv`          | Running the Python script with inline deps     | `brew install uv`                     |
| LibreOffice   | Converting `.doc` and `.docx` → PDF (headless) | `brew install --cask libreoffice`     |

On the first use of this skill in a session, **run `bash scripts/check_deps.sh`** to verify both. If anything is missing, surface the install command to the user and stop until they confirm it's installed.

`pypdf` is declared as an inline dependency in the script's PEP 723 header and is installed automatically by `uv` on first run — no separate install step.

## Configuration

Two files at the repo root:

- **`.env`** (committed) lists every parameter with documentation and blank values.
- **`.env.local`** (gitignored) is where the user sets real machine-specific values. Required keys: `CHORD_SHEETS_DIR`, `OUTPUT_DIR`.

If the script exits with `MISSING_CONFIG`:
1. Ask the user for the missing values (typically `CHORD_SHEETS_DIR` and `OUTPUT_DIR`; possibly `SOFFICE_PATH`).
2. Write/update `.env.local` with the values.
3. Re-run.

Don't write to `.env` — that file documents defaults for the repo, not the user's machine.

## Workflow

Always follow this exact sequence:

### 1. Verify dependencies (first use in a session)

```bash
bash scripts/check_deps.sh
```

If anything fails, surface the install command and stop.

### 2. Resolve titles → candidate files

```bash
uv run scripts/build_binder.py resolve "Title 1" "Title 2" "Title 3"
```

This produces a list of candidate files for each title with similarity scores. The script **never picks for you**.

### 3. Present candidates to the user

Show every title with its candidate files. Explicitly call out:
- Titles with multiple candidates (especially capo variants like "Song (Capo 3).docx") — ask the user which to use.
- Titles with no candidates above the threshold — show the top suggestions and ask the user to either rename their request or point at the right file directly.
- Even titles with a single candidate — confirm before proceeding.

**Wait for explicit confirmation** of the full file list before continuing. Do not proceed past this step on your own.

### 4. Build the binder

Once the user has confirmed the file list and (optionally) a binder name:

```bash
uv run scripts/build_binder.py build \
  --name "Sunday May 26" \
  "/full/path/to/Song A.docx" \
  "/full/path/to/Song B (Capo 3).docx" \
  "/full/path/to/Song C.docx"
```

The `--name` argument is optional — defaults to `Binder YYYY-MM-DD.pdf`. Pick a more descriptive name when you have context (service date, event name).

### 5. Report the result

The script prints a layout table — relay it. Confirm the final PDF path in `OUTPUT_DIR`.

## Exit codes (handle these explicitly)

| Code | Meaning                  | What to do                                                                                                  |
| ---- | ------------------------ | ----------------------------------------------------------------------------------------------------------- |
| 0    | Success                  | Show the user the layout table and final PDF path.                                                          |
| 2    | `MISSING_CONFIG`         | Ask user for the missing values, write `.env.local`, re-run.                                                |
| 3    | `MISSING_DEPENDENCY`     | Surface the install command, stop until the user confirms install.                                          |
| 4    | `UNEXPECTED_PAGE_COUNT`  | A song has >2 pages. Stop and ask the user (trim the source, exclude it, or override).                      |
| 5    | Usage / file error       | Show the error to the user and ask for guidance.                                                            |

## Layout algorithm (for reference)

The script enforces this — you don't have to:

- Page 1 is alone. Spreads are 2-3, 4-5, 6-7, …
- Single-page songs go anywhere.
- Two-page songs must start on an even page so they occupy a single spread. If the next position is odd, a blank page is inserted first.
- Setlist order is preserved (the user gave you the order on purpose).

## Anti-patterns to avoid

- Calling `build` directly without first showing the user the resolved candidates.
- Picking between capo variants ("Capo 3 looks fine") on the user's behalf.
- Silently filtering out a title that didn't match — always surface no-match titles.
- Re-running with a lowered `FUZZY_MATCH_THRESHOLD` to "find" a song that didn't match — instead, ask the user.
- Manually creating temp files or PDF intermediates — let the script handle it.
- Editing `.env` to set the user's paths. That file is for documentation; user paths go in `.env.local`.
