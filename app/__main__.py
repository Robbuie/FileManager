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


def main() -> int:
    from PySide6.QtWidgets import QApplication

    from app import __version__

    from app.core.bridge import Bridge
    from app.core.config import Config
    from app.core.icons import Icons
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
    left = Pane(bridge, config, "left", icons)
    right = Pane(bridge, config, "right", icons)
    volumes = Volumes(bridge, config)
    transfers = TransferQueue()
    updates = Updates(config, __version__)

    window = MainWindow(config, left, right, volumes, transfers, updates)
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
