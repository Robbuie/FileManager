"""Run the application and record who asks Qt for a font size of zero.

`QFont::setPointSize: Point size <= 0 (0), must be greater than 0` is a warning
Qt prints on stderr and then ignores the call, so the text it was about draws
at whatever size the font already had. Nothing crashes and nothing says where
it came from, which is why this file exists: the warning is printed by C++ and
carries no stack, and grepping the source for `setPointSize` finds one call
that cannot produce a zero.

So this runs the real application with two pieces of instrumentation and
writes what they catch to `Claude outputs\\font-diagnosis.txt`:

  * **A Qt message handler.** Every font warning is recorded with the Python
    stack that was on the interpreter at that moment. Even when the offending
    call is inside Qt, the stack says what this application was doing -- a
    paint, a menu being built, a widget being polished -- which is usually
    enough to name the widget.
  * **A wrapper around `QFont.setPointSize`, `setPointSizeF` and
    `setPixelSize`.** Any call with a value of zero or less is recorded the
    same way, whether or not Qt warns about it. If this catches something, the
    caller is Python and the stack names the line; if only the message handler
    fires, the call came from Qt or from the style, and the stack still says
    what it was doing at the time.

Use it the way the warning happens: start it, work normally until the console
shows the warning, then close the window. The file is written as it goes, so a
crash still leaves the record.

    python tools\\diagnose_font.py

Nothing here changes a file and nothing is sent anywhere. It is the ordinary
application with two hooks in it, and the settings file it reads and writes is
the real one -- so tabs, favourites and the rail are exactly as they were.
"""

from __future__ import annotations

import os
import sys
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

OUT_DIR = os.path.join(ROOT, "Claude outputs")
OUT = os.path.join(OUT_DIR, "font-diagnosis.txt")

#: One entry per distinct stack, so a warning printed on every repaint is one
#: entry with a count rather than four thousand copies of the same thing.
_seen: dict[str, dict] = {}


def _record(kind: str, detail: str) -> None:
    """Keep this stack, or count it again if it has been here before."""
    stack = "".join(traceback.format_stack()[:-1])
    key = f"{kind}\n{detail}\n{stack}"
    entry = _seen.get(key)
    if entry is None:
        _seen[key] = {"kind": kind, "detail": detail, "stack": stack, "count": 1}
        _write()
        return
    entry["count"] += 1
    # Written again every so often rather than every time: the count is worth
    # having, and rewriting the file on every repaint is not.
    if entry["count"] in (2, 10, 100, 1000):
        _write()


def _write() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as handle:
        handle.write("Font size diagnosis\n")
        handle.write("=" * 70 + "\n\n")
        handle.write(_context())
        if not _seen:
            handle.write("\nNothing caught yet.\n")
            return
        handle.write(f"\n{len(_seen)} distinct place(s) caught.\n\n")
        for index, entry in enumerate(_seen.values(), start=1):
            handle.write("-" * 70 + "\n")
            handle.write(f"[{index}] {entry['kind']}  x{entry['count']}\n")
            handle.write(f"    {entry['detail']}\n\n")
            handle.write(entry["stack"])
            handle.write("\n")


def _context() -> str:
    """What the application thinks its fonts are, which is half the answer.

    A font carries a size in points or in pixels and answers -1 for the one it
    is not using. Reading both off the real application font says which unit
    the sheet actually produced, and the token values say what it was asked
    for.
    """
    lines = []
    try:
        from PySide6.QtWidgets import QApplication

        from app.core.config import Config
        from app.theme import sheet

        app = QApplication.instance()
        if app is not None:
            font = app.font()
            lines.append(f"application font: family={font.family()!r} "
                         f"pointSizeF={font.pointSizeF()} pixelSize={font.pixelSize()}")
        config = Config.load()
        density = config.get("density")
        tokens = sheet.tokens(config.get("theme"), config.get("accent"), density)
        lines.append(f"density: {density}")
        for name in ("ui_font", "head_font"):
            lines.append(f"  {name}: {tokens.get(name)!r}")
    except Exception as exc:  # noqa: BLE001 - the report is the output
        lines.append(f"[context unavailable: {exc}]")
    return "\n".join(lines) + "\n"


def _install() -> None:
    from PySide6.QtCore import qInstallMessageHandler
    from PySide6.QtGui import QFont

    def handler(mode, context, message):
        text = str(message)
        # Printed as well as recorded, so the console still reads the way it
        # did without this file wrapped around it.
        print(text, file=sys.stderr)
        if "size" in text and ("Point" in text or "Pixel" in text):
            _record("Qt warning", text)

    qInstallMessageHandler(handler)

    def wrap(name):
        original = getattr(QFont, name)

        def patched(self, value, *rest):
            if value is not None and value <= 0:
                _record("QFont." + name, f"called with {value!r}")
            return original(self, value, *rest)

        setattr(QFont, name, patched)

    for name in ("setPointSize", "setPointSizeF", "setPixelSize"):
        try:
            wrap(name)
        except Exception as exc:  # noqa: BLE001 - the handler alone still works
            print(f"could not wrap QFont.{name}: {exc}", file=sys.stderr)


def main() -> int:
    _install()
    _write()
    print(f"Recording to {OUT}")
    print("Use the window normally until the warning appears, then close it.")
    from app.__main__ import main as run

    try:
        return run()
    finally:
        _write()
        print(f"\nWrote {OUT}")


if __name__ == "__main__":
    import multiprocessing

    # The pool and the transfer engine spawn processes, which re-run this file.
    multiprocessing.freeze_support()
    sys.exit(main())
