# Changelog

All notable changes to this project are recorded here. Every user-visible
change gets an entry and a version bump.

## [Unreleased]

## [0.29.11]

### Added
- **HEIC, HEIF and AVIF are decoded here rather than asked about.** What a
  phone writes was the one common picture format the previewer could not show:
  Windows draws a `.heic` only when HEIF Image Extensions and HEVC Video
  Extensions are both installed -- the second costs a dollar in the Store and
  neither is on a fresh machine -- so the thumbnail rung answered nothing, Qt
  has never read the format, and the file fell to the bottom of the ladder and
  came back as a hex dump. `pi-heif` now ships in the installer and reads them
  with libheif, offline and the same on every machine. It runs before the
  Windows rung, so it answers whether or not those extensions are installed,
  and it covers the viewer, the preview pane and the thumbnail grid together.
- **A picture nothing could draw says so.** Four kilobytes of hex under a name
  ending `.heic` or `.cr2` reads as the application not knowing what a
  photograph is. Those now end in a line naming the reason -- no decoder on
  this machine -- which also tells a missing codec apart from a damaged file.
  Hex is still the floor for everything else, and a `.png` that turns out to
  be an error page is still shown as the error page.

### Changed
- **Two new dependencies, `pi-heif` and its Pillow.** Pillow was already a
  build-time dependency for the icon; it now ships. `pi-heif` is `pillow-heif`
  without the x265 encoder -- 4 MB of libraries rather than 26 -- because this
  application reads HEIC files and will never write one. Either build
  satisfies the decoder if one is already installed.

## [0.29.10]

### Fixed
- **The window no longer falls behind the mouse at full screen.** Moving the
  hover highlight repainted the whole listing, and the pointer crosses a row
  every twenty-two pixels -- so on a large display at full size the
  application spent all of its time redrawing the list and answered nothing
  else, which is the lock-up reported on a host machine while the same build
  in a VM was fine. Measured at 3840x2160 over a folder of 20,000: crossing
  forty rows cost 4.5 seconds before this and a tenth of a second after. Only
  the row left and the row arrived at are repainted now.

## [0.29.9]

### Fixed
- **Clicking a column heading sorts the listing again.** It had done nothing
  since 0.24, when the listing was given a header of its own so the sorted
  column could carry a chevron: a header built by hand does not hear clicks
  unless it is told to, and turning sorting on does not tell it -- that only
  shows the indicator and waits for it to change. The dividers went on
  dragging the whole time, which is what made it look like the sort rather
  than the header.

## [0.29.8]

### Added
- **Slow is recorded as well as stuck.** A window that finishes every repaint
  but takes most of a second over each one is unusable and stalls nothing, so
  the 0.29.6 record stayed empty while the window was plainly not working.
  Any turn of the event loop over three quarters of a second is now a line in
  `hangs.log` saying how long it took.

## [0.29.7]

### Fixed
- **The window could stop answering the keyboard after a right-click command
  was run.** 0.29.2 put the shell host's owner window in front before running
  a command. That window has no size and nothing in it, so the keyboard went
  to something invisible and the application looked frozen. Only the window a
  command actually opens is brought forward now.
- **A dialog is placed where it can be seen.** Qt centres one on its parent,
  which puts it off the screen when the window is half off the edge or on a
  monitor that has since been unplugged -- and a modal dialog nobody can find
  is a window that answers nobody, which is indistinguishable from a freeze.
  Every dialog now stays within the screen its window is on, and comes to the
  front.

### Added
- **A remote session that starts after the window does is noticed.** The
  glass backdrop draws by sending a picture of the whole window, which over a
  remote connection is slow enough at full size to read as a freeze. Whether
  to use it is decided at startup, so connecting remotely to a machine where
  this is already running left glass on; the status bar now says so and names
  the way out.

## [0.29.6]

### Added
- **The freeze record now catches a window that answers nobody while it is
  still running.** A dialog or a menu opened off the edge of the screen holds
  the keyboard and the mouse, so the window ignores everything, and because
  nothing is actually stuck the 0.29.5 record stayed empty. A watcher on its
  own thread now writes the same stacks when Windows reports the window as
  not responding, and on request: create a file called `dump.now` beside
  `hangs.log` and the next second writes one. Every dump also lists the
  windows this process owns, with their positions on screen -- which is what
  names a dialog waiting on a monitor that is no longer attached.

## [0.29.5]

### Added
- **A record of freezes.** When the window stops answering for more than five
  seconds, the stack of every thread in it is written to `hangs.log` beside
  the settings (`%APPDATA%\FileManager`),
  and again every five seconds until it recovers or is ended. Nothing is
  written while the window is responding. This is how the freezes reported
  on opening a server folder and while previewing `.heic` files will be found.

## [0.29.4]

### Fixed
- **Clicks went through the window to the program behind it.** With the glass
  backdrop, anything the window left unpainted -- the gaps between the rail's
  rows, a row before its hover highlight was drawn, the empty title bar -- was
  fully transparent, and Windows sends a click on a fully transparent pixel to
  whatever is underneath. The window now paints a floor that cannot be seen
  but is not empty.
- **The rail was slow to respond to a click.** Moving the highlight to the row
  just chosen rebuilt the whole column, every icon included; it now only moves
  the highlight.

## [0.29.3]

### Fixed
- **The maximise button could freeze the window.** Clicking it maximised the
  window from inside the handler still answering that click, so Windows asked
  the window about its new frame while it was busy, and on a remote desktop it
  stopped responding. A frozen window cannot say where its title bar is, so
  clicks meant for it went to the windows behind. The maximise now happens
  once the click has been answered.

## [0.29.2]

