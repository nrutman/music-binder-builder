---
name: build-binder
description: >
  Build a printable PDF "binder" of chord sheets for live performance. Works in
  two modes: (1) LOCAL — user supplies a list of song titles; the skill
  fuzzy-matches them against the user's chord-sheet folder; (2) PLANNING CENTER
  — user supplies a service date; the skill looks up the Planning Center
  Services plan for that date and pulls the chord-sheet attachment for each
  song in plan order. In both modes the chosen .doc/.docx files are converted
  to PDF via LibreOffice and merged so page 1 is alone and every two-page song
  lands on a single spread (2-3, 4-5, 6-7, …) without crossing.
  TRIGGER when: the user asks to build a binder, setlist PDF, chord sheet binder,
  worship binder, etc. — either with a list of song titles (local mode) or with
  a service date / "the Sunday service" / "this week's plan" (PCO mode). Phrases
  like "build a binder for Sunday May 24", "make me a chord sheet PDF with
  these songs", "binder this setlist", "build a binder from Planning Center".
  DO NOT TRIGGER when: the user wants to author or edit a chord sheet (that's a
  different repo / workflow), wants to merge already-PDF files (this skill is for
  .doc/.docx source files), or is asking general questions about the repo.
user-invocable: true
---

# Build Binder

**Goal**: turn a list of song titles into a single, print-ready binder PDF where every chord sheet falls on a clean spread.

## Iron rules

