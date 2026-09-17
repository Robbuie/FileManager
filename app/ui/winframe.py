"""The window's own title bar, made to behave like one Windows drew.

0.26 takes the system title bar and the menu bar away and draws a thin row of
its own. Anything that draws its own title bar has to give back what the system
one did for free, and the list is longer than it looks:

- dragging by empty space, Aero Snap, and double click to maximise;
- resizing from every edge and corner;
- the drop shadow and the minimise and maximise animations;
- **the snap layouts flyout** that Windows 11 shows when the pointer rests on
  the maximise button.

A `FramelessWindowHint` window on its own loses all of them. The approach here
is the one the mature frameless-window projects settled on: keep a *real*
caption-and-thick-frame window, so Windows still thinks it has a frame and keeps
the shadow, the animations and snapping, then answer `WM_NCCALCSIZE` so that the
frame takes no room, and answer `WM_NCHITTEST` so that the edges resize, the
empty part of our title bar is a caption, and our maximise button is
`HTMAXBUTTON`. That last one is the only thing Windows 11 looks at to decide
whether to show snap layouts, which is why the maximise button's clicks are
handled here rather than by Qt: a region Windows has been told is a caption
button never delivers ordinary mouse events to the client.

`WM_NCCALCSIZE` has one trap: a maximised window is positioned with its frame
hanging off the edges of the screen, so a client area that is the whole window
loses a few pixels on every side. The frame thickness is put back when zoomed.

Nothing in here touches a filesystem. The one read that is not a window call is
the personalisation value for transparency, which is a registry value in the
current user's hive -- local, instant, and the same thing Explorer reads.

Everything that is not a Windows call is a plain function at the bottom so the
region arithmetic can be tested anywhere.
"""

from __future__ import annotations

import sys

from app.core.backdrop import Machine

WM_NCCALCSIZE = 0x0083
WM_NCHITTEST = 0x0084
WM_NCMOUSEMOVE = 0x00A0
WM_NCLBUTTONDOWN = 0x00A1
WM_NCLBUTTONUP = 0x00A2
WM_NCLBUTTONDBLCLK = 0x00A3
WM_NCMOUSELEAVE = 0x02A2
WM_MOUSEMOVE = 0x0200

HTCLIENT = 1
HTCAPTION = 2
HTMAXBUTTON = 9
HTLEFT = 10
HTRIGHT = 11
HTTOP = 12
HTTOPLEFT = 13
HTTOPRIGHT = 14
HTBOTTOM = 15
HTBOTTOMLEFT = 16
HTBOTTOMRIGHT = 17

#: Logical pixels at each edge that resize rather than click.
BORDER = 6

_GWL_STYLE = -16
_WS_CAPTION = 0x00C00000
_WS_THICKFRAME = 0x00040000
_WS_SYSMENU = 0x00080000
_WS_MINIMIZEBOX = 0x00020000
_WS_MAXIMIZEBOX = 0x00010000
_SWP_FLAGS = 0x0002 | 0x0001 | 0x0004 | 0x0020  # NOMOVE NOSIZE NOZORDER FRAMECHANGED
_DWMWA_USE_IMMERSIVE_DARK_MODE = 20
_DWMWA_SYSTEMBACKDROP_TYPE = 38
_DWMSBT_MAINWINDOW = 2  # Mica
_SM_REMOTESESSION = 0x1000
_SM_CXSIZEFRAME = 32
_SM_CYSIZEFRAME = 33
_SM_CXPADDEDBORDER = 92


def region(x: float, y: float, width: float, height: float, *,
           maximized: bool, over_max: bool, over_caption: bool,
           border: int = BORDER) -> int:
    """Which part of the window a point is, in `WM_NCHITTEST`'s terms.

    Edges first, because a corner that is also a caption has to resize or
    there is no way to grab the top corners at all. A maximised window has no
    edges to grab: resizing one is what un-maximising is for.
    """
    if not maximized:
        left = x < border
        right = x >= width - border
        top = y < border
        bottom = y >= height - border
        if top and left:
            return HTTOPLEFT
        if top and right:
            return HTTOPRIGHT
        if bottom and left:
            return HTBOTTOMLEFT
        if bottom and right:
            return HTBOTTOMRIGHT
        if left:
            return HTLEFT
        if right:
            return HTRIGHT
        if top:
            return HTTOP
        if bottom:
            return HTBOTTOM
    if over_max:
        return HTMAXBUTTON
    if over_caption:
        return HTCAPTION
    return HTCLIENT


def probe() -> Machine:
    """Read the facts `core.backdrop.choose` decides from. Never raises."""
    if sys.platform != "win32":
        return Machine()
    build = 0
    remote = False
    transparency = True
    try:
        build = int(sys.getwindowsversion().build)
    except Exception:  # noqa: BLE001 - a probe answers, it does not fail
        pass
    try:
        import ctypes

        remote = bool(ctypes.windll.user32.GetSystemMetrics(_SM_REMOTESESSION))
    except Exception:  # noqa: BLE001
        pass
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        ) as key:
            value, _kind = winreg.QueryValueEx(key, "EnableTransparency")
            transparency = bool(value)
    except Exception:  # noqa: BLE001 - a missing value means the default, on
        pass
    return Machine(windows=True, build=build, remote=remote,
                   transparency=transparency)


