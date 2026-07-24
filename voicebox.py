#!/usr/bin/env python3
"""
voicebox — always-on-top window: type text → speak it.

  vo            open window
  /settings     settings (in the window)
  /exit         quit

Speak modes:
  line   — Enter → speak the line and clear the field
  words  — speak each word right after a space

Queue: new text never interrupts the current phrase; it waits its turn.
"""

from __future__ import annotations

import asyncio
import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
import urllib.error
import urllib.request
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

# dark theme
BG = "#1a1b22"
BG2 = "#24262f"
FG = "#e4e6ed"
MUTED = "#8b90a0"
ACCENT = "#a08cff"
ACCENT2 = "#64c8b4"
ERR = "#e66e6e"
OK = "#78c88c"
ENTRY_BG = "#12131a"
BORDER = "#3a3d4a"

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
        "provider": "auto",
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


def speak_sync(text: str, cfg: dict, on_status=None) -> str:
    """
    Synchronous TTS for one chunk. Call only from the TTS worker thread.
    Returns backend name: eleven|edge|gtts.
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

    provider = resolve_provider(cfg)
    sink = _pulse_sink(cfg)
    vol = int(cfg.get("volume", 100))
    plain = text
    if plain.startswith("[") and "]" in plain:
        plain = plain.split("]", 1)[-1].strip() or text

    errors: list[str] = []

    if provider == "eleven" and (cfg.get("eleven_keys") or []):
        keys = list(cfg.get("eleven_keys") or [])
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
                _eleven_stream_play(spoken, key, voice, model, settings, vol, sink)
                return "eleven"
            except Exception:
                try:
                    _eleven_file_play(spoken, key, voice, model, settings, vol, sink)
                    return "eleven"
                except Exception as e2:
                    errors.append(f"Eleven: {e2}")

    try:
        status("Edge TTS…")
        _speak_edge_file(plain, vol, sink)
        return "edge"
    except Exception as e:
        errors.append(f"Edge: {e}")

    try:
        status("gTTS…")
        _speak_gtts_file(plain, vol, sink)
        return "gtts"
    except Exception as e:
        errors.append(f"gTTS: {e}")

    hint = ""
    if any("403" in x or "Forbidden" in x for x in errors):
        hint = " · ElevenLabs is often blocked by region (try VPN)"
    raise RuntimeError("; ".join(errors) + hint)


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

    def submit(self, text: str, cfg: dict) -> int:
        """Append a phrase. Return queue length including the current item."""
        text = text.strip()
        if not text:
            return self.qsize()
        cfg_copy = json.loads(json.dumps(cfg))
        with self._lock:
            self._pending += 1
            n = self._pending
        self._q.put((text, cfg_copy))
        waiting = max(0, n - (1 if self._playing else 0))
        if self._playing or n > 1:
            self._ui(f"queued: {n} · «{text[:40]}{'…' if len(text) > 40 else ''}»", "queue")
        return n

    def _loop(self) -> None:
        while True:
            item = self._q.get()
            if item is None:
                break
            text, cfg = item
            with self._lock:
                self._playing = True
                left = self._pending
            preview = text[:48] + ("…" if len(text) > 48 else "")
            self._ui(f"speaking: {preview}" + (f"  ·  still queued: {left - 1}" if left > 1 else ""), "info")
            try:
                used = speak_sync(text, cfg, on_status=lambda m: self._ui(m, "info"))
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


# ── GUI ────────────────────────────────────────────────────────────────────

class VoiceboxApp:
    def __init__(self) -> None:
        self.cfg = load_config()
        self.root = tk.Tk()
        self.root.title("voicebox")
        self.root.configure(bg=BG)
        self.root.geometry("520x280+80+80")
        self.root.minsize(360, 200)

        # always on top (Dota / CS / Discord / TG)
        self.root.attributes("-topmost", True)
        try:
            self.root.wm_attributes("-topmost", True)
        except tk.TclError:
            pass

        # index of already spoken text (words mode only)
        self._spoken_upto = 0

        self._build_ui()
        # TTS queue: never interrupts the current phrase
        _TTS.set_ui_callback(self._tts_ui)
        threading.Thread(target=self._setup_virt, daemon=True).start()
        self._update_status()

        self.root.protocol("WM_DELETE_WINDOW", self._exit)
        self.text.focus_set()

    def _build_ui(self) -> None:
        pad = {"padx": 12, "pady": 6}

        head = tk.Frame(self.root, bg=BG)
        head.pack(fill="x", **pad)

        tk.Label(
            head, text="voicebox", fg=ACCENT, bg=BG,
            font=("Segoe UI", 14, "bold"),
        ).pack(side="left")

        tk.Button(
            head, text="⚙ Settings", command=self._open_settings,
            bg=BG2, fg=FG, relief="flat", padx=10, pady=3,
            activebackground=ACCENT, activeforeground="#111",
            font=("Segoe UI", 9),
        ).pack(side="right", padx=(8, 0))

        self.lbl_mode = tk.Label(head, text="", fg=MUTED, bg=BG, font=("Segoe UI", 9))
        self.lbl_mode.pack(side="right")

        hint = tk.Label(
            self.root,
            text="/settings  ·  /exit  ·  always on top",
            fg=MUTED, bg=BG, font=("Segoe UI", 9),
        )
        hint.pack(anchor="w", padx=12)

        frame = tk.Frame(self.root, bg=BG2, highlightbackground=BORDER, highlightthickness=1)
        frame.pack(fill="both", expand=True, padx=12, pady=8)

        self.text = tk.Text(
            frame,
            bg=ENTRY_BG,
            fg=FG,
            insertbackground=ACCENT,
            selectbackground="#3d3560",
            relief="flat",
            font=("Segoe UI", 12),
            wrap="word",
            padx=10,
            pady=10,
            height=8,
            borderwidth=0,
            highlightthickness=0,
        )
        self.text.pack(fill="both", expand=True)
        self.text.bind("<KeyRelease>", self._on_key)
        self.text.bind("<Return>", self._on_return)
        self.text.bind("<KP_Enter>", self._on_return)
        self.root.bind("<FocusIn>", lambda e: self._keep_top())
        self.root.after(2000, self._topmost_pulse)

        self.status = tk.Label(
            self.root, text="", fg=MUTED, bg=BG, font=("Segoe UI", 9), anchor="w",
        )
        self.status.pack(fill="x", padx=12, pady=(0, 10))

    def _keep_top(self) -> None:
        try:
            self.root.attributes("-topmost", True)
        except tk.TclError:
            pass

    def _topmost_pulse(self) -> None:
        """Keep the window above games/overlays."""
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

    def _update_status(self) -> None:
        mode = self.cfg.get("speak_mode") or "line"
        if mode == "words":
            self.lbl_mode.config(text="mode: word by word")
        else:
            self.lbl_mode.config(text="mode: Enter = line")

    def _set_status(self, msg: str, color: str = MUTED) -> None:
        try:
            self.status.config(text=msg, fg=color)
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

    def _on_return(self, event: tk.Event):
        """
        line  → speak full field, clear it, no newline
        words → speak trailing word (field not necessarily cleared)
        """
        content = self.text.get("1.0", "end-1c")
        stripped = content.strip()

        if stripped in ("/exit", "/quit"):
            self._exit()
            return "break"
        if stripped in ("/settings", "/set"):
            self.text.delete("1.0", "end")
            self._spoken_upto = 0
            self._open_settings()
            return "break"
        if stripped.startswith("/"):
            return "break"

        mode = self.cfg.get("speak_mode") or "line"
        if mode == "line":
            if stripped:
                self.text.delete("1.0", "end")
                self._spoken_upto = 0
                self._enqueue_speak(stripped)
            return "break"

        # words: flush last word without trailing space
        pending = content[self._spoken_upto :].strip()
        if pending:
            self._spoken_upto = len(content)
            self._enqueue_speak(pending)
        return "break"

    def _on_key(self, event: tk.Event) -> None:
        if getattr(event, "keysym", "") in ("Return", "KP_Enter"):
            return

        content = self.text.get("1.0", "end-1c")
        stripped = content.strip()

        if stripped in ("/exit", "/quit"):
            self._exit()
            return
        if stripped in ("/settings", "/set"):
            self.text.delete("1.0", "end")
            self._spoken_upto = 0
            self._open_settings()
            return
        if stripped.startswith("/"):
            return

        # only words mode reacts on every keystroke
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
        """Append to queue — never interrupt current playback."""
        text = text.strip()
        if not text:
            return
        n = _TTS.submit(text, self.cfg)
        if n > 1:
            self._set_status(
                f"queued: {n} · «{text[:40]}{'…' if len(text) > 40 else ''}»",
                ACCENT,
            )

    def _open_settings(self) -> None:
        SettingsDialog(self.root, self.cfg, on_save=self._on_settings_saved)

    def _on_settings_saved(self, cfg: dict) -> None:
        # deep copy so speak_mode/volume apply reliably
        self.cfg = json.loads(json.dumps(cfg))
        save_config(self.cfg)
        self._spoken_upto = 0
        self._update_status()
        threading.Thread(target=self._setup_virt, daemon=True).start()
        mode = self.cfg.get("speak_mode")
        self._set_status(f"✓ settings saved · mode={mode}", OK)
        self.text.focus_set()

    def _exit(self) -> None:
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    def run(self) -> None:
        self.root.mainloop()


class SettingsDialog(tk.Toplevel):
    def __init__(self, master: tk.Tk, cfg: dict, on_save) -> None:
        super().__init__(master)
        self.cfg = json.loads(json.dumps(cfg))  # deep copy
        self.on_save = on_save
        self.title("voicebox — settings")
        self.configure(bg=BG)
        self.geometry("440x560")
        self.attributes("-topmost", True)
        self.transient(master)
        self.grab_set()

        tk.Label(
            self, text="Settings", fg=ACCENT, bg=BG, font=("Segoe UI", 13, "bold"),
        ).pack(anchor="w", padx=14, pady=(12, 4))

        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True, padx=14, pady=6)

        self._section(body, "Speak mode (pick one)")
        cur = self.cfg.get("speak_mode") or "line"
        if cur not in ("line", "words"):
            cur = "line"
        self.var_mode = tk.StringVar(value=cur)
        f = tk.Frame(body, bg=BG)
        f.pack(fill="x", pady=2)
        tk.Radiobutton(
            f, text="Line + Enter  →  speak and clear the field",
            variable=self.var_mode, value="line",
            bg=BG, fg=FG, selectcolor=BG2, activebackground=BG, activeforeground=FG,
            font=("Segoe UI", 10),
        ).pack(anchor="w")
        tk.Radiobutton(
            f, text="Word by word  →  speak each word right after space",
            variable=self.var_mode, value="words",
            bg=BG, fg=FG, selectcolor=BG2, activebackground=BG, activeforeground=FG,
            font=("Segoe UI", 10),
        ).pack(anchor="w")

        self._section(body, "Volume 0–150")
        self.var_vol = tk.StringVar(value=str(int(self.cfg.get("volume", 100))))
        tk.Entry(
            body, textvariable=self.var_vol, bg=ENTRY_BG, fg=FG, insertbackground=ACCENT,
            relief="flat", font=("Segoe UI", 11),
        ).pack(fill="x", ipady=6, pady=2)

        self._section(body, "Mood")
        self.var_mood = tk.StringVar(value=self.cfg.get("mood") or "soft")
        ttk.Combobox(
            body, textvariable=self.var_mood, values=list(MOODS.keys()), state="readonly",
        ).pack(fill="x", pady=2)

        self._section(body, "TTS model")
        self.var_model = tk.StringVar(value=self.cfg.get("model") or EL_MODEL_V3)
        ttk.Combobox(
            body, textvariable=self.var_model,
            values=[EL_MODEL_V3, EL_MODEL_V2, "eleven_turbo_v2_5"],
            state="readonly",
        ).pack(fill="x", pady=2)

        self._section(body, "Audio output")
        self.var_audio = tk.StringVar(value=self.cfg.get("audio_out") or "both")
        ttk.Combobox(
            body, textvariable=self.var_audio,
            values=["both", "virt", "speakers"], state="readonly",
        ).pack(fill="x", pady=2)
        tk.Label(
            body, text="both = cable+headphones · virt = Voicebox_Mic only · speakers",
            fg=MUTED, bg=BG, font=("Segoe UI", 8),
        ).pack(anchor="w")

        self._section(body, "ElevenLabs API keys")
        keys = self.cfg.get("eleven_keys") or []
        self.lbl_keys = tk.Label(
            body, text=f"keys: {len(keys)}  (~{len(keys) * EL_FREE_CREDITS_MONTH:,} credits/mo)",
            fg=ACCENT2, bg=BG, font=("Segoe UI", 10),
        )
        self.lbl_keys.pack(anchor="w")
        bf = tk.Frame(body, bg=BG)
        bf.pack(fill="x", pady=4)
        tk.Button(
            bf, text="+ key", command=self._add_key,
            bg=BG2, fg=FG, relief="flat", padx=10, pady=4, activebackground=ACCENT,
        ).pack(side="left", padx=(0, 6))
        tk.Button(
            bf, text="− last", command=self._del_key,
            bg=BG2, fg=FG, relief="flat", padx=10, pady=4, activebackground=ERR,
        ).pack(side="left")

        # primary save button
        bot = tk.Frame(self, bg=BG)
        bot.pack(fill="x", padx=14, pady=14)
        tk.Button(
            bot, text="✓  Save settings", command=self._save,
            bg=ACCENT, fg="#111", relief="flat", padx=18, pady=10,
            font=("Segoe UI", 11, "bold"), activebackground=OK,
        ).pack(fill="x", pady=(0, 8))
        tk.Button(
            bot, text="Cancel", command=self.destroy,
            bg=BG2, fg=FG, relief="flat", padx=12, pady=6,
        ).pack(fill="x")

    def _section(self, parent: tk.Widget, title: str) -> None:
        tk.Label(
            parent, text=title, fg=MUTED, bg=BG, font=("Segoe UI", 9),
        ).pack(anchor="w", pady=(10, 2))

    def _add_key(self) -> None:
        raw = simpledialog.askstring("API key", "Paste sk_…", parent=self)
        if not raw:
            return
        key = normalize_eleven_key(raw)
        if not key.startswith("sk_") or len(key) < 20:
            messagebox.showerror("Error", "Key looks invalid", parent=self)
            return
        keys = list(self.cfg.get("eleven_keys") or [])
        if key in keys:
            messagebox.showinfo("OK", "Already added", parent=self)
            return
        keys.append(key)
        self.cfg["eleven_keys"] = keys
        self.cfg["eleven_key_index"] = len(keys) - 1
        self.cfg["provider"] = "eleven"
        self.lbl_keys.config(
            text=f"keys: {len(keys)}  (~{len(keys) * EL_FREE_CREDITS_MONTH:,} credits/mo)"
        )

    def _del_key(self) -> None:
        keys = list(self.cfg.get("eleven_keys") or [])
        if not keys:
            return
        keys.pop()
        self.cfg["eleven_keys"] = keys
        self.cfg["eleven_key_index"] = max(0, len(keys) - 1)
        self.lbl_keys.config(
            text=f"keys: {len(keys)}  (~{len(keys) * EL_FREE_CREDITS_MONTH:,} credits/mo)"
        )

    def _save(self) -> None:
        mode = self.var_mode.get()
        if mode not in ("line", "words"):
            mode = "line"
        try:
            vol = max(0, min(150, int(self.var_vol.get().strip())))
        except ValueError:
            messagebox.showerror("Error", "Volume must be a number 0–150", parent=self)
            return

        self.cfg["speak_mode"] = mode
        self.cfg["volume"] = vol
        self.cfg["mood"] = self.var_mood.get() or "soft"
        self.cfg["model"] = self.var_model.get() or EL_MODEL_V3
        self.cfg["audio_out"] = self.var_audio.get() or "both"
        self.cfg["use_virt_mic"] = self.cfg["audio_out"] != "speakers"
        if self.cfg.get("eleven_keys"):
            self.cfg["provider"] = "eleven"

        save_config(self.cfg)
        # confirmation dialog
        messagebox.showinfo(
            "Saved",
            f"Settings applied.\nMode: {'word by word' if mode == 'words' else 'line + Enter'}",
            parent=self,
        )
        self.on_save(self.cfg)
        self.destroy()


def main() -> int:
    if not shutil.which("mpv") and not shutil.which("paplay"):
        print("mpv or paplay required: sudo apt install mpv", file=sys.stderr)
        return 1
    app = VoiceboxApp()
    app.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
