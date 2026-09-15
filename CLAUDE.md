# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What this is

A **Windows dual-pane file manager**, Python + PySide6, built to replace Double
Commander. Personal use, offline, single user. Repo:
[Robbuie/FileManager](https://github.com/Robbuie/FileManager) (public, and it
stays public — see Packaging). `PROJECT-CONTEXT.md` holds the
reasoning behind the architecture and is the document to read first; this file
is the working agreement on top of it. Where the two disagree,
`PROJECT-CONTEXT.md` wins on *what* and this file wins on *how*.

It is the third app in a family: **Redline PDF** (Electron, PDF markup) and
**DWG Viewer** (Python/PyQt6, drawing viewer). They are meant to read as one
suite. See "Look and feel" below — that is not a nice-to-have, it is a spec.

## Working agreement

- **No emojis.** Not in the UI, not in code, not in comments, not in commit
  messages, not in `CHANGELOG.md`, not in chat responses about this project.
  Status is conveyed by text and colour. Icons are SVG or real shell icons,
  never emoji glyphs. This is a technical tool for a technical user and it
  should look like one.
- **No decorative output.** No banner comments made of box-drawing characters,
  no ASCII art, no "Done!" flourishes. Comments explain *why* something exists,
  in prose, the way the existing files do.
- **Claude cannot test this app, with one exception.** No network shares to
  reach, no 50,000-file folder to enumerate, no clicking anything. The user is
  the test loop for all of that. The exception is the *look*: `tools/preview.py`
  renders the real window offscreen to a PNG, so a theme that did not apply or a
  column that collapsed is visible without a screen. Use it before saying a
  layout is right. It says nothing about whether the app behaves. So: keep
  changes small enough to be verified in one sitting, say plainly what needs to
  be tried, and never report a UI change as working.
  "This should now do X — worth checking against a live share" is honest;
  "fixed" is not.
- **Claude has direct read/write access to `C:\Users\rjokr\Projects\FileManager`
  in Cowork sessions.** Edit files in place there. Pushing is the user's job —
  the session has no GitHub credentials and no `gh`.
- **Ask before adding a dependency.** The dependency list is short on purpose
  and every addition ships inside the installer.
- Every user-visible change gets a `CHANGELOG.md` entry and a version bump.

## Commands

```
python -m venv .venv                    # once
.venv\Scripts\activate
pip install -r requirements.txt
pip install pytest                      # tests only, not shipped
pip install -r packaging/requirements-build.txt   # building an installer

pytest                                  # path, worker and pool checks
python -m app.io.harness --help
python -m app                           # the window

python tools/preview.py --path C:\Windows\System32 --out preview.png
python tools/preview.py --all-themes --out-dir previews
python tools/preview.py --queue --out queue.png       # the queue panel alone
python tools/preview.py --preview-pane --out pane.png # the preview panel, both shapes
python tools/preview.py --grid --cell 128 --out grid.png
python tools/preview.py --viewer image --out viewer.png   # also text, hex
python tools/preview.py --commands --out commands.png    # the commands editor

python packaging/build.py               # dist/: the folder, the setup exe, latest.json
python packaging/build.py --skip-installer   # freeze only, no Inno Setup
python packaging/icon.py                # only when the icon itself changes
```

Verifying the io layer against a real share — the four things that matter, in
the order worth trying them:

```
python -m app.io.harness drives                          # enumerate, no probe
python -m app.io.harness resolve S:\Jobs                 # letter, UNC, worker key
python -m app.io.harness list S:\Jobs --timeout 20       # rows, first batch, rate
python -m app.io.harness list S:\Jobs --kill-after 2000  # kill mid-listing
python -m app.io.harness soak S:\Jobs --count 40 --interval 3 --retry
python -m app.io.harness icons S:\Jobs                    # kinds in a folder, and their icons
python -m app.io.harness fileicons "C:\Program Files"     # the icons files carry themselves
python -m app.io.harness folders S:\Jobs --limit 200      # what a chevron drops down
python -m app.io.harness copy S:\Jobs\big D:\scratch --conflict rename
python -m app.io.harness move S:\Jobs\big D:\scratch --cancel-after 50000000
python -m app.io.harness erase S:\Jobs\scratch --cancel-after 200
python -m app.io.harness recycle S:\Jobs\scratch\one.txt
python -m app.io.harness run S:\Jobs compare --other D:\Archive --dry-run
python -m app.io.harness run S:\Jobs terminal            # which program, and where
```

