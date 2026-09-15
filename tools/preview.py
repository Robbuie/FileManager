"""Render the window to a PNG without a screen.

This exists because of a line in CLAUDE.md: Claude cannot test this app, no
rendering to check. That is true of behaviour and stays true, but it does not
have to be true of the look. Qt's offscreen platform draws the real window with
the real stylesheet into an image, which is enough to catch the things that are
obvious to a pair of eyes and invisible in a diff -- a token that did not
apply, a column that collapsed, chrome that does not match the other two apps.

It runs the real stack: real pool, real workers, real listing. Only the screen
is missing.

    python tools/preview.py --path C:\\Windows\\System32 --out preview.png
    python tools/preview.py --all-themes --out-dir previews
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def render(path: str, out: str, *, theme: str, accent: str, density: str,
           width: int, height: int, settle_ms: int, tabs: int = 1,
           menu: bool = False, queue: bool = False, cut: bool = False,
           pane_preview: bool = False, grid: bool = False,
           viewer: str = "", cell: int = 128, commands: bool = False) -> str:
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from app.core.bridge import Bridge
    from app.core.clipboard import Clipboard
    from app.core.capacity import Capacity
    from app.core.config import Config
    from app.core.favorites import Favorites
    from app.core.icons import Icons
    from app.core.overlays import Overlays
    from app.core.pane import Pane
    from app.core.previews import Previews
    from app.core.siblings import Siblings
    from app.core.sizes import FolderSizes
    from app.core.thumbnails import Thumbnails
    from app.core.transfers import TransferQueue
    from app.core.network import Location, Network
    from app.core.volumes import Volumes
    from app.io.pool import WorkerPool
    from app.theme import sheet
    from app.ui.window import MainWindow

    app = QApplication.instance() or QApplication(sys.argv[:1])

    config = Config({}, path=os.devnull)
    config.set("theme", theme)
    config.set("accent", accent)
    config.set("density", density)
    config.set("left.path", path)
    config.set("right.path", path)
    sheet.apply(app, theme=theme, accent=accent, density=density)

    pool = WorkerPool()
    bridge = Bridge(pool)
    icons = Icons(bridge, config)
    overlays = Overlays(bridge, config)
    volumes = Volumes(bridge, config)
    # Two invented entries, for the same reason the menu preview invents shell
    # commands: what is being looked at is the shape of the menu, and that has
    # nothing to do with which folders this machine has.
    # Two of them grouped, so the rail's headings are in the picture. What is
    # being looked at is the shape of the sections, which has nothing to do
    # with which folders this machine has.
    config.set("favorites", [
        # Pointed somewhere other than the folder on screen, so that the rail's
        # mark on the folder a pane is standing on is legible in the picture
        # rather than being on every row at once.
        {"name": name, "path": path + "/" + name.lower()} for name in
        ("Jobs", "Drawings", "Standards", "Scans", "Archive 2025",
         "Templates", "Downloads", "Site photos")
    ] + [
        {"name": name, "path": path + "/" + name.lower(),
         "group": "Current job"} for name in ("Survey", "Markups")
    ])
    sizes = FolderSizes(bridge, config)
    siblings = Siblings(bridge, config)
    clipboard = Clipboard()
    capacity = Capacity(bridge, config)
    config.set("preview.pane", bool(pane_preview))
    config.set("preview.thumb_size", int(cell))
    if grid:
        config.set("left.view", "grid")
        config.set("right.view", "grid")
    previews = Previews(bridge, config)
    thumbnails = Thumbnails(bridge, config)
    window = MainWindow(
        config,
        Pane(bridge, config, "left", icons, overlays, None, sizes, siblings,
             clipboard=clipboard, previews=previews, thumbnails=thumbnails),
        Pane(bridge, config, "right", icons, overlays, None, sizes, siblings,
             clipboard=clipboard, previews=previews, thumbnails=thumbnails),
        volumes, TransferQueue(), None, Favorites(config), capacity,
        None, _invented_network(bridge, config))
    volumes.refresh()
    icons.start()
    overlays.start()
    window.resize(width, height)
    window.show()

    for pane in window._panes:  # noqa: SLF001 - a development tool, not the app
        for _ in range(max(0, tabs - 1)):
            pane.open_tab()
        pane.refresh()

    # Long enough for a listing to arrive and the view to lay out. There is no
    # event to wait on that means "painted": the alternative is a screenshot of
    # an empty table, which would look like a bug that is not there.
    QTimer.singleShot(settle_ms, app.quit)
    app.exec()

    if cut:
        _cut_some_rows(window, clipboard)
        QTimer.singleShot(200, app.quit)
        app.exec()

    if grid:
        _fill_grid(window, thumbnails, cell)
        QTimer.singleShot(300, app.quit)
        app.exec()

    if pane_preview:
        _fill_preview(window)
        QTimer.singleShot(300, app.quit)
        app.exec()

    if viewer:
        panel = _show_viewer(window, viewer)
        QTimer.singleShot(600, app.quit)
        app.exec()
        image = panel.grab()
        image.save(out)
        pool.shutdown()
        return out

    if commands:
        panel = _show_commands(window)
        QTimer.singleShot(400, app.quit)
        app.exec()
        image = panel.grab()
        image.save(out)
        pool.shutdown()
        return out

    if queue:
        panel = _show_queue(window)
        QTimer.singleShot(400, app.quit)
        app.exec()
        image = panel.grab()
        image.save(out)
        pool.shutdown()
        return out

    popup = None
    if menu:
        popup = _show_menu(window)
        QTimer.singleShot(400, app.quit)
        app.exec()

    image = window.grab()
    if popup is not None:
        # The menu is a window of its own, so grabbing the main one does not
        # include it. Drawn on afterwards, where it actually is, which is the
        # only way to see it and the pane it belongs to in one picture.
        from PySide6.QtGui import QPainter

        painter = QPainter(image)
        painter.drawPixmap(window.mapFromGlobal(popup.mapToGlobal(popup.rect().topLeft())),
                           popup.grab())
        painter.end()
    image.save(out)
    pool.shutdown()
    return out


def _invented_network(bridge, config):
    """A network section with contents, for the render only.

    Invented for `--queue`'s reason and one of its own. The real list comes
    from the redirector's table of current connections, and this machine has
    none -- so a render of the real thing would be a picture of an empty
    heading, which says nothing about the one case the section exists for.

    The rows are the two shapes that matter: a redirected drive with **no
    letter**, which is what a Hyper-V or Remote Desktop host share looks like
    and is the reason any of this was written, and an ordinary mapped share
    that does have one.
    """
    from app.core.network import Location, Network

    network = Network(bridge, config)
    network._connections = [          # noqa: SLF001 - a development tool
        Location(r"\\tsclient\C", label="C on tsclient"),
        Location(r"\\fileserver\Jobs", local="S:"),
    ]
    return network


def _cut_some_rows(window, clipboard) -> None:
    """Mark the first few rows of both panes as cut.

    Put straight into the models rather than on the real clipboard, for
    `_show_queue`'s reason: going through the clipboard would mean going
    through `paths.parent`, which is Windows-shaped, so on any other machine
    the picture would come back with nothing faded and look like the feature
    was broken. What is being looked at is the fade -- too faint and the name
    stops being readable on the dark themes, too strong and it does not read
    as cut at all -- and that has nothing to do with where the names came from.
    """
    class Marked:
        def __init__(self, names):
            self._names = frozenset(names)

        def cut_names(self, folder):  # noqa: ARG002 - one folder in a preview
            return self._names

    for pane in window._panes:  # noqa: SLF001 - a development tool, not the app
        model = pane.current.model
        names = [entry.name for entry in
                 (model.entry(row) for row in range(min(model.rowCount(), 8)))
                 if entry is not None][:3]
        model.set_cut(Marked(names))


def _invented_picture(edge: int, seed: int):
    """A picture that is obviously a picture and obviously not a real file's.

    Drawn rather than read, for `_show_queue`'s reason: what is being looked at
    is whether a grid of pictures reads as a grid -- the cell proportions, the
    air between them, whether the two lines of name are legible under one -- and
    that has nothing to do with which photographs this machine happens to have.
    A real folder of images would also make the render depend on the `--path`
    given, so the same command would produce a different picture on every
    machine.

    Each one is a different hue and carries a diagonal, so a cell that is
    stretched or squashed is visible rather than being a plausible flat square.
    """
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPen, QPixmap

    ratios = ((4, 3), (3, 4), (16, 9), (1, 1), (3, 2))
    wide, tall = ratios[seed % len(ratios)]
    scale = edge / max(wide, tall)
    picture = QPixmap(max(1, int(wide * scale)), max(1, int(tall * scale)))
    hue = (seed * 47) % 360
    wash = QLinearGradient(QPointF(0, 0), QPointF(picture.width(), picture.height()))
    wash.setColorAt(0.0, QColor.fromHsv(hue, 120, 210))
    wash.setColorAt(1.0, QColor.fromHsv((hue + 40) % 360, 160, 120))
    painter = QPainter(picture)
    painter.fillRect(picture.rect(), wash)
    painter.setPen(QPen(QColor(255, 255, 255, 90), 2))
    painter.drawLine(0, picture.height(), picture.width(), 0)
    painter.end()
    return picture


def _fill_grid(window, thumbnails, cell: int) -> None:
    """Put invented pictures into the grid's cache for the rows on screen.

    Straight into the cache rather than by letting the real request run, which
    off Windows would come back empty -- `paths.join` is Windows-shaped, so the
    worker would be asked about `\\tmp\\dec\\photo.jpg` and find nothing. The
    picture would then be a grid of icons, which is a real state the grid has
    and not the one this is for.

    Every third row is left without one, on purpose. A grid where some cells are
    pictures and some are the icon for their kind is the ordinary case in any
    real folder, and the thing worth checking is that the two sit together
    without the icons reading as cells that failed to load.
    """
    from app.core.thumbnails import _row_key

    for pane in window._panes:  # noqa: SLF001 - a development tool, not the app
        model = pane.current.model
        for row in range(min(model.rowCount(), 60)):
            entry = model.entry(row)
            if entry is None or entry.is_dir or row % 3 == 2:
                continue
            key = f"invented-{row}"
            thumbnails._images[key] = _invented_picture(cell, row)  # noqa: SLF001
            thumbnails._rows[_row_key(model.folder, entry.name)] = (  # noqa: SLF001
                entry.mtime, entry.size, key)
    thumbnails.changed.emit()


def _fill_preview(window) -> None:
    """Show an invented preview in each pane's panel: a picture on one side, text
    on the other.

    Both, because the two are the shapes that have to work at the same width and
    they look nothing alike -- a picture centred on a backdrop against forty
    lines of monospace -- and the question the render answers is whether a panel
    narrow enough to be worth having is wide enough for either.
    """
    from PySide6.QtCore import QBuffer, QByteArray

    from app.io.protocol import Preview, PreviewForm

    store = QByteArray()
    buffer = QBuffer(store)
    buffer.open(QBuffer.WriteOnly)
    _invented_picture(900, 3).save(buffer, "PNG")
    buffer.close()

    shapes = (
        Preview(form=PreviewForm.IMAGE, source="qt", size=4_182_355,
                image=bytes(store.data()), width=4032, height=3024, shown=1600),
        Preview(form=PreviewForm.TEXT, source="text", size=61_204,
                encoding="utf-8", lines=1840, truncated=True,
                text="\n".join([
                    "; drawing register  rev C",
                    "; exported 2026-09-14",
                    "",
                    "sheet,title,scale,revision,issued",
                    "A-101,Site plan,1:500,C,2026-08-11",
                    "A-102,Ground floor,1:100,C,2026-08-11",
                    "A-103,First floor,1:100,B,2026-07-29",
                    "A-201,North elevation,1:100,C,2026-08-11",
                    "A-202,South elevation,1:100,C,2026-08-11",
                    "A-301,Section A-A,1:50,A,2026-06-02",
                    "S-101,Foundation layout,1:100,D,2026-09-01",
                ] * 4)),
    )
    for widget, shape in zip(window._widgets, shapes):  # noqa: SLF001
        name = "DSC_4417.jpg" if shape.form is PreviewForm.IMAGE \
            else "register-rev-C.csv"
        widget._preview.show_preview(f"S:\\Jobs\\24-118\\{name}",  # noqa: SLF001
                                     name, shape)


def _show_viewer(window, what: str):
    """Open the viewer on an invented file of the kind asked for.

    The three forms are three different windows to look at -- a picture with its
    zoom readout, text with its encoding, a hex dump -- and each has its own way
    of going wrong: a picture that is not centred, text at a size that does not
    fit the mono column, a dump whose character column is cut off.

    Pushed into the viewer's own handler rather than through a real decode, for
    `_fill_grid`'s reason: what is on this machine should not decide what the
    picture shows.
    """
    from PySide6.QtCore import QBuffer, QByteArray

    from app.io.protocol import Preview, PreviewForm
    from app.ui.viewer import Viewer

    previews = window._panes[0].previews  # noqa: SLF001
    viewer = Viewer(previews, window._tokens, window)  # noqa: SLF001
    window._viewer = viewer  # noqa: SLF001

    if what == "text":
        name = "install.log"
        shape = Preview(form=PreviewForm.TEXT, source="text", size=284_117,
                        encoding="cp1252", lines=6_240, truncated=True,
                        text="\n".join(
                            f"2026-09-{11 + i % 3:02d} 08:{i % 60:02d}:{(i * 7) % 60:02d}"
                            f"  worker[{i % 4}]  listed "
                            f"\\\\vault\\projects\\2026\\{i:04d} in "
                            f"{(i * 13) % 900}ms, {(i * 411) % 48000:,} rows"
                            for i in range(120)))
    elif what == "hex":
        name = "sensor.dat"
        shape = Preview(form=PreviewForm.HEX, source="hex", size=1_048_576,
                        data=bytes(range(256)) * 6)
    else:
        name = "DSC_4417.jpg"
        store = QByteArray()
        buffer = QBuffer(store)
        buffer.open(QBuffer.WriteOnly)
        _invented_picture(1600, 2).save(buffer, "PNG")
        buffer.close()
        shape = Preview(form=PreviewForm.IMAGE, source="qt", size=4_182_355,
                        image=bytes(store.data()), width=4032, height=2268,
                        shown=1600)

    viewer._folder = "S:\\Jobs\\24-118\\Site photos"  # noqa: SLF001
    viewer._names = [  # noqa: SLF001
        "DSC_4411.jpg", "DSC_4413.jpg", name, "install.log", "sensor.dat",
    ]
    viewer._at = 2  # noqa: SLF001
    viewer._name.setText(name)  # noqa: SLF001
    viewer._where.setText("3 of 5")  # noqa: SLF001
    viewer._on_ready(viewer.path, shape)  # noqa: SLF001
    viewer.resize(1040, 700)
    viewer.show()
    return viewer


def _show_queue(window):
    """The queue panel, with invented jobs in every state it can be in.

    The states are built straight into the queue's own tables rather than by
    submitting anything. Submitting would start the real ops process, which
    would then go looking for paths that do not exist on this machine and
    report them as failures a moment later -- so the picture would be of five
    jobs failing rather than of five jobs in five states.

    All five are put in at once because the thing worth seeing is whether they
    are still telling apart when they are next to each other: a bar that is
    moving, one that is held, one measured in items, one waiting and one done.
    """
    from app.core.transfers import JobState
    from app.io.protocol import JobKind

    queue = window._transfers  # noqa: SLF001 - a development tool, not the app

    def add(state: JobState) -> None:
        queue.jobs[state.id] = state
        queue.order.append(state.id)

    add(JobState(id=1, kind=JobKind.COPY, destination=r"D:\Archive\2025",
                 sources=(r"S:\Jobs\24-118\survey.zip",), state="running",
                 files=1180, total=4_100_000_000, done=1_650_000_000,
                 current="site-photos-north-elevation.jpg"))
    add(JobState(id=2, kind=JobKind.ERASE, destination="",
                 sources=(r"D:\Archive\2019\superseded",), state="running",
                 files=31_400, total=31_400, done=8_240,
                 current="sheet-A-104-rev-C.dwg"))
    add(JobState(id=3, kind=JobKind.MOVE, destination=r"S:\Jobs\24-118\sorted",
                 sources=(r"S:\Jobs\24-118\incoming",), state="queued",
                 files=94, held=True))
    add(JobState(id=4, kind=JobKind.RECYCLE, destination="",
                 sources=(r"D:\scratch\build",), state="running",
                 files=2_800, total=2_800, done=0, interruptible=False))
    add(JobState(id=5, kind=JobKind.COPY, destination=r"S:\Standards",
                 sources=(r"C:\Users\rjokr\Downloads\standards.pdf",),
                 state="done", files=1, copied=1))

    window._show_queue()  # noqa: SLF001
    panel = window._queue_dialog  # noqa: SLF001
    panel.refresh()
    panel.resize(max(560, window.width() - 200), 340)
    return panel


def _show_commands(window):
    """The commands editor, on the shipped table.

    Shown rather than executed: `exec` would block until somebody closed a
    dialog nobody can see, so it is shown and grabbed the way the queue panel
    is. What this is for is the form -- five fields, a list and two rows of
    buttons is the densest thing in this application, and a label that wraps
    badly or a field that does not line up is only visible in a picture.
    """
    from app.core import commands as table
    from app.ui.dialogs import CommandsEditor

    dialog = CommandsEditor(window, table.DEFAULTS)
    dialog.resize(680, 560)
    dialog.show()
    return dialog


def _show_menu(window) -> None:
    """Open a context menu on the left pane, with invented shell entries.

    The entries are invented on purpose: what this checks is the look of the
    menu -- the greys, the accent on the highlight, the shortcut column, how
    a submenu sits -- and that has nothing to do with which extensions this
    machine has. It uses the pane's own builder, so what is drawn here is what
    a right-click draws.
    """
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QMenu

    from app.io.protocol import (
        MENU_SEPARATOR,
        MENU_SUBMENU,
        MenuItem,
    )

    widget = window._widgets[0]  # noqa: SLF001 - a development tool, not the app
    items = [
        # Four of these are dropped by the pane because it offers them itself,
        # which is the point of drawing them here. Paste is in the list for
        # the same reason and is the one worth checking: the shell's paste has
        # no queue behind it.
        MenuItem(id=8, text="Open", verb="open", default=True),
        MenuItem(id=1, text="Open with Code"),
        MenuItem(id=0, kind=MENU_SEPARATOR),
        MenuItem(id=0, kind=MENU_SUBMENU, text="7-Zip", items=(
            MenuItem(id=2, text="Add to archive..."),
            MenuItem(id=3, text="Extract here"),
        )),
        MenuItem(id=0, kind=MENU_SUBMENU, text="TortoiseSVN", items=(
            MenuItem(id=4, text="Commit..."),
            MenuItem(id=5, text="Update"),
            MenuItem(id=6, text="Revert", enabled=False),
        )),
        MenuItem(id=0, kind=MENU_SEPARATOR),
        MenuItem(id=9, text="Cut", verb="cut"),
        MenuItem(id=10, text="Copy", verb="copy"),
        MenuItem(id=12, text="Paste", verb="paste"),
        MenuItem(id=13, text="Copy as path", verb="copyaspath"),
        MenuItem(id=11, text="Rename", verb="rename"),
        MenuItem(id=7, text="Properties", verb="properties"),
    ]
    menu = QMenu(widget)
    menu.setToolTipsVisible(True)
    widget._add_verbs(menu, ["plan.dwg"], on_row=True)  # noqa: SLF001
    menu.addSeparator()
    widget._fill(menu, items, 1, top=True)  # noqa: SLF001
    # `popup`, not `exec`: this has to return so the image can be grabbed.
    menu.popup(widget.mapToGlobal(widget.rect().topLeft()) + QPoint(80, 120))
    return menu


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--path", default=os.getcwd())
    parser.add_argument("--out", default="preview.png")
    parser.add_argument("--out-dir", default=".")
    parser.add_argument("--theme", default="dark")
    parser.add_argument("--accent", default="blue")
    parser.add_argument("--density", default="normal")
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=760)
    parser.add_argument("--settle-ms", type=int, default=1500)
    parser.add_argument("--tabs", type=int, default=1,
                        help="open this many tabs per pane, to see the strip")
    parser.add_argument("--commands", action="store_true",
                        help="the commands editor rather than the window")
    parser.add_argument("--queue", action="store_true",
                        help="render the queue panel instead of the window, "
                             "with invented jobs in every state")
    parser.add_argument("--menu", action="store_true",
                        help="open a context menu with invented shell entries, "
                             "to see how it draws")
    parser.add_argument("--cut", action="store_true",
                        help="put the first few rows on the clipboard as a cut, "
                             "to see how faded they are against the rest")
    parser.add_argument("--preview-pane", action="store_true",
                        help="open the preview panel in both panes, with an "
                             "invented picture on one side and text on the other")
    parser.add_argument("--grid", action="store_true",
                        help="both panes in grid view, with invented pictures in "
                             "two cells of every three")
    parser.add_argument("--cell", type=int, default=128, metavar="PX",
                        help="cell size for --grid")
    parser.add_argument("--viewer", default="", choices=["", "image", "text", "hex"],
                        help="render the viewer instead of the window, on an "
                             "invented file of this kind")
    parser.add_argument("--all-themes", action="store_true",
                        help="one image per theme, to check the greys together")
    args = parser.parse_args(argv)

    if not args.all_themes:
        print(render(args.path, args.out, theme=args.theme, accent=args.accent,
                     density=args.density, width=args.width, height=args.height,
                     settle_ms=args.settle_ms, tabs=args.tabs, menu=args.menu,
                     queue=args.queue, cut=args.cut, grid=args.grid,
                     commands=args.commands,
                     pane_preview=args.preview_pane, viewer=args.viewer,
                     cell=args.cell))
        return 0

    from app.theme.tokens import THEMES
    os.makedirs(args.out_dir, exist_ok=True)
    for name in THEMES:
        out = os.path.join(args.out_dir, f"{name}.png")
        print(render(args.path, out, theme=name, accent=args.accent,
                     density=args.density, width=args.width, height=args.height,
                     settle_ms=args.settle_ms, tabs=args.tabs, menu=args.menu,
                     queue=args.queue, cut=args.cut, grid=args.grid,
                     commands=args.commands,
                     pane_preview=args.preview_pane, viewer=args.viewer,
                     cell=args.cell))
    return 0


if __name__ == "__main__":
    sys.exit(main())
