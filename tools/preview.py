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
           menu: bool = False) -> str:
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from app.core.bridge import Bridge
    from app.core.config import Config
    from app.core.favorites import Favorites
    from app.core.icons import Icons
    from app.core.overlays import Overlays
    from app.core.pane import Pane
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
    config.set("favorites", [{"name": "Jobs", "path": path},
                             {"name": "Drawings", "path": path}])
    sizes = FolderSizes(bridge, config)
    window = MainWindow(
        config,
        Pane(bridge, config, "left", icons, overlays, None, sizes),
        Pane(bridge, config, "right", icons, overlays, None, sizes),
        volumes, TransferQueue(), None, Favorites(config))
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
        # Two of these are dropped by the pane because it offers them itself,
        # which is the point of drawing them here.
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
    parser.add_argument("--menu", action="store_true",
                        help="open a context menu with invented shell entries, "
                             "to see how it draws")
    parser.add_argument("--all-themes", action="store_true",
                        help="one image per theme, to check the greys together")
    args = parser.parse_args(argv)

    if not args.all_themes:
        print(render(args.path, args.out, theme=args.theme, accent=args.accent,
                     density=args.density, width=args.width, height=args.height,
                     settle_ms=args.settle_ms, tabs=args.tabs, menu=args.menu))
        return 0

    from app.theme.tokens import THEMES
    os.makedirs(args.out_dir, exist_ok=True)
    for name in THEMES:
        out = os.path.join(args.out_dir, f"{name}.png")
        print(render(args.path, out, theme=name, accent=args.accent,
                     density=args.density, width=args.width, height=args.height,
                     settle_ms=args.settle_ms, tabs=args.tabs, menu=args.menu))
    return 0


if __name__ == "__main__":
    sys.exit(main())