### Fixed
- **Properties from the right-click menu** took a long time to appear and then
  opened behind the window. The shell host slept between requests without
  running its message loop, and the Properties sheet waits on that loop before
  it draws; the host now runs it while it waits. The window also hands the
  host the right to come to the front just before a command runs, and the
  host brings forward the first window the command opens.

## [0.29.1]

### Fixed
- The release build's test run failed on Windows: a drive-list test still
  expected the fields from before USB eject. No change to the application.

## [0.29.0]

### Changed
- **Transfers show as a floating pill** over the bottom of the window instead
  of a line in the status bar. It slides in when a copy, move or delete starts
  and away when the queue is empty: a progress ring, what is being done, and
  percent, amount, speed, time left and how many are queued, with pause and
  cancel. Click it to open a card with the last minute of speed and the jobs
  waiting behind, and a button to the full queue (Ctrl+J as before).

### Added
- **Rows fill as they are copied**, in the pane the files come from and the
  one they are going to: a file fills by its own bytes, a finished one stays
  full, and a folder fills with the whole job.
- **A new folder fades in** when a pane arrives in it.
- **View > Animations** turns off the fade, the glow's move and the pill's
  slide.

## [0.28.0]

### Added
- **Command palette (Ctrl+K)**, also the "Go anywhere, run anything" box in
  the title bar. Type a few letters of any menu command, a saved folder, a
  folder either pane was recently in, or a folder inside the one on screen;
  Up and Down pick, Enter runs, Esc closes. Commands show their key. Start with
  `>` for commands only, `@` saved folders, `#` recent, `/` folders here. A
  typed path such as `S:\Jobs\Riverside` offers going straight there.
- The key hints along the bottom show Ctrl K.

## [0.27.0]

### Added
- **Eject USB drives from the rail.** Sticks, card readers and USB hard
  drives get an eject button on their row, and Eject on their right-click
  menu. A pane standing on the drive is moved to a local disk first, and a
  transfer still using the drive stops the eject with a message. When another
  program has a file open on it, the status line names that program.
- **The rail follows drives being plugged in and pulled out** without Rescan.
- **Type badges.** Each row shows a small tag with its extension, coloured by
  kind: Logix (ACD, L5X), HMI (MER, APA), drawings (DWG, DXF), PDF, sheets,
  documents, images, archives. Folders get a folder tag. View > Type badges
  instead of icons turns Windows' icons back on.
- **Folder header.** Above each listing: the folder's name, how many folders
  and files it holds and their size, and a bar of what it is made of by kind
  of file, largest share first. View > Folder header hides it.
- **History on Back and Forward.** Hold either button, or right-click it, for
  the folders behind or ahead in that tab, and jump straight to one.

## [0.26.0]

### Changed
- **The window draws its own title bar.** The Windows title bar and the menu
  bar are gone; a thin row at the top holds the menu (the mark at the left),
  a "Go to a folder" box (Ctrl+L on the active pane, and where the command
  palette arrives), and minimise, maximise and close. Dragging, double click
  to maximise, resizing from the edges, Aero Snap and Windows 11's snap
  layouts on the maximise button are all meant to behave as before.