class NativeFrame:
    """The Windows half of the custom title bar, attached to one window.

    `window` must provide `hit_parts(pos) -> (over_max, over_caption)` in its
    own logical coordinates, and `set_max_hover(bool)` for the button's look.
    Off Windows every method is a no-op, which is what lets the same window be
    rendered offscreen by `tools/preview.py` and built by the tests.
    """

    def __init__(self, window, *, glass: bool, dark: bool) -> None:
        self._window = window
        self._glass = glass
        self._dark = dark
        self._installed = False
        self.problem = ""

    @property
    def active(self) -> bool:
        return self._installed

    def install(self) -> None:
        if sys.platform != "win32" or self._installed:
            return
        try:
            import ctypes
            from ctypes import wintypes

            hwnd = wintypes.HWND(int(self._window.winId()))
            user32 = ctypes.windll.user32
            dwm = ctypes.windll.dwmapi
            user32.GetWindowLongW.restype = ctypes.c_long
            style = user32.GetWindowLongW(hwnd, _GWL_STYLE)
            user32.SetWindowLongW(hwnd, _GWL_STYLE, style | _WS_CAPTION
                                  | _WS_THICKFRAME | _WS_SYSMENU
                                  | _WS_MINIMIZEBOX | _WS_MAXIMIZEBOX)

            class MARGINS(ctypes.Structure):
                _fields_ = [("left", ctypes.c_int), ("right", ctypes.c_int),
                            ("top", ctypes.c_int), ("bottom", ctypes.c_int)]

            # -1 everywhere is "the whole window is frame", which is what Mica
            # needs to show through. Solid only needs one pixel of frame for
            # DWM to keep drawing the shadow.
            margins = MARGINS(-1, -1, -1, -1) if self._glass else MARGINS(0, 0, 1, 0)
            dwm.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(margins))
            dark = ctypes.c_int(1 if self._dark else 0)
            dwm.DwmSetWindowAttribute(hwnd, _DWMWA_USE_IMMERSIVE_DARK_MODE,
                                      ctypes.byref(dark), ctypes.sizeof(dark))
            if self._glass:
                kind = ctypes.c_int(_DWMSBT_MAINWINDOW)
                dwm.DwmSetWindowAttribute(hwnd, _DWMWA_SYSTEMBACKDROP_TYPE,
                                          ctypes.byref(kind), ctypes.sizeof(kind))
            user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, _SWP_FLAGS)
            self._installed = True
        except Exception as exc:  # noqa: BLE001 - a frame that will not attach
            # leaves an ordinary window, which is a worse look and a working
            # application. Said on the status line by the caller.
            self.problem = f"custom title bar unavailable: {exc}"

    def set_dark(self, dark: bool) -> None:
        """Mica tints itself from this, so a theme change has to reach it."""
        self._dark = dark
        if not self._installed:
            return
        try:
            import ctypes
            from ctypes import wintypes

            hwnd = wintypes.HWND(int(self._window.winId()))
            value = ctypes.c_int(1 if dark else 0)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, _DWMWA_USE_IMMERSIVE_DARK_MODE, ctypes.byref(value),
                ctypes.sizeof(value))
        except Exception:  # noqa: BLE001
            pass

    def handle(self, event_type, message) -> tuple[bool, int] | None:
        """Answer the non-client messages, or None to let Qt have it."""
        if not self._installed:
            return None
        from ctypes import wintypes

        msg = wintypes.MSG.from_address(int(message))
        kind = msg.message
        if kind == WM_NCCALCSIZE:
            return self._calcsize(msg)
        if kind == WM_NCHITTEST:
            from PySide6.QtGui import QCursor

            window = self._window
            pos = window.mapFromGlobal(QCursor.pos())
            over_max, over_caption = window.hit_parts(pos)
            return True, region(pos.x(), pos.y(), window.width(), window.height(),
                                maximized=window.isMaximized(),
                                over_max=over_max, over_caption=over_caption)
        if kind == WM_NCMOUSEMOVE:
            self._window.set_max_hover(msg.wParam == HTMAXBUTTON)
            return None
        if kind in (WM_NCMOUSELEAVE, WM_MOUSEMOVE):
            self._window.set_max_hover(False)
            return None
        if kind in (WM_NCLBUTTONDOWN, WM_NCLBUTTONDBLCLK) and msg.wParam == HTMAXBUTTON:
            return True, 0
        if kind == WM_NCLBUTTONUP and msg.wParam == HTMAXBUTTON:
            # Deferred, never done here. Maximising sends this same window a
            # fresh round of WM_NCCALCSIZE, WM_NCHITTEST and WM_SIZE while the
            # button-up is still being answered, and Qt does not survive being
            # re-entered from inside its own native event handler: on a remote
            # desktop in 0.29 the window froze on the first click. The timer
            # runs once this message has returned, which is the same position
            # double click on the caption and Win+Up are in.
            from PySide6.QtCore import QTimer

            QTimer.singleShot(0, self._window.toggle_maximized)
            return True, 0
        return None

    def _calcsize(self, msg) -> tuple[bool, int]:
        import ctypes
        from ctypes import wintypes

        # With wParam set lParam is an NCCALCSIZE_PARAMS, whose first member is
        # the proposed rectangle; without it lParam is that rectangle. Either
        # way the rectangle is at lParam.
        rect = wintypes.RECT.from_address(msg.lParam)
        user32 = ctypes.windll.user32
        if user32.IsZoomed(msg.hWnd):
            dpi = user32.GetDpiForWindow(msg.hWnd) or 96
            padded = user32.GetSystemMetricsForDpi(_SM_CXPADDEDBORDER, dpi)
            tx = user32.GetSystemMetricsForDpi(_SM_CXSIZEFRAME, dpi) + padded
            ty = user32.GetSystemMetricsForDpi(_SM_CYSIZEFRAME, dpi) + padded
            rect.left += tx
            rect.right -= tx
            rect.top += ty
            rect.bottom -= ty
        return True, 0
