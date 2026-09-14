"""Run the application and record who asks Qt for a font size of zero.

`QFont::setPointSize: Point size <= 0 (0), must be greater than 0` is a warning
Qt prints on stderr and then ignores the call, so the text it was about draws
at whatever size the font already had. Nothing crashes and nothing says where
it came from, which is why this file exists.

What the first version of this file established, and what it cost:

  * The warning is printed from C++, so the Python stack recorded beside it
    was `app.exec()` and nothing else. Twenty-two warnings, one useless stack.
  * The `QFont` setters were wrapped and caught nothing, so no line in this
    application makes the call.
  * Every font in this application is pixel-sized -- the sheet writes
    `font-size: 13px` and Qt honours that as pixels -- and a pixel-sized font
    answers **-1** when it is asked for its point size. That is the whole
    mechanism: something reads `pointSize()` off one of these fonts, does
    arithmetic on the -1 it gets, and writes the result back. `-1 + 1` is the
    `(0)` printed twenty-two times; the `(-1)` is the value going back
    untouched.
  * It does not reproduce offscreen on Linux under the Fusion style, with the
    window, the rail, the tabs, the menus, the queue panel and every dialog
    built. So the caller is the Windows style or a widget only Windows has,
    which is why this has to run on the user's machine rather than in a
    session.

So the question left is *which widget*, and a Python stack cannot answer it.
These four hooks can:

  * **A Qt message handler.** Every font warning is recorded once, with a
    count.
  * **A proxy style.** Qt calls the style to polish a widget and to measure
    one, and it passes the widget in. Recording the widget that is in hand at
    the moment the warning fires names it directly -- `QMenu`, `QHeaderView`,
    `DriveRow` -- rather than naming the event loop.
  * **Markers around the Python calls that lead into Qt's font handling**:
    applying a stylesheet, polishing, showing a widget, popping a menu. The
    innermost one still open when the warning fires says what this application
    had asked for.
  * **A snapshot of what was on screen**: the active popup, the modal dialog,
    the focus widget, the active window, each with the size its font is
    carrying. A warning during a popup with a point-sized font in the report
    is the answer without further work.

Use it the way the warning happens: start it, work normally until the console
shows the warning, then close the window. The file is written as it goes, so a
crash still leaves the record.

    python tools\\diagnose_font.py

Nothing here changes a file and nothing is sent anywhere. It is the ordinary
application with hooks in it, and the settings file it reads and writes is the
real one -- so tabs, favourites and the rail are exactly as they were.
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

#: What this application asked Qt to do, innermost last. Pushed by the markers
#: and by the proxy style; read when a warning fires. A plain list rather than
#: a context manager stack because the style's calls are not nested in Python.
_doing: list[str] = []

#: The proxy style, kept here on purpose. `QApplication.setStyle` hands the
#: style to C++, and the Python half is then free to be collected -- at which
#: point the overrides stop being called and `app.style()` starts answering
#: with the base class, silently and with no error to read. Holding the
#: reference is what keeps the Python subclass alive as long as the
#: application. This was got wrong once here and the report looked fine.
_style = None


def _push(text: str) -> None:
    _doing.append(text)
    # A guard rather than a leak: a marker whose pop is skipped because Qt
    # raised through it would otherwise grow this without limit.
    if len(_doing) > 40:
        del _doing[:20]


def _pop() -> None:
    if _doing:
        _doing.pop()


def _describe(widget) -> str:
    """A widget, named the way the report needs it: class, name, font size."""
    if widget is None:
        return "none"
    try:
        name = type(widget).__name__
        obj = widget.objectName()
        role = widget.property("role")
        font = widget.font()
        bits = [name]
        if obj:
            bits.append(f"named {obj!r}")
        if role:
            bits.append(f"role={role!r}")
        bits.append(f"pixelSize={font.pixelSize()} pointSize={font.pointSize()}")
        return " ".join(bits)
    except Exception as exc:  # noqa: BLE001 - the report is the output
        return f"[undescribable: {exc}]"


def _on_screen() -> str:
    """What Qt had in hand when the warning fired, as far as it can be asked."""
    try:
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance()
        if app is None:
            return "    [no application]\n"
        lines = []
        for label, widget in (
            ("active popup", app.activePopupWidget()),
            ("active modal", app.activeModalWidget()),
            ("focus widget", app.focusWidget()),
            ("active window", app.activeWindow()),
        ):
            lines.append(f"    {label:<14} {_describe(widget)}")
        return "\n".join(lines) + "\n"
    except Exception as exc:  # noqa: BLE001 - the report is the output
        return f"    [screen state unavailable: {exc}]\n"


def _record(kind: str, detail: str) -> None:
    """Keep this occurrence, or count it again if it has been here before."""
    stack = "".join(traceback.format_stack()[:-1])
    doing = "\n".join(f"    {step}" for step in _doing[-6:]) or "    [nothing]"
    screen = _on_screen()
    key = f"{kind}\n{detail}\n{doing}\n{screen}{stack}"
    entry = _seen.get(key)
    if entry is None:
        _seen[key] = {"kind": kind, "detail": detail, "doing": doing,
                      "screen": screen, "stack": stack, "count": 1}
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
            handle.write("  what this application had asked for:\n")
            handle.write(entry["doing"] + "\n\n")
            handle.write("  what was on screen:\n")
            handle.write(entry["screen"] + "\n")
            handle.write("  python stack (expect only the event loop):\n")
            handle.write(entry["stack"])
            handle.write("\n")


def _context() -> str:
    """What the application thinks its fonts are, which is half the answer.

    A font carries a size in points or in pixels and answers -1 for the one it
    is not using. Reading both off the real application font says which unit
    the sheet actually produced, and the token values say what it was asked
    for. A widget is read as well as the application, because the stylesheet
    is what turns a point-sized application font into a pixel-sized widget.
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
            # `QStyle.name()` rather than the class name: PySide re-wraps a
            # style owned by C++ as whatever base class it can find, so the
            # type says `QCommonStyle` for every style there is. `name()`
            # answers `windows11`, `windowsvista`, `fusion` -- and which one
            # it is is the suspect, so the imprecise answer is no use.
            base = _style.baseStyle() if _style is not None else app.style()
            lines.append(f"style: {base.name()}"
                         f"{'' if _style is not None else '  [proxy not installed]'}")
            widgets = [w for w in app.topLevelWidgets() if w.isVisible()]
            if widgets:
                lines.append(f"a real widget: {_describe(widgets[0])}")
        config = Config.load()
        density = config.get("density")
        tokens = sheet.tokens(config.get("theme"), config.get("accent"), density)
        lines.append(f"density: {density}")
        for name in ("ui_font", "head_font"):
            lines.append(f"  {name}: {tokens.get(name)!r}")
    except Exception as exc:  # noqa: BLE001 - the report is the output
        lines.append(f"[context unavailable: {exc}]")
    return "\n".join(lines) + "\n"


