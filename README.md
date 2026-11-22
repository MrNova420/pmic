# PMic

Use your phone as a microphone for your Windows PC — for gaming, calls,
recording, anything that takes a mic input. **Fully self-contained**: no
Python, no app store, no downloads at runtime — even the virtual-mic driver
ships inside the app.

## Quick start (packaged app)

1. Unzip `PMic-1.0.0-win64.zip` anywhere, run **`PMic.exe`**. A tray icon
   appears; settings, pairing PIN and certificate live in the same folder.
2. Open the dashboard: tray icon → **Open dashboard**
   (or <http://localhost:8787/panel>).
3. If no virtual microphone is installed yet, the dashboard shows
   **Install virtual mic** — one click extracts the bundled VB-CABLE driver
   and starts its installer (confirm the admin prompt), then restart PMic.
   This is what makes your voice appear as a real microphone in games,
   Discord and OBS. Without it, PMic still streams, but the audio only goes
   to an output device.
4. On your phone (same Wi-Fi), scan the QR code on the dashboard or open the
   address it shows. First time, the browser shows a **certificate warning —
   that's normal** for this setup: choose *Advanced → proceed*
   (Chrome/Android). Then allow microphone access and enter the pairing PIN
   from the dashboard.
5. In your game / Discord / OBS, select **CABLE Output** as the microphone.

Phones connect over HTTPS (port 8788) because mobile browsers only grant
microphone access to secure pages — PMic generates its own certificate on
first run. The web page and fonts are served entirely from the app; nothing
loads from the internet.

## Run from source

1. Python 3.10+ with `pip install -r requirements.txt`
   (`vendor/VBCABLE_Driver_Pack46.zip` ships the virtual mic driver; it's the
   official, unmodified package from <https://www.vb-audio.com/Cable/>).
2. `python server.py --port=8787` (console) or
   `python server.py --port=8787 --tray` (tray icon)
3. Build your own packaged app with **`Build PMic.bat`** (PyInstaller;
   produces `dist\PMic\PMic.exe` and `PMic-1.0.0-win64.zip`).

## Options

- `--port=NNNN` — dashboard/HTTP port; the phone TLS port is +1.
- `--no-tray` — never show the tray icon (console mode).
- `Install PMic autostart.ps1` — start PMic automatically at login
  (also tries to open the firewall for port 8787/8788).

## Tuning

- **Buffer** (dashboard, 20–150 ms): lower = lower latency but more dropouts
  on a weak Wi-Fi link. 60 ms is a good default. The buffer is also
  **adaptive**: after a dropout it grows by 12 ms (up to 150 ms) to absorb
  that level of jitter, and eases back to your setting once the link is
  stable again.
- **Gain**: both ends have one. If the other side says you're quiet, add gain
  on the dashboard or turn it up on the phone.

## Security

- Phones must enter a PIN shown on the dashboard (can be turned off for
  trusted home networks).
- Traffic stays on your LAN. The TLS certificate is self-signed (that's why
  the phone shows a warning once); don't expose PMic to the internet.
- Settings live in `pmic-config.json` next to the exe; delete it to reset
  (a new PIN and certificate are generated on next start).

## Status / roadmap

Working: capture → transport → adaptive jitter buffer → playback → device
selection, PIN pairing, HTTPS phone transport (mic permission on mobile),
self-hosted fonts (no CDN), bundled offline virtual-mic installer, tray app,
standalone packaged build, level metering, auto-start launchers. Not yet:
Opus compression for wide-area use, macOS/Linux hosts, signed certificate
(no cert warning). See `docs/DESIGN.md` for the full design brief.
