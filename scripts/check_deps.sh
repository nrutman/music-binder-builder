#!/bin/bash
#
# Verify every global dependency the binder builder needs. Prints the exact
# install command for anything missing. Exits non-zero if anything is missing.
#
set -e

ok=true

# Minimum uv version required for PEP 723 inline script dependencies (the
# `# /// script` header in build_binder.py). Older `uv` silently ignores the
# header, runs the script with system Python, and crashes on `import pypdf`.
MIN_UV="0.4.4"

print_ok()      { printf "  ✓ %-12s %s\n" "$1" "$2"; }
print_miss()    { printf "  ✗ %-12s not found — %s\n" "$1" "$2"; ok=false; }
print_too_old() { printf "  ✗ %-12s %s is too old; need >= %s — %s\n" "$1" "$2" "$3" "$4"; ok=false; }

# Returns 0 if $1 >= $2 by version sort.
version_ge() { printf '%s\n%s\n' "$2" "$1" | sort -V -C; }

echo "Checking music-binder-builder dependencies..."
echo ""
echo "Required:"

# --- uv --------------------------------------------------------------------
if command -v uv &>/dev/null; then
  uv_version=$(uv --version 2>&1 | awk '{print $2}')
  if version_ge "$uv_version" "$MIN_UV"; then
    print_ok "uv" "$uv_version (>= $MIN_UV)"
  else
    print_too_old "uv" "$uv_version" "$MIN_UV" "brew upgrade uv"
  fi
else
  print_miss "uv" "brew install uv  (need >= $MIN_UV for PEP 723 inline deps)"
fi

# --- LibreOffice -----------------------------------------------------------
# Mirror the resolution order used by build_binder.py:
#   1) $SOFFICE_PATH from .env.local (if set)
#   2) /Applications/LibreOffice.app/Contents/MacOS/soffice
#   3) `which soffice`
soffice_path=""
env_local="$(cd "$(dirname "$0")/.." && pwd)/.env.local"
if [ -f "$env_local" ]; then
  configured="$(grep -E '^SOFFICE_PATH=' "$env_local" | tail -1 | cut -d= -f2- | sed 's/^["'"'"']//; s/["'"'"']$//')"
  if [ -n "$configured" ] && [ -x "$configured" ]; then
    soffice_path="$configured"
  fi
fi
if [ -z "$soffice_path" ] && [ -x "/Applications/LibreOffice.app/Contents/MacOS/soffice" ]; then
  soffice_path="/Applications/LibreOffice.app/Contents/MacOS/soffice"
fi
if [ -z "$soffice_path" ] && command -v soffice &>/dev/null; then
  soffice_path="$(command -v soffice)"
fi

if [ -n "$soffice_path" ]; then
  ver=$("$soffice_path" --version 2>&1 | head -1)
  print_ok "LibreOffice" "$ver"
else
  print_miss "LibreOffice" "brew install --cask libreoffice  (used to convert .doc/.docx → PDF)"
fi

echo ""

if [ "$ok" = true ]; then
  echo "All required dependencies installed."
  exit 0
else
  echo "Some required dependencies are missing. Install them and re-run."
  exit 1
fi
