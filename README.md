# voicebox

Always-on-top Linux TTS window: type text → speak it.  
Stays above games and messengers (Dota / CS / Discord / Telegram).

Speech is **queued** — new phrases never interrupt the current one.

## Quick start

```bash
git clone https://github.com/raipu2878/voicebox.git
cd voicebox
./setup.sh
sudo apt install mpv
vo
```

`./setup.sh` creates a venv, installs dependencies, and links `vo` into `~/.local/bin`.

## One window, three pages

| Tab | Purpose |
|--|--|
| **Speak** | Type text, press Enter to speak, slash commands |
| **Phrases** | Presets, global hotkeys, `/commands`, per-phrase TTS |
| **Settings** | Mode, volume, mood, theme, default TTS, API keys |

Native window controls (minimize / maximize / close) come from your desktop.

### Speak page

| Input | Action |
|--|--|
| text + **Enter** | Speak (line mode) or flush word (words mode) |
| `/settings` | Open Settings |
| `/phrases` | Open Phrases |
| `/exit` | Quit |
| `/your-cmd` | Queue the phrase bound to that slash command |

### Phrases page

1. **+ New** → fill the editor → **✓ SAVE PHRASE**
2. **⌨ Hotkey** → press a combo (e.g. **Ctrl+Shift+1** on the top number row)
3. **/ Command** → e.g. `/hello`
4. **🎙 TTS** → `auto` / `eleven` / `edge` / `gtts`
5. **▶ Test** / **🗑 Delete**

| Trigger | Notes |
|--|--|
| Global hotkey | X11 via pynput; prefer borderless / windowed games (exclusive fullscreen may grab keys) |
| Slash command | Type `/cmd` + Enter on the Speak page |

Hotkeys support top-row digits and numpad. **Shift+digit** (e.g. `!` for `1`) is normalized to the physical digit, so **Ctrl+Shift+1** works as expected.

Reserved commands: `/exit`, `/settings`, `/phrases`.

### Settings page

- Speak mode: **line** or **words**
- Volume, mood, TTS model, audio output (`both` / `virt` / `speakers`)
- UI theme: `dark` (default), `light`, `neon`
- Default TTS for free typing on the Speak page
- Phrase audio cache clear
- ElevenLabs API keys (stored only on your machine)

Mouse wheel scrolls the settings page itself — dropdowns (Mood, TTS model, …) do not change value when you scroll.

**✓ SAVE SETTINGS** is always at the bottom.

## TTS chain

1. **ElevenLabs** (API key + network; some regions need a VPN)
2. **Edge TTS**
3. **gTTS**

Phrases can override the engine per entry. Default for free typing is set in Settings.

## Audio cache

Cached phrase audio lives under:

```text
~/.config/voicebox/cache/phrases/
```

Re-synthesized only when phrase text / engine / voice settings change.

## Local data (never committed)

All personal data stays under your home directory — not in this repo:

```text
~/.config/voicebox/config.json          # settings + API keys
~/.config/voicebox/phrases/index.json    # phrase metadata
~/.config/voicebox/phrases/<id>.txt      # phrase text
~/.config/voicebox/cache/phrases/        # cached audio
```

Do **not** commit API keys, `config.json`, or phrase files. Add keys only through the Settings UI (or edit `~/.config/voicebox/config.json` locally).

## Requirements

- Linux + Python 3.10+
- X11 (global hotkeys use pynput)
- `mpv` or `paplay` for playback
- Internet for cloud TTS
- Optional: PipeWire virtual mic for routing into games/Discord

## License

MIT
