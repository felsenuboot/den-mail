#!/usr/bin/env bash
# Updates a checkout installed with install.sh: pulls the latest commit and
# refreshes the launcher, desktop entry and icons. The launcher runs straight
# from this checkout, so the next start of Den Mail uses the new code.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
if pgrep -x den-mail >/dev/null 2>&1 || pgrep -f "python3 -m den_mail" >/dev/null 2>&1; then
  echo "Den Mail is running; quit it (Ctrl+Q) before updating so it restarts on the new code." >&2
  exit 1
fi
git -C "$HERE" pull --ff-only
"$HERE/install.sh"