1. **Never guess a song match.** Always run `resolve` / `pco-resolve` first. Show every candidate to the user **whenever there's a decision to make** — see the [Confirmation policy](#confirmation-policy) below.
2. **Never auto-resolve capo variants.** If a title matches both `Song.docx` and `Song (Capo 3).docx` (local), or a song's Key has both `Song - Chord.docx` and `Song - Chord Capo.docx` (PCO), the user picks.
3. **Never proceed past a >2-page song.** Stop and ask the user how to handle it.
4. **Never leave temp files behind.** The script's `TemporaryDirectory` handles this — don't disable it.
5. **Always alert the user about trimmed pages.** Trims indicate the source file probably needs cleanup — surface them as actionable items, not just relayed log lines (see [Trailing-chrome trimming](#trailing-chrome-trimming)).

## Confirmation policy

Confirmation is required only when there's an actual decision to make. If the resolve step is unambiguous — every song has exactly one auto-picked candidate — you can go straight to the build. Always show the resolved list in your reply either way; just don't *block* on confirmation when nothing's in doubt.

**Confirm before building when any of these are true:**
- Any title has 2+ candidate files (local) or 2+ chord-named attachments (PCO).
- Any title has 0 candidates (local: no fuzzy match above threshold; PCO: no chord-named attachments on the Key / Arrangement / Song).
- Any title's match looks suspicious — a low fuzzy score, an unexpected song title ("that doesn't sound like a song the user would pick"), or a filename that suggests the wrong version.
- The user explicitly asked you to confirm first ("show me the list before you build").
- `pco-resolve` exits with `PCO_AMBIGUOUS_SERVICE_TYPE` or `PCO_AMBIGUOUS_PLAN`.

**Skip confirmation and build directly when all of these are true:**
- Every song resolved to exactly one candidate.
- For local mode: every fuzzy match scored ≥ 0.90 (one clearly-correct file).
- For PCO mode: every song's chord file was auto-picked (`auto`, not `user pick`), no `PCO_AMBIGUOUS_*` exit.
- Nothing in the resolved list looks suspicious.

When you skip confirmation, still include the resolved table in your reply so the user can spot-check after the fact.

## Required global dependencies

This skill catalogs every global tool it needs. If any are missing, **stop and ask the user to install them** — surface the exact install command. Do not try to work around a missing dependency.

| Tool          | Used for                                       | Install command                       |
| ------------- | ---------------------------------------------- | ------------------------------------- |
| `uv`          | Running the Python script with inline deps     | `brew install uv`                     |
| LibreOffice   | Converting `.doc` and `.docx` → PDF (headless) | `brew install --cask libreoffice`     |

On the first use of this skill in a session, **run `bash scripts/check_deps.sh`** to verify both. If anything is missing, surface the install command to the user and stop until they confirm it's installed.

`pypdf` and `httpx` are declared as inline dependencies in the script's PEP 723 header and are installed automatically by `uv` on first run — no separate install step.

## Configuration

Two files at the repo root:

- **`.env`** (committed) lists every parameter with documentation and blank values.
- **`.env.local`** (gitignored) is where the user sets real machine-specific values.

Required keys per mode:

| Mode             | Required keys                                              |
| ---------------- | ---------------------------------------------------------- |
| Local (`build`)  | `CHORD_SHEETS_DIR`, `OUTPUT_DIR`                           |
| PCO (`pco-build`)| `CHORD_SHEETS_DIR`*, `OUTPUT_DIR`, `PCO_APPLICATION_ID`, `PCO_SECRET` |

\* `CHORD_SHEETS_DIR` is still validated (the script loads one config) but isn't actually read in PCO mode — keep it set.

If the script exits with `MISSING_CONFIG`:
1. Ask the user for the missing values. **Never read or quote the user's PCO credentials yourself.** Instead, tell the user which keys are missing and ask them to fill in `.env.local` manually.
2. For non-credential keys (`CHORD_SHEETS_DIR`, `OUTPUT_DIR`, etc.) you can write `.env.local` directly with the values the user supplied.
3. Re-run.

Don't write to `.env` — that file documents defaults for the repo, not the user's machine.

### Planning Center credential handling

`PCO_APPLICATION_ID` and `PCO_SECRET` are sensitive. Do not echo them, do not paste them into chat, and do not read them from `.env.local` to display. If the user needs to set them, instruct:

> Generate a Personal Access Token at https://api.planningcenteronline.com/oauth/applications (→ Personal Access Tokens → New). Paste both halves into `.env.local` yourself — I won't touch your credentials.

## Workflow — Local mode

Use when the user supplies song titles.

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

### 3. Apply the confirmation policy

Apply [Confirmation policy](#confirmation-policy):
- If every title has exactly one high-confidence candidate (≥ 0.90), skip confirmation and proceed to build. Include the resolved table in your reply.
- If any title has multiple candidates, no candidates, or a low-confidence match, present the list to the user and wait for their pick.

### 4. Build the binder

With the resolved file list (and optionally a binder name):

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

## Workflow — Planning Center mode

Use when the user supplies a service date (or asks for "the Sunday service", "this week's plan", etc.) rather than a song list. Same iron rules apply. The [Confirmation policy](#confirmation-policy) governs whether to block on user confirmation — *not* the older "always confirm" rule.

### 1. Verify dependencies (first use in a session)

Same `bash scripts/check_deps.sh` as local mode.

### 2. Resolve the plan → song list with attachment picks

```bash
uv run scripts/build_binder.py pco-resolve --date 2026-05-24
```

This:
- Looks up the PCO plan whose `sort_date` falls on the given date.
- Lists every song item in plan order.
- For each song, looks up `.doc`/`.docx` attachments scoped to the item's selected **Key** first (chord charts live on Keys in this org), then **Arrangement**, then **Song** — stopping at the first level that has a chord-named match.
- Picks the chord-named attachment when exactly one matches at the best level.
- For songs with 0 or 2+ chord-named attachments, reports the candidates (including the level they were found on) and exits with `PCO_AMBIGUOUS_ATTACHMENT` (exit code 6). Capo variants (`Song - Chord.docx` + `Song - Chord Capo.docx` on the same Key) are a common 2+ case.

### Diagnostic: pco-doctor

If PCO mode is misbehaving, run `uv run scripts/build_binder.py pco-doctor` for a read-only connectivity + shape check. It probes every attachment location (Song / Arrangement / Key / Item) for a recent past plan and prints structural info only — never credentials.

### 3. Handle ambiguities and special cases

- **`PCO_AMBIGUOUS_SERVICE_TYPE`** — the org has multiple service types. Show the list to the user, then re-run with `--service-type <ID>` (or have the user set `PCO_DEFAULT_SERVICE_TYPE_ID` in `.env.local` for future calls).
- **`PCO_NO_PLAN_FOR_DATE`** — no plan on that date. Confirm the date with the user and the service type.
- **`PCO_AMBIGUOUS_PLAN`** — multiple plans on the same date. Show titles to the user and re-run with `--plan-id <ID>`.
- **`PCO_AMBIGUOUS_ATTACHMENT`** — some songs need a `--pick`. For each unresolved song, show the user every candidate attachment (chord-named ones if any, else all `.doc`/`.docx` on the song). Collect picks from the user, then re-run:
  ```bash
  uv run scripts/build_binder.py pco-resolve --date 2026-05-24 \
    --pick 12345=67890 \
    --pick 12346=67891
  ```
  `SONG_ID` and `ATTACHMENT_ID` are both shown in the resolve output. Keep accumulating picks until `pco-resolve` exits 0.

### 4. Apply the confirmation policy

Apply [Confirmation policy](#confirmation-policy):
- If `pco-resolve` exited 0 and every song was `auto`-picked from its Key, skip confirmation and proceed to build. Include the resolved table in your reply.
- If you needed any `--pick` to disambiguate (capo variants, Chord vs Chord Capo, etc.), the user has already made those decisions, but still show the final list and wait for their go-ahead before building — because the picks themselves are the decision point.
- If anything in the resolved list looks suspicious (wrong-looking title, missing song, etc.), confirm before building.

### 5. Build the binder

Re-run with `pco-build`, passing the same `--date` (and any `--service-type` / `--plan-id` / `--pick` arguments), plus an optional `--name`:

```bash
uv run scripts/build_binder.py pco-build --date 2026-05-24 \
  --name "Sunday May 24" \
  --pick 12345=67890
```

This downloads each chord sheet from PCO into a temp dir, converts and merges via the same pipeline as local mode, then cleans up.

### 6. Report the result

Same as local mode — relay the layout table and the final PDF path. Surface any `⚠ trimmed N trailing chrome-only page(s)` warnings to the user (see [Trailing-chrome trimming](#trailing-chrome-trimming)).

## Exit codes (handle these explicitly)

| Code | Meaning                  | What to do                                                                                                  |
| ---- | ------------------------ | ----------------------------------------------------------------------------------------------------------- |
| 0    | Success                  | Show the user the layout table and final PDF path.                                                          |
| 2    | `MISSING_CONFIG`         | Ask user for the missing values. For non-credentials, write `.env.local` yourself. For PCO credentials, ask the user to fill in `.env.local` manually — don't read or echo them. Re-run. |
| 3    | `MISSING_DEPENDENCY`     | Surface the install command, stop until the user confirms install.                                          |
| 4    | `UNEXPECTED_PAGE_COUNT`  | A song has >2 pages. Stop and ask the user (trim the source, exclude it, or override).                      |
| 5    | Usage / file error       | Show the error to the user and ask for guidance.                                                            |
| 6    | `PCO_*`                  | PCO-specific (ambiguous service type / plan / attachment, or none found). Read the printed error, surface to user, then re-run with the appropriate `--service-type` / `--plan-id` / `--pick` flag(s). |

## Layout algorithm (for reference)

The script enforces this — you don't have to:

- Page 1 is alone. Spreads are 2-3, 4-5, 6-7, …
- Single-page songs go anywhere.
- Two-page songs must start on an even page so they occupy a single spread. If the next position is odd, a blank page is inserted first.
- Setlist order is preserved (the user gave you the order on purpose).

## Trailing-chrome trimming

Many source `.doc`/`.docx` chord sheets have a stray trailing newline that pushes a header/footer-only page onto a second page (no actual song content, just the recurring page-number header, song-title-and-composer line, and copyright/CCLI footer). The build script detects these — when every line on a trailing page (digits and case normalized) also appears on an earlier page and the unique residual is fewer than 5 words, the page is treated as effectively absent.

When this happens you'll see a warning like:

    ⚠ Holy Holy Holy - Chord.doc: trimmed 1 trailing chrome-only page (only header/footer content)

And the layout table shows the effective page count with the trim noted, e.g. `1 (-1)`.

### When you see a trim, alert the user

This is required behavior, not optional. A trim almost always means the source `.doc`/`.docx` has a stray trailing paragraph or page break that should be cleaned up so the file is accurate going forward. After a successful build, if any trims occurred:

1. **Call them out explicitly** in your reply — don't bury them in a log dump. List each affected file by name with the number of pages trimmed.
2. **Recommend the user examine the source file.** Suggested phrasing:
   > Heads up: I had to trim 1 trailing blank/chrome-only page from `Holy Holy Holy - Chord.doc`. The page only contained the recurring header/footer (no song content), so the source likely has a stray trailing paragraph or page break. Worth opening in Word or LibreOffice to clean up so future binders use the corrected source.
3. **Don't try to edit the source file yourself.** These live in the user's Google Drive folder and may be shared/canonical. Flag it and let the user decide.
4. **If multiple files trim**, list them all — a pattern across many files might point at a shared template that needs fixing.

## Anti-patterns to avoid

- Calling `build` directly without first showing the user the resolved candidates.
- Picking between capo variants ("Capo 3 looks fine") on the user's behalf.
- Silently filtering out a title that didn't match — always surface no-match titles.
- Re-running with a lowered `FUZZY_MATCH_THRESHOLD` to "find" a song that didn't match — instead, ask the user.
- Manually creating temp files or PDF intermediates — let the script handle it.
- Editing `.env` to set the user's paths. That file is for documentation; user paths go in `.env.local`.
