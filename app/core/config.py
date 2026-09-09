"""Settings, with a default for every key and no raw key reads anywhere.

The one place in the application outside `io` that touches a file. It is a
deliberate exception and a narrow one: `%APPDATA%` is by definition local, the
read happens once before the window exists, and the write happens on close.
Nothing here is ever given a path the user typed. If a setting ever needs to
live somewhere the user chooses, it goes through the worker like everything
else.

`get` raises on an unknown key rather than returning None. A typo that silently
reads as "off" is the kind of bug that gets found months later by someone
wondering why their preference never applied.
"""

from __future__ import annotations

import json
import os
from typing import Any

APP_FOLDER = "FileManager"
FILE_NAME = "config.json"


def _home() -> str:
    return os.environ.get("USERPROFILE") or os.path.expanduser("~")


#: Every setting the application has, and what it is when nobody has said.
DEFAULTS: dict[str, Any] = {
    "theme": "dark",
    "accent": "blue",
    "density": "normal",

    # Where the panes open, and how paths are shown. Display is a preference
    # per pane; resolution is not a preference at all.
    "left.path": _home(),
    "right.path": _home(),
    "left.show_unc": False,
    "right.show_unc": False,

    # The tabs each pane had when the window last closed, and which of them
    # was in front. A list of `{"path": ..., "locked": ...}` rather than a
    # list of strings so a lock survives a restart with the tab it belongs to.
    # `left.path` above stays as the fallback for a first run and for a
    # session that comes back empty.
    "left.tabs": [],
    "right.tabs": [],
    "left.tab": 0,
    "right.tab": 0,

    # Saved locations. A list of `{"name": ..., "path": ...}` with an optional
    # `"group"`; the name is what the menu shows and the path is what it
    # navigates to. Shared by both panes, because a favourite is a place rather
    # than a side of the window. The group key is written only for entries
    # that have one, so a list nobody has grouped is byte for byte what an
    # earlier version wrote.
    "favorites": [],

    # Whether the favorites bar is drawn under each tab strip. On, and it
    # costs nothing until there is a favourite to put in it -- the bar hides
    # itself entirely while the list is empty.
    "favorites.bar": True,

    # The navigation rail down the left of the window: places, drives with
    # capacity meters, and the favourites under their group headings. One rail
    # for the window rather than one per pane -- a click in it goes to the
    # pane that has the keyboard, which is the same rule Ctrl+1 already
    # follows. Ctrl+B collapses it; the width is what the splitter was left at.
    "rail.shown": True,
    "rail.width": 232,
    # Which sections are folded up, by heading. A list rather than a flag per
    # section so a group added later starts open without a migration.
    "rail.collapsed": [],

    # Deadlines. Seconds without progress before a request is given up on and
    # its worker restarted.
    "timeout.listing": 20.0,
    "timeout.stat": 10.0,
    "timeout.dir_size": 120.0,
    "timeout.open": 30.0,
    "timeout.mkdir": 20.0,
    "timeout.rename": 20.0,
    # Long, and deliberately: a recursive delete over SMB is not quick, and the
    # deadline here is what the watchdog would kill a worker over in the middle
    # of the shell operation. It is the ceiling on a delete, not a target.
    "timeout.delete": 300.0,
    "timeout.drives": 10.0,
    "timeout.free_space": 10.0,
    # The subfolders behind a breadcrumb chevron. Short on purpose: nothing is
    # waiting on a dropdown, so a share that is answering slowly should lose it
    # rather than hold the volume. A timeout still delivers the names it got.
    "timeout.siblings": 8.0,
    # How many of them the worker looks for before it stops. The cap is on the
    # scan, not on the drawing, so a chevron on a 50,000-row folder costs this
    # many names rather than the folder.
    "siblings.limit": 200,
    # Association lookups against the local registry, in a batch. Generous
    # because a shell extension can be slow the first time it is loaded, and
    # short of a listing because nothing is waiting on the answer.
    "timeout.icon": 15.0,
    # The Explorer context menu. Building it loads whichever shell extensions
    # are installed, which is slow exactly once per extension per session and
    # then not at all.
    "timeout.menu": 15.0,
    # Running one of its commands. Long, and it is not a mistake: what this
    # waits for is a person answering a dialog somebody else's extension
    # opened. A deadline short enough to be tidy is a deadline that closes
    # their commit dialog for them.
    "timeout.menu_invoke": 28800.0,
    # Overlays, for the rows on screen. Short, because nothing waits on them
    # and a share that is answering slowly should lose the badges rather than
    # hold the worker.
    "timeout.overlay": 8.0,
    # One refused operation, run again as administrator. It covers the
    # operation itself; the wait for the consent prompt is added on top of it
    # in the worker.
    "timeout.elevate": 300.0,

    # Network paths are polled rather than watched: SMB change notification is
    # not reliable enough to trust a view to. NOT WIRED UP YET, and off until
    # it is -- a poll today would re-list the folder, which resets the model
    # and throws away the selection and the scroll position while the user is
    # working. Polling needs the model to reconcile a new listing against the
    # old one rather than replace it, and that is its own piece of work.
    "refresh.network_seconds": 0.0,

    # The one network call the application makes, and the two things worth
    # remembering about it: whether to make it at all, and which version the
    # user has already said no to.
    "updates.check_on_launch": True,
    "updates.skip_version": "",

    # Real shell icons in the listing. On, because the point of them is that a
    # folder reads at a glance. Off is here for the day a shell extension
    # misbehaves: it costs the pictures and nothing else, and a file manager
    # that starts is worth more than one that looks right.
    "icons.shell": True,

    # Overlay badges on those icons -- shared folders, OneDrive, source
    # control. The one icon request that carries a path, so it is bounded to
    # the rows on screen and this is the switch that turns it off entirely.
    "icons.overlays": True,

    # The real Explorer context menu. Off means the pane's own verbs and
    # nothing else, which is the answer when a shell extension misbehaves --
    # and the reason it is a setting rather than a rebuild.
    "menu.shell": True,

    "window.width": 1280,
    "window.height": 760,
    "window.split": 0.5,
}


