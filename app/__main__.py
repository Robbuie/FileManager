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
    from app.core.capacity import Capacity
    from app.core.config import Config
    from app.core.favorites import Favorites
    from app.core.fileicons import FileIcons
    from app.core.icons import Icons
    from app.core.menu import ShellMenu
    from app.core.overlays import Overlays
    from app.core.pane import Pane
    from app.core.previews import Previews
    from app.core.siblings import Siblings
    from app.core.sizes import FolderSizes
    from app.core.thumbnails import Thumbnails
    from app.core.clipboard import Clipboard
    from app.core.commands import Commands
    from app.core.network import Network
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
    # Before the sheet and before the window: a translucent window has to be
    # made translucent, and the sheet has to know whether to paint the
    # backdrop at all.
    from app.core.backdrop import choose
    from app.ui.winframe import probe

    backdrop, _why = choose(config.get("window.backdrop"), probe())
    if config.get("window.frame") == "system":
        # Mica behind a system title bar and a menu bar is a different
        # window from the one glass was designed for. Solid there.
        backdrop = "solid"
    sheet.apply(
        app,
        theme=config.get("theme"),
        accent=config.get("accent"),
        density=config.get("density"),
        backdrop=backdrop,
    )

    problem = paths.win32_problem()
    if problem:
        print(f"warning: {problem}", file=sys.stderr)

    pool = WorkerPool()
    bridge = Bridge(pool)
    icons = Icons(bridge, config)
    overlays = Overlays(bridge, config)
    # The pictures inside executables and shortcuts. One cache for the window
    # like the other two: what an .exe looks like does not depend on which
    # pane is looking at it, and the cache is what stops the second pane
    # reading the file again.
    file_icons = FileIcons(bridge, config)
    # One shell menu for the window, not one per pane: there is one shell host
    # behind it and one menu on screen at a time whichever pane it belongs to.
    shell_menu = ShellMenu(bridge, config)
    # One queue for the window: a folder walk holds that volume's worker, so
    # running one at a time has to mean one at a time across every tab.
    sizes = FolderSizes(bridge, config)
    # One scan for the window, for the shell menu's reason: one breadcrumb
    # dropdown is open at a time whichever pane it hangs off.
    siblings = Siblings(bridge, config)
    # Before the panes, because a pane holds it: there is one queue in the
    # application and a delete goes into it the same way a copy does.
    transfers = TransferQueue()
    # Before the panes for the queue's reason and one of its own: there is one
    # system clipboard, so a file cut in one pane has to be greyed in the
    # other, and both panes paste from the same place.
    clipboard = Clipboard()
    # One decode outstanding for the window, for the siblings' reason: an
    # abandoned read still holds the volume the next one wants, so moving the
    # cursor in one pane has to cancel the other pane's read rather than queue
    # behind it.
    previews = Previews(bridge, config)
    # One picture cache for the window, for the icons' reason: what a photograph
    # looks like at 128 pixels does not depend on which pane is looking at it.
    thumbnails = Thumbnails(bridge, config)
    left = Pane(bridge, config, "left", icons, overlays, shell_menu, sizes,
                siblings, file_icons=file_icons, transfers=transfers,
                clipboard=clipboard, previews=previews, thumbnails=thumbnails)
    right = Pane(bridge, config, "right", icons, overlays, shell_menu, sizes,
                 siblings, file_icons=file_icons, transfers=transfers,
                 clipboard=clipboard, previews=previews, thumbnails=thumbnails)
    volumes = Volumes(bridge, config)
    # The drive meters in the rail. It measures local fixed disks only unless
    # somebody asks for more, which is what keeps a rail from probing a server.
    capacity = Capacity(bridge, config)
    updates = Updates(config, __version__)
    # One list for the window, not one per pane: a favourite is a place rather
    # than a side of the window.
    favorites = Favorites(config)
    # One table for the window: a command is a program and a key, not a
    # property of a side. Which pane `%P` means is decided when the key is
    # pressed, by the pane that answered it.
    commands = Commands(bridge, config)
    # What this session can reach that has no drive letter. One for the window
    # like the drive list, and asked for the same way: the table it reads is
    # local, so this costs nothing and touches no server.
    network = Network(bridge, config)

    window = MainWindow(config, left, right, volumes, transfers, updates,
                        favorites, capacity, commands, network, backdrop=backdrop)
    window.show()

    # Both panes list only once there is a window to paint into. Nothing has
    # touched a volume before this line.
    left.refresh()
    right.refresh()
    # And from then on, keep the folders on screen current. See `Pane.check`.
    left.start_checks()
    right.start_checks()
    volumes.refresh()
    network.refresh()
    # The screen is only knowable once there is one. A scaled display gets the
    # 32-pixel icons, which draw at the same size in the row and are the
    # difference between a crisp listing and a soft one.
    icons.start(scale=float(app.devicePixelRatio()))
    overlays.start(scale=float(app.devicePixelRatio()))
    file_icons.start(scale=float(app.devicePixelRatio()))
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
