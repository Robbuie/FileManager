# File Manager — Project Context

Windows file manager to replace Double Commander. Personal use only.

## Why

Double Commander works but has two problems worth building around:

1. **UI freezes.** Filesystem calls run on the UI thread. When a network
   share stops responding, Windows can block an SMB call for 30–45s and the
   whole app is unresponsive until it returns.
2. **Lost server connections.** Mapped drive sessions die silently and the
   app doesn't recover cleanly.

Secondary goal: a cleaner look, matching the house design system.

## Non-negotiable architectural rule

**The UI thread never touches the filesystem.** Not for listings, sizes,
icons, or drive enumeration. Everything below follows from this.

- **One worker process per volume.** Local disks share one; each network
  server gets its own. A hung share means killing that one process — the
  affected tab shows "reconnecting" with a retry, every other tab keeps
  working.
- **Streamed listings.** Rows paint as they arrive (batches of ~1000),
  never block on full enumeration.
- **Every operation has a timeout and a cancel.** No exceptions.
- **Lazy drive enumeration.** Never probe drives at startup.
- **Copy/move engine in its own process.** A stalled transfer can't take
  the window down, and survives closing the window.
- **Polled refresh on network paths, not a file watcher.** SMB watching is
  unreliable. Configurable interval + manual refresh key.

## Stack

**Python + PySide6.**

- Qt's *view* is used (excellent, virtualizes for free). Qt's stock
  `QFileSystemModel` is **not** used — it has its own network hang
  behavior, which is the thing being escaped. Custom model fed by the
  worker processes instead.
- `pywin32` for real shell context menus and shell icons, avoiding a
  native addon.
- `multiprocessing` for the worker pool — killing and restarting workers
  is trivial.
- GIL is not a concern: file I/O releases it, and hung shares are handled
  with processes rather than threads.

## Path handling

Environment uses **both mapped drive letters and UNC paths**.

- Resolve internally, display externally. `WNetGetConnection` (pywin32)
  maps `S:` → `\\server\share`. All real work happens in UNC; display form
  is per-tab preference.
- Reconnect works even when the drive letter's session is dead, because
  the UNC is retained.
- Worker pool keys on **resolved server name**, so `S:\` and
  `\\server\share\` share one worker instead of two that hang separately.
- Drive enumeration via `WNetGetConnection` reads local session state and
  never touches the network — removes the dead-mapped-drive startup hang.

## Performance target

Worst case is **5,000–50,000 files in one folder**.

- **`os.scandir`, never `os.stat`.** On Windows, `DirEntry` carries size,
  mtime, and attributes from the directory enumeration itself. A 50k
  listing with all columns is one pass, zero extra syscalls. Per-row
  `stat()` would be 50,000 SMB round trips — under a second vs over a
  minute.
- Icons, folder sizes, and anything needing the file opened stay lazy.
- Sort and filter on a plain Python list in the custom model; stays under
  100ms at 50k.

## Build order

1. **`io/` layer first, headless, with a CLI harness. No UI.**
   Verify against real shares: 50k listing over SMB, yanking a connection
   mid-listing, worker kill/restart, reconnect after a dead mapped drive.
2. Dual pane + tabs + navigation.
3. Copy/move engine with a proper queue: pause/resume, per-file and total
   progress, conflict rules (skip / overwrite / newer only / auto-rename),
   locked-file retry, preserve timestamps and attributes.

If those three are solid the app already beats the current setup.
Multi-rename, archives (bundled 7-Zip binary), compare/sync, and preview
panes bolt on cleanly afterward.

## Layout

```
filemanager/
  app/
    ui/          panes, tabs, dialogs — never touches the filesystem
    core/        models, view-state, config
    io/
      worker.py  separate process, all real I/O
      pool.py    spawns/kills/restarts workers per volume
      ops.py     copy/move queue, own process
    theme/       ported design system, Drafting-blue accent
  packaging/     PyInstaller spec + installer + updater config
```

## Known hard parts

- **The copy engine.** Not the copying — the queue, progress, conflict
  resolution, retry, and attribute preservation around it. Where most
  hobby file managers fall over.
- **Shell integration.** Real context menu, per-filetype shell icons, icon
  overlays, UAC elevation for protected folders. `pywin32` covers this.
  Third-party entries (TortoiseSVN, 7-Zip) need real `IContextMenu` handling,
  which means loading their DLLs -- so that happens in a process of its own
  and the window draws the menu from what is read out of it.
- **Default folder handler.** Windows only half allows replacing Explorer.
  Registering a Directory verb mostly works; Win+E needs a remap.

## Process

Same as redlinepdf: public GitHub repo, installer with auto-update from
releases, built iteratively across sessions.

Repo: `Robbuie/FileManager`, public. Local path
`C:\Users\rjokr\Projects\FileManager`, which Claude reads and writes
directly in Cowork sessions. Pushes are done by the user; the session has no
GitHub credentials.

Claude cannot run the GUI, see rendering, or reach the network shares.
The user is the entire test loop.