The transfer commands run the real engine: the queue, the scan, the conflict
rule and the cancel. `--cancel-after` is the one worth running against a share
-- a cancel part way through a large file must leave the destination without a
partial and the original untouched, and that is not something a screenshot can
show.

Without a share to hand, `stall` covers everything except the network itself by
wedging a worker on purpose. It is the acceptance test for the architecture and
runs anywhere:

```
python -m app.io.harness stall C:\Windows --timeout 3
```

What to look at rather than what to run:

- `list` reports **first batch** separately from **elapsed**. If the two are
  close on a 50k folder the listing is not streaming, whatever the total says.
- `--kill-after` must end in a settled status within a second or two. A hang
  there is the defect this application exists to fix, reproduced in miniature.
- `soak` is the pull-the-cable test: start it, pull the network, watch the
  status go GONE and the volume get marked unreachable, plug back in, and see
  `--retry` bring it back without restarting anything.
- `stall` must report five passes. A failure there is the architecture not
  working, not a flaky test, and nothing downstream is worth building until it
  is green again.

`pytest` bare and `python -m pytest` have to behave the same, and by default
they do not: the interpreter puts the working directory on the path and pytest
does not, so a bare run cannot import `app` at all. `pythonpath = ["."]` in
`pyproject.toml` is what makes the two agree, and the release workflow runs the
bare form -- so removing that line breaks the build and nothing else.

The tests run anywhere, including off Windows — the session table is injected
rather than read, and a hung worker is reproduced with SIGSTOP. That makes them
the one part of this project that can be checked without the user watching a
window, which is reason enough to keep them passing.

## The rule everything else follows from

**The UI thread never touches the filesystem.** Not for listings, not for sizes,
not for icons, not for drive enumeration, not for a "quick" existence check in a
dialog. Every one of those is a call that a dead SMB share can block for 30-45
seconds, and that block is the single defect this app exists to fix.

Practically, in review terms: if a diff puts `os.`, `pathlib`, `shutil`, or
`win32file` anywhere under `app/ui/`, it is wrong regardless of how harmless the
call looks. The UI asks a worker and renders what comes back.

## Architecture

Three layers, and the boundary between them is the point.

**`app/ui/`** — panes, tabs, dialogs, the widgets. Pure presentation and input.
Talks to `core`, never to `io` directly, never to the filesystem at all.

**`app/core/`** — the custom item model, view state, config, the tab and pane
model. Holds the data the UI renders and mediates between UI and `io`.

**`app/io/`** — every real filesystem call, in separate processes.

