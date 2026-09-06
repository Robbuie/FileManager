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
- **Claude cannot test this app.** No GUI to look at, no rendering to check, no
  network shares to reach, no 50,000-file folder to enumerate. The user is the
  entire test loop. So: keep changes small enough to be verified in one sitting,
  say plainly what needs to be tried, and never report a UI change as working.
  "This should now do X — worth checking against a live share" is honest;
  "fixed" is not.
- **Claude has direct read/write access to `C:\Users\rjokr\Projects\FileManager`
  in Cowork sessions.** Edit files in place there. Pushing is the user's job —
  the session has no GitHub credentials and no `gh`.
- **Ask before adding a dependency.** The dependency list is short on purpose
  and every addition ships inside the installer.
- Every user-visible change gets a `CHANGELOG.md` entry and a version bump.

## Commands

Not yet established — the repo is at the start of step 1 below. When the layout
lands, this section holds the real commands (run the app, run the headless
harness, build the installer) and stays current.

Intended shape:

```
python -m app                     # run the app
python -m app.io.harness ...      # headless CLI against the io layer, no UI
pytest                            # unit tests for the pure pieces
pyinstaller packaging/app.spec    # installer build
```

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
- **Copy/move is where hobby file managers fall over,** and the hard part is not
  the copying. It is the queue: pause and resume, per-file and total progress,
  conflict rules (skip / overwrite / newer only / auto-rename), locked-file
  retry, and preserving timestamps and attributes. Design the queue before
  writing the copy loop.
- **Destructive operations need a confirmed target before anything moves.** The
  app never picks a destination on its own and never reports what it did after
  the fact.
- **Shell integration is `pywin32`, and third-party context menu entries need
  real `IContextMenu` handling** — TortoiseSVN and 7-Zip do not appear otherwise.
  UAC elevation for protected folders is part of this, not a later addition.
- **Replacing Explorer is only half supported by Windows.** Registering a
  Directory verb mostly works; Win+E needs a key remap. Do not promise more.
- **A drive letter can be present and dead at the same time.** Presence in
  `WNetGetConnection` is not reachability, and treating it as such reintroduces
  the startup hang.

## Build order

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

One lesson carried over from Redline PDF, worth honouring before the first
release rather than after: **have the build tool build and `gh` publish, not
both.** Letting the packager publish once per target had two instances race to
create the same release, and the run still exited green while the update
metadata never arrived. Whatever CI ends up here should fail outright if the
update manifest is missing from the build output.

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
