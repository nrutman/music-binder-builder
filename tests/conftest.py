"""Shared pytest configuration.

`scripts/` isn't a package — the build script is invoked directly via `uv run`.
For tests we want to `import build_binder` and `import pco`, so we add
`scripts/` to `sys.path` here.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