- `worker.py` — one process, all real I/O for one volume.
- `pool.py` — spawns, kills and restarts workers, **keyed on resolved server
  name** so `S:\` and `\\server\share\` share a worker rather than hanging
  independently.
- `ops.py` — the copy/move queue, in a process of its own so a stalled transfer
  cannot take the window down and survives the window closing.

Rules that hold across the boundary:

- **One worker process per volume.** Local disks share one; each network server
  gets its own. A hung share means killing that one process: the affected tab
  shows "reconnecting" with a retry, every other tab keeps working.
- **Listings stream.** Rows paint as they arrive, in batches of roughly 1000.
  Nothing waits on a full enumeration.
- **Every operation carries a timeout and a cancel.** No exceptions. An
  operation without one is an incomplete operation, not a simpler one.
- **Drive enumeration is lazy and local.** Never probe drives at startup.
  `WNetGetConnection` reads local session state and does not touch the network.
- **Network paths are polled, not watched.** SMB change notification is
  unreliable. Configurable interval plus a manual refresh key.
- **Qt's view is used; `QFileSystemModel` is not.** The stock model has its own
  network hang behaviour, which is the thing being escaped. Custom model, fed by
  the workers. The view is kept because its virtualisation is free and good.

## Path handling

The environment uses both mapped drive letters and UNC paths, and they are not
interchangeable.

**Resolve internally, display externally.** `WNetGetConnection` maps `S:` to
`\\server\share`. All real work happens in UNC; the display form is a per-tab
preference. This is what lets a reconnect succeed after the drive letter's
session has died — the UNC was retained, so there is still something to
reconnect to.

Anything that keys on a path — the worker pool, caches, the operation queue —
keys on the **resolved** form. Keying on the displayed form gives two entries
for one server that then hang separately.

## Performance

The worst case is 5,000 to 50,000 files in one folder, over SMB.

- **`os.scandir`, never `os.stat`.** On Windows, `DirEntry` carries size, mtime
  and attributes from the directory enumeration itself, so a full 50k listing
  with every column is one pass and zero extra syscalls. A per-row `stat()` is
  50,000 SMB round trips — under a second becomes over a minute. Treat an
  introduced `stat()` in a listing path as a bug, not a style question.
- **Icons, folder sizes, and anything requiring the file to be opened stay
  lazy.** They are requested for visible rows and cached.
- **Sort and filter run on a plain Python list in the model.** That stays under
  100ms at 50k. Do not reach for a proxy model that re-queries.

## Look and feel

The three apps share a design system. Redline PDF is the source of truth
(`src/css/app.css` in that repo); DWG Viewer ported it to Qt; this app ports the
same thing. The goal is that someone who uses two of them recognises the third.

**Three independent axes: theme, accent, density.** They are kept independent on
purpose. A theme sets the greys. The accent sets one colour. The density sets the
chrome metrics. Anything that mixes them — a theme that hardcodes a colour, a
density that also shifts a hue — multiplies the combinations and half of them
look wrong.

**Default: dark theme, Drafting blue accent, normal density.** Redline PDF
defaults to its redline red; the blue is this family's non-markup accent and
matches DWG Viewer.

### Tokens

Dark theme (the default set):

```
bg_0   #101216   app backdrop
bg_1   #171a20   chrome (toolbar, tab strip, status bar)
bg_2   #1d2128   raised chrome (pane background, dialogs)
bg_3   #252a33   hover
bg_4   #2f3540   active / strong borders
line   #2a2f38
line_soft #22262d
txt_0  #e7ebf2   primary text
txt_1  #aab3c0   secondary
txt_2  #79828f   muted
info   #4aa8ff    good #46c98b    warn #f2c14e    sel #4aa8ff
radius 7px        radius_sm 5px
font   "Segoe UI", Inter, system-ui, sans-serif
mono   "Cascadia Mono", Consolas, monospace
```

The other four themes — **light**, **warm paper**, **blueprint**, **high
contrast** — restate the greys only, exactly as in Redline PDF's `app.css`. Port
those values verbatim rather than re-deriving them; the family look depends on
them being the same numbers.

Accents are **channel triples, not hex**, because every tint is derived from
them:

```
redline  255, 91, 74      amber  242, 165, 60     field green  70, 201, 139
cyan      54, 191, 210    drafting blue  74, 145, 255    violet  154, 122, 255
```

Derived tints: `soft` at alpha .14, `wash` at .17, `line` at .45, `glow` at .8.
`dim` is the accent mixed 70% with black, `text` mixed 74% with white.

Density metrics, sized for a file list rather than a document canvas:

```
             compact   normal   large