- **Glass backdrop on Windows 11.** The window's background is Mica, and the
  panes sit on it as cards. It switches to a solid grey by itself in a remote
  session (which includes Hyper-V's enhanced session), with transparency off,
  or on Windows older than 22H2.
- **The active pane glows** with the accent around its edge, and the glow
  moves across when the active pane changes. The idle pane keeps its fade.
- **The key hints are keycaps along the bottom** and stay there, instead of a
  message that went away after twenty seconds. A status message still covers
  them while it is showing.
- The rail sits on the backdrop rather than on its own grey band.

### Added
- **View > Window**: the title bar (this application's, or Windows' own with
  the menu bar back) and the backdrop (automatic, glass, solid). Both apply
  after a restart. The way back if the custom frame misbehaves.

## [0.25.0]

### Added
- **Flat view (Ctrl+B).** Every file under the folder in one list, in the tab
  in front. Mark, copy, move, delete, preview and F3 work on the rows as usual.
  The filter matches file names across the whole tree. Files arrive as the
  walk goes; Esc stops it and keeps what it found. It stops at 50,000 files
  (`flat.limit`) and says so, and says how many folders could not be read.
  Going to another folder, or Ctrl+B again, ends it. Rename and Duplicate are
  refused while it is on.
- **Two layouts, chosen under View > Flat view layout:** a Location column
  beside the name (double click a location to go to that folder, cursor on
  the file), or a heading for each folder with its files under it. Groups
  follow the sort's direction, so newest first puts the newest dated folder
  at the top.

### Changed
- **The navigation rail is Ctrl+Shift+B**, since Ctrl+B is flat view.

## [0.24.0]

### Changed
- **Two rows of chrome above the listing instead of three.** The favorites bar
  is off by default (the rail and the Favorites menu still list them; it can be
  turned back on there), and a settings file from an earlier version is moved
  onto the new look once.
- **The drive is the first thing in the path bar**, as a button that drops the
  list of drives down, rather than a separate picker beside it.
- **Browser-style tabs**: a band a step darker than the pane, the current tab
  cut from the pane's colour, a folder icon, a width cap with the name cut at
  the end, and a close button only on the current tab and the one under the
  pointer.
- **The rail's rows carry icons** -- a place, a favourite, a network location,
  a drive -- with the current one in the accent.
- **No `<DIR>`.** A folder's size is blank until Space counts it.
- **The Ext column starts hidden and the extension is drawn on the name**, in
  the muted grey, whenever that column is not on screen -- including when a
  narrow pane has squeezed it out. Shown again from the header menu, the name
  goes back to the stem.
- **Sizes, ages and dates use tabular figures**, so the digits line up.
- **The sorted column has a chevron** in the accent, pointing the way it sorts.
- **Softer pane edges**: the one-pixel border is gone; the active pane keeps
  its accent bar and the other one its fade.

## [0.23.0]

### Changed
- **The pane that is not taking keystrokes steps back.** Its rows, icons, size
  bars and age chips are drawn faded and its column headers go a grey quieter,
  so which side a key will land in no longer rests on a two-pixel accent bar.

### Fixed
- **A narrow pane no longer draws broken columns.** With the preview panel
  open, the columns were squeezed to 28 pixels -- "EX", "GE" and "MO" in the
  header and the age chip drawn over the size. Each column now shrinks only to
  what it can still be read at, then hides, in the order Age, Ext, Modified,
  Size, and comes back when there is room. The hiding is not stored, and the
  header menu still shows what was chosen.

## [0.22.0]

### Added
- **Live folders.** The folder on screen is listed again every two seconds on
  a local disk and every five on a share, so a file saved by another program
  appears without a Ctrl+R. A folder that is slow to list is checked less
  often -- never more than a tenth of the time -- a check that fails backs off
  and says nothing, checks stop while the window is minimised, and coming back
  to the window checks at once. `refresh.local_seconds` and
  `refresh.network_seconds` set the intervals, and 0 turns them off.

  Polled rather than watched, on every kind of volume: SMB change notification
  is not reliable enough to trust a view to, a Hyper-V redirected drive less
  so, and a watch is a request that never ends on a worker that answers one
  request at a time.
- **Drag files out of the window** -- into an email, onto the desktop, into
  another program -- from the listing or the grid. Always a copy; nothing can
  be dropped in.

### Changed
- **A refresh keeps what is on screen.** Ctrl+R, the re-list after a rename or
  a copy, and the live check all reconcile the new listing into the old one
  instead of starting over: the marks, the cursor, the scroll position, the
  filter and the sort survive, a row that went takes its mark with it, and a
  check that finds nothing repaints nothing. A new folder still streams in
  from empty.
- Rename and Duplicate find their file again by name after the dialog closes,
  because a live folder can move rows while it is open.

## [0.21.0]

### Added
- **Duplicate, on Shift+F5 and in the context menu.** Copies the item under the
  cursor beside itself under a new name, for the folder-per-day habit: duplicate
  yesterday's folder, work in today's, and yesterday's stays behind as the
  backup. The name offered is the old one **with today's date in place of its
  date, written the way it was written** -- `2026-09-15`, `2026_09_15`,
  `20260915`, `09-15-2026` and `260915` are all recognised and kept in their
  own form -- and `Name - Copy` when there is no date or today's name is taken.
  It is a job in the queue like any other copy.

  A duplicate **never merges into something already there.** The dialog refuses
  a name in use, checked against every name in the folder including the ones a
  filter is hiding, and the engine refuses it again, because a folder copied
  onto an existing folder of the same name would quietly mix two days together.

### Fixed
- **The end of a long context menu was cut off, Properties with it.** When the
  shell's entries arrived the menu was resized with `adjustSize`, which caps a
  top-level widget at two thirds of the screen, so a menu taller than that was
  drawn shorter than its contents. It is sized to its own size hint now, which
  wraps into a second column rather than running off the screen.

## [0.20.1]

### Fixed
- **A folder on a share could be slow to open, or fail to open, while Explorer
  listed it at once.** Reported from a Hyper-V guest on `\\tsclient\C`. Each
  volume had one worker answering one request at a time, and the listing
  queued in the same line as the overlay badges, file icons, thumbnails,
  previews and folder sizes -- so opening a folder waited behind the badges of
  the folder just left, each of which is a round trip per file on a
  redirected drive. A listing's deadline runs from when it is asked for, so one
  that waited long enough was timed out, its worker killed, and three of those
  in a minute marked the volume unreachable.

  Each volume now has a second worker, a side lane, for those decorations
  (`pool.SIDE_OPS`). A listing never waits behind a badge, and a side lane that
  wedges restarts on its own account: it costs the badges and not the folder.
  Retry and Ctrl+Shift+R reach both.

## [0.20.0]

The io layer, made honest about three things it was quietly wrong about. No new
surface to speak of -- one keystroke behaves differently and one refusal is new
-- and all three are cheaper to do now than after archives puts a virtual path
layer on top of the same code.

### Added
- **Long paths.** Windows' file calls stop at 260 characters unless a path
  carries the `\\?\` prefix, and nothing in this application used it. A folder
  tree on a share goes past that without anybody trying -- a job number, a
  discipline, a revision and a drawing name is most of it before the file is
  named -- and what makes it worth a release rather than a note is *how* it
  failed: `os.scandir` answers a folder that is plainly there with "The system
  cannot find the path specified", so the length arrived disguised as a bug in
  whichever feature reached it first.

  Every real filesystem call in `app/io` goes through `paths.api` now: the
  listing, the sizes, the previews, and the whole copy, move and delete engine.
  The prefix reaches the file calls and nothing else. **The shell does not take
  it** -- `SHFileOperation`, `SHGetFileInfo`, `IContextMenu`, `ShellExecuteEx`
  and the thumbnail provider all answer a prefixed path with a failure or with
  nothing -- so the Recycle Bin, the context menu and the icons still get the
  plain path, and so does the path bar: `\\?\UNC\server\share` is not something
  anybody can type back.
- **A copy is refused before it starts when the destination has no room for
  it.** The queue knows the byte total the moment its scan ends and the volume
  is one call away from saying how much room it has, so it asks then: nothing
  half-written, nothing to clean up, and a number to act on instead of a
  failure at ninety per cent with the folder already half full. The status line
  says what was needed and what was free.

  A destination that will not say how much room it has **proceeds**. An unknown
  is not a refusal, and a share that reports nothing is not a share with
  nothing left on it.
- **`harness space`**, which asks the same question the queue asks, through the
  same function: what a volume has left, and with `--need` the verdict a
  transfer of that size would get before one is started.
- **`harness resolve` now prints the form a file call gets**, and the path's
  length, and says when it is past the limit.

### Changed
- **Ctrl+Shift+R reconnects the share, then re-lists.** It used to restart the
  volume's worker and ask for the listing again, which is the right answer for
  a worker that wedged and the wrong one for a session that has died: the
  worker was never the problem, and the second listing fails exactly as the
  first one did. It attaches to the share first now, and the listing follows on
  its own -- the same operation the rail's Reconnect performs, rather than a
  second one that looks similar.

  The *share* is what is reconnected, not the folder the pane is standing in:
  Windows attaches to `\\server\share` and knows nothing about what is
  underneath it. And it works from the pane's resolved path, so a pane showing
  `S:\Jobs` is recognised as standing in the share that letter is mapped to. A
  local pane keeps the old behaviour, which is still right for a disk that
  stopped answering.

### Fixed
- **A reconnected share re-lists a pane that is showing a drive letter.** The
  rail's Reconnect matched panes against the path it had just reconnected, so a
  pane displaying `S:\Jobs` was never recognised as being in
  `\\dc01\projects` -- and sat on its error message after the reconnect that
  should have cleared it.

## [0.19.0]

Network locations that have no drive letter -- which, it turns out, is most of
what a virtual machine can reach.

Reported from inside a Hyper-V VM: the host's C: drive is shared into the
guest, Double Commander lists it, and this application had nowhere to put it.
The cause was narrow and complete. The rail's drive list comes from
`GetLogicalDrives`, which reports **letters** -- and a Hyper-V or Remote Desktop
redirected share has none. It is `\\tsclient\C` and nothing else, so no amount
of drive enumeration would ever have found it.

The path layer was never the problem, which is worth saying because it is what
a reasonable person suspects first: `\\tsclient\C` is an ordinary UNC path
here, keyed on `\\tsclient` like any other server, so it gets its own worker
and a host connection that dies cannot take the rest of the window down. Typing
it into the path bar has always worked. There was simply nowhere for it to
*appear*.

### Added
- **A Network section in the rail**, listing what this session is attached to
  that Drives cannot show. Read from the redirector's own table of current
  connections -- the same table `WNetGetConnection` answers from, which is
  local, so it contacts no server and cannot block on one that has gone.

  It deliberately does **not** enumerate the network. Asking what exists out
  there is a real round trip through the browser service and is how a file
  manager comes to hang while drawing its sidebar. What is out there is not
  this application's question; what this session already holds is.
- **Add a network location by hand**, for the shares Windows will not
  enumerate: one nobody has connected to yet is in no table, so it is typed
  once and then remembered. Saved locations are listed whether or not they are
  reachable, which is the point -- one that vanishes the moment a server
  reboots is one you cannot click to get it back.
- **Reconnect**, on a network location and on any mapped drive. A letter can be
  present and dead at the same time, so it is offered on every remote drive
  rather than only on one that has already failed. No credentials cross:
  Windows uses the session's own, which is the case that matters -- a share
  that dropped when a server restarted comes back with nobody being asked
  anything, and one that needs a different account fails with the reason said
  out loud. A pane sitting on that share re-lists itself when it works.
- **`harness network` and `harness connect`.** The first is worth running
  beside `harness drives`: between them they say which kind of thing a share
  is, which is the whole distinction this release turns on.

- **`tools/diagnose_network.py`**, because the first run of this release on the
  machine it was written for came back empty. It asks every enumeration scope
  separately, prints each `NETRESOURCE` field as it arrives -- `lpProvider`
  above all, which names which piece of Windows is holding a connection --
  tries `\\tsclient` by hand, and says which calls failed and why. One run
  settles where the share actually lives.

### Changed
- **An empty list and a failed call no longer look the same.**
  `paths.connections()` swallowed every failure into an empty list, so a
  machine with nothing mapped and a call that could not open were
  indistinguishable -- and that is exactly the position the first run left us
  in. It now reports why it is short, `harness network` prints it, and the rail
  shows it in place of "nothing here".
- `Op.NETWORK` and `Op.CONNECT` in the worker. The first reads a local table
  and probes nothing; the second is the one call in this application that is a
  network call by nature, so it carries a deadline of its own
  (`timeout.connect`, 45 seconds) and is only ever sent because somebody asked.

## [0.18.0]

The columns, which turn out to have been two separate faults wearing one
complaint. Reported from the window as "there is no good way to change the
column sizes on the fly", and both halves of that are true for different
reasons.

The name column was a `Stretch` section. Qt gives a stretched section no
usable drag handle and recomputes it on every resize, so the one column
anybody actually wants wider was the one column that could not be touched at
all. And the other four could be dragged -- and were thrown away on the next
tab switch, because the widths were laid out again from hardcoded numbers
every time a tab changed. Between them the answer to "can I change the column
widths" was no, twice.

### Added
- **Every column can be dragged, including the name**, and what it is dragged
  to is remembered -- per pane, because the two sides of a window are rarely
  the same width and rarely want the same columns.
- **Double click a divider to fit that column to what is on screen**, or use
  the header menu to fit them all. Deliberately *not* Qt's own
  `ResizeToContents`, which measures every row in the model: on a folder of
  50,000 files that is 50,000 string measurements for a double click, and the
  rows somebody is looking at are the ones they mean. Scroll to longer names
  and ask again and it widens again, which is the honest consequence and is
  why the menu entry says "on screen".
- **A header menu**: fit, reset to the shipped widths, and a checkbox per
  column to hide Ext, Size, Age or Modified. A hidden column gives its room to
  the name rather than leaving a gap where it was. The name cannot be hidden.

### Fixed
- **A narrow pane no longer collapses the name column.** This one predates the
  release: the other four columns are 328 pixels of fixed width, so below about
  380 of viewport there was nothing left for the name -- it shrank to a column
  of first letters and the listing grew a horizontal scrollbar. Now the others
  give way instead, least useful first: Age, which duplicates Modified; Ext,
  which repeats the end of the name; then Modified, which degrades gracefully;
  and Size last, because a size column too narrow for "1.2 M" is not a narrow
  size column but a missing one. A pane whose widths somebody has set is left
  alone.
- Column widths survive switching tabs, which is where most of the "it does not
  stay" came from.
- **The context menu no longer runs off the bottom of the screen when
  Explorer's entries arrive.** The menu opens with this application's own verbs
  and the shell's land in it a moment later, which is deliberate -- a menu that
  waits for a shell extension to load is a menu that is sometimes not there
  when the mouse button comes up. What was missing is the second half: Qt
  places a popup once, from the entries it has at that instant, and grows a
  visible one downwards from where it already is without looking at the screen
  again. Opened near the bottom of a screen, the new entries were simply
  unreachable. The menu is now placed again against the point it was opened at:
  it flips above the pointer rather than sliding, because a menu that slides
  has its first entry somewhere new every time.

## [0.17.0]

The programs this application does not contain, and the keys that reach them.

A file manager is where somebody's hands already are when they want a terminal
in this folder, an editor on this file, or a diff of these two folders. None of
those is written here. What is written here is the one mechanism that reaches
all of them -- a table of `name / program / arguments / shortcut` with
substitution for the things only a file manager knows -- and everything else is
a row in it.

Which is what makes the compare tool **optional by construction** rather than
by a flag. The application does not know what Beyond Compare is; it knows that
a row names it, and that a row whose program is not on this machine is a row
that says so. Replace the program with something else, or with a compare tool
of your own, and nothing else changes.

Alongside it, the compare that needs no tool at all: `Ctrl+Shift+F2` marks, in
each pane, what that side has and the other does not. It reads nothing -- both
listings are already in memory with their sizes and times -- so it costs
nothing on a share, and what it leaves behind is a selection that F5 copies.

### Added
- **A Tools menu, and a table behind it.** Ships with seven commands:
  PowerShell here on **F9**, Command prompt here on **Shift+F9**, Windows
  Terminal here on **Ctrl+F9**, Edit on **F4**, Compare the two panes on
  **Ctrl+F2**, Compare the marked files on **Alt+F2**, and Explorer here.
  Every one of them is a row that can be renamed, re-keyed, pointed at a
  different program, hidden from the menu or deleted.
- **Tools > Commands** to edit that table. Five fields and a list. A shortcut
  is refused while it is being typed if it is already the pane's own -- F5
  copies and there is no arrangement of this table that can stop it -- or if
  another row already has it.
- **The substitutions.** `%P` this pane's folder, `%T` the other pane's,
  `%N` the name under the cursor, `%F` its full path, `%S` the marked files as
  full paths, `%s` as bare names, `%L` a file listing them for a selection too
  long for a command line, `%%` a literal per cent. An argument template is
  **split into tokens before anything is substituted**, so a folder with a
  space in it lands in one argument and nothing in the table needs quoting.
- **Compare the panes, on Ctrl+Shift+F2.** Marks what is newer here, what is
  only here, and what is the same age and a different size, on both sides at
  once. Names fold to one case because Windows does, and timestamps two seconds
  apart count as the same moment because FAT and SMB round them -- an exact
  comparison calls half the files on a USB stick newer, every time.
- **`harness run`,** which expands one command and starts it, saying which
  candidate it found and where. `--dry-run` prints the argument vector and
  starts nothing. This is the half of the feature that a machine has to answer,
  and it answers it without a window.

### Changed
- `Op.RUN` in the worker. Starting a program is a filesystem call: finding the
  executable is a read, and the folder it starts in is opened. It is also the
  reason it cannot be three lines in a slot -- a child inherits its parent's
  working directory, so a terminal started by the window process would hold
  that folder open for as long as somebody left the terminal running.
- Two settings: `commands` (the table) and `compare.tolerance` (the two
  seconds). Both editable in the settings file for anybody who wants to.

### Fixed
- **The side buttons on the mouse walk the history**, back and forward, in the
  tab of whichever pane the pointer is over -- including the inactive one,
  which becomes active as it goes. They did nothing before: the history behind
  Alt+Left has always been there, but no widget in Qt answers those buttons by
  itself and nothing here was listening, so Windows delivered them and they
  were dropped. Reported from the window while using 0.17.
- The test suite could segfault inside `QApplication.processEvents()` when the
  garbage collector happened to destroy a Qt object with a running timer at the
  wrong moment. Collected between tests instead. Nothing in the application
  does this -- the objects concerned live as long as the window.

## [0.16.0]

Seeing what is in a file without leaving the window. Three surfaces and one
decoder behind them: F3 opens a viewer, Ctrl+P opens a panel beside the
listing, and Ctrl+Shift+P turns the rows into cells with pictures in them.

They are one feature rather than three because they ask the same question at
three sizes. A viewer shows a photograph large, a panel shows it small beside
the folder, and the grid shows ninety of them at 128 pixels -- the decision
about what a file *is* is made once, in `app/io/decode.py`, and the three
callers differ only in the box they ask for.

**No dependency was added**, which was not the plan and is worth saying. The
four format families asked for -- pictures, camera raw, documents and video --
came out of what is already installed: PySide6 ships Qt's image plugins, and
the PDF plugin among them registers as an image format, so a page of a drawing
set costs what a photograph costs. A camera raw file is unwrapped rather than
demosaiced -- every one of them carries a full JPEG of the shot in its header,
so pulling it out is a scan for two byte markers. And a frame of a video comes
from the thumbnail handler Windows already has.

### Added
- **F3 opens a viewer.** Images with zoom and fit, the arrow keys stepping
  through the folder; text with the encoding it was actually decoded as; a hex
  dump for everything else. The three are one window rather than three, because
  what a person is doing is looking through a folder, and stepping from a JPEG
  to the readme beside it should not close one window and open another.

  Which shape you get is decided by what came back rather than by the
  extension, so a photograph somebody saved as `plan.bak` opens as a
  photograph, and a `.png` that is really a text file opens as text.

  The keys are on the footer of the window, because a viewer opened with a
  function key is a window somebody arrives in without having read anything.
  `+` and `-` zoom, `F` fits, `0` is one-to-one, PgUp and PgDn step the pages
  of a PDF, Esc closes. The listing follows the walk, so closing it leaves the
  cursor on the file that was last on screen.
- **A preview pane, on Ctrl+P.** A panel beside the listing showing whatever
  the cursor is on. It is inside the pane rather than being one panel for the
  window, because in a dual-pane file manager the question is never only
  "where" but "which side".
- **A thumbnail grid, on Ctrl+Shift+P.** The same rows as cells. The same
  model, not a copy -- so the sort the header set still applies, the filter
  still applies, and the selection is the same selection, which means switching
  view mid-task cannot lose what was marked. Four cell sizes under View, and
  the grid is per pane rather than per window: a grid of photographs on one
  side and a listing of where they are going on the other is the case that
  makes a dual-pane file manager worth using.
- **`harness preview` and `harness thumbnails`,** for measuring the decoder
  against a real share. `preview` says which rung of the ladder answered, which
  is the difference between a missing codec pack and a bug; `thumbnails`
  reports the cost per file, which is what decides whether the grid fills in
  visibly or appears.

### Changed
- **F3 is the viewer now; the quick search's find-next moved to Ctrl+G**
  (Ctrl+Shift+G for the previous match, and Enter also steps while a search is
  live). F3 was find-next since 0.10. It moved because a viewer is worth a bare
  function key in a way that stepping a search is not, because it is what
  Double Commander does with the key, and because Ctrl+G is what every editor
  uses -- so it was already the second guess.

  **Ctrl+G steps the last name looked for, not only one still being typed.**
  The quick search stops accumulating 1.5 seconds after the last keystroke,
  which is about not extending a search nobody remembers making -- it is not a
  statement that the search is over. F3 read it as one, so find-next only ever
  worked within a second and a half of typing. Escape forgets the name, and so
  does leaving the folder.

### Notes on cost, since this is the feature that opens files
- **The ordinary case costs nothing.** A listing in the normal view never asks
  for a preview of anything. The panel is closed by default and the grid is a
  view somebody switches to, so a folder of 50,000 drawings costs exactly what
  it cost in 0.15 unless a person asks to look inside it.
- **Pictures are scaled before they cross the process boundary,** not after. A
  6,000 x 4,000 photograph is forty megabytes of pixels and about ninety
  kilobytes at the size a pane can show it, and Qt's JPEG reader scales during
  decompression -- so the large version never exists anywhere.
- **The preview pane is debounced and one-at-a-time.** A held arrow key crosses
  thirty rows on the way to row thirty-one and asks about none of them, and an
  abandoned decode is cancelled *at the worker* rather than merely ignored --
  it is still a file being read off a volume, and the next row wants that
  worker.
- **The grid asks only about cells that could draw one**, a screenful at a
  time, in one request per folder, with the pictures keyed on a digest so forty
  copies of one drawing are one image.
- **Three switches under View**, for the day something misbehaves: pictures in
  the grid, Windows thumbnail handlers, and the preview pane itself. The middle
  one turns off the only rung of the decoder that runs somebody else's code.

## [0.15.0]

Ctrl+C, Ctrl+X and Ctrl+V, talking to Explorer in both directions. Until now
this application was the only window on the machine where those three keys did
nothing, which made it feel like a separate thing rather than part of Windows.

### Added
- **Copy and cut put files on the Windows clipboard**, in the format Explorer
  reads: the paths as `CF_HDROP`, and `Preferred DropEffect` saying whether it
  was a copy or a cut. That second part is the whole difference between Ctrl+X
  and Ctrl+C, and an application that leaves it out has a cut that quietly
  copies -- which the user finds out about when the original is still there.

  Nothing is put on as text. `Ctrl+Shift+C` is the key that copies a path as
  text and it stays a separate gesture, because an application that quietly
  does both surprises whatever is on the other end of the paste.
- **Paste puts a job in the queue.** Ctrl+V in a folder copies or moves what is
  on the clipboard into it, through the same queue a copy from F5 goes into --
  so it reports as it goes, it can be held, paused and cancelled, and a paste
  of 30,000 files over a share is a job rather than a frozen window.

  There is no dialog. The destination is the folder the pane is standing in,
  which is a person pointing at it; a prompt asking "into here?" after they
  have already said where trains people to dismiss prompts. What is refused
  instead is the paste that cannot be undone by looking at it:

  - A folder pasted into itself, or into a folder inside it. That copy is a
    walk that keeps finding what it has just written, and it ends when the
    disk is full.
  - A cut pasted back where it came from, which is nothing happening reported
    as a move.
  - An empty clipboard, which gets a word rather than a key that appears not
    to have arrived.

  A refusal is said on **the pane's own status line**, under the listing it is
  about, in the same place and the same colour as any other thing that pane
  declines to do. Copy and cut report themselves there too. Nothing goes to
  the window's status bar: it is the far corner of the window from the pane
  that was just right-clicked, and six seconds later it is gone, which reads
  as a key that did nothing.

  All of that is compared on the resolved path, because `S:\Jobs` and
  `\\server\jobs` are one folder and a check that believed otherwise would let
  a folder be pasted into itself through a drive letter.

  A copy pasted into the folder it came from is not refused -- it is how a
  duplicate is made -- and it goes in asking the queue to rename rather than
  asking about every name it is about to collide with on purpose.
- **A cut row is drawn faded**, in both panes and in every tab showing that
  folder, the way Explorer draws one. The mark is a fact about the clipboard
  rather than about a pane, and it is recomputed from whatever is on the
  clipboard whenever it changes -- so a cut made in Explorer greys the row
  here too, and a copy made anywhere takes the mark down.
- **Copy, Cut and Paste in the File menu and in the right-click menu.** Paste
  is offered on the empty part of a listing as well as on a row, because
  pasting into an empty folder is exactly when there is no row to click.
- `python tools/preview.py --cut` renders the window with a few rows marked
  cut, which is the only way to see whether the fade is readable against the
  rows around it in each theme.
- `python tools/diagnose_clipboard.py` prints what is actually on the Windows
  clipboard, with `CF_HDROP` and `Preferred DropEffect` decoded, and its
  `--put` and `--put-cut` forms write the clipboard the way Explorer does --
  by hand, through Windows rather than Qt -- so this application's reading can
  be tested without Explorer in the way. Copy and paste between two programs
  has three ways to fail that look identical from either end: the copy wrote
  nothing, it wrote a format the other program does not read, or the data was
  gone by the time the paste asked. This says which.

### Changed
- **The shell's Cut, Copy and Paste are no longer drawn in the right-click
  menu.** The pane has its own three now, so the menu was offering each of
  them twice. They were deliberately left in before 0.15, when this
  application's Copy meant the other pane and the shell's meant the clipboard:
  two commands that shared a word, and dropping the shell's would have taken
  away the only clipboard entry there was.

  Paste is the one worth stating plainly: it was not only a duplicate, it was
  the wrong implementation. The shell's paste copies the files in the menu
  host with no queue, no progress and no cancel -- which over a share is the
  wait this application exists to escape, arriving through its own context
  menu.

  Matched on the shell's verb rather than the label, as the other four already
  are, which is why `Copy as path` survives: a different command with a
  similar name.
- `TransferQueue.copy` and `.move` take a conflict rule, so a paste into the
  folder the files came from can say "rename" rather than asking about every
  name.

### Notes
- The three keys are handled by the pane rather than as window shortcuts, for
  the reason the function keys are: a window shortcut on Ctrl+C would take
  copy away from the path bar and the filter box, and there the cost is worse
  than a lost keystroke -- it is a folder full of files pasted somewhere
  because somebody meant to paste text into a field.
- Explorer keeps its own rows greyed after this application has pasted its
  cut. Telling it otherwise means reporting a `Performed DropEffect` back
  through the data object, which is not something a clipboard read can do.
  Refreshing the Explorer window clears it.

## [0.14.0]

The queue stops being a copy queue and becomes the one place work is listed.
Deletes go into it, jobs can be held and reordered, and the panel behind the
status line is a panel rather than three lines of text.

### Added
- **Deleting is a job in the queue.** Del and Shift+Del no longer send a
  request to a worker and wait on a deadline; they put a job in the same list a
  copy goes into, and it reports as it goes.

  The deadline is why. A delete was one request against `timeout.delete`, so a
  recycle of 30,000 files on a share ran past it: the pane said the delete had
  not finished while the shell carried on deleting, which is the worst of both
  answers. A job has no deadline -- it has a person watching it.

  The two deletes behave differently in the queue, because they are two
  different operations rather than one with a switch:

  - **To the Recycle Bin** is still one shell call for the whole selection,
    because one call is what makes one undo in Explorer. It cannot be paused
    or cancelled once it has started, and the panel says so in the row and
    greys the buttons rather than offering something that will not happen. It
    can be cancelled while it is still waiting its turn.
  - **Permanently** is now this application walking the tree itself, file by
    file, with the same checkpoint between items that a copy has between
    chunks. So it can be paused, held and cancelled, it says which file it is
    on, and it counts items rather than bytes -- 40,000 small files take far
    longer than one big one, and a bar drawn from bytes would sit still and
    then jump. A cancel leaves everything it had not reached where it was.

  A file Windows refuses is told apart from one that simply failed, and only
  the refusal is offered as a retry with administrator rights -- the same offer
  the worker path already made, for exactly the items that were refused.
- **Jobs can be held and reordered.** A job that has not started can be moved
  up or down the queue or held back; a running one can be held, which stops it
  where it is without stopping anything else. Holding keeps a job's place
  rather than sending it to the back: holding something is saying "not yet",
  not "after everything else".
- **The queue panel** (Ctrl+J) lists everything -- running, waiting and
  finished -- one row each, with the destination, what it is working on, and a
  bar with the percentage beside it. The buttons act on the selection and only
  light up for what they can actually do: Cancel is offered for a job that can
  be cancelled, Up and Down for one that is still waiting, and a recycle the
  shell has already started offers neither.
- `python -m app.io.harness recycle <paths>` and `... erase <paths>` run the
  two deletes as real queued jobs from a console, with progress and
  `--cancel-after`. `erase` against a folder on a share is the one worth
  running: the thing to watch is that it reports as it goes and that a cancel
  part way through leaves the rest of the tree alone.
- `python tools/preview.py --queue` renders the queue panel with invented jobs
  in every state it can be in, which is the only way to see whether they still
  tell apart when they are next to each other.

### Changed
- The status line readout counts a delete in items and a transfer in bytes,
  and names the job that is actually running rather than the first one that
  has not finished -- with holds in the queue those are no longer the same job.
- Pause is unavailable while a shell recycle is running, for the reason above.
- The dialog shown when the window is closed with work still going no longer
  says "transfers": it covers deletes now, and saying the wrong word at that
  particular moment is worse than saying a vaguer one.
- `timeout.delete` now applies only to a delete run through a worker, which is
  the elevated retry and the harness's own `delete` command. The Del key does
  not go near it.

## [0.13.0]

The rows a person recognises by their picture. Everything drawn in the listing
until now came from the association database, which is a fact about this
machine and therefore free; this release adds the few kinds whose picture is a
fact about the file, and spends a file read on each of them.

### Added
- **Programs, shortcuts and icon files draw their own icon.** Until now every
  row drew the picture for its type, which is right for a folder of drawings
  and useless for a folder of installers: twenty rows of the same generic
  executable icon, and the only way to tell them apart is to read the names.
  An `.exe`, `.lnk`, `.ico`, `.cur`, `.scr`, `.msc`, `.cpl` or `.url` now shows
  what is inside it.

  This is the second request in the application that opens a file, after the
  overlay badges, and it is bounded the same way with one addition that does
  most of the work: **only those kinds are ever asked about**. A folder of
  50,000 drawings sends nothing at all and costs exactly what it did before.
  Beyond that it is the rows on screen rather than the folder, one request per
  folder against that folder's own worker, a short deadline, and one picture
  per distinct picture -- a Start-menu folder of forty shortcuts to the same
  program is one image, not forty.

  What is remembered is keyed on the file's own modified time and size, which
  is where this differs from the badges and the difference is worth stating: a
  badge changes while the file does not, so badges are thrown away whenever a
  folder is listed again. An icon is inside the file and cannot change unless
  the file does -- so a rebuilt program shows its new icon the moment its new
  row arrives, and refreshing a folder that has not changed reads nothing.

  `View > Icons from the file itself` turns it off, which is the switch to
  reach for in a folder of programs on a share that has gone slow. With it off
  every row draws as its kind again, exactly as before.
- `tools\diagnose_font.py` runs the application with two hooks in it and
  writes `Claude outputs\font-diagnosis.txt`: every font-size warning Qt
  prints, with the Python stack that was running at the time, and every call
  that asks a font for a size of zero or less. Qt prints those warnings from
  C++ with no stack of their own, which is otherwise nothing to go on.
- `python -m app.io.harness fileicons <folder>` reports how many of a folder's
  rows could carry their own icon, how many answered, how many distinct
  pictures came back, and what the whole thing cost. The first number is the
  one to watch on an ordinary folder: it should be zero.

### Fixed
- **The age chip follows the density again.** It is meant to draw one step
  smaller than the row it sits in, and it stepped down in *points* -- but
  every font here is sized in pixels, because the sheet says `13px` and Qt
  honours that as pixels. A pixel-sized font answers -1 when it is asked for
  its point size, so the step landed on the floor of the calculation and the
  chip drew at the same small fixed size at every density. It now steps in
  whichever unit the font was actually set in.

## [0.12.0]

The navigation rail, and the chevrons in the path bar becoming targets. Both
were named in 0.11 and deliberately left out of it, because neither is chrome:
a rail has to decide which pane a click lands in, and a chevron dropdown is a
scan of a folder over the network.

### Added
- **A navigation rail down the left of the window.** Places, drives and the
  saved folders in one column, so the place you are going to is somewhere you
  can read rather than something you have to remember. One click goes there, a
  middle click opens it in a tab behind, and a right click on a saved folder
  moves, renames or removes it. Ctrl+B folds the whole rail away and brings it
  back; each heading folds on its own and stays folded; the width is whatever
  the splitter was left at.

  It goes to **the pane that has the keyboard**, which is the same rule Ctrl+1
  to Ctrl+9 have always followed -- and the reason nothing in the rail takes
  focus is so that clicking in it cannot change the answer. One rail rather
  than one per pane: the favorites bar is per-pane because it is a move into
  the pane it sits in, and a rail is the list you read before you decide.
- **Drives with capacity meters.** Each letter shows what it is -- a mapping
  shows the share it points at, `\\vault\projects` rather than "remote" --
  and a measured drive shows a bar and how much is left, in the warn colour
  past ninety per cent.

  **Only local fixed disks are measured without being asked**, and that is the
  whole design of it. `disk_usage` on a mapped drive whose server has gone is
  the thirty-second block this application exists to escape, and a rail
  opening is not somebody asking about a share. Any other drive is measured
  from its own right-click menu, and a drive that failed is remembered as
  having failed rather than asked again on every redraw.
- **Favorites in named groups.** A saved folder can sit under a heading --
  "Current job", "Standards" -- through the right-click menu on it in the
  rail, and the headings fold. A group is made by putting something in it and
  is gone when the last entry leaves, so there is no separate list of groups
  to fall out of step with the folders. A favourite saved before this release
  has no group, reads back exactly as it was written, and appears under one
  heading rather than none.
- **The chevrons in the path bar drop down what is inside the crumb to their
  left.** Along one folder is now a click rather than up, scroll, double
  click. This is the piece the 0.11 restyle drew and did not build: listing a
  sibling folder is a worker request, so it carries its own deadline and its
  own cancel, one is outstanding at a time and a second chevron withdraws the
  first at the worker, and the scan stops at two hundred names rather than
  enumerating the folder -- a dropdown that stops short says so. It opens
  saying it is looking, and fills in or says why it cannot.
- `python -m app.io.harness folders <path>` runs the same scan from the
  command line, with `--limit` to see the cap work. The number worth looking
  at on a large folder is that it comes back in the time a listing's first
  batch takes rather than the time the whole listing takes.

### Changed
- The drive picker in each pane is unchanged and stays: the rail is for
  reading and choosing, the picker is for the pane you are already in.

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
