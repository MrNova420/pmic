#!/usr/bin/env python3
"""PMic server — phone-as-microphone bridge for Windows.

One process does everything:
  * serves the phone client and PC dashboard (HTTP)
  * control REST API (JSON)
  * audio ingest WebSocket (/audio) — PCM16LE mono 48 kHz binary frames
  * WASAPI playback into the selected output device (a virtual mic when present)
"""

from __future__ import annotations

import asyncio
import contextlib
import ctypes
import ipaddress
import json
import math
import mimetypes
import os
import secrets
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import numpy as np
import sounddevice as sd

try:
    import default_mic as dmic
    _HAS_DMIC = True
except Exception:
    _HAS_DMIC = False

try:
    import sysstate
except Exception:
    sysstate = None

try:
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    import datetime
    _HAS_CRYPTO = True
except ImportError:
    _HAS_CRYPTO = False

try:
    import qrcode  # type: ignore
    _HAS_QR = True
except ImportError:
    _HAS_QR = False

from aiohttp import WSMsgType, web

APP_DIR = Path(__file__).resolve().parent if not getattr(sys, "frozen", False) \
    else Path(sys.executable).resolve().parent
# PyInstaller bundles data files under _MEIPASS (onedir: <exe>_internal)
BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", APP_DIR)) if getattr(sys, "frozen", False) else APP_DIR
WEB_DIR = BUNDLE_DIR / "web"


def _is_writable(directory: Path) -> bool:
    """True if this process can create files in `directory`."""
    try:
        probe = directory / ".pmic-write-probe"
        probe.write_text("")
        probe.unlink()
        return True
    except OSError:
        return False


def _resolve_state_dir(app_dir: Path | None = None) -> Path:
    """Directory for mutable state: config, journal, TLS certificate + key.

    Portable and from-source runs keep state next to the app, which is what
    existing users already have. An installed build lives under
    %ProgramFiles%, which is read-only for standard users, so those fall back
    to %LOCALAPPDATA%\\PMic rather than failing every write.
    """
    app_dir = APP_DIR if app_dir is None else app_dir
    if _is_writable(app_dir):
        return app_dir
    for env in ("LOCALAPPDATA", "APPDATA"):
        base = os.environ.get(env)
        if not base:
            continue
        candidate = Path(base) / "PMic"
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            return candidate
        except OSError:
            continue
    return Path(tempfile.gettempdir()) / "PMic"


STATE_DIR = _resolve_state_dir()
CONFIG_PATH = STATE_DIR / "pmic-config.json"

VERSION = "1.4.1-beta.1"
SAMPLE_RATE = 48_000
CHANNELS = 1
SAMPLE_WIDTH = 2          # int16
DEFAULT_PORT = 8787
DEFAULT_BUFFER_MS = 60
DEFAULT_GAIN = 1.0
CHANNEL_BYTES = SAMPLE_WIDTH * CHANNELS

VIRTUAL_MIC_HINTS = (
    "cable input", "voicemeeter input", "virtual audio", "blackhole",
    "sonobus virtual", "vb-audio",
)

# virtual mic driver ships inside the app — fully offline, nothing to download
# source: https://www.vb-audio.com/Cable/ (official package, unmodified)
CABLE_ZIP_NAME = "VBCABLE_Driver_Pack46.zip"


# --------------------------------------------------------------------------
# configuration
# --------------------------------------------------------------------------

def load_config() -> dict:
    cfg = {
        "device": None,          # sounddevice index (int) or None = auto
        "gain": DEFAULT_GAIN,
        "buffer_ms": DEFAULT_BUFFER_MS,
        "pin": None,             # current pairing PIN (str) or None
        "pin_required": True,
        "mute": False,
        "cleanup": "studio",
        "gate_db": -40.0,
        "client_name": None,     # active phone, informational
    }
    if CONFIG_PATH.exists():
        try:
            saved = json.loads(CONFIG_PATH.read_text())
            for key in ("device", "device_name", "gain", "buffer_ms",
                        "pin_required", "mute", "cleanup", "gate_db", "pin"):
                if key in saved:
                    cfg[key] = saved[key]
        except Exception:
            pass
    return cfg


def save_config(cfg: dict) -> None:
    try:
        CONFIG_PATH.write_text(json.dumps({
            "device": cfg.get("device"),
            "device_name": cfg.get("device_name"),
            "gain": cfg.get("gain", 1.0),
            "buffer_ms": cfg.get("buffer_ms", 60),
            "pin_required": cfg.get("pin_required", True),
            "mute": cfg.get("mute", False),
            "cleanup": cfg.get("cleanup", "studio"),
            "gate_db": cfg.get("gate_db", -40.0),
            # the pairing code is deliberately NOT persisted: it is regenerated
            # on every launch, so writing it down only creates stale state
        }, indent=2))
    except Exception:
        pass


# --------------------------------------------------------------------------
# audio output: jitter buffer + WASAPI callback thread
# --------------------------------------------------------------------------

# --------------------------------------------------------------------------
# audio cleanup DSP: high-pass filter → noise gate → compressor/leveler
# --------------------------------------------------------------------------

