"""Copy diagnostics (0.43): what to send when something goes wrong.

When a share misbehaves on the plant network, the useful report is not a
description of what the window did -- it is which version, which Python and
Qt, whether pywin32 loaded, which settings are not the defaults, and the last
things the panes said and failed at. This keeps the last of those in memory
and puts the lot on the clipboard as plain text on request.

**Nothing is written to disk and nothing is sent anywhere.** The events are a
ring buffer in this process; the report exists only when somebody asks for
it, and then only on their clipboard, where they can read it before pasting it
anywhere. Settings that are about the user's own folders -- tabs, favourites,
labels, notes, workspaces, remembered sorts, saved network locations -- are
reported by count, never by content: a report is something that gets pasted
into a chat or an issue, and a list of job folders on a customer's server is
not something it should carry by accident.
"""

from __future__ import annotations

import platform
import sys
import time
from collections import deque
from typing import Any, Iterable

#: How many events are kept.
KEPT = 300

#: Settings whose values are the user's own places and words. Reported as a
#: count; see the module docstring.
PRIVATE_PREFIXES = ("left.", "right.", "favorites", "labels", "workspaces", "network.",
                    "listing.sorts", "search.last", "rename.last", "basket", "commands",
                    "history")


class Events:
    """The recent things the panes said, with when."""

    def __init__(self, kept: int = KEPT) -> None:
        self._events: deque[tuple[float, str, str]] = deque(maxlen=kept)

    def add(self, where: str, text: str) -> None:
        if text:
            self._events.append((time.time(), where, text))

    def lines(self) -> list[str]:
        return [f"{time.strftime('%H:%M:%S', time.localtime(when))}  {where:<6} {text}"
                for when, where, text in self._events]


def _private(key: str) -> bool:
    return any(key == prefix.rstrip(".") or key.startswith(prefix) for prefix in PRIVATE_PREFIXES)


def settings_lines(values: dict[str, Any], defaults: dict[str, Any]) -> list[str]:
    """Every setting that differs from its default, private ones by count."""
    out = []
    for key in sorted(values):
        if key not in defaults or values[key] == defaults[key]:
            continue
        value = values[key]
        if _private(key):
            size = len(value) if hasattr(value, "__len__") and not isinstance(value, str) else 1
            out.append(f"{key} = ({size} item{'s' if size != 1 else ''}, not shown)")
        else:
            out.append(f"{key} = {value!r}")
    return out


def report(*, version: str, values: dict[str, Any], defaults: dict[str, Any],
           events: Iterable[str], extra: dict[str, str] | None = None) -> str:
    """The text that goes on the clipboard."""
    try:
        from PySide6 import __version__ as pyside
        from PySide6.QtCore import qVersion
        qt = f"PySide6 {pyside}, Qt {qVersion()}"
    except Exception:  # noqa: BLE001 - a report must always come out
        qt = "Qt not loaded"
    try:
        import win32api  # noqa: F401
        pywin32 = "loaded"
    except Exception as exc:  # noqa: BLE001
        pywin32 = f"not loaded ({type(exc).__name__})"
    lines = [
        f"File Manager {version}",
        f"{platform.platform()}  ·  Python {sys.version.split()[0]}"
        f"{' (frozen)' if getattr(sys, 'frozen', False) else ''}",
        qt,
        f"pywin32 {pywin32}",
    ]
    for key, value in (extra or {}).items():
        lines.append(f"{key}: {value}")
    changed = settings_lines(values, defaults)
    lines.append("")
    lines.append(f"Settings changed from the defaults ({len(changed)}):")
    lines.extend(f"  {line}" for line in changed or ["none"])
    recent = list(events)
    lines.append("")
    lines.append(f"Recent events ({len(recent)}):")
    lines.extend(f"  {line}" for line in recent or ["none"])
    return "\n".join(lines) + "\n"
