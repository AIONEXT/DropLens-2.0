#!/usr/bin/env bash
# Regenerate requirements-lock.txt from requirements.txt in a clean venv.
#
#   scripts/lock_requirements.sh
#
# A clean venv matters: freezing the developer's environment would silently
# capture dev/build-only packages and platform-specific extras.
set -euo pipefail

cd "$(dirname "$0")/.."
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

python3 -m venv "$TMP/venv"
"$TMP/venv/bin/python" -m pip install --quiet --upgrade pip
"$TMP/venv/bin/python" -m pip install --quiet -r requirements.txt

{
  cat <<'HEADER'
# DropLens — pinned runtime dependency lock
#
# Purpose: reproducible installs. Every package the runtime actually imports is
# pinned to an exact version so two machines (and two builds) resolve the same
# tree. `requirements.txt` stays the human-authored *range* file; this file is
# what CI, the release build and enterprise rollouts install.
#
#   pip install -r requirements-lock.txt
#
# Regenerate after changing requirements.txt:
#   Linux/macOS: scripts/lock_requirements.sh
#   Windows:     scripts/lock_requirements.ps1
#
# NOTE: the set below is resolved for the machine that ran the lock script. A few
# packages are platform-specific (tkinterdnd2, pystray backends,
# backports.zstd); regenerate on the target OS before a release build.
HEADER
  echo ""
  "$TMP/venv/bin/python" -m pip freeze | sort
} > requirements-lock.txt

echo "wrote requirements-lock.txt ($(grep -c '==' requirements-lock.txt) pinned packages)"
