# Changelog

All notable changes to this project are recorded here. Every user-visible
change gets an entry and a version bump.

## [Unreleased]

## [0.6.0]

### Added
- The copy and move engine, in a process of its own. **F5** copies and **F6**
  moves to the other pane's folder, through a dialog that shows the
  destination and lets it be edited -- there is no path from a keystroke to a
  file being written that does not pass through a confirmation.
- The queue around it, which is the part that matters: one job at a time, a
  byte total established by scanning before anything is written, pause and
  resume, cancel, and conflict rules -- replace, replace if newer, keep both,
  skip -- with an answer that can be applied to the rest of the transfer. One
  question for a folder of collisions rather than one per file.
- A move within a volume is a rename, attempted per source before anything is
  scanned. Moving 40GB between two folders on one disk is instant, because it
  is.
- Every file is written beside its target and renamed onto it at the end.
  Nothing half-written ever wears the real name: a cancel, a crash or a pulled
  cable leaves the original intact rather than a fragment with the right name.
  A move deletes its source only after the copy is there and the right size.
- A locked file is retried three times with a backoff and then reported,
  rather than retried forever or failed instantly.
- The progress readout in the status bar, with the queue behind it: **Ctrl+J**
  or the Queue button opens it, and pause, cancel and clear-finished live
  there. Closing the window with transfers running asks first.
- `harness copy` and `harness move`, with `--conflict`, `--on-conflict` and
  `--cancel-after`, so a 50,000-file transfer over SMB can be run and
  cancelled from a console.
- Tests for the engine that run on POSIX: trees copied whole, every conflict
  rule, an answer applied to the rest, a cancel inside a file leaving neither
  a partial nor a damaged original, and a move that must not delete its source
  until the copy is verified.

### Changed
- **F5 now copies, and refresh moved to Ctrl+R.** The Norton keymap that
  Double Commander uses, matching the F7 and F8 already here. Reconnect moved
  to Ctrl+Shift+R.
- The keyboard hint in the status bar is shown for twenty seconds rather than
  permanently: that line is where a transfer reports itself, and two things
  cannot share it.

### Known limits
- A transfer does not outlive the window. The ops process is a child of the
  window's, so closing ends it -- the window says so and asks before it does.
  Making it survive means a process that is not a child and a way to reattach
  to it, which is its own piece of work.
- Nothing is written to the destination while a conflict is waiting for an
  answer: the job stops, the queue stops with it. With one job at a time that
  is correct rather than merely simple, but it is worth knowing.

## [0.5.0]

### Added
- The operations that change something: **F7** makes a folder, **F2** renames,
  **Delete** or **F8** sends to the Recycle Bin, **Shift+Delete** deletes
  permanently. They act on the marked rows, or on the row under the cursor when
  nothing is marked. `..` is never one of them.
- Delete goes through the shell (`SHFileOperation`), so it lands in the Recycle
  Bin and gets the cases that are tedious by hand: a read-only attribute, a
  long path, a junction that must not be followed into. Where pywin32 is not
  importable a recycle is **refused rather than made permanent** -- quietly
  turning "delete" into "delete forever" because a library is missing is how
  somebody loses work.
- A confirmation that names what is about to go, up to a dozen of them, and
  says where it is going. Cancel takes the Return key, so Delete by accident
  followed by Enter by reflex does nothing.
- Rename opens with the name selected but not the extension, refuses the
  characters Windows will not take, and will not overwrite an existing file --
  though it still allows a change of case, which is the same file to Windows
  and a thing people do.
- After making or renaming something the cursor lands on it. The cursor, not a
  selection: nothing gets marked by having been created.
- A pane that changes a folder refreshes the other pane when it is showing the
  same one. Both panes on one folder is the normal way to work, and a stale
  listing of files that are gone is the oldest bug in dual-pane file managers.
- `harness mkdir`, `harness rename` and `harness delete`, so the Recycle Bin
  behaviour and the overwrite refusal can be checked against a real share from
  a console, with no selection to get wrong.

### Notes
- These are worker ops rather than queued work, because each is a single call.
  A copy of 4,000 files needs pausing, per-file progress and a conflict rule,
  and that is `app/io/ops.py`, still unwritten. A recursive delete is the one
  that sits between the two: it is here for now, with a long deadline, and
  moves to the queue when the queue exists.
- The keys are handled by the pane rather than as window shortcuts. A window
  shortcut on Delete would take the key away from the path bar and the filter
  box, and backspacing over a typo would start deleting files.

## [0.4.1]

### Fixed
- A double click opened everything twice. Qt emits `activated` for a double
  click as well as for Enter, and `doubleClicked` was connected alongside it,
  so one gesture sent two requests and the shell launched two copies. Only
  `activated` is connected now, which also means a single click opens where the
  user has told Windows that is what a click does.
- Files opened through the extension rather than through the item, which is why
  Windows was offering the "how do you want to open this file" picker for types
  that already have an application. `os.startfile` is `ShellExecuteW` on a path
  string and resolves the association from the extension alone; the shell is
  now asked with `ShellExecuteEx` and `SEE_MASK_INVOKEIDLIST`, which builds the
  shell item and invokes the default verb from its context menu the way
  Explorer does. Per-user choices made through Open With live on the item, not
  on the extension. COM is initialised in the worker first, since the shell
  extensions this reaches expect an apartment. `os.startfile` remains the
  fallback where pywin32 is missing.

