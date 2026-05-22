# Agent instructions — music-binder-builder

This file is the canonical agent-facing reference for this repo. `CLAUDE.md` is a symlink to this file.

## What this repo does

Builds a single, print-ready PDF "binder" of chord sheets for live performance. Two modes:

1. **Local** — user supplies a list of song titles; the skill fuzzy-matches them against the user's local chord-sheet folder (typically a Google Drive mount).
2. **Planning Center** — user supplies a service date; the skill calls the Planning Center Services API, finds the plan for that date, walks its song items in order, and pulls each song's chord-sheet attachment.

Either way, the chosen `.doc`/`.docx` files are converted to PDF via LibreOffice and merged into a single binder PDF in the user's output folder.

## Skill: `build-binder`

The skill is the entry point for any "build me a binder" request. It auto-wires into both pi and Claude Code:

- **Canonical**: [`skills/build-binder/SKILL.md`](skills/build-binder/SKILL.md)
- **pi** picks it up via the symlink at `.agents/skills/build-binder` (pi scans `.agents/skills/` in cwd and ancestors).
- **Claude Code** picks it up via the symlink at `.claude/skills/build-binder`.

Read the skill before running anything — it documents the workflow, exit codes, and anti-patterns.

## Required global dependencies

The skill catalogs these too. If anything is missing, stop and surface the install command — don't try to work around it.

| Tool         | Purpose                                  | Install                              |
| ------------ | ---------------------------------------- | ------------------------------------ |
| `uv`         | Runs the Python script with inline deps  | `brew install uv`                    |
| LibreOffice  | `.doc`/`.docx` → PDF (headless)          | `brew install --cask libreoffice`    |

Verify with `bash scripts/check_deps.sh`. `pypdf` is declared as an inline dep in the script's PEP 723 header — `uv` installs it automatically on first run.

## Configuration

- **`.env`** (committed) documents every parameter with blank defaults.
- **`.env.local`** (gitignored) is where the user sets real values.

Required keys per mode:

| Mode             | Required keys                                                        |
| ---------------- | -------------------------------------------------------------------- |
| Local            | `CHORD_SHEETS_DIR`, `OUTPUT_DIR`                                     |
| Planning Center  | `CHORD_SHEETS_DIR`, `OUTPUT_DIR`, `PCO_APPLICATION_ID`, `PCO_SECRET` |

Optional keys (documented in `.env`): `SOFFICE_PATH`, `FUZZY_MATCH_THRESHOLD`, `PCO_DEFAULT_SERVICE_TYPE_ID`.

If the script exits with `MISSING_CONFIG`, ask the user for the missing values and write them to `.env.local` (never to `.env`), then re-run. **Exception**: never read or write `PCO_APPLICATION_ID` / `PCO_SECRET` yourself. Ask the user to fill those in manually ("Generate a Personal Access Token at https://api.planningcenteronline.com/oauth/applications and paste both halves into `.env.local`").

## Workflow contract

Always two phases, in order. Same contract for both modes — only the subcommand names change.

**Local mode** (user gave a setlist of song titles):
1. **Resolve** — `uv run scripts/build_binder.py resolve "Title" …`. Surface every candidate (including capo variants) to the user and wait for confirmation.
2. **Build** — `uv run scripts/build_binder.py build [--name "…"] FILE …` with explicit file paths chosen by the user.

**Planning Center mode** (user gave a service date):
1. **Resolve** — `uv run scripts/build_binder.py pco-resolve --date YYYY-MM-DD […]`. The script picks chord-named attachments where it can; songs with 0 or 2+ chord-named `.doc`/`.docx` attachments exit with `PCO_AMBIGUOUS_ATTACHMENT`. Resolve those by asking the user, then re-run with `--pick SONG_ID=ATTACHMENT_ID`.
2. **Build** — `uv run scripts/build_binder.py pco-build --date YYYY-MM-DD […] [--name "…"]` with the same flags as the successful resolve. Downloads attachments and runs the same conversion/merge pipeline.

Always show the resolved song list to the user before building, even when every song resolves automatically. The script never builds without an explicit `*-build` call.

## Layout guarantees (enforced by the script)

- Page 1 stands alone.
- Spreads are pages 2-3, 4-5, 6-7, …
- A two-page song never crosses a spread; a blank page is inserted before it if needed.
- Setlist order is preserved.
- A song with >2 pages (after chrome trimming) aborts with exit code 4 — stop and ask the user.
- Trailing pages that contain only recurring header/footer chrome (page-number header, song-title line, copyright/CCLI) are auto-trimmed before page counting and merging. A `⚠ ...trimmed N trailing chrome-only page(s)` warning is printed for each affected song. **Always alert the user explicitly** about trims after a build and recommend they examine the source file — a trim usually means the source has a stray trailing paragraph that should be cleaned up. Never edit the source file yourself; flag it and let the user decide. See the skill's *Trailing-chrome trimming* section for suggested phrasing.

## File map

```
.
├── AGENTS.md                       # ← you are here (CLAUDE.md is a symlink)
├── CLAUDE.md  → AGENTS.md
├── README.md                       # human-facing quick start
├── .env                            # documented blank defaults
├── .env.local                      # (gitignored) user's real values
├── .env.local.example              # template
├── .gitignore
├── .agents/skills/build-binder  → ../../skills/build-binder   # pi auto-discovery
├── .claude/skills/build-binder  → ../../skills/build-binder   # Claude Code auto-discovery
├── skills/build-binder/
│   └── SKILL.md                    # the skill (canonical)
└── scripts/
    ├── build_binder.py             # main script: resolve/build (local) + pco-resolve/pco-build (PCO)
    ├── pco.py                      # Planning Center Services API client + plan resolution
    └── check_deps.sh               # verifies uv + LibreOffice
```

## When adding things

- A new config parameter? Add it to `.env` with documentation + a blank value, then document overrides in `README.md` → Setup.
- A new dependency? Add it to `scripts/check_deps.sh` AND the dependency table in `skills/build-binder/SKILL.md` AND this file. The skill must catalog every global dependency.
- A new script? Document it in the README and (if agent-invocable) in the skill.