class CleanupChain:
    """Studio-style phone-mic cleanup, all float32, block-wise.

    Modes:
      off    - raw passthrough
      basic  - high-pass (rumble handling) + noise gate
      studio - basic + compressor/leveler with makeup gain (even loudness)
    """

    def __init__(self, sample_rate: int):
        self.sr = sample_rate
        self.mode = "off"
        self.gate_threshold = 0.01     # RMS amplitude (~ -40 dBFS)
        # high-pass biquad state (RBJ cookbook, f0=85 Hz, Q=0.707)
        f0, q = 85.0, 0.7071
        w0 = 2 * math.pi * f0 / self.sr
        alpha = math.sin(w0) / (2 * q)
        cw = math.cos(w0)
        a0 = 1 + alpha
        self.b = ((1 + cw) / 2 / a0, -(1 + cw) / a0, (1 + cw) / 2 / a0)
        self.a = (-2 * cw / a0, (1 - alpha) / a0)
        self.x1 = self.x2 = self.y1 = self.y2 = 0.0
        # envelopes
        self.gate_gain = 0.0
        self.comp_env = 0.0
        self.block_s = 480 / self.sr   # 10 ms

    def configure(self, mode: str, gate_db: float) -> None:
        if mode in ("off", "basic", "studio"):
            self.mode = mode
        self.gate_threshold = max(0.002, min(0.2, 10 ** (gate_db / 20)))

    def process(self, x: np.ndarray) -> np.ndarray:
        if self.mode == "off" or x.size == 0:
            return x
        # 1. high-pass: kill rumble/handling noise
        xf = x.astype(np.float32)
        y = np.empty_like(xf)
        b0, b1, b2 = self.b
        a1, a2 = self.a
        x1, x2, y1, y2 = self.x1, self.x2, self.y1, self.y2
        for i in range(xf.size):
            xn = xf[i]
            yn = b0 * xn + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
            y[i] = yn
            x2, x1, y2, y1 = x1, xn, y1, yn
        self.x1, self.x2, self.y1, self.y2 = x1, x2, y1, y2

        # 2. noise gate (smoothed, click-free attack/release)
        rms = float(np.sqrt(np.mean(np.square(y))))
        target = 1.0 if rms > self.gate_threshold else 0.05
        speed = self.block_s / (0.002 if target > self.gate_gain else 0.15)
        self.gate_gain += (target - self.gate_gain) * min(1.0, speed)
        y *= self.gate_gain

        # 3. compressor/leveler (studio mode): even, controlled loudness
        if self.mode == "studio":
            threshold, ratio = 0.18, 4.0            # ≈ -15 dBFS, 4:1
            fast = self.block_s / 0.01
            slow = self.block_s / 0.15
            coeff = fast if rms > self.comp_env else slow
            self.comp_env += (rms - self.comp_env) * min(1.0, coeff)
            env = max(self.comp_env, 1e-6)
            if env > threshold:
                # static curve: out = T * (env/T)^(1/r)
                gain = (threshold / env) * (env / threshold) ** (1.0 / ratio)
            else:
                gain = 1.0
            makeup = min(3.0, threshold ** (1.0 - 1.0 / ratio) * 12.0)
            y = y * gain * makeup

        return y


