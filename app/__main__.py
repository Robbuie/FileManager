"""Entry point. Builds the three layers in the only order they can go together.

The pool is started before the window and shut down after it, so a transfer or
a listing in flight is settled rather than orphaned when the window closes. The
worker processes are children of this process; leaving them behind would leave
a hung share's process running with nothing to answer to.

A staged update is the last thing that happens, after the window has gone and
the pool with it. An installer started any earlier would be replacing files
that this process still has open.
"""

from __future__ import annotations

import multiprocessing
import sys

#: The switch that makes this an elevated helper rather than the application.
#: Spelled out here rather than imported, because recognising it has to happen
#: before `app.io` is imported at all.
ELEVATED = "--elevated"


def main() -> int:
    # The elevated helper, before anything else and before Qt. A process
    # started with the `runas` verb to make one change in a protected folder
    # has no window, no pool and no settings: it does the one operation named
    # in its plan and exits. See `app/io/elevate.py` for why it is a second
    # process at all.
    if len(sys.argv) > 2 and sys.argv[1] == ELEVATED:
        from app.io.elevate import perform

        return perform(sys.argv[2])

    from PySide6.QtWidgets import QApplication

    from app import __version__

    from app.core.bridge import Bridge
    from app.core.config import Config
    from app.core.favorites import Favorites
    from app.core.icons import Icons
    from app.core.menu import ShellMenu
    from app.core.overlays import Overlays
    from app.core.pane import Pane
    from app.core.transfers import TransferQueue
    from app.core.updates import Updates
    from app.core.volumes import Volumes
    from app.io.pool import WorkerPool
    from app.io import paths
    from app.theme import sheet
    from app.ui.window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("File Manager")

    config = Config.load()
    sheet.apply(
        app,
        theme=config.get("theme"),
        accent=config.get("accent"),
        density=config.get("density"),
    )

    problem = paths.win32_problem()
    if problem:
        print(f"warning: {problem}", file=sys.stderr)

    pool = WorkerPool()
    bridge = Bridge(pool)
    icons = Icons(bridge, config)
    overlays = Overlays(bridge, config)
    # One shell menu for the window, not one per pane: there is one shell host
    # behind it and one menu on screen at a time whichever pane it belongs to.
    shell_menu = ShellMenu(bridge, config)
    left = Pane(bridge, config, "left", icons, overlays, shell_menu)
    right = Pane(bridge, config, "right", icons, overlays, shell_menu)
    volumes = Volumes(bridge, config)
    transfers = TransferQueue()
    updates = Updates(config, __version__)
    # One list for the window, not one per pane: a favourite is a place rather
    # than a side of the window.
    favorites = Favorites(config)

    window = MainWindow(config, left, right, volumes, transfers, updates,
                        favorites)
    window.show()

    # Both panes list only once there is a window to paint into. Nothing has
    # touched a volume before this line.
    left.refresh()
    right.refresh()
    volumes.refresh()
    # The screen is only knowable once there is one. A scaled display gets the
    # 32-pixel icons, which draw at the same size in the row and are the
    # difference between a crisp listing and a soft one.
    icons.start(scale=float(app.devicePixelRatio()))
    overlays.start(scale=float(app.devicePixelRatio()))
    updates.start_if_wanted()

    try:
        return app.exec()
    finally:
        transfers.shutdown()
        pool.shutdown()
        updates.install_staged()


if __name__ == "__main__":
    # The worker and ops processes are spawned, which re-runs this file. In a
    # frozen build that would re-run the application instead of the child;
    # `packaging/entry.py` calls this first there, and it is repeated here so
    # `python -m app` behaves the same way.
    multiprocessing.freeze_support()
    sys.exit(main())
