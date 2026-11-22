# Contributing to PMic

PMic is a small, single-author beta. Bug reports and focused pull requests are
welcome — especially from people whose setup behaves differently from mine.

## Reporting a bug

Use the [bug report form](.github/ISSUE_TEMPLATE/bug_report.yml). The three
things that make a report solvable:

1. **What the dashboard said** — Phone/Buffer/Stream/Dropouts/Clock drift.
2. **Your audio path** — Windows version, which virtual device (CABLE Output,
   VoiceMeeter), and whether audio worked without the virtual mic.
3. **Console output** if you run from source — `python server.py --port=8787`.

## Development setup

```bat
pip install -r requirements.txt
python server.py --port=8787
```

Then open <http://localhost:8787/panel> on the PC and the LAN address on a phone.
For UI work, the phone client reloads on refresh; no build step exists.

## Ground rules

- **No outbound network calls.** The point of PMic is that audio never leaves the
  LAN. Any dependency that phones home is a non-starter.
- **Zero-install phone client.** No app store, no sideloading, no service worker
  cache tricks that strand users on old code.
- **Never make a one-way change to the system.** If PMic changes something on the
  machine, journal it in `sysstate.py` first so it can be undone — including after
  a crash.
- **Measure latency claims.** Don't say "low latency"; say what you measured.

## Building a release

```bat
Build PMic.bat     :: portable build + zip
Build MSI.bat      :: MSI installer (rebuilds the portable build first)
```

Both scripts verify their own output. The MSI needs WiX pinned to 5.x:

```bat
dotnet tool install --global wix --version 5.0.2
```

Do not commit build output (`dist/`, `build/`, `*.msi`, `*.zip`), generated WiX
sources (`installer/`), TLS material (`pmic-*.pem`) or user config
(`pmic-config.json`) — all of it is in `.gitignore`. If you find yourself adding
an exception, that's a bug.