class AudioBridge:
    """Receives PCM frames from the network, plays them to a WASAPI device."""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.lock = threading.Lock()
        self.ring = bytearray()          # raw int16 bytes, mono
        self.target_bytes = 0            # overflow cap
        self.device = None               # sd index in use, None = default
        self.stream: sd.RawOutputStream | None = None
        self.playing = False
        self.client_ws = None            # active phone (aiohttp websocket)
        self.client_name = None

        # adaptive jitter buffer: base target from settings; effective target
        # grows after underruns, decays back when the link stays healthy
        self.base_bytes = 0
        self.adapt_bytes = 0

        # clock recovery: the phone's mic clock and the PC's sound clock are
        # independent crystals (~±100 ppm apart). Over 30 min that drifts by a
        # second — enough to break any fixed buffer. The playback position is
        # advanced fractionally against measured buffer level, which re-times
        # the stream continuously and inaudibly.
        self.read_pos = 0.0        # float sample position within the ring
        self.advance = 1.0         # playback rate relative to real time
        self._err_ema = 0.0        # smoothed buffer-level error (samples)
        self._push_hist = []       # (time, cumulative pushed samples)
        self._push_total = 0       # for measuring the phone's true clock rate
        self._rate_ratio = 1.0
        self._last_open_attempt = 0.0   # reopen-rate limiter
        self._last_output_ts = 0.0      # silence detector (real audio out)
        self._dev_cache = None          # (timestamp, devices) — see list_devices

        # stats
        self.packets = 0
        self.bytes_in = 0
        self.underruns = 0
        self.level_rms = 0.0             # smoothed 0..1 (post-gain, pre-mute)
        self.last_packet_at = 0.0
        self.started_at = None

        # cleanup DSP (mode + gate threshold from config)
        self.cleanup = CleanupChain(SAMPLE_RATE)
        self.cleanup.configure(cfg.get("cleanup", "studio"), cfg.get("gate_db", -40.0))

        self._recalc_target()
        self._open_stream()

    # ---- device handling ------------------------------------------------

    def list_devices(self, force: bool = False) -> list[dict]:
        # Enumerating audio devices walks every host API (MME/DirectSound/
        # WASAPI) and costs real time. The overlay polls status ~3x/second, so
        # without a cache this runs hundreds of times a minute forever and
        # keeps poking the audio subsystem. Cache briefly; force on demand.
        now = time.time()
        cached = getattr(self, "_dev_cache", None)
        if not force and cached is not None and (now - cached[0]) < 3.0:
            return cached[1]
        out = []
        try:
            devs = sd.query_devices()
            for i, d in enumerate(devs):
                if d["max_output_channels"] > 0 and "Sound Mapper" not in d["name"]:
                    out.append({
                        "id": i,
                        "name": d["name"].strip(),
                        "channels": d["max_output_channels"],
                        "hostapi": sd.query_hostapis(d["hostapi"])["name"],
                        "default": (i == sd.default.device[1]),
                        "virtual": self._is_virtual(d["name"]),
                    })
        except Exception:
            pass
        self._dev_cache = (now, out)
        return out

    @staticmethod
    def _is_virtual(name: str) -> bool:
        n = name.lower()
        return any(h in n for h in VIRTUAL_MIC_HINTS)

    def find_virtual_device(self) -> int | None:
        """Among virtual-mic candidates prefer the real WASAPI endpoint that
        pairs with the 'CABLE Output' recording device."""
        cands = [d for d in self.list_devices() if d["virtual"]]
        if not cands:
            return None
        cands.sort(key=lambda d: (
            0 if "wasapi" in d["hostapi"].lower() else 1,
            0 if d["channels"] <= 2 else 1,
            0 if "cable" in d["name"].lower() else 1,
        ))
        return cands[0]["id"]

    def resolve_saved_device(self) -> int | None:
        """Device indexes shift whenever Windows re-enumerates audio devices,
        so the saved choice is matched by NAME (preferring the WASAPI
        endpoint, which is the one that pairs with the virtual mic)."""
        name = self.cfg.get("device_name")
        if not name:
            return None
        cands = [d for d in self.list_devices() if d["name"] == name]
        if not cands:
            return None
        cands.sort(key=lambda d: (
            0 if "wasapi" in d["hostapi"].lower() else 1,
            d["channels"],
        ))
        return cands[0]["id"]

    def auto_pick_device(self) -> int | None:
        """Prefer the saved (by-name) device, then a virtual mic, then default."""
        resolved = self.resolve_saved_device()
        if resolved is not None:
            self.cfg["device"] = resolved
            return resolved
        self.cfg["device"] = None
        return self.find_virtual_device()

    def select_device(self, device_id: int | None) -> bool:
        try:
            if device_id is not None:
                d = sd.query_devices(device_id)
                if d["max_output_channels"] <= 0:
                    return False
                self.cfg["device_name"] = d["name"].strip()
            else:
                self.cfg["device_name"] = None
            self.cfg["device"] = device_id
            self._open_stream(device_id)
            save_config(self.cfg)
            return True
        except Exception:
            return False

    # ---- stream lifecycle ------------------------------------------------

    MAX_ADAPT_MS = 150
    ADAPT_STEP_MS = 12               # added after each underrun

    def _recalc_target(self) -> None:
        self.base_bytes = max(
            1, int(SAMPLE_RATE * self.cfg.get("buffer_ms", DEFAULT_BUFFER_MS) / 1000)
        ) * CHANNEL_BYTES
        self.target_bytes = self.base_bytes * 3   # overflow cap
        # keep prior adaptation, bounded by the new settings
        floor = self.base_bytes
        ceiling = min(self.target_bytes,
                      int(SAMPLE_RATE * self.MAX_ADAPT_MS / 1000) * CHANNEL_BYTES)
        self.adapt_bytes = max(floor, min(self.adapt_bytes or floor, ceiling))

    def _open_stream(self, device_id: int | None = None) -> None:
        # reopen-rate limiter: reopening the stream hammers the audio driver
        # (a virtual one especially) and can wedge the Windows audio graph.
        now = time.time()
        gap = min(15.0, 5.0 + getattr(self, "_open_failures", 0) * 5.0)
        if now - self._last_open_attempt < gap:
            return
        self._last_open_attempt = now
        self._dev_cache = None          # device lists shift; start from truth
        if device_id is None:
            device_id = self.auto_pick_device()
        with self.lock:
            self.playing = False
            self.ring.clear()
            self.read_pos = 0.0
            self.advance = 1.0
            self._err_ema = 0.0
        if self.stream is not None:
            with contextlib.suppress(Exception):
                self.stream.close()
            self.stream = None
        self.device = device_id
        try:
            self.stream = sd.RawOutputStream(
                samplerate=SAMPLE_RATE,
                blocksize=480,                 # 10 ms
                channels=CHANNELS,
                dtype="int16",
                device=device_id,
                callback=self._audio_callback,
            )
            self.stream.start()
        except Exception as e:
            print(f"[pmic] audio output failed to open: {e}", file=sys.stderr)
            self.stream = None

    def restart_stream(self) -> dict:
        """Manually tear down and rebuild the output stream.

        The 'Fix audio' action: drops a stuck stream, re-resolves the saved
        device by name and opens a fresh one. Deliberately bypasses the
        reopen-rate limiter because the user asked for it explicitly.
        """
        with self.lock:
            self.playing = False
            self.ring.clear()
            self.read_pos = 0.0
            self._err_ema = 0.0
        stream, self.stream = self.stream, None
        if stream is not None:
            with contextlib.suppress(Exception):
                stream.stop()
            with contextlib.suppress(Exception):
                stream.close()
        self._last_open_attempt = 0.0     # user-initiated: no backoff
        self._open_failures = 0
        time.sleep(0.25)                  # let the driver release the endpoint
        self._open_stream(None)
        return self.status()

    def close(self) -> None:
        with self.lock:
            self.playing = False
            self.ring.clear()
            self.read_pos = 0.0
            self.advance = 1.0
            self._err_ema = 0.0
        if self.stream is not None:
            with contextlib.suppress(Exception):
                self.stream.stop()
                self.stream.close()
            self.stream = None

    def set_buffer_ms(self, ms: float) -> None:
        self.cfg["buffer_ms"] = max(20, min(200, float(ms)))
        self._recalc_target()
        save_config(self.cfg)

    # ---- ingest ----------------------------------------------------------

    def attach_client(self, ws, name: str | None) -> None:
        """A new phone takes ownership of the mic; any previous one is dropped."""
        old = self.client_ws
        if old is not None:
            with contextlib.suppress(Exception):
                asyncio.get_event_loop().create_task(self._kick(old))
        with self.lock:
            self.ring.clear()
        self.client_ws = ws
        self.client_name = name
        self.packets = 0
        self.bytes_in = 0
        self.underruns = 0
        with self.lock:
            self.ring.clear()
            self.read_pos = 0.0
            self.advance = 1.0
            self._err_ema = 0.0
            self._rate_ratio = 1.0
            self._push_hist.clear()
            self._push_total = 0
            self.adapt_bytes = self.base_bytes    # fresh link, fresh buffer
        self.started_at = time.time()

    @staticmethod
    async def _kick(ws) -> None:
        with contextlib.suppress(Exception):
            await ws.send_json({"type": "displaced",
                                "reason": "another device took the mic"})
            await ws.close()

    def detach_client(self, ws) -> None:
        if self.client_ws is ws:
            self.client_ws = None
            self.client_name = None

    def push(self, pcm: bytes) -> None:
        """Network thread: append a PCM16LE mono frame."""
        self.packets += 1
        self.bytes_in += len(pcm)
        self.last_packet_at = time.time()

        # level from ~10 ms of the tail (fast, representative)
        samples = np.frombuffer(pcm[-960 * CHANNEL_BYTES:], dtype=np.int16)
        if samples.size:
            rms = float(np.sqrt(np.mean(np.square(samples.astype(np.float64))))) / 32768.0
            self.level_rms = self.level_rms * 0.7 + rms * 0.3

        if self.cfg["mute"]:
            return
        self._push_total += len(pcm) // CHANNEL_BYTES
        now = time.time()
        self._push_hist.append((now, self._push_total))
        if len(self._push_hist) > 512:
            del self._push_hist[:256]
        cutoff = now - 10.0
        old = next((h for h in self._push_hist if h[0] >= cutoff), None)
        if old is not None and now - old[0] >= 5.0:
            span = now - old[0]
            rate = (self._push_total - old[1]) / span / SAMPLE_RATE
            self._rate_ratio = self._rate_ratio * 0.9 + rate * 0.1
        with self.lock:
            overflow = len(self.ring) + len(pcm) - self.target_bytes
            if overflow > 0:
                del self.ring[:overflow]     # cap drift; prefer freshest audio
                self.read_pos = max(0.0, self.read_pos - overflow / CHANNEL_BYTES)
            self.ring.extend(pcm)

    # ---- playback callback (sounddevice thread) ---------------------------

    def _audio_callback(self, outdata, frames, _time, status) -> None:
        with self.lock:
            total = len(self.ring) // CHANNEL_BYTES     # samples in ring
            ahead = total - self.read_pos               # samples buffered ahead
            target = self.base_bytes // CHANNEL_BYTES
            if not self.playing:
                if ahead >= self.adapt_bytes / CHANNEL_BYTES:
                    self.playing = True                 # start after prebuffer filled
                else:
                    outdata[:] = b"\x00" * (frames * CHANNEL_BYTES)
                    return
            if self.read_pos + frames * self.advance >= total - 1:
                # underrun: consume everything, re-prebuffer; grow the effective
                # target so the next prebuffer absorbs whatever the network just
                # proved it can't do
                self.playing = False
                self.underruns += 1
                step = int(SAMPLE_RATE * self.ADAPT_STEP_MS / 1000) * CHANNEL_BYTES
                self.adapt_bytes = min(self.target_bytes,
                                       int(SAMPLE_RATE * self.MAX_ADAPT_MS / 1000) * CHANNEL_BYTES,
                                       self.adapt_bytes + step)
                # release consumed audio NOW so the next prebuffer starts at
                # ring position 0 — reading from a stale position is what
                # caused boundary crashes before
                drop = int(self.read_pos)
                if drop > 0:
                    del self.ring[:drop * CHANNEL_BYTES]
                    self.read_pos -= drop
                if self.read_pos < 0:
                    self.read_pos = 0.0
                outdata[:] = b"\x00" * (frames * CHANNEL_BYTES)
                return
            # clock recovery: keep the buffered level at target by advancing the
            # read position fractionally. P-controller, clamped to ±600 ppm.
            err = ahead - target
            drift = max(-0.0006, min(0.0006, err * 2.0e-7))
            self.advance = max(0.9994, min(1.0006, self.advance * 0.995 + (1.0 + drift) * 0.005))

            data = np.frombuffer(bytes(self.ring), dtype=np.int16).astype(np.float64)
            pos = self.read_pos + np.arange(frames) * self.advance
            i0 = np.floor(pos).astype(np.int64)
            i1 = np.minimum(i0 + 1, total - 1)
            frac = pos - i0
            data = data[i0] * (1.0 - frac) + data[i1] * frac
            self.read_pos += frames * self.advance
            self._last_output_ts = time.time()

            # garbage-collect fully consumed samples off the ring front
            drop = int(self.read_pos)
            if drop > SAMPLE_RATE:                       # keep >= 1 s of history
                del self.ring[:drop * CHANNEL_BYTES]
                self.read_pos -= drop
            # healthy: drift the effective target back toward the user's base
            if ahead > target:
                decay = int(SAMPLE_RATE * 0.001) * CHANNEL_BYTES   # 1 ms / block
                self.adapt_bytes = max(self.base_bytes, self.adapt_bytes - decay)
        # cleanup DSP (normalized float) → gain → int16
        f = data.astype(np.float32) / 32768.0
        f = self.cleanup.process(f)
        g = self.cfg.get("gain", 1.0)
        if g != 1.0:
            f = f * g
        out = np.clip(np.round(f * 32767.0), -32768, 32767).astype(np.int16)
        outdata[:] = out.tobytes()

    # ---- status -----------------------------------------------------------

    def status(self) -> dict:
        buf_ms = 0.0
        with self.lock:
            buf_ms = (len(self.ring) / CHANNEL_BYTES - self.read_pos) / SAMPLE_RATE * 1000.0
        active = self.client_ws is not None
        stale = active and (time.time() - self.last_packet_at) > 2.0
        return {
            "client_name": self.client_name,
            "active": active and not stale,
            "buffer_ms": round(buf_ms, 1),
            "target_buffer_ms": self.cfg["buffer_ms"],
            "effective_buffer_ms": round(self.adapt_bytes / CHANNEL_BYTES / SAMPLE_RATE * 1000.0, 1),
            "underruns": self.underruns,
            "drift_ppm": round((self.advance - 1.0) * 1e6, 1),
            "packets": self.packets,
            "kbps": round(self.bytes_in * 8 / 1000 / max(0.001, time.time() - (self.started_at or time.time())), 1) if active else 0.0,
            "level": round(self.level_rms, 4),
            "device": self.device,
            "est_latency_ms": round(buf_ms + 40, 0),
            "silent_s": round(max(0.0, time.time() - self._last_output_ts), 1) if active else 0.0,
        }


