# voicebox

Always-on-top Linux TTS app: type text → speak it.  
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

`./setup.sh` creates a venv, installs dependencies, links `vo` into `~/.local/bin`, and installs a **desktop entry** so you can open voicebox from the Super/launcher menu.

### Run as a real app (not tied to the terminal)

```bash
vo
```

The shell returns immediately; closing that terminal does **not** kill voicebox.

Debug (keep attached to the terminal):

```bash
VOICEBOX_FOREGROUND=1 vo
```

### Open from Super / app menu

After `./setup.sh`, press **Super**, type **voicebox**, Enter.

## Main menu

| Button | Action |
|--|--|
| **Speak** | Open the type box (Enter = line, or word+space mode) |
| **Phrases** | Presets, global hotkeys, `/commands`, per-phrase TTS |
| **Settings** | Mode, volume, mood, theme, default TTS, API keys |
| **Exit** | Quit |

Subpages have **← Menu** (or press Esc on Speak).

### Speak page

| Input | Action |
|--|--|
| text + **Enter** | Speak (line mode) or flush word (words mode) |
| `/menu` | Back to main menu |
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
| Global hotkey | X11 via pynput; prefer borderless / windowed games |
| Slash command | Type `/cmd` + Enter on the Speak page |

Hotkeys support top-row digits and numpad. **Shift+digit** (e.g. `!` for `1`) is normalized to the physical digit.

### Shared hotkeys / commands (weighted random)

You can assign the **same hotkey** and/or **same slash command** to several phrases. On each trigger, one phrase is picked with a **weighted pseudo-random** rule (anti-streak / pity):

1. Start equal (two phrases → 50/50).
2. The winner keeps **half** of its weight; the other half is split among the losers.
3. Example: A wins → A 25% / B 75%. A wins again → A 12.5% / B 87.5%.

Weights live in `~/.config/voicebox/phrases/index.json`. The Phrases panel shows `pool×N · next ~X%` when shared.

Reserved commands: `/exit`, `/settings`, `/phrases`, `/menu`.

### Settings page

- Speak mode: **line** or **words**
- Volume, mood, TTS model, audio output (`both` / `virt` / `speakers`)
- UI theme: `dark` (default), `light`, `neon`
- Default TTS for free typing
- Phrase audio cache clear
- ElevenLabs API keys (stored only on your machine)

Mouse wheel scrolls the settings page — dropdowns do not change value when you scroll.

## TTS chain

1. **ElevenLabs** (API key + network; some regions need a VPN)
2. **Edge TTS**
3. **gTTS**

## Audio cache

```text
~/.config/voicebox/cache/phrases/
```

## Local data (never committed)

```text
~/.config/voicebox/config.json
~/.config/voicebox/phrases/
~/.config/voicebox/cache/phrases/
```

## Requirements

- Linux + Python 3.10+
- X11 (global hotkeys use pynput)
- `mpv` or `paplay`
- Internet for cloud TTS
- Optional: PipeWire virtual mic

## Games note

Exclusive fullscreen titles may minimize when another window takes focus — that is the game/OS, not voicebox. Prefer **Borderless Window** in the game video settings. Global hotkeys still work when the game has focus (unless the game grabs the keyboard exclusively).

## License

MIT
