"""What kind of place a pane is standing in: here, a share, or somewhere live.

0.49. A delete on a server's folder and a delete on C: look the same until
something says otherwise, and on the folders a plant runs from -- the ones the
user marks as live -- the difference matters more than anywhere. The pane draws
a stripe and a tag from this answer.

Decided from strings only: the resolved path (a mapped letter has already been
turned into its UNC by `paths.resolve`, which reads the local session table and
touches no server) and the list of live folders from the settings. Nothing here
asks a volume anything, so it is safe on every navigation and tested anywhere.
"""

from __future__ import annotations

from app.io import paths

LOCAL = ""
SERVER = "server"
LIVE = "live"


def _key(path: str) -> str:
    return paths.normalize(path or "").rstrip("\\").lower()


def live_root(path: str, live: list[str] | tuple[str, ...]) -> str | None:
    """The marked folder `path` is in or under, or None.

    By whole path segments: marking `P:\\Line3` does not make `P:\\Line30` live.
    """
    here = _key(path)
    if not here:
        return None
    for folder in live or ():
        root = _key(str(folder))
        if root and (here == root or here.startswith(root + "\\")):
            return str(folder)
    return None


def kind_of(path: str, resolved: str, live: list[str] | tuple[str, ...]) -> str:
    """LIVE, SERVER or LOCAL for a pane at `path` (`resolved` is its UNC form
    when it has one). Live is checked against both spellings, so a folder
    marked through its drive letter is still live when shown as UNC."""
    if live_root(path, live) or live_root(resolved, live):
        return LIVE
    if paths.is_unc(resolved) or paths.is_unc(path):
        return SERVER
    return LOCAL


def toggled(path: str, live: list[str] | tuple[str, ...]) -> list[str]:
    """`live` with `path` marked, or with the mark covering it removed."""
    current = [str(folder) for folder in live or ()]
    covering = live_root(path, current)
    if covering is not None:
        return [folder for folder in current if folder != covering]
    return current + [path]