ui_font        12px    13px     14.5px
toolbar_h      30px    34px     38px
row_h          20px    22px     26px      list row
tab_h          26px    30px     34px
status_h       22px    26px     30px
sidebar_w     238px   268px    304px
button_h       26px    30px     34px
```

Row height matters more here than anywhere in the other two apps: this is the
thing being looked at 50,000 times.

### Porting the system to Qt

QSS has no custom properties and no `color-mix`. Do not try to fake them.

- **`app/theme/` owns a token dictionary in Python** — one dict per theme, one
  triple per accent, one dict per density — and renders a QSS **template** by
  substitution. Derived tints are computed in Python (`rgba(74, 145, 255, 0.14)`
  written out literally into the generated sheet).
- **Changing theme, accent or density re-renders the sheet and re-applies it**
  to the app. That is the whole mechanism; there is no partial update path.
- **No literal colour anywhere outside `app/theme/`.** A hardcoded `#1d2128` in
  a widget is a spot that stops following the theme, which reads to the user as
  a picker that half works. If a check script gets written, this is the first
  thing it should check — Redline PDF's `test/verify.js` fails the build on a
  literal accent, and that rule has earned its keep.
- **Selection, hover and focus come from the accent**, not from Qt's platform
  palette. Set the palette explicitly; the default Windows blue does not belong
  here.
- Semantic colours that must agree with something else (a conflict marker, a
  diff colour in a future compare view) do **not** follow the accent. They stay
  fixed and stay named, so nobody tidies them into the accent later.

## Keys

The Norton keymap, which is what Double Commander uses and what the user's
hands already know. They are handled by the pane rather than as window
shortcuts, because a window shortcut on Delete takes the key away from the path
bar and the filter box -- and backspacing over a typo would start deleting
files.

```
F2   rename                F5   copy to the other pane
F7   new folder            F6   move to the other pane
F8   delete (Recycle Bin)  Del  the same; Shift+Del is permanent
Ins  mark and move down    Tab  the other pane
Ctrl+R  refresh            Ctrl+Shift+R  reconnect
Ctrl+F  filter             Ctrl+L  edit the path
Ctrl+J  the job queue     Ctrl+D  save this folder as a favourite
Ctrl+U  swap panes         Ctrl+Shift+M  other pane comes here
Alt+Left / Alt+Right  back, forward -- and the two side buttons on the mouse,
which walk the history of the tab in the pane the pointer is over (0.17)
Ctrl+B  the navigation rail
F3   view the file under the cursor
Ctrl+P  the preview pane      Ctrl+Shift+P  the thumbnail grid

External commands, which are a table rather than code (0.17)
F9   PowerShell here       Shift+F9  Command prompt here
Ctrl+F9  Windows Terminal  F4        Edit
Ctrl+F2  compare the two panes with the tool  Alt+F2  compare the marked files
Ctrl+Shift+F2  compare the panes here, marking what differs
Every one of those is a row in `commands` and can be re-keyed or removed. The
keys are the pane's, matched against the table before the built-in function
keys -- which is what lets Ctrl+F2 be a command while F2 stays rename.

The rail and the path bar
click a place, a drive, a saved folder  the pane that has the keyboard goes
middle click  the same, in a tab behind
click a heading  fold that section     right click a drive  measure it
click a chevron in the path bar  the folders inside the crumb on its left

Tabs
Ctrl+T  new                Ctrl+W  close
Ctrl+Shift+T  duplicate    Ctrl+Shift+W  close the others
Ctrl+Tab / Ctrl+Shift+Tab  next, previous
Alt+1 .. Alt+9  by number  Ctrl+Shift+L  lock this one
Ctrl+Enter  the folder under the cursor, in a new tab
middle click  a folder in a tab behind; a tab in the strip, closed
double click  the empty part of the strip, a new tab

Favorites
Ctrl+D  save this folder    Ctrl+1 .. Ctrl+9  go to the first nine
click a bar button  go there     middle click  go there in a new tab
right click one in the rail  move it to a group, reorder it, remove it

In the viewer
arrows  the next file, the previous one   Home / End  the first, the last
+ / -  zoom      0 or 1  one to one      F  fit      Esc  close
PgUp / PgDn  the pages of a document

Finding and marking
type a name  jump to it    Ctrl+G / Ctrl+Shift+G  the next, the previous match
Ctrl+G steps the last name looked for, not only one still being typed; Enter
steps while the search is live. Esc forgets it, and so does leaving the folder.
F3 became the viewer in 0.16
Space   count what is under the marked folders
Ctrl+Shift+Space  count every folder in the listing
Num +   select a group     Num -   unselect a group     Num *  invert
Alt+Num +/-  the rest of the files of this kind
Ctrl+A / Ctrl+Shift+A  all, none
Ctrl+= / Ctrl+- / Ctrl+8  the group keys on a keyboard with no pad
```

