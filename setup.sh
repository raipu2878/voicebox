#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

python3 -m venv .venv
.venv/bin/pip install -U pip -q
.venv/bin/pip install -r requirements.txt -q

mkdir -p "$HOME/.local/bin"
ln -sfn "$ROOT/vo" "$HOME/.local/bin/vo"
chmod +x "$ROOT/vo" "$ROOT/setup.sh"

echo "Готово:  vo  — окно always-on-top"
echo "  /settings  /exit"
