# music-binder-builder

Turn a list of song titles into a single, print-ready PDF "binder" of chord sheets — page 1 alone, every two-page song on a clean spread, no awkward page turns mid-song.

You give an agent a setlist. The agent fuzzy-matches each title against your chord-sheet folder, shows you the candidates (so capo variants don't get picked silently), converts the chosen `.doc`/`.docx` files to PDF via LibreOffice, and drops a merged binder in your output folder.

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

Required keys:

| Key                 | What it is                                                              |
| ------------------- | ----------------------------------------------------------------------- |
| `CHORD_SHEETS_DIR`  | Absolute path to your folder of `.doc`/`.docx` chord sheets (recursive) |
| `OUTPUT_DIR`        | Where generated binder PDFs land                                        |

Optional keys (documented in `.env`):

| Key                     | What it is                                                       |
| ----------------------- | ---------------------------------------------------------------- |
| `SOFFICE_PATH`          | Path to LibreOffice's `soffice`. Leave blank to autodetect.      |
| `FUZZY_MATCH_THRESHOLD` | Min similarity score for title→filename matching (default 0.75). |

The build script refuses to run if `CHORD_SHEETS_DIR` or `OUTPUT_DIR` is unset, and tells you exactly what to add.

### 3. Build a binder (via an agent)

The skill is auto-wired for pi (via `.agents/skills/`) and Claude Code (via `.claude/skills/`) when you start a session in this directory. Just ask:

> Build me a binder for Sunday with Amazing Grace, How Great Thou Art, and Come Thou Fount.

The agent will:

1. Resolve each title against `CHORD_SHEETS_DIR` and **show you the candidates** (including any capo variants).
2. Wait for you to confirm the file list.
3. Convert each to PDF, lay them out so two-page songs land on a single spread, and write the merged PDF to `OUTPUT_DIR`.

### 4. Build a binder (manually)

```bash
# Step 1 — see what matches
uv run scripts/build_binder.py resolve "Amazing Grace" "How Great Thou Art"

# Step 2 — build from explicit paths (after picking among any capo variants)
uv run scripts/build_binder.py build \
  --name "Sunday May 26" \
  "/path/to/Amazing Grace.docx" \
  "/path/to/How Great Thou Art (Capo 2).docx"
```

## How the layout works

- **Page 1 stands alone.** Spreads are 2-3, 4-5, 6-7, …
- **One-page songs** can land anywhere.
- **Two-page songs** never cross a spread — if the next available position is odd, a blank page is inserted first so the song falls on a single facing spread.
- **Setlist order is preserved.** No reordering for efficiency.
- **More than 2 pages?** The script stops and asks. Trim the source or exclude the song.

## Repo layout

```
.
├── AGENTS.md               # canonical agent instructions
├── CLAUDE.md  → AGENTS.md  # symlink
├── README.md               # this file
├── .env                    # documented blank defaults
├── .env.local              # (gitignored) your machine paths
├── .env.local.example      # template
├── .agents/skills/build-binder  → ../../skills/build-binder   # pi auto-discovery
├── .claude/skills/build-binder  → ../../skills/build-binder   # Claude Code auto-discovery
├── skills/build-binder/
│   └── SKILL.md            # the skill (canonical)
└── scripts/
    ├── build_binder.py     # `resolve` + `build` subcommands
    └── check_deps.sh       # verify uv + LibreOffice
```

## How the skill is auto-wired

`skills/build-binder/SKILL.md` is the single source of truth. Two symlinks make it visible to the two agent harnesses without duplicating content:

- `.agents/skills/build-binder` → pi scans `.agents/skills/` in the working directory and its ancestors.
- `.claude/skills/build-binder` → Claude Code scans `.claude/skills/` for project-scoped skills.

Both symlinks point at the same `skills/build-binder/` directory, so edits propagate everywhere.

## Troubleshooting

- **`MISSING_CONFIG`** — `.env.local` is missing or doesn't set a required key. Open it and set `CHORD_SHEETS_DIR` and `OUTPUT_DIR`.
- **`MISSING_DEPENDENCY: LibreOffice`** — `brew install --cask libreoffice`, or set `SOFFICE_PATH` in `.env.local` if it lives somewhere unusual.
- **No candidates found for a title** — try a longer or more distinctive piece of the title, or check the filename in `CHORD_SHEETS_DIR`. You can also tune `FUZZY_MATCH_THRESHOLD` down a bit, but the safer fix is to ask the agent to search with a better query.
- **Multiple candidates for one title** — that's the point of the resolve step. Pick the file you want and pass its full path to `build`.
- **A song has > 2 pages** — the script aborts. Trim the source `.docx` or exclude the song from the setlist.
