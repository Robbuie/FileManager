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

Early. The headless I/O layer is being built and verified against real shares
before any UI exists. See `PROJECT-CONTEXT.md` for the full design and
`CLAUDE.md` for the working rules.

## Running

```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m app
```

## Licence

Personal project. Not currently licensed for redistribution.