The selection and search keys are the pane's for the same reason the function
keys are. `Ctrl+A` as a window shortcut takes select-all away from the path bar
and the filter box; a bare `+` is either the keypad key or a character being
typed into a quick search, and only the widget holding the search can tell
those apart -- it does it by insisting the keypad ones carry `KeypadModifier`.
Two of them have to be intercepted *before* the view rather than allowed to
bubble to the pane: `QAbstractItemView` answers a printable key with its own
`keyboardSearch` and `Space` by toggling the selection.

## Conventions

- Python 3.11+, 4-space indent, type hints on anything crossing a module
  boundary. A short module docstring at the top of each file explaining *why* it
  exists, not what it contains.
- **UI and IO never share objects.** Everything crossing the process boundary is
  a plain picklable dataclass or dict. No `Path`, no Qt types, no open handles.
- **Worker replies carry a request id and are idempotent.** A tab that navigated
  away must be able to drop a late reply without special-casing.
- **Every worker call returns an envelope**, success or failure both — the caller
  never gets a bare value it has to guess about, and a timeout is a normal result
  rather than an exception path.
- **Qt signals for UI-to-core, queues for core-to-IO.** Do not let a Qt signal
  cross the process boundary.
- New settings go through the config module with a default; never read a raw key.

## Things that will bite you

- **A killed worker leaves in-flight operations orphaned.** Whatever restarts the
  worker also has to fail the outstanding requests, or a tab waits forever on a
  reply that will never come. Restart and re-request; do not resume.
- **A delete is a job, not a request.** Del and Shift+Del go into the queue in
  `app/io/ops.py`, not to a worker, and the reason is the deadline: a recycle
  of 30,000 files on a share ran past `timeout.delete`, so the pane reported a
  failure while the shell carried on deleting. `Op.DELETE` still exists and is
  still the worker's own handler -- it is what the elevated retry runs and what
  `harness delete` exercises -- and the queue's recycle calls that same handler
  rather than carrying a copy of it. **There must never be two implementations
  of a destructive operation**, which is also why `Pane.delete` does nothing at
  all when there is no queue rather than falling back to the worker.

  The two deletes are separate job kinds because they behave differently and
  the UI has to say so: a recycle is one uninterruptible shell call (one call
  is what makes one undo), and an erase is a walk this application drives, so
  it pauses, cancels and counts items. `JobState.interruptible` is what the
  panel greys its buttons off, and it is False only while such a step is
  actually running.
- **Copy/move is where hobby file managers fall over,** and the hard part was
  never the copying. `app/io/ops.py` has the queue -- pause, resume, cancel,
  conflict rules with an answer that applies to the rest, bounded retry on a
  locked file, timestamps and attributes preserved. Two invariants in there are
  not negotiable and are the reason it can be trusted with somebody's files:
  **every file is written beside its target and renamed onto it**, so nothing
  half-written ever wears the real name; and **a move deletes its source only
  after the copy is verified by size**. Anything added to that module keeps
  both.
- **Destructive operations need a confirmed target before anything moves.** The
  app never picks a destination on its own and never reports what it did after
  the fact.
- **The context menu runs somebody else's code, so it runs in its own
  process.** `app/io/menu.py` builds the `IContextMenu`, walks the `HMENU`
  into plain items and invokes by id; the window draws its own menu from
  those. Two rules in there are not negotiable: an id is only ever used with
  the token it arrived with, because the next menu numbers its commands the
  same way; and the menu is released after an invoke or a dismissal, because a
  live `IContextMenu` keeps a third-party DLL — and sometimes the folder —
  open. Never build one in the window's process, whatever it would simplify.
