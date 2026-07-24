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

# Desktop entry for Super/launcher search ("voicebox")
APP_DIR="$HOME/.local/share/applications"
mkdir -p "$APP_DIR"
# Resolve absolute path for Exec so it works even if PATH is incomplete
{
  echo "[Desktop Entry]"
  echo "Type=Application"
  echo "Version=1.0"
  echo "Name=voicebox"
  echo "GenericName=TTS Voice Box"
  echo "Comment=Always-on-top text-to-speech for Linux"
  echo "Exec=$HOME/.local/bin/vo"
  echo "TryExec=$HOME/.local/bin/vo"
  echo "Icon=audio-input-microphone"
  echo "Terminal=false"
  echo "Categories=AudioVideo;Utility;Accessibility;"
  echo "Keywords=voice;tts;speech;speak;voicebox;elevenlabs;"
  echo "StartupNotify=true"
  echo "StartupWMClass=voicebox"
} > "$APP_DIR/voicebox.desktop"
chmod +x "$APP_DIR/voicebox.desktop"

if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database "$APP_DIR" 2>/dev/null || true
fi

# Ensure ~/.local/bin is on PATH for this user (best-effort)
if ! echo ":$PATH:" | grep -q ":$HOME/.local/bin:"; then
  for rc in "$HOME/.bashrc" "$HOME/.profile" "$HOME/.zshrc"; do
    if [[ -f "$rc" ]] && ! grep -q '\.local/bin' "$rc" 2>/dev/null; then
      echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$rc"
      echo "Note: added ~/.local/bin to PATH in $rc (open a new terminal)"
      break
    fi
  done
fi

echo "Done."
echo "  vo                 — open voicebox (terminal stays free)"
echo "  Super → voicebox   — app launcher entry installed"
echo "  VOICEBOX_FOREGROUND=1 vo  — debug (stay in terminal)"