### Added
- `harness open`, so the shell call can be made once, on its own, from the same
  code path the window uses -- which is what separates "the window asked twice"
  from "the shell did something odd".

## [0.4.0]

### Added
- Files open. Double-click or Enter on a file hands it to the shell, through a
  worker like everything else: an association lookup reads the file, and on a
  share that has stopped answering that read blocks exactly as a listing does.
- A drive picker per pane, filled from the local session table. It probes no
  volume, so it opens instantly with a mapped server that is down -- the
  failure appears when a listing is asked for, which is where it belongs.
  `Ctrl+Shift+D` rescans after mapping a drive elsewhere.
- Free space for the pane's volume, on the right of its status line. Asked for
  after the listing rather than with it, and a failure is a blank readout
  rather than an error: nobody navigates to a folder to find out about free
  space.
- A quick filter, `Ctrl+F`, over the rows already in memory. `*` or `?` in the
  text makes it a glob, anything else is a substring. Clearing it costs nothing
  and never re-lists the folder; navigating away drops it, and the box goes
  with it.
- The selection is reported as it changes: how many folders, how many files,
  and how much. `Insert` marks the row and moves down.
- Swap panes (`Ctrl+U`), send the other pane to this folder (`Ctrl+Shift+M`),
  and copy the path of the row under the cursor (`Ctrl+Shift+C`).
- `Op.OPEN`, `Op.DRIVES` and `Op.FREE_SPACE` in the worker protocol, with the
  timeouts for them in the config defaults.

### Fixed
- A folder no longer opens with its first row already selected. It reads as a
  mark the user made, and marks are what an operation will act on -- which
  would eventually mean copying something nobody chose. The cursor is placed;
  nothing is selected.
- Counts read as English: "1 folder", not "1 folders".

## [0.3.0]

### Added
- The window: two panes, tabs per pane, and navigation. Nothing in `app/ui`
  touches the filesystem, imports `app.io`, or contains a colour.
- `app/core/bridge.py` — the one place a worker reply becomes something Qt may
  touch. Replies arrive on the pool's reader threads and cross to the UI thread
  through a queued signal.
- `app/core/listing.py` — the custom item model. Rows arrive in batches while
  the view is already showing them; sorting is a `list.sort` at the end, with
  folders always first.
- `app/core/pane.py` — tabs, history, and navigation. A reply is only believed
  if it answers the request the tab is currently waiting on, so an abandoned
  listing cannot land in the wrong folder.
- `app/core/config.py` — settings with a default for every key; an unknown key
  raises rather than reading as off.
- `app/theme/sheet.py` — the QSS template and the one call that applies a
  (theme, accent, density) combination, including the palette Qt paints
  selection from.
- View menu with all five themes, six accents and three densities, built from
  the token tables so a new theme appears without anybody wiring it up.
- `tools/preview.py` — renders the real window offscreen to a PNG. The look can
  now be checked without a screen; behaviour still cannot.

### Fixed
- The listing sorted backwards on open. `QHeaderView` defaults its sort
  indicator to descending and `setSortingEnabled` applies it, so a model that
  had just sorted itself ascending was immediately flipped. Found by looking at
  a render, which is the only way it would have been found.
- Selection painted as solid accent rather than the intended wash: Qt draws it
  from the palette, and a palette colour cannot be translucent. The tint is now
  pre-mixed against the surface it sits on, with a weaker one for the pane that
  does not have focus.

## [0.2.0]

### Added
- The io layer, headless: path resolution, one worker process per volume, the
  pool that kills and restarts them, and a CLI harness to exercise all of it
  against real shares.
- `app/io/paths.py` — resolves drive letters to UNC through the local session
  table, keys volumes on server name so a letter and its UNC share one worker,
  and enumerates drives without probing any of them.
- `app/io/worker.py` — `os.scandir` listings streamed in batches of 1000, plus
  stat, recursive folder size, resolve and ping. Every request answered,
  including the ones that fail.
- `app/io/pool.py` — per-volume workers, a watchdog that treats a passed
  deadline as a stuck process, and settlement of every request outstanding on a
  worker it restarts. A volume that will not recover is marked unreachable
  rather than restarted forever.
- `app/io/harness.py` — `list`, `stat`, `dirsize`, `ping`, `resolve`, `drives`,
  `soak` and `stall`, with timings, mid-listing cancel and mid-listing worker
  kill.
- `harness stall` and `Op.STALL`: fault injection that wedges a worker on
  purpose, so the timeout, the settlement of everything outstanding, and the
  restart can be verified on Windows without a share to yank. It reports each
  of those as a pass or a failure and exits accordingly.
- Tests for path resolution and for the io layer end to end, including a stuck
  worker reproduced with SIGSTOP. They run off Windows.

### Fixed
- A missing or broken pywin32 no longer degrades silently. Without it, drive
  letters resolve to themselves, every network path is keyed as a local volume,
  and a hung share takes the local disks with it. `paths.win32_problem()`
  reports the actual import error and the harness prints it on every command.
- Workers no longer die on Ctrl-C. A console interrupt reaches every process in
  the group, and a worker that took it left the parent holding requests that
  would never be answered. Workers ignore SIGINT; when one ends is the pool's
  decision alone.

## [0.1.0]

### Added
- Repository scaffold: package layout, design-system tokens, worker protocol
  definitions, project documentation.