class Config:
    """A flat dotted-key store over `DEFAULTS`."""

    def __init__(self, values: dict[str, Any] | None = None, path: str | None = None):
        self._values = dict(values or {})
        self._path = path or self.default_path()

    @staticmethod
    def default_path() -> str:
        base = os.environ.get("APPDATA") or os.path.join(_home(), ".config")
        return os.path.join(base, APP_FOLDER, FILE_NAME)

    @classmethod
    def load(cls, path: str | None = None) -> "Config":
        """Read the settings file, falling back to defaults on any problem.

        A corrupt settings file must not stop the application starting. The
        user loses their preferences, which is recoverable; a window that will
        not open is not.
        """
        target = path or cls.default_path()
        try:
            with open(target, "r", encoding="utf-8") as handle:
                values = json.load(handle)
            if not isinstance(values, dict):
                values = {}
        except (OSError, ValueError):
            values = {}
        return cls({k: v for k, v in values.items() if k in DEFAULTS}, target)

    def get(self, key: str) -> Any:
        if key not in DEFAULTS:
            raise KeyError(f"unknown setting {key!r}; add it to config.DEFAULTS")
        return self._values.get(key, DEFAULTS[key])

    def set(self, key: str, value: Any) -> None:
        if key not in DEFAULTS:
            raise KeyError(f"unknown setting {key!r}; add it to config.DEFAULTS")
        self._values[key] = value

    def save(self) -> bool:
        """Write the settings out. Returns False rather than raising: failing
        to save a preference is not worth an error dialog on the way out.
        """
        try:
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
            with open(self._path, "w", encoding="utf-8") as handle:
                json.dump(self._values, handle, indent=2, sort_keys=True)
            return True
        except OSError:
            return False