# --------------------------------------------------------------------------
# HTTP handlers
# --------------------------------------------------------------------------

_NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}


def lan_ip() -> str:
    with contextlib.suppress(Exception):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    return "127.0.0.1"


def make_qr_svg(text: str) -> str | None:
    if not _HAS_QR:
        return None
    try:
        from qrcode.image.svg import SvgPathImage
        qr = qrcode.QRCode(border=2, box_size=6)
        qr.add_data(text)
        qr.make(fit=True)
        return qr.make_image(image_factory=SvgPathImage).to_string().decode()
    except Exception:
        return None


def ensure_tls_cert(cert_path: Path, key_path: Path, ip: str) -> bool:
    """Create a self-signed cert once. Phones need HTTPS to grant mic access."""
    if cert_path.exists() and key_path.exists():
        return True
    if not _HAS_CRYPTO:
        return False
    try:
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([
            x509.NameAttribute(NameOID.COMMON_NAME, u"PMic"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, u"PMic"),
        ])
        san = x509.SubjectAlternativeName([
            x509.DNSName(u"localhost"),
            x509.IPAddress(ipaddress.ip_address(ip)),
        ])
        now = datetime.datetime.now(datetime.timezone.utc)
        cert = (x509.CertificateBuilder()
                .subject_name(name).issuer_name(name)
                .public_key(key.public_key())
                .serial_number(x509.random_serial_number())
                .not_valid_before(now - datetime.timedelta(days=1))
                .not_valid_after(now + datetime.timedelta(days=3650))
                .add_extension(san, critical=False)
                .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
                .sign(key, hashes.SHA256()))
        key_path.write_bytes(key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption()))
        cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        return True
    except Exception as e:
        print(f"[pmic] tls certificate generation failed: {e}", file=sys.stderr)
        return False