- **Do not queue anything behind an invoke.** A verb that opens a dialog holds
  the host until a person answers it. A menu request queued behind that dialog
  expires, the watchdog reads the host as wedged, and killing it closes their
  dialog. `core/menu.py` refuses to ask while a command is open, and
  `timeout.menu_invoke` is hours rather than seconds for the same reason.
- **Elevation is one operation, never the application.** `app/io/elevate.py`
  writes a plan, starts one process with the `runas` verb, and that process
  runs the *worker's own handler* and exits. Only `elevate.ACTIONS` may be
  elevated and the check is made on both sides. A copy of an operation that
  only runs when elevated is a copy that is only tested when elevated.
- **Overlays are the one exception to the rule below, and are bounded by
  hand.** A badge is a fact about a file, so `Op.OVERLAY` carries a path and
  goes to that file's own volume. What keeps it affordable is bookkeeping:
  the rows on screen rather than the folder, one request per folder, a short
  deadline, one picture per badge-on-a-kind, and the answers dropped when the
  folder is listed again. Anything added there keeps all five.
- **A mouse button nothing claims is a mouse button Qt drops.** The side
  buttons did nothing until 0.17 -- not because the history was missing, but
  because no widget in Qt answers `BackButton` by itself and nothing here was
  listening. Two things the fix has to keep: the **press** is swallowed as well
  as the release, because `QAbstractItemView` reads an unknown button as a
  click on a row and clears the selection; and the handler **claims the pane**,
  because a side button does not move the focus the way a click on a row does,
  so without it a thumb press over the inactive pane would walk that pane while
  every keystroke still went to the other one.
- **A control that takes no focus is invisible to the active-pane rule.**
  Every borderless control in a pane is `NoFocus` so the listing keeps the
  keyboard, and the window works out the active pane from
  `QApplication.focusChanged` -- so a control that never takes focus is a
  control the window cannot see being used, and clicking it in the *inactive*
  pane walks that pane while every keystroke still goes to the other one.
  `PaneWidget._claim` is what answers it, and **every control added to a pane
  from now on has to call it.** The navigation rail is the deliberate
  exception and inverts the rule: it is not in a pane, so it claims nothing,
  takes no focus anywhere, and goes to whichever pane already had the
  keyboard. A rail that took focus would leave the *next* click going wherever
  the last one left things.
- **Anything the rail can open is an opened volume, so it is asked for by
  hand.** A drive capacity meter is `disk_usage`, which on a mapped drive
  whose server has gone is the block this application exists to escape. Only
  local fixed disks are measured without somebody asking (`capacity.AUTOMATIC`
  is the whole filter), a failure is remembered as a failure rather than
  re-asked on every redraw, and no place in the rail is ever checked for
  existence -- five `isdir` calls at startup is the startup probe with a
  different name.
- **A breadcrumb chevron is a scan, not chrome.** `Op.FOLDERS` is capped in
  the worker rather than sliced by the caller, so a chevron on a 50,000-row
  folder costs two hundred names -- which also means past the cap the names
  are the ones the folder handed over first and not the first alphabetically,
  and the menu says so. One is outstanding at a time and a second chevron
  cancels the first *at the worker*: an abandoned scan still holds the volume
  the next one wants.
- **Icons are asked for by kind, never by row.** `SHGFI_USEFILEATTRIBUTES` is
  what makes them safe: the shell answers from the extension alone and does
  not go near the path, so the request goes to the local worker and cannot be
  stuck behind the share being listed. An icon request that carries a path is
  a 50,000-round-trip listing waiting to happen, and `_shell_icon` refuses
  one. Reading the icon out of an executable or a shortcut is the exception,
  and has to be a per-path request against that file's own volume.
- **That exception is `Op.FILE_ICON`, and its bound is the kind list.**
  `SELF_ICON_KINDS` in `protocol.py` is what makes reading a file for its
  picture affordable: a folder of drawings matches none of it and sends
  nothing, so the ordinary case costs what it did before this existed. Both
  ends check it, for `elevate.ACTIONS`' reason. Everything else about it is
  the overlays' bookkeeping -- the rows on screen, one request per folder, a
  short deadline, one picture per distinct picture -- with one difference:
  the answers are keyed on the row's own mtime and size rather than dropped
  when the folder is listed again, because an icon cannot change unless the
  file does. Adding a kind to that set is a decision about cost, not a
  formatting change; `.dll` is left out deliberately and says why.
