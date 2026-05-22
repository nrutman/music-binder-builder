# Agent instructions — music-binder-builder

This file is the canonical agent-facing reference for this repo. `CLAUDE.md` is a symlink to this file.

## What this repo does

Builds a single, print-ready PDF "binder" of chord sheets for live performance from a list of song titles. Source files are `.doc`/`.docx` chord sheets that live outside the repo (typically in a Google Drive folder). The user gives an agent a setlist, the agent runs the skill, and a merged PDF lands in the user's output folder.

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
- **`.env.local`** (gitignored) is where the user sets real values. Required keys: `CHORD_SHEETS_DIR`, `OUTPUT_DIR`.

If the script exits with `MISSING_CONFIG`, ask the user for the missing values and write them to `.env.local` (never to `.env`). Re-run.

## Workflow contract

Always two phases, in order:

1. **Resolve** — `uv run scripts/build_binder.py resolve "Title" …`. Surface every candidate (including capo variants) to the user and wait for confirmation. The script never picks for you, and you must not pick for the user either.
2. **Build** — `uv run scripts/build_binder.py build [--name "…"] FILE …` with explicit file paths chosen by the user.

Always show the resolved file list to the user before building, even when every title has exactly one candidate.

## Layout guarantees (enforced by the script)

- Page 1 stands alone.
- Spreads are pages 2-3, 4-5, 6-7, …
- A two-page song never crosses a spread; a blank page is inserted before it if needed.
- Setlist order is preserved.
- A song with >2 pages aborts with exit code 4 — stop and ask the user.

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
    ├── build_binder.py             # main `resolve` + `build` script
    └── check_deps.sh               # verifies uv + LibreOffice
```

## When adding things

- A new config parameter? Add it to `.env` with documentation + a blank value, then document overrides in `README.md` → Setup.
- A new dependency? Add it to `scripts/check_deps.sh` AND the dependency table in `skills/build-binder/SKILL.md` AND this file. The skill must catalog every global dependency.
- A new script? Document it in the README and (if agent-invocable) in the skill.
