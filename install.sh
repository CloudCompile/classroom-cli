#!/usr/bin/env bash
# Install classroom: dedicated venv + ~/.local/bin symlink.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="${CLASSROOM_VENV:-$HOME/.venv-classroom}"
BIN="${CLASSROOM_BIN:-$HOME/.local/bin}"

python3 -m venv "$VENV"
"$VENV/bin/pip" -q install --upgrade pip
"$VENV/bin/pip" -q install -e "$HERE"

mkdir -p "$BIN"
ln -sf "$VENV/bin/classroom" "$BIN/classroom"

mkdir -p "$HOME/.config/classroom"
if [ ! -e "$HOME/.config/classroom/config" ]; then
    umask 077
    cat > "$HOME/.config/classroom/config" <<'CFG'
# classroom CLI — chmod 600
# The ONLY Google account the classroom profile may use.
# account=you@yourschool.org
CFG
    chmod 600 "$HOME/.config/classroom/config"
    echo "wrote template $HOME/.config/classroom/config"
fi

echo "installed: $BIN/classroom  ->  $VENV"
"$BIN/classroom" --help >/dev/null && echo "ok"