def _proxy_style(base):
    """A style that says which widget Qt has in hand.

    Qt hands the style the widget for both of the calls that resolve a font --
    polishing one, and measuring one -- so a marker pushed around those two is
    the name the Python stack cannot give. Only those two are wrapped: the
    drawing calls run tens of thousands of times in a listing and a Python
    frame on each of them would make the application too slow to use, which
    would change the thing being measured.
    """
    from PySide6.QtWidgets import QProxyStyle, QWidget

    class Watch(QProxyStyle):
        def polish(self, target):
            if isinstance(target, QWidget):
                _push(f"style polishing {_describe(target)}")
                try:
                    return super().polish(target)
                finally:
                    _pop()
            return super().polish(target)

        def sizeFromContents(self, kind, option, size, widget=None):
            if widget is None:
                return super().sizeFromContents(kind, option, size, widget)
            _push(f"style measuring {kind} on {_describe(widget)}")
            try:
                return super().sizeFromContents(kind, option, size, widget)
            finally:
                _pop()

    return Watch(base)


def _mark(owner, name: str, label) -> None:
    """Wrap one Python method so the report knows it was running."""
    original = getattr(owner, name, None)
    if original is None:
        return

    def patched(self, *args, **kwargs):
        _push(label(self, args))
        try:
            return original(self, *args, **kwargs)
        finally:
            _pop()

    try:
        setattr(owner, name, patched)
    except Exception as exc:  # noqa: BLE001 - the other hooks still work
        print(f"could not wrap {owner.__name__}.{name}: {exc}", file=sys.stderr)


def _install() -> None:
    from PySide6.QtCore import qInstallMessageHandler
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QApplication, QMenu, QWidget

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

    # The four Python calls that lead into Qt's font handling. A stylesheet is
    # the one that matters most: applying it is when Qt resolves every font in
    # the tree, and this application applies one at startup and again on every
    # theme, accent or density change.
    _mark(QWidget, "setStyleSheet", lambda w, a: f"setStyleSheet on {_describe(w)}")
    _mark(QWidget, "ensurePolished", lambda w, a: f"ensurePolished {_describe(w)}")
    _mark(QWidget, "setFont", lambda w, a: f"setFont on {_describe(w)}")
    _mark(QMenu, "popup", lambda m, a: f"popup {_describe(m)}")
    _mark(QMenu, "exec", lambda m, a: f"exec {_describe(m)}")

    # The proxy style has to be in place before the window is built, because
    # polishing happens on the way to the first show. Patching the constructor
    # is the only seam early enough that does not mean editing the application.
    original_init = QApplication.__init__

    def patched_init(self, *args, **kwargs):
        global _style
        original_init(self, *args, **kwargs)
        try:
            _style = _proxy_style(self.style())
            self.setStyle(_style)
            if not isinstance(self.style(), type(_style)):
                print("the proxy style did not take", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001 - the other hooks still work
            print(f"could not install the proxy style: {exc}", file=sys.stderr)

    try:
        QApplication.__init__ = patched_init
    except Exception as exc:  # noqa: BLE001 - the other hooks still work
        print(f"could not wrap QApplication: {exc}", file=sys.stderr)


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
