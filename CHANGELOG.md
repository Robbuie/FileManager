# Changelog

All notable changes to this project are recorded here. Every user-visible
change gets an entry and a version bump.

## [Unreleased]

## [0.11.0]

The chrome. No feature moved and no shortcut changed -- what changed is that
almost nothing keeps its one-pixel border, and the weight that is left went
where it means something.

### Added
- **The path is a row of places rather than a line of text.** Every folder
  above this one is a target, so up two and along one is a click instead of
  Backspace, Backspace, scroll, double click. A UNC share is one crumb --
  `\\server\share` -- because a server on its own is not somewhere a pane can
  be, and a path too deep to fit is elided from the middle, keeping the two
  ends that are worth keeping. Ctrl+L, or a click on the bar's empty space,
  still swaps in the plain editable field; Enter or Escape swaps it back.
  Anyone who types paths keeps typing paths.
- **An Age column.** How long ago, in three characters: `2h`, `3d`, `27d`,
  `9M`. The Modified column already says exactly when, which is sixteen
  characters to read; this says how long ago, which is the question actually
  being asked of a folder after a build or a sync. Tinted in three steps for
  today, this week and this month, and plain past that -- a fourth colour
  would say what the text already says, and in most folders most rows are old.
  The tint is always a second signal and never the only one, so the column
  reads the same in the high contrast theme.
- **A size bar** under each figure, two pixels tall, as wide as that file is
  against the largest file in the listing. Finding what is big in a folder
  stops being arithmetic done by eye down a column of numbers. Files only: a
  folder measured with Space keeps its total and gets no bar, because a folder
  total can be orders of magnitude past anything in the folder and on that
  scale every real file would draw as nothing.
- **A filter button** in each pane's controls, beside refresh. Ctrl+F already
  did this and stays the fast way; the button is how anyone finds out it
  exists.

### Changed
- **The listing is flat.** No zebra stripes, no gridlines, no border around
  the table -- two devices doing the job of row spacing, inside a container
  that was already drawn. A row lights up under the mouse instead, and the
  selected row is a rounded band inset from the edges rather than a rectangle
  bleeding into them. One hairline is kept, under the column headers, because
  the rows below it scroll and it does not.
- **The active pane is a bar down its left edge**, not a rectangle around
  everything. A rectangle competes with the listing inside it; two pixels of
  accent against a hairline does not. Both states are drawn at the same widths
  so nothing shifts by a pixel when focus moves.
- **Tabs are pills.** No trapezoids and no boxes: the one in front is filled
  and carries the accent as a rule under it.
- **The nav controls are borderless.** The drive letter, three arrows, refresh
  and filter were six outlined rectangles in a row before any of their content
  was read. They are icons now, lit by the mouse rather than outlined at rest.
- **The arrows are drawn rather than typed.** Back, forward, up, refresh and
  filter are painted from coordinates on a 24 by 24 grid at one stroke weight,
  so they match each other and follow the theme. A text arrow follows the
  colour but is drawn by whichever font answered, at whatever weight and size
  it felt like. Nothing new ships in the installer for this -- no QtSvg, no
  resource file.
- **Scrollbars are thin and have no trough**, the column headers are small
  upper case in the muted grey, the drive picker is a quiet chip rather than a
  combo box, and the favourites read as a row of places rather than a second
  tab strip.
- Two derived tints were added to the token set for the age chip, and one
  larger radius for containers. **No grey and no accent changed**, so Redline
  PDF and DWG Viewer are still the same family.

### Fixed
- **A borderless control in a pane did not make that pane the active one.**
  Every one of them takes no focus so the listing keeps the keyboard, and the
  window works out the active pane from where the focus went -- so a control
  that never takes focus is a control the window cannot see being used.
  Clicking Up, a crumb, a favourite or the drive picker in the pane that was
  not active walked that pane while every keystroke still went to the other
  one. That is 0.10's bug with a different first cause, and each of those four
  now says it was used.

