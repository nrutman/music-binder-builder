# music-binder-builder

Turn a setlist into a single, print-ready PDF "binder" of chord sheets — page 1 alone, every two-page song on a clean spread, no awkward page turns mid-song.

Two ways to feed it:

- **Local mode** — give an agent a list of song titles. The agent fuzzy-matches each title against your chord-sheet folder, shows you the candidates (so capo variants don't get picked silently), and builds the binder.
- **Planning Center mode** — give an agent a service date. The agent calls the Planning Center Services API, walks your service plan in order, pulls each song's chord-sheet attachment, and builds the binder.

Either way, the chosen `.doc`/`.docx` files get converted to PDF via LibreOffice and merged into a single binder in your output folder.

## Quick start

### 1. Install global dependencies

```bash
brew install uv
brew install --cask libreoffice
```

Verify:

```bash
bash scripts/check_deps.sh
```

### 2. Configure your machine paths

`.env` (committed) lists every parameter the build script reads, with documentation and intentionally-blank values. To set real values, create `.env.local` (which is gitignored) and override the keys you care about. **`.env.local` wins over `.env`.**

Start from the template:

```bash
cp .env.local.example .env.local
$EDITOR .env.local
```

Required keys (always):

| Key                 | What it is                                                              |
| ------------------- | ----------------------------------------------------------------------- |
| `CHORD_SHEETS_DIR`  | Absolute path to your folder of `.doc`/`.docx` chord sheets (recursive) |
| `OUTPUT_DIR`        | Where generated binder PDFs land                                        |

Required only for Planning Center mode:

| Key                     | What it is                                                                                                  |
| ----------------------- | ----------------------------------------------------------------------------------------------------------- |
| `PCO_APPLICATION_ID`    | Planning Center Personal Access Token — application ID half. Generate at <https://api.planningcenteronline.com/oauth/applications>. |
| `PCO_SECRET`            | Personal Access Token secret half (paired with the application ID above).                                   |

Optional keys (documented in `.env`):

| Key                            | What it is                                                                                  |
| ------------------------------ | ------------------------------------------------------------------------------------------- |
| `SOFFICE_PATH`                 | Path to LibreOffice's `soffice`. Leave blank to autodetect.                                 |
| `FUZZY_MATCH_THRESHOLD`        | Min similarity score for title→filename matching (default 0.75).                            |
| `PCO_DEFAULT_SERVICE_TYPE_ID`  | Default service type ID. Only useful if your org has multiple service types in PCO.         |

The build script refuses to run if `CHORD_SHEETS_DIR` or `OUTPUT_DIR` is unset, and tells you exactly what to add. The PCO subcommands additionally require `PCO_APPLICATION_ID` and `PCO_SECRET`.

### 3. Build a binder (via an agent)

The skill is auto-wired for pi (via `.agents/skills/`) and Claude Code (via `.claude/skills/`) when you start a session in this directory. Either way, just ask:

> Build me a binder for Sunday with Amazing Grace, How Great Thou Art, and Come Thou Fount.

…or with Planning Center:

> Build me a binder from the May 24 Sunday service.

The agent will resolve the song list, **show you the candidates** (including any capo variants or PCO attachment choices), wait for your confirmation, and then build the binder.

### 4. Build a binder (manually)

**Local mode** — you supply a setlist:

```bash
# Step 1 — see what matches
uv run scripts/build_binder.py resolve "Amazing Grace" "How Great Thou Art"

# Step 2 — build from explicit paths (after picking among any capo variants)
uv run scripts/build_binder.py build \
  --name "Sunday May 26" \
  "/path/to/Amazing Grace.docx" \
  "/path/to/How Great Thou Art (Capo 2).docx"
```

**Planning Center mode** — you supply a service date:

```bash
# Step 1 — resolve the plan + propose attachments. Exits 6 if any song has
# 0 or 2+ chord-named .doc/.docx attachments; re-run with --pick to resolve.
uv run scripts/build_binder.py pco-resolve --date 2026-05-24

# Step 2 — download attachments and build the binder. Pass any --pick flags
# you needed in step 1.
uv run scripts/build_binder.py pco-build --date 2026-05-24 \
  --name "Sunday May 24" \
  --pick 12345=67890
```

`SONG_ID` and `ATTACHMENT_ID` are both shown in the `pco-resolve` output. There's also a `pco-doctor` subcommand for a read-only connectivity + shape diagnostic (`uv run scripts/build_binder.py pco-doctor`).

**Where PCO chord charts live**: in this app's experience, chord charts are attached to the **Key** that a song is set in for a given plan (so that different keys can have different chord charts — e.g. `Song - Chord.docx` plus `Song - Chord Capo.docx` on the same Key). Lyric sheets are attached to **Arrangements**. The resolver follows that priority: Key first, then Arrangement, then Song-level.

## How the layout works

- **Page 1 stands alone.** Spreads are 2-3, 4-5, 6-7, …
- **One-page songs** can land anywhere.
- **Two-page songs** never cross a spread — if the next available position is odd, a blank page is inserted first so the song falls on a single facing spread.
- **Setlist order is preserved.** No reordering for efficiency.
- **Trailing “chrome-only” pages** — source `.doc`/`.docx` files sometimes have a stray trailing paragraph that pushes the header/footer onto a second page with no actual song content. The script detects these (when every line is recurring boilerplate from prior pages and the unique residual is under 5 words) and trims them before layout, with a `⚠ ...trimmed N trailing chrome-only page(s)` warning so it's never silent. The original source isn't modified — only the converted PDF used for the binder.
- **More than 2 pages?** The script stops and asks. Trim the source or exclude the song.

## Repo layout

```
.
├── AGENTS.md               # canonical agent instructions
├── CLAUDE.md  → AGENTS.md  # symlink
├── README.md               # this file
├── .env                    # documented blank defaults
├── .env.local              # (gitignored) your machine paths + PCO creds
├── .env.local.example      # template
├── .agents/skills/build-binder  → ../../skills/build-binder   # pi auto-discovery
├── .claude/skills/build-binder  → ../../skills/build-binder   # Claude Code auto-discovery
├── skills/build-binder/
│   └── SKILL.md            # the skill (canonical)
└── scripts/
    ├── build_binder.py     # local resolve/build + PCO pco-resolve/pco-build subcommands
    ├── pco.py              # Planning Center Services API client + plan resolution
    └── check_deps.sh       # verify uv + LibreOffice
```

## How the skill is auto-wired

`skills/build-binder/SKILL.md` is the single source of truth. Two symlinks make it visible to the two agent harnesses without duplicating content:

- `.agents/skills/build-binder` → pi scans `.agents/skills/` in the working directory and its ancestors.
- `.claude/skills/build-binder` → Claude Code scans `.claude/skills/` for project-scoped skills.

Both symlinks point at the same `skills/build-binder/` directory, so edits propagate everywhere.

## Troubleshooting

- **`MISSING_CONFIG`** — `.env.local` is missing or doesn't set a required key. For local mode set `CHORD_SHEETS_DIR` and `OUTPUT_DIR`. For PCO mode also set `PCO_APPLICATION_ID` and `PCO_SECRET`.
- **`MISSING_DEPENDENCY: LibreOffice`** — `brew install --cask libreoffice`, or set `SOFFICE_PATH` in `.env.local` if it lives somewhere unusual.
- **No candidates found for a title** (local) — try a longer or more distinctive piece of the title, or check the filename in `CHORD_SHEETS_DIR`. You can also tune `FUZZY_MATCH_THRESHOLD` down a bit, but the safer fix is to ask the agent to search with a better query.
- **Multiple candidates for one title** (local) — that's the point of the resolve step. Pick the file you want and pass its full path to `build`.
- **`PCO_AMBIGUOUS_SERVICE_TYPE`** — your org has multiple service types. Re-run with `--service-type <ID>` or set `PCO_DEFAULT_SERVICE_TYPE_ID` in `.env.local`.
- **`PCO_NO_PLAN_FOR_DATE`** — no plan exists on that date for the chosen service type. Double-check the date and service type in PCO.
- **`PCO_AMBIGUOUS_PLAN`** — multiple plans on the same date. Re-run with `--plan-id <ID>` picked from the listed candidates.
- **`PCO_AMBIGUOUS_ATTACHMENT`** — a song has 0 or 2+ chord-named attachments. The resolve output lists candidates; re-run with `--pick SONG_ID=ATTACHMENT_ID` (repeatable).
- **A song has > 2 pages** — the script aborts. Trim the source `.docx` or exclude the song from the setlist.
