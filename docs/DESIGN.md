# PMic — Design Brief

**PMic** turns a phone into a real microphone for a Windows PC — for gaming, calls,
recording, anything that takes a mic input — with no app install on the phone.

## Why

People who don't own a dedicated microphone still carry one: the phone. Existing
tools for this (WO Mic, DroidCam mic, etc.) are clunky, add heavy latency, and
look abandoned. PMic is the polished, low-latency, zero-friction version:
open a URL on the phone, enter a PIN, and you're a mic.

## Architecture

```
Phone (any browser)                    PC
┌──────────────────────────┐   WS    ┌─────────────────────────────┐
│ getUserMedia (mono 48k)  │ ──────► │ aiohttp server              │
│ AudioWorklet → PCM16     │ binary  │  ├ jitter buffer (adaptive) │
│ ~40ms frames             │ frames  │  ├ level metering           │
└──────────────────────────┘         │  └ sounddevice RawOutput    │
                                     │        ↓ WASAPI             │
                                     │   Virtual mic device        │
                                     │   (VB-CABLE / VoiceMeeter)  │
                                     │        ↓                    │
                                     │   Games, Discord, OBS...    │
                                     └─────────────────────────────┘
```

- **Phone side:** a web page served by the PC itself (`http://<pc-ip>:8787`).
  Works on Android and iOS instantly. Capture is mono 48 kHz through an
  AudioWorklet, chunked into ~40 ms PCM16 frames and streamed over a WebSocket.
- **PC side:** one Python process. HTTP serves the client and dashboard, REST
  handles control, a WebSocket ingests audio. Audio is fed through an adaptive
  jitter buffer into a WASAPI output stream pointed at a virtual mic device.
- **Virtual mic:** to appear as a *microphone* to games, the PC needs a virtual
  audio device. PMic auto-detects VB-CABLE ("CABLE Input") or VoiceMeeter; if
  none is present it walks the user through a one-time install. It also works
  with any normal output device for non-mic uses (testing, speakers, recording).

## Product decisions

- **Zero-install phone client.** No app store, no sideloading; a QR code on the
  dashboard is the whole onboarding.
- **PIN pairing.** Streaming requires a 6-digit PIN shown on the PC dashboard —
  neighbours on the café Wi-Fi can't silently become your mic.
- **Single active phone.** The most recent connected phone owns the mic;
  previous ones are disconnected. A mic is a single-source device.
- **Latency budget.** Target < 150 ms mouth-to-app: ~40 ms capture frames +
  30–80 ms jitter buffer (user-tunable) + WASAPI. Raw PCM on the LAN is cheap
  (~1.5 Mbps) and avoids codec latency; Opus compression is a future path for
  wide-area use.
- **The meter is the interface.** Signal state is the primary UI on both ends.

## Non-goals (v1)

- Internet/WAN streaming (STUN, relays), Opus codecs, recording, multi-phone
  mixing, macOS/Linux hosts. These are deliberate future paths, not omissions.

## Anti-goals

- Anything that makes the phone step worse than a real mic *because of the
  tool*: dropouts, drift, confusing setup, telemetry, ads, upsells.