### Known limits
- The chevrons between crumbs are separators. Making one open its folder's
  siblings means listing that folder, which is a worker request with its own
  timeout and cancel rather than a piece of chrome, and it belongs with the
  navigation rail.
- The navigation rail -- places, drives with capacity and their UNC, favourites
  in named groups -- is deliberately not in this release. A click in it has to
  land in one pane, which is the rule the Fixed entry above is about, and that
  deserves its own version rather than riding along with a restyle.

## [0.10.0]

### Added
- **Tabs are a feature rather than a list.** They have existed since 0.3 and
  almost nothing reached them. Now: the session is written out and restored so
  the tabs come back where they were; a middle click opens a folder in a tab
  behind and closes a tab in the strip; Ctrl+Shift+T duplicates one;
  Ctrl+Enter or the row menu opens the folder under the cursor in a new one;
  Ctrl+Tab and Alt+1 to Alt+9 move between them; double-clicking the empty
  part of the tab strip opens one; the tab strip has a menu with duplicate,
  close others and close to the right; and there is a Tabs menu to find all of
  it in.
- **Locked tabs.** A locked tab keeps its folder: navigating away from it
  opens a new tab at the target rather than refusing to move, so the tab
  pinned to a job folder stays there while a double click still goes
  somewhere. It refuses to close, close-others spares it, and its name is
  drawn in brackets.
- **Favorites, on a bar in each pane.** Ctrl+D saves the folder on screen
  under a name you confirm, and it appears as a button under that pane's tab
  strip: one click goes there, a middle click opens it in a tab behind, a
  right click renames, reorders or removes it. Ctrl+1 to Ctrl+9 reach the
  first nine without the mouse. The bar is in the pane rather than in the
  window because the question is never only "where" but "which side", and the
  panes sit side by side so two bars cost the height of one. What does not fit
  goes behind a trailing button and comes back when the pane is widened.
  `View`/`Favorites > Show the favorites bar` turns it off, and it hides
  itself entirely while nothing is saved. The Favorites menu and
  `Manage favorites` are still there.
- **Quick search.** Typing in the listing jumps to a name -- prefix first,
  then anything containing what was typed, wrapping, and continuing from
  where the cursor already is so a second letter narrows the answer. F3 steps
  forward, Shift+F3 back, Backspace shortens, Esc stops. What was typed shows
  at the front of the status line and says so when nothing matches.
- **Folder sizes on demand.** Space counts what is under the marked folders,
  or the one under the cursor, and puts the total in the size column.
  Ctrl+Shift+Space does every folder in the listing. One walk at a time across
  the whole window, because a walk holds that volume's worker; Escape
  withdraws them; an answer is dropped when its folder is listed again; and a
  walk that ran out of time keeps what it counted and marks it with a
  trailing plus. Sorting by size puts counted folders in order by what they
  hold.
- **Selecting a group.** Num+ and Num- mark or unmark everything matching a
  pattern, Num* inverts, Alt+Num+ takes the rest of the files of the kind
  under the cursor, Ctrl+A selects all. Ctrl+=, Ctrl+- and Ctrl+8 do the same
  on a keyboard with no numeric pad. A pattern is a glob when it has * or ? in
  it and a substring otherwise, and several can be given at once separated by
  a semicolon: `*.dwg;*.dxf`.

### Fixed
- Dragging a tab in the strip reordered the widget and not the pane, so
  afterwards every index -- the one a click selected, the one a close button
  reported -- named a different tab than the one under it.
- The favorites bar set a floor under the pane it was in, so the splitter
  could not be dragged past the width of the favourites and a window asked for
  820 pixels came back 1596 wide. A layout writes its widget's minimumSize
  property by default, and that property beats any size hint.
- **Clicking into a pane did not make it the active one.** Only the Tab key
  did, so after clicking into the other pane's listing every command that
  begins with "this pane" acted on the one before it: Ctrl+D saved the wrong
  folder, F5 copied the wrong way, Ctrl+L edited the wrong path bar, and the
  accent border said so the whole time. A `QFrame` never takes focus itself --
  its listing or its path bar does -- so the pane's own `focusInEvent` fired
  for Tab, which moves focus explicitly, and for nothing else. The window now
  follows the application's focus into whichever pane holds it.

