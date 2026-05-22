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

Always include the resolved song list in your reply so the user can spot-check. **Block on confirmation only when there's an actual decision to make** — multiple candidates, no candidates, low-confidence matches, or a `PCO_AMBIGUOUS_*` exit. When every song resolves cleanly (one high-confidence candidate each, no ambiguity), go straight to the build. See the skill's *Confirmation policy* section for the full rule.

## Layout guarantees (enforced by the script)

- Page 1 stands alone.
- Spreads are pages 2-3, 4-5, 6-7, …
- A two-page song never crosses a spread; a blank page is inserted before it if needed.
- Setlist order is preserved.
- A song with >2 pages (after chrome trimming) aborts with exit code 4 — stop and ask the user.
- Trailing pages that contain only recurring header/footer chrome (page-number header, song-title line, copyright/CCLI) are auto-trimmed before page counting and merging. A `⚠ ...trimmed N trailing chrome-only page(s)` warning is printed for each affected song. **Always alert the user explicitly** about trims after a build and recommend they examine the source file — a trim usually means the source has a stray trailing paragraph that should be cleaned up. Never edit the source file yourself; flag it and let the user decide. See the skill's *Trailing-chrome trimming* section for suggested phrasing.

## Diagnostics

If PCO mode misbehaves (auth errors, unexpected shapes, missing attachments), run `uv run scripts/build_binder.py pco-doctor`. It's a read-only command that walks the API path the resolver uses (service types → plans → items → attachments at Song/Arrangement/Key/Item level) for a recent past plan and prints structural info only — no credentials. Useful for verifying a freshly configured `.env.local`.

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
├── .github/workflows/ci.yml        # CI: ruff, pytest, hygiene checks, gitleaks, E2E
├── pyproject.toml                  # ruff + pytest config, test-only deps
├── .agents/skills/build-binder  → ../../skills/build-binder   # pi auto-discovery
├── .claude/skills/build-binder  → ../../skills/build-binder   # Claude Code auto-discovery
├── skills/build-binder/
│   └── SKILL.md                    # the skill (canonical)
├── scripts/
│   ├── build_binder.py             # main: resolve/build (local) + pco-resolve/pco-build/pco-doctor
│   ├── pco.py                      # Planning Center Services API client + plan resolution
│   └── check_deps.sh               # verifies uv + LibreOffice
└── tests/                          # pytest suite (run with `uv run pytest`)
```

## Development workflow (when changing code, not just running it)

Main is a protected branch. To make a change:

1. Branch off main: `git checkout -b your-branch`.
2. Make changes. Add or update tests for any non-trivial behavior change.
3. Run the local check suite — same things CI runs:
   ```bash
   uv sync --extra test
   uv run ruff format --check .
   uv run ruff check .
   uv run pytest                # includes e2e if LibreOffice is installed locally
   ```
4. Push and open a PR (`gh pr create`).
5. CI runs two jobs in parallel:
   - **Lint, hygiene, unit tests** — ruff, unit pytest, symlink validity, sensitive-keys-blank check, script `+x` check, gitleaks. (~10s)
   - **End-to-end (LibreOffice)** — installs LibreOffice, runs `pytest -m e2e`. (~2min)
   Both must pass; both are required by branch protection.
6. Squash-merge — only allowed merge mode (`gh pr merge --squash --delete-branch`).

Do NOT push directly to `main`. The repo ruleset blocks it server-side. Force-pushes and branch deletion are also blocked.

## When adding things

- **New config parameter?** Add it to `.env` with documentation + a blank value. If sensitive (credential), add it to the `SENSITIVE_KEYS` list in `.github/workflows/ci.yml` so CI enforces it stays blank in the committed `.env`. Then document overrides in `README.md` → Setup.
- **New global dependency?** Add it to `scripts/check_deps.sh` AND the dependency table in `skills/build-binder/SKILL.md` AND this file. The skill must catalog every global dependency.
- **New Python dependency?** If it's a runtime dep, add it to the PEP 723 header of the script that uses it. If it's a test/lint dep, add it to `pyproject.toml`'s `[project.optional-dependencies].test`.
- **New behavior worth a test?** Add it to `tests/`. The unit suite is fast (~1s) and lives in pure-logic files; the `@pytest.mark.e2e` suite needs LibreOffice. Match the existing file naming (`test_<area>.py`).
- **New script?** Document it in the README and (if agent-invocable) in the skill. Make it `+x` so the CI hygiene check passes.
- **New required CI status check?** After it lands and runs once, add its job name to the repo ruleset (`gh api repos/.../rulesets/...`).