- **A preview opens a file, so the whole feature is off until somebody asks.**
  `app/io/decode.py` is one ladder read by three surfaces, and the only thing
  keeping it affordable is that nothing asks it anything by default: the
  preview pane is closed, the grid is a view somebody switches to, and F3 is a
  key somebody presses. A listing in the normal view sends nothing, so a folder
  of 50,000 drawings costs what it cost before this existed. **Anything that
  makes a preview happen without a person asking for it is the mistake to
  refuse**, however convenient -- a thumbnail in the listing's icon column, a
  preview on hover, a decode "warmed up" while the folder is idle.

  Past that, the three bounds are the ones the overlays and the file icons
  already use, plus one each of their own.

  - **The pictures are scaled in the worker, before the process boundary.** A
    6,000-pixel photograph is forty megabytes of pixels and about ninety
    kilobytes at the size a pane can show it, and `QImageReader.setScaledSize`
    before `read` means the large version never exists. This is most of the
    cost of the feature and it is one call; a change that scales after the
    boundary instead will pass every test and be thirty times slower.
  - **The preview pane is debounced and one outstanding at a time,** and the
    abandoned one is cancelled *at the worker* -- the breadcrumb chevron's
    rule, for the same reason: an abandoned decode still holds the volume the
    next row wants. One `Previews` for the window, not one per pane.
  - **The grid's bound is `draws_a_thumbnail`,** the way FILE_ICON's is
    `carries_own_icon`. Text is deliberately not a thumbnail: ninety cells of
    grey lines at 128 pixels are ninety identical squares, and the icon for the
    kind says more in less space.
  - **Qt is imported inside the functions that need it, never at module
    level.** `worker.py` imports `decode.py`, and `worker.py` is spawned once
    per volume -- an unconditional PySide6 import would put fifty megabytes and
    a fifth of a second into every worker including the ones that only list
    folders.
  - **The shell thumbnail rung runs in the volume's worker, not in a host.**
    Deliberately unlike the context menu, and the line is whether anything is
    held across a person's decision: a menu keeps a live `IContextMenu` and a
    third-party DLL loaded until somebody clicks, and a thumbnail provider is
    one call in and pixels out. `Op.OVERLAY` and `Op.FILE_ICON` already load
    third-party shell code in that process on exactly that reasoning. A host
    would buy isolation from a hang and cost a wedged file on one share taking
    thumbnails down for every volume, which for a grid of a hundred cells is
    the worse trade.
- **A command that starts a program is a worker op, and the working directory
  is why.** `Op.RUN` looks like something the window could do with three lines
  of `subprocess` -- and a child process inherits its parent's working
  directory, so a terminal opened in a folder on a share would hold that folder
  open for as long as somebody left the terminal running. Finding the
  executable is a read as well. Both belong in the volume's worker, and the
  reply says the process exists rather than waiting for it to finish: waiting
  would hold every listing on that volume for the life of an editor.
- **The command table is not allowed to take a key the pane already answers.**
  `commands.RESERVED` is that list and `shortcut_refusal` is where it is
  enforced, in the editor, while somebody can still see what they pressed. A
  row that took F5 would be a copy key that silently stopped copying, and
  nothing about pressing it once would say why. Adding a key to this
  application from now on means adding it to that set in the same commit.
- **Replacing Explorer is only half supported by Windows.** Registering a
  Directory verb mostly works; Win+E needs a key remap. Do not promise more.
- **A drive letter can be present and dead at the same time.** Presence in
  `WNetGetConnection` is not reachability, and treating it as such reintroduces
  the startup hang.

## Build order