## [0.9.0]

### Added
- **The real Explorer context menu.** Right-click a row and the entries the
  installed shell extensions add are there -- TortoiseSVN, 7-Zip, Open with,
  Properties, whatever else this machine has -- under the application's own
  verbs. Shift-right-click asks for the entries Explorer hides behind Shift.
- Right-clicking the background of a folder gives the folder's own menu -- New,
  Paste, Refresh, Sort by, and the background entries a program installs --
  which comes from the folder's view object rather than from the folder as an
  item. They are two different menus in the shell and only one of them is what
  a right-click on empty space means.
- The selection menu is asked for with the flags Explorer uses, including
  CMF_CANRENAME and CMF_ITEMMENU. Without the first there is no Rename in it,
  and handlers written against Windows 8 and later read the second to know
  they are being asked about an item at all.
- Submenus are told they are opening, through IContextMenu2 or IContextMenu3.
  Send to, Open with, New and most of what an extension puts in a submenu
  arrive empty and fill themselves at that point, so without the handshake
  they are arrows that open onto nothing.
- The application's own operations come first, in their own words and with
  their keys: Open, Copy F5, Move F6, Rename F2, Delete, Delete permanently,
  New folder F7, Refresh. They are there whether or not the shell answers.
- The shell's entries are **walked, not shown**: a separate process reads the
  menu Windows built into plain items, and the window draws its own menu from
  them. So the menu matches the rest of the application -- the same greys, the
  same accent on the highlight -- rather than being a Windows menu dropped
  into a themed window. Submenus, checked entries, disabled entries, the
  default entry in bold and the extensions' own icons all come across.
- **A shell host process.** Asking for a context menu means loading somebody
  else's DLL and running their code against the selection, so it happens in a
  process that owns nothing else. An extension that blocks on a share that has
  gone away is killed by the pool's watchdog and costs a menu; in the window's
  process it would freeze the application, which is the defect this whole
  architecture exists to avoid.
- **Running one refused operation as administrator.** A new folder, a rename
  or a delete that Windows answers with ACCESS DENIED now offers to do that
  one operation elevated. It starts a second process with the consent prompt,
  which does exactly what the plan says and exits. The application itself is
  never elevated, nothing is elevated silently, and the elevated process runs
  the worker's own code rather than a second copy of it.
- **Icon overlays.** The shared-folder arrow, the OneDrive tick and source
  control status marks are drawn on the rows that carry them. This is the one
  icon lookup that asks the shell about a file rather than about its type, so
  it is bounded on purpose: only the rows on screen, one request per folder,
  a short deadline, and one picture per badge-on-a-kind rather than one per
  file -- a working copy of 400 modified files is a handful of images.
- `View > Icon overlays` and `View > Explorer context menu` turn either of
  them off without a restart, for the day an extension misbehaves.
