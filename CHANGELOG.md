# Changelog

Notable changes to PMic. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/); versions follow [SemVer](https://semver.org/).

## [1.4.1-beta.1] — first public beta

First public release. Functional, tested end to end, and honest about what it
doesn't do yet.

### Added
- **MSI installer** (`Build MSI.bat`): per-machine x64 install, Start Menu and
  desktop shortcuts, license page, proper Add/Remove Programs entry, and major
  upgrade handling so one beta installs over another cleanly.
- **Portable build renamed and versioned** — `PMic-<version>-portable-win64.zip`
  now carries the version it actually contains (it was hardcoded to 1.0.0).
- **App icon** (`tools/make_icon.py`): the EXE, installer and tray icon finally
  share one mark, and the EXE carries a real Windows version resource, so
  Properties → Details is filled in.
- `tools/check_bundle.py` — fails the build if the web assets, driver pack or
  icon are missing from the bundle, instead of letting users discover it.
- `tools/make_checksums.py` — publishes `SHA256SUMS.txt` for every release.
- `LICENSE` (MIT), `CONTRIBUTING.md` and a bug-report issue template.

### Fixed
- **Installed builds could not save settings.** State was written next to the
  executable, which is read-only in `Program Files` for standard users, so an
  installed PMic would fail every config, PIN and certificate write. State now
  resolves to a writable directory — next to the EXE for portable/source runs
  (unchanged for existing users), `%LOCALAPPDATA%\PMic` otherwise.
- The MSI build no longer packages whatever happens to be in `dist/`: it always
  rebuilds the portable app first.

### Known limitations
- Binaries are unsigned (SmartScreen warns; verify against `SHA256SUMS.txt`).
- The virtual-mic driver install still needs one admin prompt.
- LAN only — no relay, no Opus, no wide-area support.
- Windows only.