0. Done so far: the io layer (0.2), the window with panes, tabs and navigation
   (0.3), the chrome that makes it usable -- opening files, drives, filter,
   selection, free space (0.4), single-call operations: mkdir, rename and
   delete to the Recycle Bin (0.5), the copy/move engine with its queue (0.6),
   the installer and auto-update (0.7), shell icons in the listing (0.8), the
   Explorer context menu, overlays and elevation (0.9), and the working
   comforts -- tabs that persist and lock, favourites, quick search, folder
   sizes on demand, selecting a group (0.10), the restyle -- flat listing,
   breadcrumb path bar, age and size-bar columns (0.11), and the navigation
   rail with drive capacity meters, grouped favourites and sibling dropdowns
   on the breadcrumb chevrons (0.12), and the icons that live inside a file
   rather than in the association database -- programs, shortcuts, .ico files
   (0.13), the job queue with deletes in it (0.14), Windows clipboard interop
   (0.15), and the previewer -- one decoder behind an F3 viewer, a preview pane
   and a thumbnail grid (0.16), and the external commands with the pane
   compare beside them (0.17). What is left before this replaces Double
   Commander day to day: a transfer that outlives the window.

1. **`app/io/` first, headless, with a CLI harness. No UI at all.** Verified
   against real shares: a 50k listing over SMB, a connection yanked mid-listing,
   worker kill and restart, reconnect after a dead mapped drive. Nothing else
   starts until these hold.
2. Dual pane, tabs, navigation.
3. The copy/move engine with its queue.

Those three beat the current setup on their own. Multi-rename, archives (bundled
7-Zip binary), compare and sync, and preview panes bolt on cleanly afterwards —
and are out of scope until step 3 is solid.

## Packaging and releasing

Same shape as Redline PDF: **public GitHub repo, installer with auto-update from
GitHub releases, built iteratively across sessions.** PyInstaller spec and
installer config live in `packaging/`.

The repo (`Robbuie/FileManager`) stays public, because a shipped updater reading
a private release feed would need a token baked into the installer.

One lesson carried over from Redline PDF, honoured in
`.github/workflows/release.yml`: **the build tool builds and `gh` publishes,
never both.** Letting the packager publish once per target had two instances
race to create the same release, and the run still exited green while the
update metadata never arrived. `packaging/build.py` makes no network call at
all, and both it and the workflow fail outright if `latest.json` is missing or
names a file that is not in `dist/`.

How the pieces fit:

- `packaging/entry.py` is the frozen entry point and exists for one line,
  `multiprocessing.freeze_support()`. The pool and the transfer engine spawn
  processes, which re-launch the executable; without that call each one
  re-runs the application and opens another window.
- `packaging/filemanager.spec` freezes a **folder**, not a single file. A
  onefile build unpacks itself on every launch, and this is an application
  opened twenty times a day.
- `packaging/installer.iss` installs **per user**, into `%LOCALAPPDATA%`. A
  Program Files install would put a UAC prompt in front of every update, and an
  update that needs a password is an update that gets postponed.
- Output filenames carry no spaces. GitHub turns a space in an asset name into
  a dot on upload, so a manifest written before the upload would point at
  nothing -- which is exactly how it failed in Redline PDF, and only on
  machines running the older build.
- `app/core/updates.py` is the shipped half: it reads `latest.json` from
  `releases/latest/download/`, refuses any URL outside this repository's
  releases, and verifies size and SHA-256 before anything is run. Its threads
  are threads rather than processes because the timeout and the socket are ours
  to close -- but the rule still holds, and none of it runs on the UI thread.
- Bumping a version means three files: `app/__init__.py`, `pyproject.toml` and
  `CHANGELOG.md`. The release workflow refuses a tag that disagrees with the
  first of them.

## Scope

Offline, single-user, local-file desktop app. No accounts, no telemetry, no
analytics, no cloud anything. Nothing about the user's filesystem leaves the
machine.

**The one exception is the update check**, and it is narrow on purpose: one
request to the release feed shortly after launch, only when auto-update is
enabled, nothing downloaded without a prompt, nothing sent outward. A second
network call is a new decision, not an extension of this one.

This is not being built for Encore machines. Leave that environment out of
design decisions entirely.