class PMicServer:
    def __init__(self, port: int):
        self.port = port
        self.https_port = port + 1
        self.cfg = load_config()
        self.bridge = AudioBridge(self.cfg)
        # pairing PIN is persistent: pair once, auto-reconnect forever
        self.pin = secrets.randbelow(900000) + 100000
        self.cfg["pin"] = self.pin      # in memory only; never written to disk
        self.ip = lan_ip()
        self.started = time.time()
        # phones need TLS for microphone permission on mobile browsers
        self.cert_path = STATE_DIR / "pmic-cert.pem"
        self.key_path = STATE_DIR / "pmic-key.pem"
        self.https_available = ensure_tls_cert(self.cert_path, self.key_path, self.ip)
        # cross-thread stop control (overlay/tray -> asyncio server)
        self._loop = None
        self._stop = None

    def request_stop(self) -> None:
        if self._loop is not None and self._stop is not None:
            self._loop.call_soon_threadsafe(self._stop.set)

    @property
    def phone_url(self) -> str:
        if self.https_available:
            return f"https://{self.ip}:{self.https_port}"
        return f"http://{self.ip}:{self.port}"

    # ---- helpers ----

    def pin_ok(self, request) -> bool:
        if not self.cfg.get("pin_required"):
            return True
        return request.query.get("pin", "") == str(self.pin)

    async def index(self, request):
        """One address, correct screen: phones get the mic client, PCs get the
        dashboard. Explicit /panel and /app override the detection."""
        ua = request.headers.get("User-Agent", "").lower()
        mobile = any(k in ua for k in ("android", "iphone", "ipad", "ipod", "mobile"))
        if mobile:
            return web.FileResponse(WEB_DIR / "index.html", headers=_NO_STORE)
        return web.FileResponse(WEB_DIR / "panel.html", headers=_NO_STORE)

    async def app_page(self, request):
        return web.FileResponse(WEB_DIR / "index.html", headers=_NO_STORE)

    async def panel(self, request):
        return web.FileResponse(WEB_DIR / "panel.html", headers=_NO_STORE)

    async def static_file(self, request):
        """Static files with hard no-store: phone browsers must never run
        yesterday's client code after an update."""
        tail = request.match_info["tail"]
        safe = os.path.normpath(tail).replace("\\", "/")
        if safe.startswith("..") or "/../" in f"/{safe}/":
            return web.Response(status=404)
        path = WEB_DIR / safe
        if not path.is_file():
            return web.Response(status=404)
        ctype = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        return web.Response(body=path.read_bytes(), content_type=ctype,
                            headers=_NO_STORE)

    async def api_status(self, request):
        devices = self.bridge.list_devices()
        virtual = self.bridge.find_virtual_device()
        # plug-and-play: the moment a virtual mic exists (fresh driver install,
        # even mid-session), own it automatically and make it the system mic
        if virtual is not None and self.cfg["device"] is None and self.bridge.device != virtual:
            self.bridge.select_device(virtual)
            print("[pmic] virtual mic detected, switched output automatically", file=sys.stderr)
        # NOTE: the system default mic is deliberately NOT set here. Silently
        # changing a system-wide default is exactly how this app broke audio
        # before; it is an explicit, journaled dashboard action instead.
        default_out = next((d["id"] for d in devices if d["default"]), None)
        effective_device = (self.cfg["device"] if self.cfg["device"] is not None
                            else (virtual if virtual is not None else default_out))
        phone_url = self.phone_url
        # QR carries the pairing code too: one scan connects, nothing to type
        connect_url = f"{phone_url}/?pin={self.pin}"
        return web.json_response({
            "version": VERSION,
            "uptime_s": int(time.time() - self.started),
            "ip": self.ip,
            "port": self.port,
            "https_port": self.https_port if self.https_available else None,
            "https_available": self.https_available,
            "connect_url": phone_url,
            "connect_link": connect_url,
            "pin_required": self.cfg["pin_required"],
            "pin": str(self.pin),
            "gain": self.cfg["gain"],
            "mute": self.cfg["mute"],
            "cleanup": self.cfg.get("cleanup", "studio"),
            "gate_db": self.cfg.get("gate_db", -40.0),
            "buffer_ms": self.cfg["buffer_ms"],
            "device": effective_device,
            "device_explicit": self.cfg["device"] is not None,
            "virtual_device_present": virtual is not None,
            "default_mic_journaled": bool(
                sysstate is not None and sysstate.has_default_mic_entry(str(CONFIG_PATH))),
            "devices": devices,
            "bridge": self.bridge.status(),
            "qr_svg": make_qr_svg(connect_url),
        })

    async def api_select(self, request):
        body = await request.json()
        device = body.get("id")
        if device is not None and not isinstance(device, int):
            return web.json_response({"error": "id must be int or null"}, status=400)
        ok = self.bridge.select_device(device)
        return web.json_response({"ok": ok})

    async def api_gain(self, request):
        body = await request.json()
        g = body.get("gain")
        if not isinstance(g, (int, float)) or not (0.0 <= g <= 4.0):
            return web.json_response({"error": "gain must be 0..4"}, status=400)
        self.cfg["gain"] = float(g)
        save_config(self.cfg)
        return web.json_response({"ok": True})

    async def api_buffer(self, request):
        body = await request.json()
        ms = body.get("ms")
        if not isinstance(ms, (int, float)):
            return web.json_response({"error": "ms required"}, status=400)
        self.bridge.set_buffer_ms(ms)
        return web.json_response({"ok": True, "buffer_ms": self.cfg["buffer_ms"]})

    async def api_mute(self, request):
        body = await request.json()
        self.cfg["mute"] = bool(body.get("mute", False))
        save_config(self.cfg)
        return web.json_response({"ok": True})

    async def api_cleanup(self, request):
        body = await request.json()
        mode = body.get("mode")
        gate_db = body.get("gate_db")
        if mode is not None:
            if mode not in ("off", "basic", "studio"):
                return web.json_response({"error": "bad mode"}, status=400)
            self.cfg["cleanup"] = mode
        if gate_db is not None:
            if not isinstance(gate_db, (int, float)) or not (-70 <= gate_db <= -20):
                return web.json_response({"error": "gate_db must be -70..-20"}, status=400)
            self.cfg["gate_db"] = float(gate_db)
        save_config(self.cfg)
        self.bridge.cleanup.configure(self.cfg.get("cleanup", "studio"),
                                      self.cfg.get("gate_db", -40.0))
        return web.json_response({"ok": True, "cleanup": self.cfg.get("cleanup"),
                                  "gate_db": self.cfg.get("gate_db", -40.0)})

    async def api_pin_toggle(self, request):
        body = await request.json()
        self.cfg["pin_required"] = bool(body.get("required", True))
        save_config(self.cfg)
        return web.json_response({"ok": True})

    async def api_disconnect(self, request):
        ws = self.bridge.client_ws
        if ws is not None:
            self.bridge.detach_client(ws)
            with contextlib.suppress(Exception):
                await ws.close()
        return web.json_response({"ok": True})

    # ---- one-click virtual mic installer (bundled, offline) ----

    _cable_state = {"status": "idle", "message": ""}
    _cable_lock = threading.Lock()

    # ---- default microphone (system-wide, so games just detect it) ----

    def try_set_default_mic(self) -> dict:
        """Point the Windows default recording device at the virtual mic."""
        if not _HAS_DMIC:
            return {"ok": False, "mode": "unavailable"}
        try:
            import comtypes
            comtypes.CoInitialize()   # event-loop thread may lack COM init
        except Exception:
            pass
        target = None
        for eid, name in dmic.list_capture_endpoints():
            if any(h in (name or "").lower() for h in ("cable", "virtual")):
                target = eid
                break
        if target is None:
            return {"ok": False, "mode": "not_found"}
        before = dmic.get_default_capture_id()
        if dmic.set_default_capture(target):
            # transactional: journal it so it can always be reverted, even
            # after a crash (next startup restores the pre-PMic default)
            if sysstate is not None:
                with contextlib.suppress(Exception):
                    sysstate.record_default_mic(str(CONFIG_PATH), before, target)
            return {"ok": True, "mode": "direct"}
        return {"ok": False, "mode": "needs_admin", "device": target}

    async def api_set_default_mic(self, request):
        result = self.try_set_default_mic()
        if result.get("mode") == "needs_admin":
            # relaunch ourselves elevated: PMic.exe --set-default-mic <id>
            try:
                import subprocess
                me = sys.executable
                subprocess.Popen([
                    "powershell", "-NoProfile", "-Command",
                    f"Start-Process '{me}' -ArgumentList '--set-default-mic','{result["device"]}' -Verb RunAs"
                ], creationflags=subprocess.CREATE_NO_WINDOW)
                result = {"ok": True, "mode": "elevated"}
            except Exception:
                result = {"ok": False, "mode": "failed"}
        return web.json_response(result)

    async def api_restore_default_mic(self, request):
        """Undo what PMic changed: restore the pre-PMic default mic."""
        if sysstate is None:
            return web.json_response({"ok": False, "detail": "unavailable"})
        result = await asyncio.get_event_loop().run_in_executor(
            None, sysstate.revert_all, str(CONFIG_PATH))
        return web.json_response({"ok": result.get("default_mic") == "reverted",
                                  "detail": result})

    async def api_fix_audio(self, request):
        """Rebuild the audio output stream — the light in-app unstick."""
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, self.bridge.restart_stream)
        return web.json_response({"ok": True, "bridge": result})

    async def api_repair_audio(self, request):
        """Self-heal for the audio-routing break.

        Clears the per-app routing table that strands applications on audio
        endpoints which no longer exist, then restarts the audio services.
        This is the exact repair that restored sound on this machine, carried
        inside the app so it never needs a support session again. Needs admin,
        so it raises one UAC prompt and backs the table up first.
        """
        try:
            import tempfile
            lines = [
                '$dst = Join-Path $env:TEMP ("PolicyConfig-" + '
                '(Get-Date -Format yyyyMMdd-HHmmss) + ".reg")',
                "reg export 'HKCU\\Software\\Microsoft\\Internet Explorer\\"
                "LowRegistry\\Audio\\PolicyConfig' $dst /y | Out-Null",
                "reg delete 'HKCU\\Software\\Microsoft\\Internet Explorer\\"
                "LowRegistry\\Audio\\PolicyConfig' /f | Out-Null",
                "Restart-Service AudioEndpointBuilder -Force",
                "Start-Sleep 1",
                "Restart-Service Audiosrv -Force",
            ]
            path = os.path.join(tempfile.gettempdir(), "pmic-repair-audio.ps1")
            with open(path, "w", encoding="utf-8-sig") as f:
                f.write("\n".join(lines))
            rc = ctypes.windll.shell32.ShellExecuteW(
                None, "runas", "powershell",
                f'-NoProfile -ExecutionPolicy Bypass -File "{path}"', None, 0)
            if rc > 32:
                return web.json_response({
                    "ok": True, "status": "launched",
                    "message": "Confirm the admin prompt, then wait ~5 s."})
            return web.json_response({"ok": False, "status": "denied",
                                      "message": "Admin prompt declined."})
        except Exception as e:
            return web.json_response({"ok": False, "status": "error",
                                      "message": str(e)})

    async def api_install_cable(self, request):
        """Extract the bundled VB-CABLE package and launch its installer.
        The installer itself asks for admin permission (UAC)."""
        with self._cable_lock:
            if self._cable_state["status"] == "working":
                return web.json_response({"ok": False,
                                          "message": "Already installing."}, status=409)
            self._cable_state.update(status="working", message="Extracting the bundled installer…")
        loop = asyncio.get_event_loop()
        loop.run_in_executor(None, self._install_cable_job)
        return web.json_response({"ok": True})

    async def api_install_cable_status(self, request):
        s = dict(self._cable_state)
        s["virtual_device_present"] = self.bridge.find_virtual_device() is not None
        return web.json_response(s)

    def _install_cable_job(self) -> None:
        import zipfile
        try:
            bundled = BUNDLE_DIR / "vendor" / CABLE_ZIP_NAME
            if not bundled.exists():
                raise FileNotFoundError(
                    "virtual mic driver not bundled - get it from vb-audio.com/Cable/")
            tmp = Path(tempfile.mkdtemp(prefix="pmic-cable-"))
            self._cable_state["message"] = "Extracting the bundled installer…"
            with zipfile.ZipFile(bundled) as z:
                z.extractall(tmp)
            exe = None
            for cand in ("VBCABLE_Setup_x64.exe", "VBCABLE_Setup.exe", "setup64.exe", "setup.exe"):
                p = tmp / cand
                if p.exists():
                    exe = p
                    break
            if exe is None:
                raise FileNotFoundError("setup not found in the bundled package")
            self._cable_state["message"] = "Waiting for your admin confirmation…"
            # the installer requires elevation; runas triggers the UAC prompt
            rc = ctypes.windll.shell32.ShellExecuteW(
                None, "runas", str(exe), None, str(tmp), 1)  # SW_SHOWNORMAL
            if rc > 32:
                self._cable_state["status"] = "launched"
            else:
                raise OSError(f"installer launch rejected (code {rc})")
        except Exception as e:
            self._cable_state["status"] = "error"
            self._cable_state["message"] = f"Install failed: {e}"
            print(f"[pmic] cable install failed: {e}", file=sys.stderr)

    # ---- audio websocket ----

    async def audio_ws(self, request):
        if not self.pin_ok(request):
            return web.json_response({"error": "wrong PIN"}, status=403)

        name = request.query.get("name", "phone")
        # heartbeat: dead connections are detected and closed within ~50 s
        # instead of hanging forever, so phones can reconnect cleanly
        ws = web.WebSocketResponse(max_msg_size=1 << 20, heartbeat=25)
        await ws.prepare(request)
        self.bridge.attach_client(ws, name[:40])

        try:
            await ws.send_json({
                "type": "welcome",
                "buffer_ms": self.cfg["buffer_ms"],
                "gain": self.cfg["gain"],
                "sample_rate": SAMPLE_RATE,
            })
            stats_task = asyncio.create_task(self._stats_pusher(ws))
            async for msg in ws:
                if msg.type == WSMsgType.BINARY:
                    self.bridge.push(msg.data)
                elif msg.type == WSMsgType.TEXT:
                    with contextlib.suppress(Exception):
                        data = json.loads(msg.data)
                        if data.get("type") == "ping":
                            await ws.send_json({"type": "pong", "t": data.get("t")})
                elif msg.type == WSMsgType.ERROR:
                    print(f"[pmic] ws error: {ws.exception()}", file=sys.stderr)
                    break
        except Exception as e:
            import traceback
            print(f"[pmic] audio handler exception: {e}\n{traceback.format_exc()}", file=sys.stderr)
        finally:
            stats_task.cancel()
            self.bridge.detach_client(ws)
        return ws

    async def _stats_pusher(self, ws) -> None:
        """Tell the phone what the PC actually hears, once a second — so the
        client can prove the chain instead of assuming it."""
        try:
            while not ws.closed:
                st = self.bridge.status()
                await ws.send_json({"type": "stats", "level": st["level"],
                                    "active": st["active"],
                                    "buffer_ms": st["buffer_ms"]})
                await asyncio.sleep(1)
        except (asyncio.CancelledError, ConnectionError, RuntimeError):
            pass
        except Exception:
            pass

    # ---- resilience watchdogs ----

    def watchdog_tick(self) -> None:
        """Audio device vanished (unplugged, driver restart, index shift)?
        Recover by re-resolving the saved device by name."""
        try:
            if self.bridge.stream is None:
                return
            ids = {d["id"] for d in self.bridge.list_devices(force=True)}
            if self.bridge.device is not None and self.bridge.device not in ids:
                print("[pmic] output device vanished, re-resolving...", file=sys.stderr)
                self.bridge._open_stream()   # full re-resolve by name/virtual
        except Exception:
            pass

    async def watchdog_loop(self) -> None:
        loop = asyncio.get_event_loop()
        while True:
            await asyncio.sleep(10)
            await loop.run_in_executor(None, self.watchdog_tick)

    # ---- app assembly ----

    def build_app(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/", self.index)
        app.router.add_get("/panel", self.panel)
        app.router.add_get("/app", self.app_page)
        app.router.add_get("/api/status", self.api_status)
        app.router.add_post("/api/select", self.api_select)
        app.router.add_post("/api/gain", self.api_gain)
        app.router.add_post("/api/buffer", self.api_buffer)
        app.router.add_post("/api/mute", self.api_mute)
        app.router.add_post("/api/cleanup", self.api_cleanup)
        app.router.add_post("/api/pin", self.api_pin_toggle)
        app.router.add_post("/api/disconnect", self.api_disconnect)
        app.router.add_post("/api/install-cable", self.api_install_cable)
        app.router.add_get("/api/install-cable/status", self.api_install_cable_status)
        app.router.add_post("/api/set-default-mic", self.api_set_default_mic)
        app.router.add_post("/api/restore-default-mic", self.api_restore_default_mic)
        app.router.add_post("/api/fix-audio", self.api_fix_audio)
        app.router.add_post("/api/repair-audio", self.api_repair_audio)
        app.router.add_get("/audio", self.audio_ws)
        app.router.add_get("/static/{tail:.*}", self.static_file)
        return app


def main() -> None:
    port = DEFAULT_PORT
    no_tray = False
    for arg in sys.argv[1:]:
        if arg.startswith("--port="):
            with contextlib.suppress(ValueError):
                port = int(arg.split("=", 1)[1])
        elif arg == "--no-tray":
            no_tray = True
        elif arg == "--set-default-mic":
            # elevated helper: set default recording device to <endpoint id>, exit
            idx = sys.argv.index("--set-default-mic")
            if _HAS_DMIC and idx + 1 < len(sys.argv):
                ok = dmic.set_default_capture(sys.argv[idx + 1])
                print("set-default-mic:", "ok" if ok else "failed")
            return

    # startup janitor: if a previous run died without cleaning up (crash,
    # taskkill, power loss), undo its leftover system changes FIRST — before
    # anything else happens. A crash must never leave a permanent trace.
    if sysstate is not None:
        try:
            janitor = sysstate.revert_all(str(CONFIG_PATH))
            if janitor.get("default_mic") == "reverted":
                print("[pmic] startup janitor: restored pre-PMic default mic",
                      file=sys.stderr)
        except Exception as e:
            print(f"[pmic] startup janitor skipped: {e}", file=sys.stderr)

    server = PMicServer(port)
    _DASHBOARD_SERVER[0] = server
    app = server.build_app()
    stop = asyncio.Event()

    use_tray = (not no_tray) and (getattr(sys, "frozen", False) or "--tray" in sys.argv)
    use_overlay = "--no-overlay" not in sys.argv and (
        getattr(sys, "frozen", False) or "--overlay" in sys.argv)

    def run_server() -> None:
        asyncio.run(serve(app, port, server, stop, use_tray))

    threading.Thread(target=run_server, daemon=True).start()

    if use_overlay:
        try:
            import overlay
            ui_stop = threading.Event()
            started = threading.Event()
            overlay.run_overlay(server, ui_stop, started)   # blocks the main thread
            return
        except Exception as e:
            print(f"[pmic] overlay unavailable ({e}); running headless", file=sys.stderr)
    # headless: block the main thread until the server is asked to stop
    import time as _t
    while not stop.is_set():
        _t.sleep(0.2)


async def serve(app, port, server, stop, use_tray) -> None:
    server._loop = asyncio.get_running_loop()
    server._stop = stop
    runner = web.AppRunner(app)
    await runner.setup()
    site_http = web.TCPSite(runner, "0.0.0.0", port)
    await site_http.start()
    if server.https_available:
        import ssl as _ssl
        ctx = _ssl.SSLContext(_ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(server.cert_path, server.key_path)
        site_tls = web.TCPSite(runner, "0.0.0.0", server.https_port, ssl_context=ctx)
        await site_tls.start()

    if use_tray:
        threading.Thread(target=start_tray, args=(server, stop), daemon=True).start()
    watchdog = asyncio.create_task(server.watchdog_loop())
    try:
        await stop.wait()
    finally:
        watchdog.cancel()
        # transactional cleanup: undo everything PMic changed on the system.
        # A clean quit reverts; a crash is covered by the startup janitor.
        if sysstate is not None:
            with contextlib.suppress(Exception):
                await asyncio.get_event_loop().run_in_executor(
                    None, sysstate.revert_all, str(CONFIG_PATH))
        await runner.cleanup()


def open_dashboard_window(server) -> None:
    """Open the dashboard as a real desktop window (native app feel).
    Falls back to the browser only if the embedded window can't run."""
    import threading

    url = f"http://localhost:{server.port}/panel"

    def job() -> None:
        # already open? bring that exact window to the front.
        win = _DASH_WINDOW[0]
        if win is not None:
            try:
                win.show()
                return
            except Exception:
                _DASH_WINDOW[0] = None
        # already ran once: a webview engine cannot be restarted in-process, so
        # fall back to the browser, which reuses and focuses its open tab
        if _DASH_STARTED[0]:
            try:
                import webbrowser
                webbrowser.open(url)
            except Exception:
                pass
            return
        try:
            import webview
            _DASH_STARTED[0] = True
            _DASH_WINDOW[0] = webview.create_window(
                f"PMic {VERSION} — dashboard", url,
                width=980, height=790, min_size=(780, 580))
            webview.start()
            _DASH_WINDOW[0] = None
        except Exception:
            _DASH_STARTED[0] = True
            try:
                import webbrowser
                webbrowser.open(url)
            except Exception:
                pass

    threading.Thread(target=job, daemon=True).start()


_DASHBOARD_SERVER = [None]
_DASH_WINDOW = [None]
_DASH_STARTED = [False]


def start_tray(server, stop: asyncio.Event) -> None:
    """System-tray icon: dashboard, phone link, quit. Degrades silently."""
    try:
        import pystray
        from PIL import Image, ImageDraw
    except ImportError:
        return

    img = None
    try:
        with Image.open(BUNDLE_DIR / "assets" / "pmic.ico") as ico:
            img = ico.convert("RGBA")
    except Exception:
        img = None
    if img is None:  # asset missing (old build): draw the mark instead
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.ellipse([4, 4, 60, 60], fill=(14, 17, 22, 255), outline=(76, 194, 255, 255), width=4)
        d.ellipse([24, 24, 40, 40], fill=(76, 194, 255, 255))

    def open_dashboard(_icon, _item):
        open_dashboard_window(server)

    def open_phone(_icon, _item):
        import webbrowser
        webbrowser.open(server.phone_url)

    def quit_app(_icon, _item):
        stop.set()

    menu = pystray.Menu(
        pystray.MenuItem("Open dashboard", open_dashboard, default=True),
        pystray.MenuItem("Open phone page", open_phone),
        pystray.MenuItem("Quit", quit_app),
    )
    try:
        icon = pystray.Icon("PMic", img, f"PMic {VERSION} - PIN {server.pin}", menu)
        icon.run()
    except Exception:
        pass


if __name__ == "__main__":
    main()
