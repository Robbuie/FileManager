"""Entry point. Builds the three layers in the only order they can go together.

The pool is started before the window and shut down after it, so a transfer or
a listing in flight is settled rather than orphaned when the window closes. The
worker processes are children of this process; leaving them behind would leave
a hung share's process running with nothing to answer to.
"""

from __future__ import annotations

import sys


def main() -> int:
    from PySide6.QtWidgets import QApplication

    from app.core.bridge import Bridge
    from app.core.config import Config
    from app.core.pane import Pane
    from app.core.transfers import TransferQueue
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
    left = Pane(bridge, config, "left")
    right = Pane(bridge, config, "right")
    volumes = Volumes(bridge, config)
    transfers = TransferQueue()

    window = MainWindow(config, left, right, volumes, transfers)
    window.show()

    # Both panes list only once there is a window to paint into. Nothing has
    # touched a volume before this line.
    left.refresh()
    right.refresh()
    volumes.refresh()

    try:
        return app.exec()
    finally:
        transfers.shutdown()
        pool.shutdown()


if __name__ == "__main__":
    sys.exit(main())
