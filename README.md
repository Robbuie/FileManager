# File Manager

A Windows dual-pane file manager. Python + PySide6. Personal use, offline,
single user.

## Why

It replaces Double Commander, which works but has two problems worth building
around:

1. **UI freezes.** Filesystem calls run on the UI thread, so an unresponsive
   network share blocks the whole app for as long as Windows takes to give up
   on the SMB call — routinely 30 to 45 seconds.
2. **Lost server connections.** Mapped drive sessions die silently and the app
   does not recover cleanly.

Everything in the design follows from one rule: **the UI thread never touches
the filesystem.** All real I/O happens in worker processes, one per volume, so
a hung share means one tab showing "reconnecting" rather than a frozen window.

## Status

Early, but installable. The headless I/O layer exists and is verified by tests,
though it still has to be run against real network shares. The window has two
panes, tabs, navigation, the operations that change something -- new folder,
rename, delete -- and a copy and move engine with a proper queue. There is an
installer and it updates itself. Shell icons, the real context menu and
transfers that outlive the window are not done. See `PROJECT-CONTEXT.md` for
the design and `CLAUDE.md` for the working rules.

## Installing

Download `FileManager-Setup-<version>.exe` from
[Releases](https://github.com/Robbuie/FileManager/releases) and run it. It
installs for the current user -- no administrator prompt -- and thereafter
checks for a new version a few seconds after launch. Nothing is downloaded
until the offer is accepted, and an update installs when the application quits.
**Help > Check for updates** asks on demand; the same menu turns the automatic
check off.

## Running

```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

```
python -m app
```

The I/O layer can also be exercised on its own, without a window:

```
python -m app.io.harness --help
python -m app.io.harness list S:\Jobs --timeout 20
```

## Building the installer

Needs [Inno Setup 6.3 or newer](https://jrsoftware.org/isdl.php) on PATH, or
`INNO_SETUP` pointing at `ISCC.exe`.

```
pip install -r packaging/requirements-build.txt
python packaging/build.py
```

Three things land in `dist/`: the unpacked `FileManager\` folder, the setup exe,
and `latest.json` -- the manifest every installed copy reads to find out a new
version exists. The build refuses to finish if the manifest and the installer
beside it disagree.

Releasing is a tag. `git tag v0.7.0 && git push --follow-tags` builds on a
runner and publishes both assets; the version in the tag has to match
`app/__init__.py` or the run stops.

## Licence

Personal project. Not currently licensed for redistribution.
