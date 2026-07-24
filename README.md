# voicebox

Always-on-top window: type text → cloud TTS.  
Stays above games and messengers (Dota / CS / Discord / Telegram).

Speech is **queued**: a new phrase never interrupts the current one.

## Quick start

```bash
git clone https://github.com/raipu2878/voicebox.git
cd voicebox
./setup.sh
# audio player:
sudo apt install mpv
vo
```

## In the window

| | |
|--|--|
| text + **Enter** | speak (line mode) |
| `/settings` or **⚙ Settings** | open settings |
| **✓ Save settings** | apply and persist |
| `/exit` | quit |

## Speak modes

| Mode | Behavior |
|--|--|
| **Line + Enter** (default) | Enter → speak → clear the field |
| **Word by word** | after space, the word is spoken immediately |

## TTS chain

1. **ElevenLabs** — if you have an API key and network access (some regions need VPN)  
2. **Edge TTS** — free fallback  
3. **gTTS** — last-resort fallback  

API keys are **not stored in this repo**. They live only on your machine:

```text
~/.config/voicebox/config.json
```

Or paste them via **Settings → + key** / env var `ELEVENLABS_API_KEY`.

## Virtual microphone

On launch, **Voicebox_Mic** is created (PipeWire/PulseAudio).  
Select it as the input in Discord / TG / Dota if you want apps to hear the TTS.

- `both` — virtual cable + headphones  
- `virt` — cable only  
- `speakers` — speakers only  

## Requirements

- Linux + Python 3.10+  
- `mpv` or `paplay`  
- internet for TTS  

## Install commands

```bash
./setup.sh          # venv + deps + symlink ~/.local/bin/vo
vo                  # run
```

## License

MIT — do whatever you want.
