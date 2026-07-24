#!/usr/bin/env python3
"""
voicebox — always-on-top window: type text → speak it.

  vo            open window
  /settings     settings (in the window)
  /exit         quit

Speak modes:
  line   — Enter → speak the line and clear the field
  words  — speak each word right after a space

Preset phrases with global hotkeys (e.g. Ctrl+… in games).
Shared hotkeys/commands pick a phrase with weighted pseudo-random
(winner loses half its weight to the others — anti-streak pity).
Queue: new text never interrupts the current phrase; it waits its turn.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import queue
import random
import shutil
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from tkinter import messagebox, simpledialog, ttk

try:
    import edge_tts
except ImportError:
    print("edge-tts is not installed. Run ./setup.sh", file=sys.stderr)
    sys.exit(1)

# ── constants ──────────────────────────────────────────────────────────────
CONFIG_DIR = Path.home() / ".config" / "voicebox"
CONFIG_FILE = CONFIG_DIR / "config.json"
PHRASES_DIR = CONFIG_DIR / "phrases"
PHRASES_INDEX = PHRASES_DIR / "index.json"
CACHE_DIR = CONFIG_DIR / "cache" / "phrases"

EL_DEFAULT_VOICE = "cgSgspJ2msm6clMCkdW9"
EL_VOICE_NAME = "Jessica"
EL_MODEL_V3 = "eleven_v3"
EL_MODEL_V2 = "eleven_multilingual_v2"
EL_API = "https://api.elevenlabs.io/v1/text-to-speech"
EL_FREE_CREDITS_MONTH = 10_000
EL_STREAM_LAT = 3

EDGE_VOICE = "ru-RU-SvetlanaNeural"
EDGE_RATE = "+0%"
EDGE_PITCH = "+0Hz"

VIRT_SINK = "voicebox_virt"
VIRT_SOURCE = "voicebox_mic"
VIRT_SINK_DESC = "Voicebox"
VIRT_SOURCE_DESC = "Voicebox_Mic"

# ── themes (mutable globals, applied via apply_theme) ─────────────────────
THEMES = {
    "dark": {
        "BG": "#14151c", "BG2": "#1e2030", "BG3": "#2a2d3e",
        "FG": "#e8eaf2", "MUTED": "#9aa0b4",
        "ACCENT": "#b4a0ff", "ACCENT2": "#5ed4bc",
        "ERR": "#ff7b7b", "OK": "#7ddea0",
        "ENTRY_BG": "#0e0f16", "BORDER": "#3a3f55",
        "BTN_PRIMARY_BG": "#8b7cff", "BTN_PRIMARY_FG": "#0e0f16",
        "BTN_SECONDARY_BG": "#2c3044", "BTN_DANGER_BG": "#5a2a32",
    },
    "light": {
        "BG": "#f3f4f8", "BG2": "#ffffff", "BG3": "#e6e8f0",
        "FG": "#1a1b22", "MUTED": "#6b7280",
        "ACCENT": "#6d5efc", "ACCENT2": "#0d9488",
        "ERR": "#dc2626", "OK": "#16a34a",
        "ENTRY_BG": "#ffffff", "BORDER": "#c9cdd8",
        "BTN_PRIMARY_BG": "#6d5efc", "BTN_PRIMARY_FG": "#ffffff",
        "BTN_SECONDARY_BG": "#e6e8f0", "BTN_DANGER_BG": "#fecaca",
    },
    "neon": {
        "BG": "#0a0614", "BG2": "#140a24", "BG3": "#221040",
        "FG": "#f3e8ff", "MUTED": "#a78bfa",
        "ACCENT": "#e879f9", "ACCENT2": "#22d3ee",
        "ERR": "#fb7185", "OK": "#4ade80",
        "ENTRY_BG": "#070412", "BORDER": "#7c3aed",
        "BTN_PRIMARY_BG": "#d946ef", "BTN_PRIMARY_FG": "#0a0614",
        "BTN_SECONDARY_BG": "#2e1065", "BTN_DANGER_BG": "#881337",
    },
}

BG = BG2 = BG3 = FG = MUTED = ACCENT = ACCENT2 = ERR = OK = ENTRY_BG = BORDER = ""
BTN_PRIMARY_BG = BTN_PRIMARY_FG = BTN_SECONDARY_BG = BTN_DANGER_BG = ""
CURRENT_THEME = "dark"


def apply_theme(name: str) -> str:
    """Apply named theme to module globals. Returns applied name."""
    global BG, BG2, BG3, FG, MUTED, ACCENT, ACCENT2, ERR, OK
    global ENTRY_BG, BORDER, BTN_PRIMARY_BG, BTN_PRIMARY_FG
    global BTN_SECONDARY_BG, BTN_DANGER_BG, CURRENT_THEME
    name = (name or "dark").lower()
    if name not in THEMES:
        name = "dark"
    t = THEMES[name]
    BG, BG2, BG3 = t["BG"], t["BG2"], t["BG3"]
    FG, MUTED = t["FG"], t["MUTED"]
    ACCENT, ACCENT2 = t["ACCENT"], t["ACCENT2"]
    ERR, OK = t["ERR"], t["OK"]
    ENTRY_BG, BORDER = t["ENTRY_BG"], t["BORDER"]
    BTN_PRIMARY_BG, BTN_PRIMARY_FG = t["BTN_PRIMARY_BG"], t["BTN_PRIMARY_FG"]
    BTN_SECONDARY_BG, BTN_DANGER_BG = t["BTN_SECONDARY_BG"], t["BTN_DANGER_BG"]
    CURRENT_THEME = name
    return name


apply_theme("dark")



def add_window_controls(win: tk.Misc, *, on_close=None) -> tk.Frame:
    """Minimize / maximize / close strip for dialogs and main window."""
    bar = tk.Frame(win, bg=BG2, highlightbackground=BORDER, highlightthickness=1)
    state = {"zoomed": False, "geom": None}

    def minimize() -> None:
        try:
            win.iconify()
        except tk.TclError:
            pass

    def toggle_max() -> None:
        try:
            if not state["zoomed"]:
                state["geom"] = win.geometry()
                win.state("zoomed")
                # fallback if zoomed unsupported
                try:
                    if win.state() != "zoomed":
                        win.attributes("-zoomed", True)
                except tk.TclError:
                    sw = win.winfo_screenwidth()
                    sh = win.winfo_screenheight()
                    win.geometry(f"{sw}x{sh}+0+0")
                state["zoomed"] = True
            else:
                try:
                    win.state("normal")
                except tk.TclError:
                    pass
                try:
                    win.attributes("-zoomed", False)
                except tk.TclError:
                    pass
                if state["geom"]:
                    win.geometry(state["geom"])
                state["zoomed"] = False
        except tk.TclError:
            pass

    def close() -> None:
        if on_close:
            on_close()
        else:
            try:
                win.destroy()
            except tk.TclError:
                pass

    for txt, cmd, danger in (
        ("—", minimize, False),
        ("□", toggle_max, False),
        ("✕", close, True),
    ):
        bg = BTN_DANGER_BG if danger else BTN_SECONDARY_BG
        tk.Button(
            bar, text=txt, command=cmd, bg=bg, fg=FG,
            activebackground=ERR if danger else BG3,
            relief="flat", borderwidth=0, width=3, pady=2,
            cursor="hand2", font=("Segoe UI", 10, "bold"),
        ).pack(side="right", padx=2, pady=2)
    return bar


def ui_button(
    parent: tk.Misc,
    text: str,
    command,
    *,
    primary: bool = False,
    danger: bool = False,
    padx: int = 14,
    pady: int = 8,
    font_size: int = 10,
    bold: bool = False,
) -> tk.Button:
    """Consistent, high-contrast button for dark UI."""
    if primary:
        bg, fg, abg = BTN_PRIMARY_BG, BTN_PRIMARY_FG, ACCENT2
    elif danger:
        bg, fg, abg = BTN_DANGER_BG, FG, ERR
    else:
        bg, fg, abg = BTN_SECONDARY_BG, FG, BG3
    return tk.Button(
        parent,
        text=text,
        command=command,
        bg=bg,
        fg=fg,
        activebackground=abg,
        activeforeground=fg if not primary else BTN_PRIMARY_FG,
        relief="flat",
        borderwidth=0,
        highlightthickness=0,
        padx=padx,
        pady=pady,
        cursor="hand2",
        font=("Segoe UI", font_size, "bold" if (bold or primary) else "normal"),
    )


def ui_entry(parent: tk.Misc, textvariable: tk.Variable | None = None, **kw) -> tk.Entry:
    opts = dict(
        bg=ENTRY_BG,
        fg=FG,
        insertbackground=ACCENT,
        relief="flat",
        highlightthickness=1,
        highlightbackground=BORDER,
        highlightcolor=ACCENT,
        font=("Segoe UI", 11),
    )
    opts.update(kw)
    if textvariable is not None:
        opts["textvariable"] = textvariable
    return tk.Entry(parent, **opts)


def ui_label(parent: tk.Misc, text: str, *, muted: bool = False, title: bool = False) -> tk.Label:
    if title:
        return tk.Label(parent, text=text, fg=ACCENT, bg=BG, font=("Segoe UI", 14, "bold"))
    if muted:
        return tk.Label(parent, text=text, fg=MUTED, bg=BG, font=("Segoe UI", 9))
    return tk.Label(parent, text=text, fg=FG, bg=BG, font=("Segoe UI", 10))

MOODS: dict[str, dict] = {
    "neutral": {"tag": "", "settings": {"stability": 0.40, "similarity_boost": 0.80, "style": 0.20, "use_speaker_boost": True}},
    "happy": {"tag": "excited", "settings": {"stability": 0.25, "similarity_boost": 0.78, "style": 0.55, "use_speaker_boost": True}},
    "soft": {"tag": "softly", "settings": {"stability": 0.45, "similarity_boost": 0.82, "style": 0.25, "use_speaker_boost": True}},
    "whisper": {"tag": "whispers", "settings": {"stability": 0.50, "similarity_boost": 0.75, "style": 0.15, "use_speaker_boost": False}},
    "sad": {"tag": "sad", "settings": {"stability": 0.35, "similarity_boost": 0.80, "style": 0.40, "use_speaker_boost": True}},
    "angry": {"tag": "angry", "settings": {"stability": 0.22, "similarity_boost": 0.78, "style": 0.60, "use_speaker_boost": True}},
    "playful": {"tag": "playfully", "settings": {"stability": 0.28, "similarity_boost": 0.80, "style": 0.50, "use_speaker_boost": True}},
    "calm": {"tag": "calm", "settings": {"stability": 0.55, "similarity_boost": 0.82, "style": 0.10, "use_speaker_boost": True}},
    "curious": {"tag": "curious", "settings": {"stability": 0.30, "similarity_boost": 0.80, "style": 0.45, "use_speaker_boost": True}},
}

WORD_BREAKS = set(" \t\n.,!?;:…—–-)]}«»\"'")


# ── config ─────────────────────────────────────────────────────────────────

def normalize_eleven_key(raw: str) -> str:
    key = (raw or "").strip().strip('"').strip("'")
    if ":" in key and not key.startswith("sk_"):
        key = key.split(":")[-1].strip()
    while key.startswith("sk_sk_"):
        key = "sk_" + key[len("sk_sk_") :]
    return key


def default_config() -> dict:
    return {
        "provider": "eleven",
        "eleven_keys": [],
        "eleven_key_index": 0,
        "eleven_voice": EL_DEFAULT_VOICE,
        "mood": "soft",
        "model": EL_MODEL_V3,
        "volume": 100,
        "audio_out": "both",  # virt | both | speakers
        "use_virt_mic": True,
        # line  = Enter → speak line and clear
        # words = speak word right after space/punct
        "speak_mode": "line",
        "theme": "dark",  # dark | light | neon
        "default_tts": "eleven",  # eleven | auto | edge | gtts for free typing
    }


def load_config() -> dict:
    cfg = default_config()
    if CONFIG_FILE.is_file():
        try:
            cfg.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            pass

    keys: list[str] = []
    for k in cfg.get("eleven_keys") or []:
        nk = normalize_eleven_key(str(k))
        if nk and nk not in keys:
            keys.append(nk)
    legacy = normalize_eleven_key(str(cfg.get("eleven_key", "") or ""))
    if legacy and legacy not in keys:
        keys.insert(0, legacy)
    env_key = normalize_eleven_key(os.environ.get("ELEVENLABS_API_KEY", ""))
    if env_key and env_key not in keys:
        keys.insert(0, env_key)
    cfg["eleven_keys"] = keys
    cfg.pop("eleven_key", None)

    if keys:
        cfg["eleven_key_index"] = max(0, min(int(cfg.get("eleven_key_index") or 0), len(keys) - 1))
    else:
        cfg["eleven_key_index"] = 0

    if (cfg.get("eleven_voice") or "") in ("", "EXAVITQu4vr4xnSDxMaL"):
        cfg["eleven_voice"] = EL_DEFAULT_VOICE
    if (cfg.get("mood") or "") not in MOODS:
        cfg["mood"] = "soft"
    if (cfg.get("model") or "") not in (EL_MODEL_V3, EL_MODEL_V2, "eleven_turbo_v2_5"):
        cfg["model"] = EL_MODEL_V3
    if (cfg.get("audio_out") or "") not in ("virt", "both", "speakers"):
        cfg["audio_out"] = "both"
    # migrate legacy modes
    mode = cfg.get("speak_mode") or "line"
    if mode in ("delay", "debounce"):
        mode = "line"
    if mode not in ("line", "words"):
        mode = "line"
    cfg["speak_mode"] = mode
    theme = (cfg.get("theme") or "dark").lower()
    if theme not in THEMES:
        theme = "dark"
    cfg["theme"] = theme
    apply_theme(theme)
    dtts = (cfg.get("default_tts") or "auto").lower()
    if dtts not in ("auto", "eleven", "edge", "gtts"):
        dtts = "auto"
    cfg["default_tts"] = dtts
    try:
        cfg["volume"] = max(0, min(150, int(cfg.get("volume", 100))))
    except (TypeError, ValueError):
        cfg["volume"] = 100
    cfg["use_virt_mic"] = bool(cfg.get("use_virt_mic", True))

    cfg.pop("input_mode", None)
    cfg.pop("stt_lang", None)
    cfg.pop("delay_ms", None)

    save_config(cfg)
    return cfg


def save_config(cfg: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    keys = []
    for k in cfg.get("eleven_keys") or []:
        nk = normalize_eleven_key(str(k))
        if nk and nk not in keys:
            keys.append(nk)
    try:
        vol = max(0, min(150, int(cfg.get("volume", 100))))
    except (TypeError, ValueError):
        vol = 100
    mode = cfg.get("speak_mode") or "line"
    if mode not in ("line", "words"):
        mode = "line"
    out = {
        "provider": cfg.get("provider", "auto"),
        "eleven_keys": keys,
        "eleven_key_index": int(cfg.get("eleven_key_index") or 0),
        "eleven_voice": cfg.get("eleven_voice", EL_DEFAULT_VOICE),
        "mood": cfg.get("mood", "soft"),
        "model": cfg.get("model", EL_MODEL_V3),
        "volume": vol,
        "audio_out": cfg.get("audio_out", "both"),
        "use_virt_mic": bool(cfg.get("use_virt_mic", True)),
        "speak_mode": mode,
        "theme": cfg.get("theme", "dark") if cfg.get("theme") in THEMES else "dark",
        "default_tts": cfg.get("default_tts", "auto") if cfg.get("default_tts") in ("auto", "eleven", "edge", "gtts") else "auto",
    }
    if out["eleven_keys"]:
        out["eleven_key_index"] = max(0, min(out["eleven_key_index"], len(out["eleven_keys"]) - 1))
    CONFIG_FILE.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        CONFIG_FILE.chmod(0o600)
    except OSError:
        pass


def resolve_provider(cfg: dict) -> str:
    p = (cfg.get("provider") or "auto").lower()
    has = bool(cfg.get("eleven_keys"))
    if p in ("eleven", "el"):
        return "eleven" if has else "edge"
    if p == "edge":
        return "edge"
    return "eleven" if has else "edge"


# ── virtual cable ──────────────────────────────────────────────────────────

def _pactl(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["pactl", *args], capture_output=True, text=True, check=False)


def ensure_virtual_cable(hear_locally: bool = True) -> tuple[bool, str]:
    if not shutil.which("pactl"):
        return False, "pactl not found"
    sinks = _pactl("list", "short", "sinks").stdout or ""
    sources = _pactl("list", "short", "sources").stdout or ""

    if VIRT_SINK not in sinks:
        r = _pactl(
            "load-module", "module-null-sink",
            f"sink_name={VIRT_SINK}",
            f"sink_properties=device.description={VIRT_SINK_DESC}",
        )
        if r.returncode != 0:
            return False, (r.stderr or r.stdout or "null-sink fail").strip()

    sources = _pactl("list", "short", "sources").stdout or ""
    if VIRT_SOURCE not in sources:
        r = _pactl(
            "load-module", "module-remap-source",
            f"master={VIRT_SINK}.monitor",
            f"source_name={VIRT_SOURCE}",
            f"source_properties=device.description={VIRT_SOURCE_DESC}",
        )
        if r.returncode != 0:
            return False, (r.stderr or r.stdout or "remap fail").strip()

    if hear_locally:
        detail = _pactl("list", "modules").stdout or ""
        if f"{VIRT_SINK}.monitor" not in detail or "module-loopback" not in detail:
            _pactl(
                "load-module", "module-loopback",
                f"source={VIRT_SINK}.monitor",
                "latency_msec=20",
            )

    sinks2 = _pactl("list", "short", "sinks").stdout or ""
    sources2 = _pactl("list", "short", "sources").stdout or ""
    ok = VIRT_SINK in sinks2 and VIRT_SOURCE in sources2
    return (True, f"mic={VIRT_SOURCE_DESC}") if ok else (False, "failed to create")


# ── TTS ────────────────────────────────────────────────────────────────────

def apply_mood_to_text(text: str, mood: str, model: str) -> str:
    text = text.strip()
    if not text.startswith("[") and model == EL_MODEL_V3:
        tag = (MOODS.get(mood) or {}).get("tag") or ""
        if tag:
            return f"[{tag}] {text}"
    return text


def voice_settings_for(cfg: dict) -> dict:
    mood = cfg.get("mood") or "soft"
    return dict((MOODS.get(mood) or MOODS["soft"])["settings"])


async def synth_edge(text: str, out_path: Path) -> None:
    c = edge_tts.Communicate(text, EDGE_VOICE, rate=EDGE_RATE, pitch=EDGE_PITCH)
    await c.save(str(out_path))


def _pulse_sink(cfg: dict) -> str | None:
    if not cfg.get("use_virt_mic", True):
        return None
    if (cfg.get("audio_out") or "both") in ("virt", "both"):
        return VIRT_SINK
    return None


def _eleven_stream_play(
    text: str, api_key: str, voice_id: str, model: str, settings: dict,
    volume: int, pulse_sink: str | None,
) -> None:
    pcm_rate = 24000
    url = (
        f"{EL_API}/{voice_id}/stream"
        f"?optimize_streaming_latency={EL_STREAM_LAT}"
        f"&output_format=pcm_{pcm_rate}"
    )
    payload = {"text": text, "model_id": model, "voice_settings": settings}
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "xi-api-key": api_key,
            "Content-Type": "application/json",
            "Accept": "application/octet-stream",
        },
        method="POST",
    )
    env = os.environ.copy()
    if pulse_sink:
        env["PULSE_SINK"] = pulse_sink
    paplay_vol = max(0, min(65536, int(65536 * (max(0, min(150, volume)) / 100.0))))

    if shutil.which("paplay"):
        cmd = [
            "paplay", "--raw", f"--rate={pcm_rate}", "--channels=1",
            "--format=s16le", f"--volume={paplay_vol}",
        ]
    elif shutil.which("mpv"):
        cmd = [
            "mpv", "--no-video", "--really-quiet", "--force-window=no",
            f"--volume={max(0, min(150, volume))}",
            "--demuxer=rawaudio",
            f"--demuxer-rawaudio-rate={pcm_rate}",
            "--demuxer-rawaudio-channels=1",
            "--demuxer-rawaudio-format=s16le",
            "-",
        ]
        if pulse_sink:
            cmd.insert(-1, f"--audio-device=pulse/{pulse_sink}")
    else:
        raise RuntimeError("paplay or mpv required")

    proc = subprocess.Popen(
        cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, env=env,
    )
    assert proc.stdin is not None
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            while True:
                chunk = resp.read(4096)
                if not chunk:
                    break
                try:
                    proc.stdin.write(chunk)
                    proc.stdin.flush()
                except BrokenPipeError:
                    break
        try:
            proc.stdin.close()
        except Exception:
            pass
        proc.wait(timeout=60)
    finally:
        if proc.poll() is None:
            try:
                proc.kill()
            except Exception:
                pass


def _play_mp3_file(path: Path, volume: int, pulse_sink: str | None) -> None:
    if not shutil.which("mpv"):
        raise RuntimeError("mpv required")
    cmd = [
        "mpv", "--no-video", "--really-quiet", "--force-window=no",
        f"--volume={max(0, min(150, volume))}", str(path),
    ]
    if pulse_sink:
        cmd.insert(-1, f"--audio-device=pulse/{pulse_sink}")
    subprocess.run(cmd, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120)


def _eleven_file_play(
    text: str, api_key: str, voice_id: str, model: str, settings: dict,
    volume: int, pulse_sink: str | None,
) -> None:
    url = f"{EL_API}/{voice_id}"
    payload = {"text": text, "model_id": model, "voice_settings": settings}
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "xi-api-key": api_key,
            "Content-Type": "application/json",
            "Accept": "audio/mpeg",
        },
        method="POST",
    )
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
        path = Path(tmp.name)
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            path.write_bytes(resp.read())
        _play_mp3_file(path, volume, pulse_sink)
    finally:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def _speak_edge_file(text: str, volume: int, pulse_sink: str | None) -> None:
    """Microsoft Edge neural TTS (works in many regions without a key)."""
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
        path = Path(tmp.name)
    try:
        asyncio.run(synth_edge(text, path))
        if path.stat().st_size < 100:
            raise RuntimeError("Edge TTS: empty audio")
        _play_mp3_file(path, volume, pulse_sink)
    finally:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def _speak_gtts_file(text: str, volume: int, pulse_sink: str | None) -> None:
    """Google Translate TTS — last-resort fallback."""
    try:
        from gtts import gTTS
    except ImportError as e:
        raise RuntimeError("gTTS is not installed: pip install gTTS") from e
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
        path = Path(tmp.name)
    try:
        gTTS(text=text, lang="ru").save(str(path))
        if path.stat().st_size < 100:
            raise RuntimeError("gTTS: empty audio")
        _play_mp3_file(path, volume, pulse_sink)
    finally:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def _is_geo_block(err: BaseException) -> bool:
    s = str(err).lower()
    return any(
        x in s
        for x in (
            "403",
            "forbidden",
            "451",
            "restrict",
            "country",
            "region",
            "not available",
            "blocked",
        )
    )




def _ensure_cache_dir() -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)


def clear_phrase_cache() -> int:
    """Delete all cached phrase audio. Returns number of removed files."""
    _ensure_cache_dir()
    n = 0
    for p in CACHE_DIR.glob("*"):
        try:
            if p.is_file():
                p.unlink()
                n += 1
        except OSError:
            pass
    return n


def _voice_fingerprint(cfg: dict, provider: str) -> dict:
    return {
        "provider": provider,
        "model": cfg.get("model") or EL_MODEL_V3,
        "voice": cfg.get("eleven_voice") or EL_DEFAULT_VOICE,
        "mood": cfg.get("mood") or "soft",
        "edge_voice": EDGE_VOICE,
        "edge_rate": EDGE_RATE,
        "edge_pitch": EDGE_PITCH,
    }


def phrase_cache_key(text: str, cfg: dict, provider: str) -> str:
    payload = {"text": text.strip(), **_voice_fingerprint(cfg, provider)}
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:28]


def phrase_cache_paths(phrase_id: str) -> tuple[Path, Path]:
    _ensure_cache_dir()
    return CACHE_DIR / f"{phrase_id}.mp3", CACHE_DIR / f"{phrase_id}.meta.json"


def phrase_has_cache(phrase_id: str) -> bool:
    mp3, _ = phrase_cache_paths(phrase_id)
    try:
        return mp3.is_file() and mp3.stat().st_size > 100
    except OSError:
        return False


def invalidate_phrase_cache(phrase_id: str) -> None:
    """Delete the single audio file for this phrase (no version history)."""
    mp3, meta = phrase_cache_paths(phrase_id)
    for p in (mp3, meta):
        try:
            p.unlink(missing_ok=True)
        except OSError:
            pass


def _load_cache_meta(phrase_id: str) -> dict | None:
    _, meta = phrase_cache_paths(phrase_id)
    if not meta.is_file():
        return None
    try:
        return json.loads(meta.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _save_cache(
    phrase_id: str,
    provider: str,
    src_mp3: Path,
    *,
    text: str = "",
    mood: str = "",
    model: str = "",
) -> None:
    """Overwrite phrase audio in place (one mp3 per phrase id — no old copies kept)."""
    mp3, meta = phrase_cache_paths(phrase_id)
    try:
        shutil.copyfile(src_mp3, mp3)
        meta.write_text(
            json.dumps(
                {
                    "provider": provider,
                    "text": (text or "")[:200],
                    "mood": mood or "",
                    "model": model or "",
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except OSError:
        pass


def _synth_to_mp3(text: str, cfg: dict, provider: str, out_path: Path, on_status=None) -> str:
    """
    Synthesize to out_path using forced provider (or auto chain).
    provider: auto|eleven|edge|gtts
    Returns backend used.
    """
    def status(msg: str) -> None:
        if on_status:
            try:
                on_status(msg)
            except Exception:
                pass

    plain = text.strip()
    if plain.startswith("[") and "]" in plain:
        plain = plain.split("]", 1)[-1].strip() or text
    errors: list[str] = []
    want = (provider or "auto").lower()
    if want not in ("auto", "eleven", "edge", "gtts"):
        want = "auto"

    def try_eleven() -> bool:
        keys = cfg.get("eleven_keys") or []
        if not keys:
            errors.append("Eleven: no API key")
            return False
        model = cfg.get("model") or EL_MODEL_V3
        mood = cfg.get("mood") or "soft"
        spoken = apply_mood_to_text(text, mood, model)
        voice = (cfg.get("eleven_voice") or EL_DEFAULT_VOICE).strip()
        settings = voice_settings_for(cfg)
        n = len(keys)
        start = int(cfg.get("eleven_key_index") or 0) % max(n, 1)
        for attempt in range(n):
            idx = (start + attempt) % n
            cfg["eleven_key_index"] = idx
            key = keys[idx]
            try:
                status("ElevenLabs…")
                url = f"{EL_API}/{voice}"
                payload = {"text": spoken, "model_id": model, "voice_settings": settings}
                req = urllib.request.Request(
                    url,
                    data=json.dumps(payload).encode("utf-8"),
                    headers={
                        "xi-api-key": key,
                        "Content-Type": "application/json",
                        "Accept": "audio/mpeg",
                    },
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=45) as resp:
                    out_path.write_bytes(resp.read())
                if out_path.stat().st_size < 100:
                    raise RuntimeError("empty audio")
                return True
            except Exception as e:
                errors.append(f"Eleven: {e}")
        return False

    def try_edge() -> bool:
        try:
            status("Edge TTS…")
            asyncio.run(synth_edge(plain, out_path))
            if out_path.stat().st_size < 100:
                raise RuntimeError("empty audio")
            return True
        except Exception as e:
            errors.append(f"Edge: {e}")
            return False

    def try_gtts() -> bool:
        try:
            status("gTTS…")
            from gtts import gTTS
            gTTS(text=plain, lang="ru").save(str(out_path))
            if out_path.stat().st_size < 100:
                raise RuntimeError("empty audio")
            return True
        except Exception as e:
            errors.append(f"gTTS: {e}")
            return False

    order = []
    if want == "auto":
        # prefer eleven if keys exist
        if cfg.get("eleven_keys"):
            order = ["eleven", "edge", "gtts"]
        else:
            order = ["edge", "gtts"]
    else:
        order = [want]

    for backend in order:
        ok = {"eleven": try_eleven, "edge": try_edge, "gtts": try_gtts}[backend]()
        if ok:
            return backend

    hint = ""
    if any("403" in x or "Forbidden" in x for x in errors):
        hint = " · ElevenLabs is often blocked by region (try VPN)"
    raise RuntimeError("; ".join(errors) + hint)


def speak_sync(
    text: str,
    cfg: dict,
    on_status=None,
    *,
    provider_override: str | None = None,
    phrase_id: str | None = None,
    force_rebuild: bool = False,
) -> str:
    """
    Synchronous TTS for one chunk. Call only from the TTS worker thread.
    Returns backend name: eleven|edge|gtts|cache.

    Phrase audio files (one .mp3 per phrase id):
      - If a file exists and force_rebuild is False → play it (ignore current mood).
      - If missing, or force_rebuild (Save phrase) → synth with current settings,
        overwrite that phrase's file, then play.
    Free typing (no phrase_id) never uses the phrase cache.
    """
    text = text.strip()
    if not text:
        return ""

    def status(msg: str) -> None:
        if on_status:
            try:
                on_status(msg)
            except Exception:
                pass

    sink = _pulse_sink(cfg)
    vol = int(cfg.get("volume", 100))

    # effective provider
    prov = (provider_override or cfg.get("default_tts") or "eleven").lower()
    if prov not in ("auto", "eleven", "edge", "gtts"):
        prov = "eleven"

    # Locked phrase audio: play existing file without re-synth
    if phrase_id and not force_rebuild and phrase_has_cache(phrase_id):
        mp3, _ = phrase_cache_paths(phrase_id)
        status("cache…")
        _play_mp3_file(mp3, vol, sink)
        return "cache"

    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
        out_path = Path(tmp.name)
    try:
        used = _synth_to_mp3(text, cfg, prov, out_path, on_status=status)
        if phrase_id:
            _save_cache(
                phrase_id,
                used,
                out_path,
                text=text,
                mood=str(cfg.get("mood") or ""),
                model=str(cfg.get("model") or ""),
            )
            status(f"saved audio ({used})")
        _play_mp3_file(out_path, vol, sink)
        return used
    finally:
        try:
            out_path.unlink(missing_ok=True)
        except OSError:
            pass



class TTSQueue:
    """
    Single TTS queue: one phrase at a time, no interruptions.
    New text is always appended to the end.
    """

    def __init__(self) -> None:
        self._q: queue.Queue = queue.Queue()
        self._lock = threading.Lock()
        self._pending = 0  # waiting + currently playing
        self._playing = False
        self._on_ui = None  # callable(msg, color?)
        self._thread = threading.Thread(target=self._loop, daemon=True, name="voicebox-tts-queue")
        self._thread.start()

    def set_ui_callback(self, cb) -> None:
        """cb(msg: str, kind: str) kind=info|ok|err|queue"""
        self._on_ui = cb

    def _ui(self, msg: str, kind: str = "info") -> None:
        if self._on_ui:
            try:
                self._on_ui(msg, kind)
            except Exception:
                pass

    def qsize(self) -> int:
        with self._lock:
            return self._pending

    def submit(
        self,
        text: str,
        cfg: dict,
        *,
        provider: str | None = None,
        phrase_id: str | None = None,
        force_rebuild: bool = False,
    ) -> int:
        """Append a phrase. Return queue length including the current item."""
        text = text.strip()
        if not text:
            return self.qsize()
        cfg_copy = json.loads(json.dumps(cfg))
        with self._lock:
            self._pending += 1
            n = self._pending
        self._q.put((text, cfg_copy, provider, phrase_id, force_rebuild))
        if self._playing or n > 1:
            self._ui(f"queued: {n} · «{text[:40]}{'…' if len(text) > 40 else ''}»", "queue")
        return n

    def _loop(self) -> None:
        while True:
            item = self._q.get()
            if item is None:
                break
            force_rebuild = False
            if len(item) == 2:
                text, cfg = item
                provider = phrase_id = None
            elif len(item) == 4:
                text, cfg, provider, phrase_id = item
            else:
                text, cfg, provider, phrase_id, force_rebuild = item
            with self._lock:
                self._playing = True
                left = self._pending
            preview = text[:48] + ("…" if len(text) > 48 else "")
            self._ui(f"speaking: {preview}" + (f"  ·  still queued: {left - 1}" if left > 1 else ""), "info")
            try:
                used = speak_sync(
                    text, cfg,
                    on_status=lambda m: self._ui(m, "info"),
                    provider_override=provider,
                    phrase_id=phrase_id,
                    force_rebuild=bool(force_rebuild),
                )
                tail = f" ({used})" if used and used != "eleven" else ""
                with self._lock:
                    rest = max(0, self._pending - 1)
                if rest:
                    self._ui(f"done{tail} · next in queue ({rest})", "ok")
                else:
                    self._ui(f"done{tail}", "ok")
            except Exception as e:
                self._ui(f"error: {e}", "err")
            finally:
                with self._lock:
                    self._pending = max(0, self._pending - 1)
                    self._playing = False
                    self._q.task_done()


# one global worker per process
_TTS = TTSQueue()


# ── preset phrases + global hotkeys ────────────────────────────────────────

def _ensure_phrases_dir() -> None:
    PHRASES_DIR.mkdir(parents=True, exist_ok=True)


def normalize_slash_command(raw: str) -> str:
    """'/Terz ' → '/terz' ; empty if invalid."""
    cmd = (raw or "").strip().lower()
    if not cmd:
        return ""
    if not cmd.startswith("/"):
        cmd = "/" + cmd
    # single token only
    cmd = cmd.split()[0]
    # keep letters/digits/_-
    cleaned = "/" + "".join(ch for ch in cmd[1:] if ch.isalnum() or ch in "_-")
    if cleaned == "/":
        return ""
    return cleaned


# reserved app commands (cannot bind phrases to these)
RESERVED_COMMANDS = {
    "/exit", "/quit", "/settings", "/set",
    "/phrases", "/phrase", "/ph",
    "/menu", "/home",
}


def _phrase_weight(p: dict) -> float:
    try:
        w = float(p.get("weight", 1.0))
    except (TypeError, ValueError):
        w = 1.0
    return w if w > 1e-12 else 1e-12


def load_phrases() -> list[dict]:
    """
    Load phrase index. Each item:
      {id, name, hotkey, command, tts_provider, file, weight}
    Text body lives in phrases/<id>.txt
    weight is used for shared hotkey/command pseudo-random picks.
    """
    _ensure_phrases_dir()
    if not PHRASES_INDEX.is_file():
        return []
    try:
        data = json.loads(PHRASES_INDEX.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            return []
        out = []
        for item in data:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            prov = str(item.get("tts_provider") or "auto").lower()
            if prov not in ("auto", "eleven", "edge", "gtts"):
                prov = "auto"
            out.append({
                "id": str(item["id"]),
                "name": str(item.get("name") or ""),
                "hotkey": str(item.get("hotkey") or ""),
                "command": normalize_slash_command(str(item.get("command") or "")),
                "tts_provider": prov,
                "file": str(item.get("file") or f"{item['id']}.txt"),
                "weight": _phrase_weight(item),
            })
        return out
    except (json.JSONDecodeError, OSError):
        return []


_PHRASES_IO_LOCK = threading.RLock()


def _save_phrases_unlocked(phrases: list[dict]) -> None:
    _ensure_phrases_dir()
    clean = []
    for p in phrases:
        prov = str(p.get("tts_provider") or "auto").lower()
        if prov not in ("auto", "eleven", "edge", "gtts"):
            prov = "auto"
        clean.append({
            "id": p["id"],
            "name": p.get("name") or "",
            "hotkey": p.get("hotkey") or "",
            "command": normalize_slash_command(p.get("command") or ""),
            "tts_provider": prov,
            "file": p.get("file") or f"{p['id']}.txt",
            "weight": round(_phrase_weight(p), 6),
        })
    PHRASES_INDEX.write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        PHRASES_INDEX.chmod(0o600)
    except OSError:
        pass


def save_phrases(phrases: list[dict]) -> None:
    with _PHRASES_IO_LOCK:
        _save_phrases_unlocked(phrases)


def find_phrases_by_command(cmd: str, phrases: list[dict] | None = None) -> list[dict]:
    """All phrases bound to this slash command (case-insensitive)."""
    n = normalize_slash_command(cmd)
    if not n or n in RESERVED_COMMANDS:
        return []
    phrases = phrases if phrases is not None else load_phrases()
    return [
        p for p in phrases
        if normalize_slash_command(p.get("command") or "") == n
    ]


def find_phrase_by_command(cmd: str, phrases: list[dict] | None = None) -> dict | None:
    """First phrase with this command (no weight mutation). Prefer pick for playback."""
    found = find_phrases_by_command(cmd, phrases)
    return found[0] if found else None


def find_phrases_by_hotkey(hotkey: str, phrases: list[dict] | None = None) -> list[dict]:
    """All phrases bound to the same normalized hotkey."""
    hk = normalize_hotkey_tokens((hotkey or "").split("+"))
    if not hk:
        return []
    phrases = phrases if phrases is not None else load_phrases()
    out = []
    for p in phrases:
        ph = normalize_hotkey_tokens((p.get("hotkey") or "").split("+"))
        if ph == hk:
            out.append(p)
    return out


def phrase_pick_chance(phrase: dict, pool: list[dict]) -> float:
    """Probability (0..1) that phrase would be chosen from pool right now."""
    if not pool:
        return 0.0
    if len(pool) == 1:
        return 1.0
    total = sum(_phrase_weight(p) for p in pool)
    if total <= 0:
        return 1.0 / len(pool)
    return _phrase_weight(phrase) / total


def pick_weighted_phrase(
    candidates: list[dict],
    all_phrases: list[dict] | None = None,
    *,
    persist: bool = True,
) -> dict:
    """
    Weighted pseudo-random pick among candidates that share a hotkey/command.

    After a pick, the winner keeps half of its weight; the other half is split
    equally among the losers (pity / anti-streak). Example with two phrases
    starting at weight 1 (50/50): A wins → A=0.5, B=1.5 → 25%/75%; A wins
    again → A=0.25, B=1.75 → 12.5%/87.5%.

    Weights are stored on each phrase and persist in the index file.
    """
    if not candidates:
        raise ValueError("no candidates")
    if len(candidates) == 1:
        return candidates[0]

    with _PHRASES_IO_LOCK:
        weights = [_phrase_weight(p) for p in candidates]
        chosen = random.choices(candidates, weights=weights, k=1)[0]
        w = _phrase_weight(chosen)
        half = w / 2.0
        chosen["weight"] = half
        others = [p for p in candidates if p["id"] != chosen["id"]]
        share = half / len(others)
        for p in others:
            p["weight"] = _phrase_weight(p) + share
        # keep group sum stable (~N) so floats stay well-scaled
        total = sum(_phrase_weight(p) for p in candidates)
        target = float(len(candidates))
        if total > 0:
            scale = target / total
            for p in candidates:
                p["weight"] = _phrase_weight(p) * scale
        if persist:
            _save_phrases_unlocked(all_phrases if all_phrases is not None else candidates)
    return chosen


def phrase_text_path(phrase: dict) -> Path:
    return PHRASES_DIR / (phrase.get("file") or f"{phrase['id']}.txt")


def read_phrase_text(phrase: dict) -> str:
    path = phrase_text_path(phrase)
    if not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def write_phrase_text(phrase: dict, text: str) -> None:
    _ensure_phrases_dir()
    path = phrase_text_path(phrase)
    path.write_text(text.strip() + ("\n" if text.strip() else ""), encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def delete_phrase_files(phrase: dict) -> None:
    try:
        phrase_text_path(phrase).unlink(missing_ok=True)
    except OSError:
        pass


# Shift+top-row digit produces symbols; always map back to the physical digit key.
# Includes common US and RU layout shifted chars on the number row.
_SHIFT_CHAR_TO_DIGIT = {
    "!": "1", "@": "2", "#": "3", "$": "4", "%": "5",
    "^": "6", "&": "7", "*": "8", "(": "9", ")": "0",
    # unambiguous shifted digits on RU / other layouts
    '"': "2", "№": "3",
}
_SHIFT_KEYSYM_TO_DIGIT = {
    "exclam": "1", "at": "2", "numbersign": "3", "dollar": "4", "percent": "5",
    "asciicircum": "6", "ampersand": "7", "asterisk": "8",
    "parenleft": "9", "parenright": "0",
    "quotedbl": "2", "numerosign": "3",
}
# On Linux pynput, KeyCode.vk is the X11 *keysym* (not hardware keycode).
_X11_KEYSYM_TO_DIGIT = {
    # unshifted digits 0–9
    0x0030: "0", 0x0031: "1", 0x0032: "2", 0x0033: "3", 0x0034: "4",
    0x0035: "5", 0x0036: "6", 0x0037: "7", 0x0038: "8", 0x0039: "9",
    # shifted US number row
    0x0021: "1",  # exclam
    0x0040: "2",  # at
    0x0023: "3",  # numbersign
    0x0024: "4",  # dollar
    0x0025: "5",  # percent
    0x005E: "6",  # asciicircum
    0x0026: "7",  # ampersand
    0x002A: "8",  # asterisk
    0x0028: "9",  # parenleft
    0x0029: "0",  # parenright
    0x0022: "2",  # quotedbl (RU shift+2)
}


def _canonical_key_token(tok: str | None) -> str | None:
    """Normalize a single hotkey token (! → 1, Control_L → ctrl, …)."""
    if not tok:
        return None
    t = str(tok).strip().lower().strip("<>")
    if not t:
        return None
    if t in ("control", "control_l", "control_r", "ctrl_l", "ctrl_r"):
        return "ctrl"
    if t in ("shift_l", "shift_r"):
        return "shift"
    if t in ("alt_l", "alt_r", "alt_gr", "meta", "meta_l", "meta_r", "option"):
        return "alt"
    if t in ("super_l", "super_r", "win", "win_l", "win_r", "cmd", "cmd_l", "cmd_r"):
        return "super"
    if t in _SHIFT_CHAR_TO_DIGIT:
        return _SHIFT_CHAR_TO_DIGIT[t]
    if t in _SHIFT_KEYSYM_TO_DIGIT:
        return _SHIFT_KEYSYM_TO_DIGIT[t]
    if t.startswith("xk_"):
        t2 = t[3:]
        if t2 in _SHIFT_KEYSYM_TO_DIGIT:
            return _SHIFT_KEYSYM_TO_DIGIT[t2]
        if t2.isdigit() and len(t2) == 1:
            return t2
    return t


def normalize_hotkey_tokens(tokens: list[str] | set[str]) -> str:
    """Order mods first, then other keys. Max meaningful combo stored as a+b+c."""
    toks: set[str] = set()
    for raw in tokens:
        t = _canonical_key_token(raw if isinstance(raw, str) else str(raw or ""))
        if t:
            toks.add(t)
    if not toks:
        return ""
    mods_order = ["ctrl", "shift", "alt", "super"]
    mods = [m for m in mods_order if m in toks]
    others = sorted(t for t in toks if t not in mods_order)
    ordered = mods + others
    if len(ordered) > 3:
        ordered = ordered[:3]
    return "+".join(ordered)


def hotkey_display(hotkey: str) -> str:
    """'ctrl+shift+1' / 'ctrl+num1' → 'Ctrl+Shift+1' / 'Ctrl+Num1'"""
    if not hotkey:
        return "—"
    parts = []
    for p in hotkey.split("+"):
        p = _canonical_key_token(p) or p.strip().lower()
        mapping = {
            "ctrl": "Ctrl", "control": "Ctrl",
            "alt": "Alt", "shift": "Shift",
            "super": "Super", "win": "Super", "cmd": "Super",
            "space": "Space", "enter": "Enter", "tab": "Tab",
            "esc": "Esc", "escape": "Esc",
            "plus": "+", "minus": "-", "star": "*", "slash": "/",
        }
        if p in mapping:
            parts.append(mapping[p])
        elif p.startswith("num") and len(p) > 3:
            parts.append("Num" + p[3:].upper())
        elif p.startswith("f") and p[1:].isdigit():
            parts.append(p.upper())
        elif len(p) == 1:
            parts.append(p.upper())
        else:
            parts.append(p.capitalize())
    return "+".join(parts)


def pynput_key_to_token(key, *, x_keycode: int | None = None) -> str | None:
    """Map pynput key object to our hotkey token (physical top-row digits, not !@#)."""
    try:
        from pynput.keyboard import KeyCode
    except ImportError:
        return None

    # X11 hardware keycode (PC: 10=1 … 18=9, 19=0) — stable under Ctrl+Shift+layout.
    # pynput often reports wrong keysym (e.g. Ctrl+Shift+4 → ';') without this.
    kc = x_keycode
    if kc is None:
        kc = getattr(key, "_x_keycode", None)
    if isinstance(kc, int) and 10 <= kc <= 19:
        return "1234567890"[kc - 10]

    if isinstance(key, KeyCode):
        # Prefer shifted-digit char (!@#) → digit, before generic printable
        ch = key.char
        if ch:
            if ch in _SHIFT_CHAR_TO_DIGIT:
                return _SHIFT_CHAR_TO_DIGIT[ch]
            if ch.isprintable() and not ch.isspace():
                # letters / plain digits
                if ch.isdigit() or ch.isalpha():
                    return ch.lower()
                if len(ch) == 1:
                    return ch.lower()
            # Ctrl+1 often arrives as '\x01' (non-printable) — fall through to vk

        # Linux: vk is X11 keysym. Windows: virtual-key code (0x30–0x39 for digits).
        vk = getattr(key, "vk", None)
        if isinstance(vk, int):
            if vk in _X11_KEYSYM_TO_DIGIT:
                return _X11_KEYSYM_TO_DIGIT[vk]
            if 0x30 <= vk <= 0x39:  # ASCII / Win VK '0'–'9'
                return chr(vk)

        # pynput X11 may set _symbol (e.g. "exclam", "1")
        symbol = getattr(key, "_symbol", None)
        if symbol:
            n = str(symbol).lower().removeprefix("xk_")
            can = _canonical_key_token(n)
            if can:
                return can

        name = getattr(key, "name", None)
        if name:
            return _canonical_key_token(str(name).lower())
        return None

    # Key enum (modifiers, F-keys, numpad names)
    try:
        kname = key.name  # type: ignore[attr-defined]
    except Exception:
        kname = str(key).replace("Key.", "")

    kname = (kname or "").lower()
    can = _canonical_key_token(kname)
    if can in {"ctrl", "shift", "alt", "super"}:
        return can

    num_map = {f"numpad{i}": f"num{i}" for i in range(10)}
    num_map.update({
        "numpad_add": "numplus",
        "numpad_subtract": "numminus",
        "numpad_multiply": "numstar",
        "numpad_divide": "numslash",
        "numpad_decimal": "numdot",
        "numpad_enter": "enter",
    })
    if kname in num_map:
        return num_map[kname]

    special = {
        "space": "space", "enter": "enter", "tab": "tab",
        "esc": "esc", "escape": "esc",
        "up": "up", "down": "down", "left": "left", "right": "right",
        "home": "home", "end": "end", "page_up": "pageup", "page_down": "pagedown",
        "insert": "insert", "delete": "delete", "backspace": "backspace",
    }
    if kname in special:
        return special[kname]
    if kname.startswith("f") and kname[1:].isdigit():
        return kname
    if kname in _SHIFT_KEYSYM_TO_DIGIT:
        return _SHIFT_KEYSYM_TO_DIGIT[kname]
    return can or None


def tk_keysym_to_token(keysym: str, keycode: int | None = None) -> str | None:
    """Map Tk keysym (+ optional X11 keycode) to hotkey token."""
    low = (keysym or "").lower()
    mods = {
        "control_l": "ctrl", "control_r": "ctrl", "control": "ctrl",
        "shift_l": "shift", "shift_r": "shift", "shift": "shift",
        "alt_l": "alt", "alt_r": "alt", "alt": "alt",
        "meta_l": "alt", "meta_r": "alt",
        "super_l": "super", "super_r": "super",
        "win_l": "super", "win_r": "super",
    }
    if low in mods:
        return mods[low]

    # Physical top-row digits via X11 keycode (stable under Ctrl/Shift/layout).
    # PC keyboards: 10=1 … 18=9, 19=0
    if keycode is not None:
        try:
            kc = int(keycode)
            if 10 <= kc <= 19:
                return "1234567890"[kc - 10]
        except (TypeError, ValueError):
            pass

    if not low:
        return None

    # Shift+1 → keysym "exclam" — map to physical digit "1"
    if low in _SHIFT_KEYSYM_TO_DIGIT:
        return _SHIFT_KEYSYM_TO_DIGIT[low]

    # numpad
    if low.startswith("kp_"):
        rest = low[3:]
        if rest.isdigit():
            return f"num{rest}"
        kp = {
            "enter": "enter",
            "add": "numplus",
            "subtract": "numminus",
            "multiply": "numstar",
            "divide": "numslash",
            "decimal": "numdot",
            "begin": "num5",
        }
        return kp.get(rest)

    if low in ("space", "return", "tab", "escape", "esc"):
        return {"return": "enter", "escape": "esc"}.get(low, low)

    if low.startswith("f") and low[1:].isdigit():
        return low

    # bare digits / letters / shifted char as keysym
    if len(keysym) == 1:
        ch = keysym
        if ch in _SHIFT_CHAR_TO_DIGIT:
            return _SHIFT_CHAR_TO_DIGIT[ch]
        return ch.lower()

    name_map = {
        "minus": "-", "equal": "=", "plus": "+",
        "bracketleft": "[", "bracketright": "]",
        "semicolon": ";", "apostrophe": "'",
        "comma": ",", "period": ".", "slash": "/",
        "backslash": "\\", "grave": "`",
    }
    if low in name_map:
        return name_map[low]
    return _canonical_key_token(low)


class HotkeyService:
    """
    Global hotkeys via pynput listener + exact key-set match (1–3 keys).
    Supports top-row digits and numpad. Prefer borderless/windowed games.
    """

    def __init__(self) -> None:
        self._listener = None
        self._lock = threading.Lock()
        # normalized hotkey -> list of phrase ids (shared keys = weighted pool)
        self._bindings: dict[str, list[str]] = {}
        self._phrases: list[dict] = []
        self._cfg_provider = lambda: {}
        self._on_fire = None
        self._pressed: set[str] = set()
        self._fired: set[str] = set()  # bindings already fired this hold

    def set_config_provider(self, fn) -> None:
        self._cfg_provider = fn

    def set_fire_callback(self, fn) -> None:
        self._on_fire = fn

    def reload(self, phrases: list[dict] | None = None) -> tuple[bool, str]:
        try:
            from pynput import keyboard  # noqa: F401
        except ImportError:
            return False, "pynput not installed (./setup.sh)"

        phrases = phrases if phrases is not None else load_phrases()
        self._phrases = phrases
        bindings: dict[str, list[str]] = {}
        for p in phrases:
            hk = normalize_hotkey_tokens((p.get("hotkey") or "").split("+"))
            if not hk:
                continue
            bindings.setdefault(hk, []).append(p["id"])

        with self._lock:
            self._bindings = bindings
            self._stop_listener_unlocked()
            self._pressed.clear()
            self._fired.clear()
            if not bindings:
                return True, "no hotkeys set"
            ok, msg = self._start_listener_unlocked()
            return ok, msg

    def _stop_listener_unlocked(self) -> None:
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:
                pass
            self._listener = None

    def _start_listener_unlocked(self) -> tuple[bool, str]:
        from pynput import keyboard

        # Thread-local last X11 keycode from patched pynput (Linux only)
        last_x_keycode: dict[str, int | None] = {"v": None}

        def on_press(key) -> None:
            tok = _canonical_key_token(
                pynput_key_to_token(key, x_keycode=last_x_keycode.get("v"))
            )
            last_x_keycode["v"] = None
            if not tok:
                return
            self._pressed.add(tok)
            self._check_fire()

        def on_release(key) -> None:
            tok = _canonical_key_token(
                pynput_key_to_token(key, x_keycode=last_x_keycode.get("v"))
            )
            last_x_keycode["v"] = None
            if tok and tok in self._pressed:
                self._pressed.discard(tok)
            else:
                # Press may have been '!'→1 while release arrives as bare '1' (or reverse).
                if tok and tok.isdigit():
                    self._pressed.discard(tok)
            # clear fired for bindings no longer fully held
            held = set(self._pressed)
            self._fired = {
                b for b in self._fired
                if set(b.split("+")).issubset(held)
            }

        try:
            # Attach X11 hardware keycode to each event (Ctrl+Shift+4 otherwise becomes ';')
            try:
                from pynput.keyboard import _xorg as _pynput_xorg  # type: ignore

                _orig_event_to_key = _pynput_xorg.Listener._event_to_key

                def _event_to_key_with_code(self, display, event):  # type: ignore[no-untyped-def]
                    key = _orig_event_to_key(self, display, event)
                    try:
                        last_x_keycode["v"] = int(event.detail)
                    except Exception:
                        last_x_keycode["v"] = None
                    return key

                _pynput_xorg.Listener._event_to_key = _event_to_key_with_code  # type: ignore[method-assign]
            except Exception:
                pass

            listener = keyboard.Listener(on_press=on_press, on_release=on_release)
            listener.start()
            self._listener = listener
            return True, f"{len(self._bindings)} hotkey(s) active"
        except Exception as e:
            return False, f"hotkey listener failed: {e}"

    def _check_fire(self) -> None:
        held = {_canonical_key_token(t) or t for t in self._pressed}
        held.discard(None)  # type: ignore[arg-type]
        with self._lock:
            items = list(self._bindings.items())
        for hk, pids in items:
            keys = set(hk.split("+")) if hk else set()
            if not keys or hk in self._fired:
                continue
            # exact match, or binding ⊆ held with only extra modifiers allowed
            if keys == held:
                self._fired.add(hk)
                self._activate(pids)
            elif keys.issubset(held):
                extra = held - keys
                mods = {"ctrl", "shift", "alt", "super"}
                if not (extra - mods):
                    self._fired.add(hk)
                    self._activate(pids)

    def _activate(self, phrase_ids: list[str] | str) -> None:
        if isinstance(phrase_ids, str):
            phrase_ids = [phrase_ids]
        id_set = set(phrase_ids)
        candidates = [p for p in self._phrases if p["id"] in id_set]
        if not candidates:
            self._phrases = load_phrases()
            candidates = [p for p in self._phrases if p["id"] in id_set]
        # skip empty bodies
        candidates = [p for p in candidates if read_phrase_text(p).strip()]
        if not candidates:
            return
        pool_n = len(candidates)
        phrase = pick_weighted_phrase(candidates, self._phrases, persist=True)
        text = read_phrase_text(phrase).strip()
        if not text:
            return
        name = phrase.get("name") or text[:24]
        cfg = self._cfg_provider()
        prov = (phrase.get("tts_provider") or "auto").lower()
        _TTS.submit(text, cfg, provider=prov, phrase_id=phrase.get("id"))
        if self._on_fire:
            try:
                chance = phrase_pick_chance(phrase, candidates)
                self._on_fire(name, text, pool_n, chance)
            except TypeError:
                try:
                    self._on_fire(name, text)
                except Exception:
                    pass
            except Exception:
                pass

    def stop(self) -> None:
        with self._lock:
            self._stop_listener_unlocked()


_HOTKEYS = HotkeyService()


# ── single-window UI ───────────────────────────────────────────────────────

class VoiceboxApp:
    """One native window; home menu + speak / phrases / settings pages."""

    def __init__(self) -> None:
        self.cfg = load_config()
        apply_theme(self.cfg.get("theme") or "dark")

        # className → WM_CLASS for desktop launchers (Super → "voicebox")
        self.root = tk.Tk(className="voicebox")
        self.root.title("voicebox")
        self.root.geometry("420x480+80+80")
        self.root.minsize(360, 400)
        self.root.resizable(True, True)
        self.root.configure(bg=BG)
        try:
            self.root.attributes("-topmost", True)
        except tk.TclError:
            pass

        self._spoken_upto = 0
        self.phrases = load_phrases()
        self._page = "menu"
        self._edit_phrase_id: str | None = None
        self._hotkey_target: str | None = None
        self._hotkey_mods: set[str] = set()

        self._style = ttk.Style(self.root)
        try:
            self._style.theme_use("clam")
        except tk.TclError:
            pass

        self._build_shell()
        self._build_page_menu()
        self._build_page_speak()
        self._build_page_settings()
        self._build_page_phrases()
        self._build_page_hotkey()

        _TTS.set_ui_callback(self._tts_ui)
        _HOTKEYS.set_config_provider(lambda: self.cfg)
        _HOTKEYS.set_fire_callback(self._on_hotkey_fire)
        threading.Thread(target=self._setup_virt, daemon=True).start()
        self.root.after(120, self._reload_hotkeys)

        self.show_page("menu")
        self.root.protocol("WM_DELETE_WINDOW", self._exit)
        self.root.after(2000, self._topmost_pulse)

    # ── shell ───────────────────────────────────────────────────────────

    def _build_shell(self) -> None:
        self.shell = tk.Frame(self.root, bg=BG)
        self.shell.pack(fill="both", expand=True)

        nav = tk.Frame(self.shell, bg=BG2, highlightbackground=BORDER, highlightthickness=1)
        nav.pack(fill="x", side="top")
        self._nav = nav

        left = tk.Frame(nav, bg=BG2)
        left.pack(side="left", padx=14, pady=12)
        self.lbl_brand = tk.Label(
            left, text="voicebox", fg=ACCENT, bg=BG2, font=("Segoe UI", 15, "bold"),
        )
        self.lbl_brand.pack(side="left")
        self.lbl_mode = tk.Label(left, text="", fg=MUTED, bg=BG2, font=("Segoe UI", 9))
        self.lbl_mode.pack(side="left", padx=(12, 0))

        # compact «Menu» chip when not on home
        right = tk.Frame(nav, bg=BG2)
        right.pack(side="right", padx=10, pady=8)
        self.btn_nav_home = ui_button(
            right, "Menu", lambda: self.show_page("menu"), padx=12, pady=6,
        )
        self.btn_nav_home.pack(side="left")

        self.content = tk.Frame(self.shell, bg=BG)
        self.content.pack(fill="both", expand=True)

        self.status = tk.Label(
            self.shell, text="", fg=MUTED, bg=BG2, font=("Segoe UI", 9),
            anchor="w", padx=14, pady=8,
        )
        self.status.pack(fill="x", side="bottom")

        self.pages: dict[str, tk.Frame] = {}

    def show_page(self, name: str) -> None:
        self._page = name
        for n, fr in self.pages.items():
            if n == name:
                fr.pack(fill="both", expand=True)
            else:
                fr.pack_forget()
        # Menu chip: hide on home, show elsewhere
        try:
            if name == "menu":
                self.btn_nav_home.pack_forget()
            else:
                self.btn_nav_home.pack(side="left")
        except tk.TclError:
            pass
        self._update_mode_label()
        # window size: compact menu / roomy editors
        try:
            if name == "menu":
                self.root.minsize(360, 400)
                self.root.geometry("420x480")
            elif name == "speak":
                self.root.minsize(400, 280)
                self.root.geometry("480x320")
            else:
                self.root.minsize(520, 420)
                self.root.geometry("640x520")
        except tk.TclError:
            pass
        if name == "speak":
            try:
                self.text.focus_set()
            except tk.TclError:
                pass
        elif name == "phrases":
            self._phrases_refresh()
        elif name == "settings":
            self._settings_load_fields()
        elif name == "hotkey":
            try:
                self.pages["hotkey"].focus_set()
                self.root.bind("<KeyPress>", self._hk_press)
                self.root.bind("<KeyRelease>", self._hk_release)
            except tk.TclError:
                pass
            return
        # leave hotkey page → unbind capture
        try:
            self.root.unbind("<KeyPress>")
            self.root.unbind("<KeyRelease>")
        except tk.TclError:
            pass

    def _set_status(self, msg: str, color: str | None = None) -> None:
        try:
            self.status.configure(text=msg, fg=color or MUTED, bg=BG2)
        except tk.TclError:
            pass

    def _update_mode_label(self) -> None:
        mode = self.cfg.get("speak_mode") or "line"
        label = "mode: word by word" if mode == "words" else "mode: Enter = line"
        dtts = self.cfg.get("default_tts") or "auto"
        self.lbl_mode.configure(text=f"{label}  ·  tts={dtts}  ·  {CURRENT_THEME}")

    def _keep_top(self) -> None:
        try:
            self.root.attributes("-topmost", True)
        except tk.TclError:
            pass

    def _topmost_pulse(self) -> None:
        self._keep_top()
        try:
            self.root.after(2000, self._topmost_pulse)
        except tk.TclError:
            pass

    def _setup_virt(self) -> None:
        if not self.cfg.get("use_virt_mic", True):
            return
        out = self.cfg.get("audio_out") or "both"
        if out == "speakers":
            return
        ok, msg = ensure_virtual_cable(hear_locally=(out == "both"))

        def ui() -> None:
            if ok:
                self._set_status(f"virt mic: {VIRT_SOURCE_DESC}  ·  {msg}", OK)
            else:
                self._set_status(f"virt cable: {msg}", ERR)

        try:
            self.root.after(0, ui)
        except tk.TclError:
            pass

    def _tts_ui(self, msg: str, kind: str = "info") -> None:
        colors = {"info": ACCENT2, "ok": OK, "err": ERR, "queue": ACCENT}
        color = colors.get(kind, MUTED)

        def go() -> None:
            self._set_status(msg, color)

        try:
            self.root.after(0, go)
        except tk.TclError:
            pass

    def _on_hotkey_fire(
        self,
        name: str,
        text: str,
        pool_n: int = 1,
        next_chance: float | None = None,
    ) -> None:
        def go() -> None:
            preview = text[:40] + ("…" if len(text) > 40 else "")
            extra = ""
            if pool_n > 1:
                pct = f" · next ~{int(round((next_chance or 0) * 100))}%" if next_chance is not None else ""
                extra = f" · pool×{pool_n}{pct}"
            self._set_status(f"hotkey «{name}»: {preview}{extra}", ACCENT2)
            # weights may have changed on disk / in memory
            try:
                self.phrases = load_phrases()
                _HOTKEYS._phrases = self.phrases
            except Exception:
                pass

        try:
            self.root.after(0, go)
        except tk.TclError:
            pass

    def _reload_hotkeys(self) -> None:
        ok, msg = _HOTKEYS.reload(self.phrases)
        color = OK if ok else (MUTED if "no hotkeys" in msg else ERR)
        self._set_status(f"hotkeys: {msg}", color)

    # ── page: home menu ─────────────────────────────────────────────────

    def _build_page_menu(self) -> None:
        page = tk.Frame(self.content, bg=BG)
        self.pages["menu"] = page

        center = tk.Frame(page, bg=BG)
        center.pack(expand=True, fill="both", padx=28, pady=20)
        self._menu_center = center

        tk.Label(
            center, text="voicebox", fg=ACCENT, bg=BG,
            font=("Segoe UI", 22, "bold"),
        ).pack(pady=(8, 4))
        tk.Label(
            center, text="Type → speak  ·  always on top",
            fg=MUTED, bg=BG, font=("Segoe UI", 10),
        ).pack(pady=(0, 28))

        def menu_btn(text, command, *, primary=False, danger=False):
            if primary:
                bg, fg = BTN_PRIMARY_BG, BTN_PRIMARY_FG
            elif danger:
                bg, fg = BTN_DANGER_BG, FG
            else:
                bg, fg = BTN_SECONDARY_BG, FG
            btn = tk.Button(
                center, text=text, command=command,
                bg=bg, fg=fg, activebackground=ACCENT, activeforeground=FG,
                relief="flat", borderwidth=0, cursor="hand2",
                font=("Segoe UI", 14, "bold"), pady=16,
            )
            btn.pack(fill="x", pady=7)
            return btn

        menu_btn("Speak", lambda: self.show_page("speak"), primary=True)
        menu_btn("Phrases", lambda: self.show_page("phrases"))
        menu_btn("Settings", lambda: self.show_page("settings"))
        menu_btn("Exit", self._exit, danger=True)

    # ── page: speak (type box) ──────────────────────────────────────────

    def _build_page_speak(self) -> None:
        page = tk.Frame(self.content, bg=BG)
        self.pages["speak"] = page

        top = tk.Frame(page, bg=BG)
        top.pack(fill="x", padx=14, pady=(10, 4))
        tk.Label(
            top, text="Speak", fg=ACCENT, bg=BG, font=("Segoe UI", 14, "bold"),
        ).pack(side="left")
        ui_button(top, "← Menu", lambda: self.show_page("menu"), padx=10, pady=4).pack(
            side="right",
        )

        hint = tk.Label(
            page,
            text="Enter = speak line  ·  or word+space  ·  /menu  /phrases  /settings  /exit",
            fg=MUTED, bg=BG, font=("Segoe UI", 9),
        )
        hint.pack(anchor="w", padx=14, pady=(0, 4))
        self._speak_hint = hint

        wrap = tk.Frame(page, bg=BORDER)
        wrap.pack(fill="both", expand=True, padx=14, pady=(4, 10))
        inner = tk.Frame(wrap, bg=ENTRY_BG)
        inner.pack(fill="both", expand=True, padx=1, pady=1)

        self.text = tk.Text(
            inner,
            bg=ENTRY_BG, fg=FG, insertbackground=ACCENT,
            selectbackground=BG3, selectforeground=FG,
            relief="flat", font=("Segoe UI", 13), wrap="word",
            padx=12, pady=10, borderwidth=0, highlightthickness=0,
            height=6,
        )
        self.text.pack(fill="both", expand=True)
        self.text.bind("<KeyRelease>", self._on_key)
        self.text.bind("<Return>", self._on_return)
        self.text.bind("<KP_Enter>", self._on_return)
        self.text.bind("<Escape>", lambda e: (self.show_page("menu"), "break")[-1])

    # ── page: settings ──────────────────────────────────────────────────

    def _build_page_settings(self) -> None:
        page = tk.Frame(self.content, bg=BG)
        self.pages["settings"] = page

        # scroll
        shell = tk.Frame(page, bg=BG)
        shell.pack(fill="both", expand=True, padx=8, pady=8)
        shell.columnconfigure(0, weight=1)
        shell.rowconfigure(0, weight=1)

        canvas = tk.Canvas(shell, bg=BG, highlightthickness=0, bd=0)
        body = tk.Frame(canvas, bg=BG)
        body.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        win = canvas.create_window((0, 0), window=body, anchor="nw")

        def _cfg(e):
            canvas.itemconfigure(win, width=e.width)

        canvas.bind("<Configure>", _cfg)
        canvas.grid(row=0, column=0, sticky="nsew")
        self._settings_canvas = canvas
        self._settings_body = body

        def section(title: str):
            tk.Label(
                body, text=title, fg=MUTED, bg=BG, font=("Segoe UI", 9, "bold"),
            ).pack(anchor="w", padx=16, pady=(16, 6))

        section("Speak mode")
        self.var_mode = tk.StringVar(value="line")
        for text, val in (
            ("Line + Enter  →  speak and clear", "line"),
            ("Word by word  →  speak after space", "words"),
        ):
            tk.Radiobutton(
                body, text=text, variable=self.var_mode, value=val,
                bg=BG, fg=FG, selectcolor=BG3,
                activebackground=BG, activeforeground=FG,
                highlightthickness=0, font=("Segoe UI", 10), anchor="w",
            ).pack(fill="x", padx=20, pady=2)

        section("Volume (0–150)")
        self.var_vol = tk.StringVar(value="100")
        ui_entry(body, self.var_vol).pack(fill="x", padx=16, ipady=8)

        section("Mood")
        self.var_mood = tk.StringVar(value="soft")
        self.cmb_mood = ttk.Combobox(
            body, textvariable=self.var_mood, values=list(MOODS.keys()), state="readonly",
        )
        self.cmb_mood.pack(fill="x", padx=16, pady=4)

        section("TTS model")
        self.var_model = tk.StringVar(value=EL_MODEL_V3)
        self.cmb_model = ttk.Combobox(
            body, textvariable=self.var_model,
            values=[EL_MODEL_V3, EL_MODEL_V2, "eleven_turbo_v2_5"], state="readonly",
        )
        self.cmb_model.pack(fill="x", padx=16, pady=4)

        section("Audio output")
        self.var_audio = tk.StringVar(value="both")
        self.cmb_audio = ttk.Combobox(
            body, textvariable=self.var_audio,
            values=["both", "virt", "speakers"], state="readonly",
        )
        self.cmb_audio.pack(fill="x", padx=16, pady=4)

        section("UI theme")
        self.var_theme = tk.StringVar(value="dark")
        self.cmb_theme = ttk.Combobox(
            body, textvariable=self.var_theme,
            values=["dark", "light", "neon"], state="readonly",
        )
        self.cmb_theme.pack(fill="x", padx=16, pady=4)

        section("Default TTS (main Speak page)")
        self.var_dtts = tk.StringVar(value="auto")
        self.cmb_dtts = ttk.Combobox(
            body, textvariable=self.var_dtts,
            values=["auto", "eleven", "edge", "gtts"], state="readonly",
        )
        self.cmb_dtts.pack(fill="x", padx=16, pady=4)
        tk.Label(
            body, text="auto = Eleven → Edge → gTTS  ·  phrases can override",
            fg=MUTED, bg=BG, font=("Segoe UI", 8),
        ).pack(anchor="w", padx=16)

        section("Phrase audio cache")
        tk.Label(
            body,
            text=(
                f"{CACHE_DIR}\n"
                "One .mp3 per phrase. Changing mood/settings does NOT re-record.\n"
                "Re-Save a phrase to bake current mood into that phrase only."
            ),
            fg=MUTED, bg=BG, font=("Segoe UI", 8), justify="left",
        ).pack(anchor="w", padx=16)
        row = tk.Frame(body, bg=BG)
        row.pack(fill="x", padx=16, pady=6)
        ui_button(row, "Clear phrase cache", self._settings_clear_cache, danger=True).pack(side="left")

        section("ElevenLabs API keys")
        self.lbl_keys = tk.Label(body, text="keys: 0", fg=ACCENT2, bg=BG, font=("Segoe UI", 10))
        self.lbl_keys.pack(anchor="w", padx=16)
        krow = tk.Frame(body, bg=BG)
        krow.pack(fill="x", padx=16, pady=8)
        ui_button(krow, "+ Add key", self._settings_add_key).pack(side="left", padx=(0, 8))
        ui_button(krow, "− Remove last", self._settings_del_key, danger=True).pack(side="left")

        tk.Frame(body, bg=BG, height=20).pack(fill="x")

        # footer
        foot = tk.Frame(page, bg=BG2, highlightbackground=BORDER, highlightthickness=1)
        foot.pack(fill="x", side="bottom")
        fi = tk.Frame(foot, bg=BG2)
        fi.pack(fill="x", padx=16, pady=14)
        tk.Button(
            fi, text="✓  SAVE SETTINGS", command=self._settings_save,
            bg=BTN_PRIMARY_BG, fg=BTN_PRIMARY_FG, activebackground=ACCENT,
            relief="flat", borderwidth=0, cursor="hand2",
            font=("Segoe UI", 12, "bold"), pady=12,
        ).pack(fill="x", pady=(0, 8))
        ui_button(fi, "← Menu", lambda: self.show_page("menu")).pack(fill="x")

        # Wheel scrolls the settings panel — not Mood/TTS Combobox values.
        # Bind after all children exist (incl. comboboxes).
        self._settings_bind_wheel(page)

    def _settings_on_wheel(self, event: tk.Event) -> str:
        """Scroll settings page; swallow event so Comboboxes don't change values."""
        canvas = getattr(self, "_settings_canvas", None)
        if canvas is None:
            return "break"
        if getattr(event, "delta", 0):
            # Windows / macOS / some X11 setups
            steps = int(-1 * (event.delta / 120)) or (-1 if event.delta > 0 else 1)
            canvas.yview_scroll(steps, "units")
        elif getattr(event, "num", None) == 4:
            canvas.yview_scroll(-3, "units")
        elif getattr(event, "num", None) == 5:
            canvas.yview_scroll(3, "units")
        return "break"

    def _settings_bind_wheel(self, widget: tk.Misc) -> None:
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            widget.bind(seq, self._settings_on_wheel, add="+")
        try:
            children = widget.winfo_children()
        except tk.TclError:
            return
        for child in children:
            self._settings_bind_wheel(child)

    def _settings_load_fields(self) -> None:
        c = self.cfg
        self.var_mode.set(c.get("speak_mode") or "line")
        self.var_vol.set(str(int(c.get("volume", 100))))
        self.var_mood.set(c.get("mood") or "soft")
        self.var_model.set(c.get("model") or EL_MODEL_V3)
        self.var_audio.set(c.get("audio_out") or "both")
        self.var_theme.set(c.get("theme") or "dark")
        self.var_dtts.set(c.get("default_tts") or "auto")
        keys = c.get("eleven_keys") or []
        self.lbl_keys.configure(
            text=f"keys: {len(keys)}  (~{len(keys) * EL_FREE_CREDITS_MONTH:,} credits/mo)",
            fg=ACCENT2, bg=BG,
        )

    def _settings_add_key(self) -> None:
        raw = simpledialog.askstring("API key", "Paste sk_…", parent=self.root)
        if not raw:
            return
        key = normalize_eleven_key(raw)
        if not key.startswith("sk_") or len(key) < 20:
            messagebox.showerror("Error", "Key looks invalid", parent=self.root)
            return
        keys = list(self.cfg.get("eleven_keys") or [])
        if key in keys:
            messagebox.showinfo("OK", "Already added", parent=self.root)
            return
        keys.append(key)
        self.cfg["eleven_keys"] = keys
        self.cfg["eleven_key_index"] = len(keys) - 1
        self.cfg["provider"] = "eleven"
        self._settings_load_fields()

    def _settings_del_key(self) -> None:
        keys = list(self.cfg.get("eleven_keys") or [])
        if not keys:
            return
        keys.pop()
        self.cfg["eleven_keys"] = keys
        self.cfg["eleven_key_index"] = max(0, len(keys) - 1)
        self._settings_load_fields()

    def _settings_clear_cache(self) -> None:
        n = clear_phrase_cache()
        messagebox.showinfo("Cache", f"Removed {n} cached file(s).\n{CACHE_DIR}", parent=self.root)

    def _settings_save(self) -> None:
        mode = self.var_mode.get() if self.var_mode.get() in ("line", "words") else "line"
        try:
            vol = max(0, min(150, int(self.var_vol.get().strip())))
        except ValueError:
            messagebox.showerror("Error", "Volume must be 0–150", parent=self.root)
            return
        theme = self.var_theme.get() if self.var_theme.get() in THEMES else "dark"
        dtts = self.var_dtts.get() if self.var_dtts.get() in ("auto", "eleven", "edge", "gtts") else "auto"

        self.cfg["speak_mode"] = mode
        self.cfg["volume"] = vol
        self.cfg["mood"] = self.var_mood.get() or "soft"
        self.cfg["model"] = self.var_model.get() or EL_MODEL_V3
        self.cfg["audio_out"] = self.var_audio.get() or "both"
        self.cfg["use_virt_mic"] = self.cfg["audio_out"] != "speakers"
        self.cfg["theme"] = theme
        self.cfg["default_tts"] = dtts
        if self.cfg.get("eleven_keys") and dtts in ("auto", "eleven"):
            self.cfg["provider"] = "eleven"
        elif dtts in ("edge", "gtts"):
            self.cfg["provider"] = dtts

        apply_theme(theme)
        # Do NOT wipe phrase audio on settings save — each phrase keeps its own .mp3
        # until that phrase is re-saved or cache is cleared manually.
        save_config(self.cfg)
        self._recolor_shell()
        self._set_status(f"✓ settings saved · mode={mode} · theme={theme}", OK)
        messagebox.showinfo(
            "Saved",
            f"Settings applied.\nMode: {mode}\nTheme: {theme}\nDefault TTS: {dtts}",
            parent=self.root,
        )
        self.show_page("menu")

    def _recolor_shell(self) -> None:
        """Apply current theme colors to shell widgets."""
        try:
            self.root.configure(bg=BG)
            self.shell.configure(bg=BG)
            self.content.configure(bg=BG)
            self._nav.configure(bg=BG2, highlightbackground=BORDER)
            for w in self._nav.winfo_children():
                try:
                    w.configure(bg=BG2)
                except tk.TclError:
                    pass
            self.lbl_brand.configure(bg=BG2, fg=ACCENT)
            self.lbl_mode.configure(bg=BG2, fg=MUTED)
            self.status.configure(bg=BG2, fg=MUTED)
            self._speak_hint.configure(bg=BG, fg=MUTED)
            self.text.configure(
                bg=ENTRY_BG, fg=FG, insertbackground=ACCENT,
                selectbackground=BG3, selectforeground=FG,
            )
            for page in self.pages.values():
                page.configure(bg=BG)
            self.show_page(self._page)
        except tk.TclError:
            pass

    # ── page: phrases ───────────────────────────────────────────────────

    def _build_page_phrases(self) -> None:
        page = tk.Frame(self.content, bg=BG)
        self.pages["phrases"] = page
        page.columnconfigure(0, weight=1)
        page.rowconfigure(1, weight=1)

        top = tk.Frame(page, bg=BG)
        top.grid(row=0, column=0, sticky="ew", padx=16, pady=(12, 6))
        tk.Label(
            top, text="Phrases", fg=ACCENT, bg=BG, font=("Segoe UI", 14, "bold"),
        ).pack(side="left")
        tk.Label(
            top, text="select a row · actions below · Edit opens the form",
            fg=MUTED, bg=BG, font=("Segoe UI", 9),
        ).pack(side="left", padx=12)

        # FULL WIDTH list (no right editor column)
        wrap = tk.Frame(page, bg=BORDER)
        wrap.grid(row=1, column=0, sticky="nsew", padx=16, pady=4)
        wrap.columnconfigure(0, weight=1)
        wrap.rowconfigure(0, weight=1)
        inner = tk.Frame(wrap, bg=ENTRY_BG)
        inner.grid(row=0, column=0, sticky="nsew", padx=1, pady=1)
        inner.columnconfigure(0, weight=1)
        inner.rowconfigure(0, weight=1)

        cols = ("hotkey", "command", "tts", "name", "text")
        self._configure_tree_style()
        self.tree = ttk.Treeview(
            inner, columns=cols, show="headings", style="App.Treeview", selectmode="browse",
        )
        for col, label, w in (
            ("hotkey", "Hotkey", 120),
            ("command", "Command", 90),
            ("tts", "TTS", 70),
            ("name", "Name", 110),
            ("text", "Preview", 280),
        ):
            self.tree.heading(col, text=label)
            self.tree.column(col, width=w, stretch=(col == "text"), minwidth=50)
        self.tree.grid(row=0, column=0, sticky="nsew")
        # mousewheel scroll, no scrollbar widget
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._ph_on_select())
        self.tree.bind("<Double-1>", lambda e: self._ph_edit())
        self.tree.bind("<MouseWheel>", self._tree_wheel)
        self.tree.bind("<Button-4>", self._tree_wheel)
        self.tree.bind("<Button-5>", self._tree_wheel)

        # selection info + action buttons (where editor used to be)
        panel = tk.Frame(page, bg=BG2, highlightbackground=BORDER, highlightthickness=1)
        panel.grid(row=2, column=0, sticky="ew", padx=16, pady=8)
        pf = tk.Frame(panel, bg=BG2)
        pf.pack(fill="x", padx=12, pady=10)

        self.ph_info = tk.Label(
            pf, text="No phrase selected",
            fg=MUTED, bg=BG2, font=("Segoe UI", 10), justify="left", anchor="w",
        )
        self.ph_info.pack(fill="x", pady=(0, 8))

        actions = tk.Frame(pf, bg=BG2)
        actions.pack(fill="x")
        for i, (label, cmd, danger, primary) in enumerate((
            ("+ New", self._ph_add, False, False),
            ("✎ Edit", self._ph_edit, False, True),
            ("⌨ Hotkey", self._ph_hotkey, False, False),
            ("/ Command", self._ph_command, False, False),
            ("🎙 TTS", self._ph_tts, False, False),
            ("▶ Test", self._ph_test, False, False),
            ("🗑 Delete", self._ph_delete, True, False),
        )):
            b = ui_button(actions, label, cmd, danger=danger, primary=primary, padx=10, pady=7)
            b.grid(row=i // 4, column=i % 4, padx=4, pady=4, sticky="ew")
            actions.columnconfigure(i % 4, weight=1)

        # editor (hidden until Edit / New)
        self._ph_editor = tk.Frame(page, bg=BG2, highlightbackground=BORDER, highlightthickness=1)
        # not gridded until shown
        ef = tk.Frame(self._ph_editor, bg=BG2)
        ef.pack(fill="both", expand=True, padx=12, pady=10)
        self.ph_lbl = tk.Label(
            ef, text="Editor", fg=ACCENT, bg=BG2, font=("Segoe UI", 12, "bold"),
        )
        self.ph_lbl.pack(anchor="w")
        row = tk.Frame(ef, bg=BG2)
        row.pack(fill="x", pady=4)
        tk.Label(row, text="Name", fg=MUTED, bg=BG2, width=10, anchor="w").pack(side="left")
        self.ph_var_name = tk.StringVar()
        ui_entry(row, self.ph_var_name).pack(side="left", fill="x", expand=True, ipady=7)
        row2 = tk.Frame(ef, bg=BG2)
        row2.pack(fill="x", pady=4)
        tk.Label(row2, text="Command", fg=MUTED, bg=BG2, width=10, anchor="w").pack(side="left")
        self.ph_var_cmd = tk.StringVar()
        ui_entry(row2, self.ph_var_cmd).pack(side="left", fill="x", expand=True, ipady=7)
        tk.Label(ef, text="Spoken text", fg=MUTED, bg=BG2).pack(anchor="w", pady=(6, 2))
        self.ph_txt = tk.Text(
            ef, bg=ENTRY_BG, fg=FG, insertbackground=ACCENT, height=6,
            relief="flat", font=("Segoe UI", 11), wrap="word", padx=10, pady=8,
            highlightthickness=1, highlightbackground=BORDER, highlightcolor=ACCENT,
        )
        self.ph_txt.pack(fill="both", expand=True)
        erow = tk.Frame(ef, bg=BG2)
        erow.pack(fill="x", pady=(10, 0))
        tk.Button(
            erow, text="✓  SAVE PHRASE", command=self._ph_save_editor,
            bg=BTN_PRIMARY_BG, fg=BTN_PRIMARY_FG, relief="flat",
            font=("Segoe UI", 12, "bold"), padx=16, pady=12, cursor="hand2",
        ).pack(side="left")
        ui_button(erow, "Cancel", self._ph_hide_editor).pack(side="left", padx=8)

        foot = tk.Frame(page, bg=BG)
        foot.grid(row=3, column=0, sticky="ew", padx=16, pady=8)
        ui_button(foot, "← Menu", lambda: self.show_page("menu"), primary=True).pack(
            side="right",
        )
        self._ph_editor_visible = False

    def _tree_wheel(self, event: tk.Event) -> str:
        if event.delta:
            self.tree.yview_scroll(int(-1 * (event.delta / 120)), "units")
        elif getattr(event, "num", None) == 4:
            self.tree.yview_scroll(-3, "units")
        elif getattr(event, "num", None) == 5:
            self.tree.yview_scroll(3, "units")
        return "break"

    def _configure_tree_style(self) -> None:
        self._style.configure(
            "App.Treeview",
            background=ENTRY_BG,
            foreground=FG,
            fieldbackground=ENTRY_BG,
            rowheight=30,
            borderwidth=0,
            font=("Segoe UI", 10),
        )
        self._style.configure(
            "App.Treeview.Heading",
            background=BG2,
            foreground=MUTED,
            font=("Segoe UI", 9, "bold"),
            relief="flat",
        )
        self._style.map(
            "App.Treeview",
            background=[("selected", BG3)],
            foreground=[("selected", FG)],
        )

    def _phrases_refresh(self) -> None:
        try:
            self.phrases = load_phrases()
            self._configure_tree_style()
            for iid in self.tree.get_children():
                self.tree.delete(iid)
            for p in self.phrases:
                text = read_phrase_text(p).replace("\n", " ").strip()
                if len(text) > 60:
                    text = text[:57] + "…"
                name = p.get("name") or text[:18] or p["id"][:8]
                iid = f"ph_{p['id']}"
                self.tree.insert(
                    "", "end", iid=iid,
                    values=(
                        hotkey_display(p.get("hotkey") or ""),
                        p.get("command") or "—",
                        p.get("tts_provider") or "auto",
                        name,
                        text or "(empty)",
                    ),
                )
            self._ph_update_info()
            self._set_status(f"phrases: {len(self.phrases)} loaded", MUTED)
        except Exception as e:
            self._set_status(f"phrases error: {e}", ERR)
            messagebox.showerror("Phrases", str(e), parent=self.root)

    def _ph_selected(self) -> dict | None:
        sel = self.tree.selection()
        if not sel:
            return None
        iid = sel[0]
        pid = iid[3:] if iid.startswith("ph_") else iid
        return next((p for p in self.phrases if p["id"] == pid), None)

    def _ph_update_info(self) -> None:
        p = self._ph_selected()
        if not p:
            self.ph_info.configure(text="No phrase selected — click a row, then Edit / Hotkey / …")
            return
        text = read_phrase_text(p).replace("\n", " ").strip()
        if len(text) > 80:
            text = text[:77] + "…"
        pool_bits = []
        hk = p.get("hotkey") or ""
        if hk:
            hpool = find_phrases_by_hotkey(hk, self.phrases)
            if len(hpool) > 1:
                pct = int(round(phrase_pick_chance(p, hpool) * 100))
                pool_bits.append(f"hotkey pool×{len(hpool)} · next ~{pct}%")
        cmd = p.get("command") or ""
        if cmd:
            cpool = find_phrases_by_command(cmd, self.phrases)
            if len(cpool) > 1:
                pct = int(round(phrase_pick_chance(p, cpool) * 100))
                pool_bits.append(f"cmd pool×{len(cpool)} · next ~{pct}%")
        pool_line = ("\n🎲 " + "  ·  ".join(pool_bits)) if pool_bits else ""
        audio = "audio ✓" if phrase_has_cache(p["id"]) else "audio — (Test or Save to record)"
        self.ph_info.configure(
            text=(
                f"Name: {p.get('name') or '—'}   ·   "
                f"Hotkey: {hotkey_display(p.get('hotkey') or '')}   ·   "
                f"Cmd: {p.get('command') or '—'}   ·   "
                f"TTS: {p.get('tts_provider') or 'eleven'}   ·   {audio}"
                f"{pool_line}\n"
                f"Text: {text or '(empty)'}"
            ),
            fg=FG,
        )

    def _ph_on_select(self) -> None:
        self._ph_update_info()

    def _ph_show_editor(self) -> None:
        if not self._ph_editor_visible:
            self._ph_editor.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 4))
            self._ph_editor_visible = True

    def _ph_hide_editor(self) -> None:
        if self._ph_editor_visible:
            self._ph_editor.grid_forget()
            self._ph_editor_visible = False
        self._edit_phrase_id = None
        self.ph_var_name.set("")
        self.ph_var_cmd.set("")
        self.ph_txt.delete("1.0", "end")

    def _ph_clear_editor(self) -> None:
        self._ph_hide_editor()
        try:
            self.tree.selection_remove(self.tree.selection())
        except tk.TclError:
            pass
        self._ph_update_info()

    def _ph_add(self) -> None:
        self._edit_phrase_id = None
        self.ph_var_name.set("")
        self.ph_var_cmd.set("")
        self.ph_txt.delete("1.0", "end")
        self.ph_lbl.configure(text="New phrase")
        self._ph_show_editor()
        self.ph_txt.focus_set()

    def _ph_edit(self) -> None:
        p = self._ph_selected()
        if not p:
            messagebox.showinfo("Phrases", "Select a phrase first", parent=self.root)
            return
        self._edit_phrase_id = p["id"]
        self.ph_var_name.set(p.get("name") or "")
        self.ph_var_cmd.set(p.get("command") or "")
        self.ph_txt.delete("1.0", "end")
        self.ph_txt.insert("1.0", read_phrase_text(p))
        self.ph_lbl.configure(text=f"Edit: {p.get('name') or p['id']}")
        self._ph_show_editor()
        self.ph_txt.focus_set()

    def _ph_save_editor(self) -> None:
        try:
            name = self.ph_var_name.get().strip()
            text = self.ph_txt.get("1.0", "end-1c").strip()
            cmd_raw = self.ph_var_cmd.get().strip()
            if not text:
                messagebox.showerror("Error", "Phrase text is empty", parent=self.root)
                return
            if not name:
                name = text[:24]
            command = normalize_slash_command(cmd_raw) if cmd_raw else ""
            if cmd_raw and not command:
                messagebox.showerror("Error", "Invalid command (use /name)", parent=self.root)
                return
            if command and command in RESERVED_COMMANDS:
                messagebox.showerror("Error", f"{command} is reserved", parent=self.root)
                return
            # Same command on several phrases is allowed → weighted random pool

            if self._edit_phrase_id:
                p = next((x for x in self.phrases if x["id"] == self._edit_phrase_id), None)
                if not p:
                    messagebox.showerror("Error", "Phrase not found", parent=self.root)
                    return
                p["name"] = name
                p["command"] = command
                write_phrase_text(p, text)
            else:
                pid = uuid.uuid4().hex[:12]
                p = {
                    "id": pid, "name": name, "hotkey": "", "command": command,
                    "tts_provider": "eleven", "file": f"{pid}.txt", "weight": 1.0,
                }
                write_phrase_text(p, text)
                self.phrases.append(p)

            save_phrases(self.phrases)
            self.phrases = load_phrases()
            # Re-find after reload (new dict objects)
            p = next((x for x in self.phrases if x["id"] == p["id"]), p)
            self._reload_hotkeys()
            self._phrases_refresh()
            try:
                self.tree.selection_set(f"ph_{p['id']}")
                self.tree.see(f"ph_{p['id']}")
            except tk.TclError:
                pass
            self._ph_hide_editor()
            self._ph_update_info()
            # Rebuild THIS phrase's audio only, with current mood/model/engine
            text_now = read_phrase_text(p).strip()
            prov = p.get("tts_provider") or "eleven"
            _TTS.submit(
                text_now, self.cfg, provider=prov, phrase_id=p["id"], force_rebuild=True,
            )
            self._set_status(
                f"✓ phrase saved: {name} · recording audio ({prov}, mood={self.cfg.get('mood')})",
                OK,
            )
        except Exception as e:
            messagebox.showerror("Error", str(e), parent=self.root)
            self._set_status(f"save failed: {e}", ERR)

    def _ph_hotkey(self) -> None:
        p = self._ph_selected()
        if not p:
            messagebox.showinfo("Phrases", "Select a phrase first", parent=self.root)
            return
        self._hotkey_target = p["id"]
        self._capture_tokens = []
        cur = p.get("hotkey") or ""
        if cur:
            self._capture_tokens = [x for x in cur.split("+") if x]
        self.hk_lbl.configure(text=hotkey_display(normalize_hotkey_tokens(self._capture_tokens)))
        self.hk_hint.configure(
            text="Press 1–3 keys (incl. numpad). Then Save.  Esc = back.\n"
                 "Same combo on several phrases = weighted random pool.",
        )
        self.show_page("hotkey")

    def _ph_command(self) -> None:
        p = self._ph_selected()
        if not p:
            messagebox.showinfo("Phrases", "Select a phrase first", parent=self.root)
            return
        raw = simpledialog.askstring(
            "Command",
            "Slash command (e.g. /hello). Empty = clear.\n"
            "Same command on several phrases = weighted random pool.",
            initialvalue=p.get("command") or "",
            parent=self.root,
        )
        if raw is None:
            return
        if not raw.strip():
            p["command"] = ""
            save_phrases(self.phrases)
            self._phrases_refresh()
            return
        cmd = normalize_slash_command(raw)
        if not cmd or cmd in RESERVED_COMMANDS:
            messagebox.showerror("Error", "Invalid or reserved command", parent=self.root)
            return
        p["command"] = cmd
        save_phrases(self.phrases)
        self._phrases_refresh()
        pool = find_phrases_by_command(cmd, self.phrases)
        if len(pool) > 1:
            self._set_status(
                f"command {cmd} set · shared pool×{len(pool)} (weighted random)", OK,
            )
        else:
            self._set_status(f"command {cmd} set", OK)

    def _ph_tts(self) -> None:
        p = self._ph_selected()
        if not p:
            messagebox.showinfo("Phrases", "Select a phrase first", parent=self.root)
            return
        cur = (p.get("tts_provider") or "auto").lower()
        win = tk.Toplevel(self.root)
        win.title("TTS engine")
        win.configure(bg=BG)
        win.geometry("400x320+140+140")
        win.resizable(True, True)
        win.attributes("-topmost", True)
        win.transient(self.root)
        tk.Label(
            win, text="Speak this phrase with",
            fg=ACCENT, bg=BG, font=("Segoe UI", 13, "bold"),
        ).pack(anchor="w", padx=16, pady=(16, 8))
        var = tk.StringVar(value=cur)
        for label, val in (
            ("Auto (Eleven → Edge → gTTS)", "auto"),
            ("ElevenLabs only (VPN/key)", "eleven"),
            ("Edge TTS", "edge"),
            ("gTTS", "gtts"),
        ):
            tk.Radiobutton(
                win, text=label, variable=var, value=val,
                bg=BG, fg=FG, selectcolor=BG3,
                activebackground=BG, activeforeground=FG,
                highlightthickness=0, font=("Segoe UI", 10), anchor="w",
            ).pack(fill="x", padx=20, pady=3)

        def save() -> None:
            p["tts_provider"] = var.get()
            # Engine change does not wipe audio; re-Save phrase to re-record.
            save_phrases(self.phrases)
            self._phrases_refresh()
            win.destroy()
            self._set_status(
                f"TTS for «{p.get('name')}» = {var.get()} · re-Save phrase to re-record audio",
                OK,
            )

        foot = tk.Frame(win, bg=BG2)
        foot.pack(fill="x", side="bottom")
        fi = tk.Frame(foot, bg=BG2)
        fi.pack(fill="x", padx=14, pady=12)
        tk.Button(
            fi, text="✓  SAVE TTS", command=save,
            bg=BTN_PRIMARY_BG, fg=BTN_PRIMARY_FG, relief="flat",
            font=("Segoe UI", 12, "bold"), pady=12, cursor="hand2",
        ).pack(fill="x", pady=(0, 8))
        ui_button(fi, "Cancel", win.destroy).pack(fill="x")

    def _ph_test(self) -> None:
        p = self._ph_selected()
        if not p:
            messagebox.showinfo("Phrases", "Select a phrase first", parent=self.root)
            return
        text = read_phrase_text(p).strip()
        if not text:
            messagebox.showerror("Error", "Empty phrase", parent=self.root)
            return
        prov = p.get("tts_provider") or "eleven"
        has = phrase_has_cache(p["id"])
        # Play locked file if present; only synth+save when no file yet
        _TTS.submit(text, self.cfg, provider=prov, phrase_id=p["id"], force_rebuild=False)
        if has:
            self._set_status(f"test cache: {text[:40]}", ACCENT2)
        else:
            self._set_status(f"test synth+save ({prov}): {text[:40]}", ACCENT2)

    def _ph_delete(self) -> None:
        p = self._ph_selected()
        if not p:
            messagebox.showinfo("Phrases", "Select a phrase first", parent=self.root)
            return
        if not messagebox.askyesno(
            "Delete", f"Delete «{p.get('name') or p['id']}»?", parent=self.root,
        ):
            return
        delete_phrase_files(p)
        invalidate_phrase_cache(p["id"])
        self.phrases = [x for x in self.phrases if x["id"] != p["id"]]
        save_phrases(self.phrases)
        self._reload_hotkeys()
        self._phrases_refresh()
        self._ph_hide_editor()
        self._set_status("phrase deleted", OK)

    # ── page: hotkey capture ────────────────────────────────────────────

    def _build_page_hotkey(self) -> None:
        page = tk.Frame(self.content, bg=BG)
        self.pages["hotkey"] = page
        self._capture_tokens: list[str] = []

        tk.Label(
            page, text="Set hotkey", fg=ACCENT, bg=BG, font=("Segoe UI", 16, "bold"),
        ).pack(pady=(36, 8))
        self.hk_hint = tk.Label(
            page,
            text="Press 1–3 keys (top row or numpad). Then Save.",
            fg=MUTED, bg=BG, font=("Segoe UI", 10),
        )
        self.hk_hint.pack(pady=4)
        self.hk_lbl = tk.Label(
            page, text="—", fg=FG, bg=BG, font=("Segoe UI", 22, "bold"),
        )
        self.hk_lbl.pack(pady=20)

        foot = tk.Frame(page, bg=BG2, highlightbackground=BORDER, highlightthickness=1)
        foot.pack(fill="x", side="bottom")
        fi = tk.Frame(foot, bg=BG2)
        fi.pack(fill="x", padx=20, pady=16)
        tk.Button(
            fi, text="✓  SAVE HOTKEY", command=self._hk_save,
            bg=BTN_PRIMARY_BG, fg=BTN_PRIMARY_FG, relief="flat",
            font=("Segoe UI", 12, "bold"), pady=12, cursor="hand2",
        ).pack(fill="x", pady=(0, 8))
        ui_button(fi, "Clear hotkey", self._hk_clear, danger=True).pack(fill="x", pady=(0, 8))
        ui_button(fi, "← Back without saving", self._hk_cancel).pack(fill="x")

    def _hk_press(self, event: tk.Event) -> str:
        if self._page != "hotkey":
            return ""
        low = (event.keysym or "").lower()
        if low in ("escape", "esc"):
            self._hk_cancel()
            return "break"
        # ignore pure enter for save — use button
        if low in ("return", "kp_enter"):
            return "break"

        # Pass X11 keycode so Ctrl+Shift+1 (keysym "exclam") → physical "1"
        kc = getattr(event, "keycode", None)
        tok = tk_keysym_to_token(event.keysym or "", kc)
        tok = _canonical_key_token(tok)
        if not tok:
            return "break"
        # accumulate up to 3 unique tokens
        if tok not in self._capture_tokens:
            if len(self._capture_tokens) >= 3:
                # keep first 3 only
                pass
            else:
                self._capture_tokens.append(tok)
        # rebuild normalized order for display
        norm = normalize_hotkey_tokens(self._capture_tokens)
        self._capture_tokens = norm.split("+") if norm else []
        self.hk_lbl.configure(text=hotkey_display(norm) if norm else "—")
        return "break"

    def _hk_release(self, event: tk.Event) -> str:
        return "break"

    def _hk_save(self) -> None:
        hk = normalize_hotkey_tokens(self._capture_tokens)
        self._hk_apply(hk)

    def _hk_clear(self) -> None:
        self._capture_tokens = []
        self.hk_lbl.configure(text="—")
        # apply empty immediately
        self._hk_apply("")

    def _hk_cancel(self) -> None:
        self._hotkey_target = None
        self._capture_tokens = []
        self.show_page("phrases")

    def _hk_apply(self, hk: str) -> None:
        pid = self._hotkey_target
        if not pid:
            self.show_page("phrases")
            return
        p = next((x for x in self.phrases if x["id"] == pid), None)
        if not p:
            self.show_page("phrases")
            return
        hk = normalize_hotkey_tokens(hk.split("+")) if hk else ""
        # Same hotkey on several phrases is allowed → weighted random pool
        p["hotkey"] = hk
        save_phrases(self.phrases)
        self._reload_hotkeys()
        self._hotkey_target = None
        self._capture_tokens = []
        self.show_page("phrases")
        if hk:
            pool = find_phrases_by_hotkey(hk, self.phrases)
            if len(pool) > 1:
                self._set_status(
                    f"hotkey {hotkey_display(hk)} saved · shared pool×{len(pool)} "
                    f"(weighted random)",
                    OK,
                )
            else:
                self._set_status(f"hotkey {hotkey_display(hk)} saved", OK)
        else:
            self._set_status("hotkey (cleared) saved", OK)

    # ── speak input handlers ────────────────────────────────────────────

    def _on_return(self, event: tk.Event):
        content = self.text.get("1.0", "end-1c")
        stripped = content.strip()

        if stripped in ("/exit", "/quit"):
            self._exit()
            return "break"
        if stripped in ("/menu", "/home"):
            self.text.delete("1.0", "end")
            self._spoken_upto = 0
            self.show_page("menu")
            return "break"
        if stripped in ("/settings", "/set"):
            self.text.delete("1.0", "end")
            self._spoken_upto = 0
            self.show_page("settings")
            return "break"
        if stripped in ("/phrases", "/phrase", "/ph"):
            self.text.delete("1.0", "end")
            self._spoken_upto = 0
            self.show_page("phrases")
            return "break"
        if stripped.startswith("/") and self._try_phrase_command(stripped):
            return "break"
        if stripped.startswith("/"):
            self._set_status(f"unknown command: {stripped.split()[0]}", ERR)
            return "break"

        mode = self.cfg.get("speak_mode") or "line"
        if mode == "line":
            if stripped:
                self.text.delete("1.0", "end")
                self._spoken_upto = 0
                self._enqueue_speak(stripped)
            return "break"
        pending = content[self._spoken_upto :].strip()
        if pending:
            self._spoken_upto = len(content)
            self._enqueue_speak(pending)
        return "break"

    def _try_phrase_command(self, stripped: str) -> bool:
        token = stripped.split()[0]
        pool = find_phrases_by_command(token, self.phrases)
        if not pool:
            self.phrases = load_phrases()
            pool = find_phrases_by_command(token, self.phrases)
        if not pool:
            return False
        # drop empty bodies
        pool = [p for p in pool if read_phrase_text(p).strip()]
        if not pool:
            self.text.delete("1.0", "end")
            self._spoken_upto = 0
            self._set_status(f"{token}: empty phrase", ERR)
            return True
        pool_n = len(pool)
        phrase = pick_weighted_phrase(pool, self.phrases, persist=True)
        text = read_phrase_text(phrase).strip()
        self.text.delete("1.0", "end")
        self._spoken_upto = 0
        name = phrase.get("name") or token
        prov = phrase.get("tts_provider") or "auto"
        n = _TTS.submit(text, self.cfg, provider=prov, phrase_id=phrase.get("id"))
        extra = ""
        if pool_n > 1:
            pct = int(round(phrase_pick_chance(phrase, pool) * 100))
            extra = f" · pool×{pool_n} · next ~{pct}%"
        self._set_status(
            f"cmd {token} → «{name}» ({prov}){extra}" + (f" · q={n}" if n > 1 else ""),
            ACCENT2,
        )
        return True

    def _on_key(self, event: tk.Event) -> None:
        if getattr(event, "keysym", "") in ("Return", "KP_Enter"):
            return
        content = self.text.get("1.0", "end-1c")
        stripped = content.strip()
        if stripped in ("/exit", "/quit"):
            self._exit()
            return
        if stripped in ("/menu", "/home"):
            self.text.delete("1.0", "end")
            self._spoken_upto = 0
            self.show_page("menu")
            return
        if stripped in ("/settings", "/set"):
            self.text.delete("1.0", "end")
            self._spoken_upto = 0
            self.show_page("settings")
            return
        if stripped in ("/phrases", "/phrase", "/ph"):
            self.text.delete("1.0", "end")
            self._spoken_upto = 0
            self.show_page("phrases")
            return
        if stripped.startswith("/"):
            return
        if (self.cfg.get("speak_mode") or "line") == "words":
            self._handle_words(content)

    def _handle_words(self, content: str) -> None:
        pending = content[self._spoken_upto :]
        if not pending:
            return
        last_break = -1
        for i, ch in enumerate(pending):
            if ch in WORD_BREAKS:
                last_break = i
        if last_break < 0:
            return
        piece = pending[: last_break + 1]
        speakable = piece.strip()
        self._spoken_upto += last_break + 1
        if speakable:
            self._enqueue_speak(speakable)

    def _enqueue_speak(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        prov = self.cfg.get("default_tts") or "auto"
        n = _TTS.submit(text, self.cfg, provider=prov)
        if n > 1:
            self._set_status(
                f"queued: {n} · «{text[:40]}{'…' if len(text) > 40 else ''}»",
                ACCENT,
            )

    def _exit(self) -> None:
        try:
            _HOTKEYS.stop()
        except Exception:
            pass
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    def run(self) -> None:
        def on_show(e=None):
            if self._page == "hotkey":
                try:
                    self.pages["hotkey"].focus_set()
                except tk.TclError:
                    pass
        self.root.bind("<Map>", on_show, add="+")
        self.root.mainloop()



def main() -> int:
    if not shutil.which("mpv") and not shutil.which("paplay"):
        print("mpv or paplay required: sudo apt install mpv", file=sys.stderr)
        return 1
    app = VoiceboxApp()
    app.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
