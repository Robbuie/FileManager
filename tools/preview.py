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
           width: int, height: int, settle_ms: int, tabs: int = 1) -> str:
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from app.core.bridge import Bridge
    from app.core.config import Config
    from app.core.icons import Icons
    from app.core.pane import Pane
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
    volumes = Volumes(bridge, config)
    window = MainWindow(config, Pane(bridge, config, "left", icons),
                        Pane(bridge, config, "right", icons), volumes, TransferQueue())
    volumes.refresh()
    icons.start()
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

    window.grab().save(out)
    pool.shutdown()
    return out


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
    parser.add_argument("--all-themes", action="store_true",
                        help="one image per theme, to check the greys together")
    args = parser.parse_args(argv)

    if not args.all_themes:
        print(render(args.path, args.out, theme=args.theme, accent=args.accent,
                     density=args.density, width=args.width, height=args.height,
                     settle_ms=args.settle_ms, tabs=args.tabs))
        return 0

    from app.theme.tokens import THEMES
    os.makedirs(args.out_dir, exist_ok=True)
    for name in THEMES:
        out = os.path.join(args.out_dir, f"{name}.png")
        print(render(args.path, out, theme=name, accent=args.accent,
                     density=args.density, width=args.width, height=args.height,
                     settle_ms=args.settle_ms, tabs=args.tabs))
    return 0


if __name__ == "__main__":
    sys.exit(main())