- `tools\diagnose_menu.py` collects the item menu, the extended menu, the
  folder background menu and the overlays into `Claude outputs\` in one run,
  and the reply behind it says how many entries the shell built before
  anything was read and why any of them were dropped. A menu that is short
  because the shell had nothing and one that is short because the walk could
  not read it look identical from the window.
- Three harness commands, so all of it can be exercised without the window:
  `menu <folder> [names...]` prints the menu tree with the ids and verbs and
  will `--invoke` one of them, `overlays <folder>` reports how many rows carry
  a badge and how many distinct pictures that cost, and
  `elevate <action> <path>` runs one operation through the consent prompt.

### Known limits
- An entry an extension paints itself rather than naming arrives with no text
  and is labelled from its verb. Explorer's recent-files entries are the
  common example.
- While a command from the menu has a dialog open, the shell entries are not
  offered again -- the pane's own verbs still are. Queuing a second menu
  behind that dialog is how a watchdog ends up closing it.
- A transfer that Windows refuses is not offered elevation. Copy and move run
  in the transfer engine rather than as a single call, and elevating one is
  its own piece of work.

## [0.8.0]

### Added
- Real shell icons in the listing. Every row draws the icon Windows uses for
  its file type, so a folder reads at a glance rather than as a column of
  names.
- Icons are asked for by **kind**, not by row. A folder of 50,000 files
  holding thirty extensions is thirty association lookups, and the answers are
  kept for the rest of the session -- the second folder of the day usually
  costs nothing. `python -m app.io.harness icons <folder>` lists a folder,
  works out the kinds in it and reports both numbers, which is the way to see
  that this is behaving.
- The lookup goes to the **local** worker whatever volume the rows came from,
  and asks the shell with `SHGFI_USEFILEATTRIBUTES` -- which answers from the
  extension alone and does not go near the path. Icons for a listing of a
  share that is answering slowly are not queued behind that share, because
  nothing about them is on it.
- A scaled display gets the 32-pixel icons and draws them at the same size in
  the row, so the listing is sharp without the rows growing.
- `icons.shell` in the settings file turns the whole thing off. It is on by
  default and there for the day a shell extension misbehaves: turning it off
  costs the pictures and nothing else.

### Changed
- `tests/conftest.py` builds one Qt application for the whole test run. Qt
  allows one per process and will not widen it later, so leaving it to
  collection order meant a new test could break an unrelated one by getting
  there first.

### Known limits
- A file with an icon of its own -- an executable, a shortcut, an `.ico` --
  draws the generic icon for its type. Reading the real one means opening the
  file, which is a per-path request against that file's own volume rather than
  a lookup that cannot block, and it is deliberately not in this release.
- Overlay icons are not drawn: no shared-folder arrow, no OneDrive tick, no
  TortoiseSVN status marks. Those come from the same shell extensions as the
  context menu and belong with it.

## [0.7.0]

### Added
- An installer. `packaging/build.py` freezes the application with PyInstaller,
  packs it with Inno Setup and writes `FileManager-Setup-<version>.exe` into
  `dist/`. Installs per user into `%LOCALAPPDATA%\Programs\FileManager`, with a
  Start menu entry and an optional desktop shortcut.
- Auto-update. **Help > Check for updates**, and one check a few seconds after
  launch unless that is turned off. A newer version is offered, never taken:
  nothing is downloaded until the offer is accepted, and the download runs off
  the UI thread and installs when the application quits. A version can be
  skipped, and stays skipped until a newer one appears.
- What is downloaded is checked against the size and the SHA-256 in the release
  manifest and written to a `.part` file that is renamed onto the real name
  only if both match -- the same rule the transfer engine uses. A download that
  does not match is discarded rather than run.
- `packaging/build.py` writes `latest.json`, the manifest the shipped updater
  reads, and refuses to finish if it names a file that is not there or a
  version that disagrees with `app/__init__.py`.
- An icon, drawn by `packaging/icon.py`: the family's dark tile with two panes
  on it and the Drafting blue on the active one.
- `.github/workflows/` -- tests on every push, and a tagged build that produces
  the installer and publishes it. The build tool builds and `gh` publishes,
  which is the one thing Redline PDF got wrong and this repository is not
  going to repeat.

### Changed
- The application is now started through `packaging/entry.py` when frozen,
  which calls `multiprocessing.freeze_support()` before anything else. Without
  it every spawned worker would re-run the entry point and open another window.

### Known limits
- The installer is unsigned, so the trust boundary for an update is HTTPS to
  github.com. The checksum proves the download arrived intact, not who built
  it. Signing is the upgrade path if this ever runs on a machine other than the
  author's, and it is written down in `app/core/updates.py` so it does not
  become an accident.
- Updating only works from an installed build, and the check is a real one:
  the application compares the folder it is running from against the
  `InstallLocation` Inno Setup recorded, so a copy started out of
  `dist\FileManager\` or off a stick says where the installed copy is instead
  of downloading an update that would land somewhere else and leave the old
  version running.

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
