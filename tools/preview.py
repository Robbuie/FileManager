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
           menu: bool = False, queue: bool = False, cut: bool = False) -> str:
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
    from app.core.siblings import Siblings
    from app.core.sizes import FolderSizes
    from app.core.transfers import TransferQueue
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
    window = MainWindow(
        config,
        Pane(bridge, config, "left", icons, overlays, None, sizes, siblings,
             clipboard=clipboard),
        Pane(bridge, config, "right", icons, overlays, None, sizes, siblings,
             clipboard=clipboard),
        volumes, TransferQueue(), None, Favorites(config), capacity)
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
    parser.add_argument("--queue", action="store_true",
                        help="render the queue panel instead of the window, "
                             "with invented jobs in every state")
    parser.add_argument("--menu", action="store_true",
                        help="open a context menu with invented shell entries, "
                             "to see how it draws")
    parser.add_argument("--cut", action="store_true",
                        help="put the first few rows on the clipboard as a cut, "
                             "to see how faded they are against the rest")
    parser.add_argument("--all-themes", action="store_true",
                        help="one image per theme, to check the greys together")
    args = parser.parse_args(argv)

    if not args.all_themes:
        print(render(args.path, args.out, theme=args.theme, accent=args.accent,
                     density=args.density, width=args.width, height=args.height,
                     settle_ms=args.settle_ms, tabs=args.tabs, menu=args.menu,
                     queue=args.queue, cut=args.cut))
        return 0

    from app.theme.tokens import THEMES
    os.makedirs(args.out_dir, exist_ok=True)
    for name in THEMES:
        out = os.path.join(args.out_dir, f"{name}.png")
        print(render(args.path, out, theme=name, accent=args.accent,
                     density=args.density, width=args.width, height=args.height,
                     settle_ms=args.settle_ms, tabs=args.tabs, menu=args.menu,
                     queue=args.queue, cut=args.cut))
    return 0


if __name__ == "__main__":
    sys.exit(main())
