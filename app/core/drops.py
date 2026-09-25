r"""Dragging rows from one listing onto another, and what a drop there means.

0.29.13. Until then a drag could only leave the window -- into an email, onto
the desktop -- and a drop inside it was refused, because a drop is a copy or a
move and nothing in this application writes a file without the transfer prompt
having been answered first. That rule still holds: a drop here goes through the
same `TransferPrompt` F5 and F6 use, with the destination filled in from where
the pointer let go.

What this module owns is the part that is arithmetic rather than widgetry:

- **What is being dragged.** The rows carry their full paths in a format of
  this application's own, beside the `text/uri-list` Windows programs read. A
  drag that does not carry it -- a file from Explorer, text from an editor --
  is not one this application takes; bringing files *in* from outside is a
  different decision with different questions (whose copy, which conflicts),
  and is left for when somebody asks for it.
- **Copy or move.** Copy, unless Ctrl is held when the button is let go, in
  which case move. That is the opposite of Explorer, where Ctrl forces a copy,
  and it is on purpose: it is what was asked for, and a copy by default is the
  answer that loses nothing when the key was pressed by accident. The drag
  itself only ever offers Qt a copy, so a drop onto Explorer can never take
  the files away -- the reason `ListingModel.supportedDragActions` gives.
- **Whether a place can take the drop at all.** Onto the folder the rows came
  from is nothing, and into one of the dragged folders, or anywhere under one,
  is a folder copied into itself. Both are refused while the pointer is still
  over the place, so the cursor says no before the button is let go.

Nothing here opens a file or asks whether a path exists. The paths are strings
from a listing already on screen, and the checks are string comparisons in the
resolved form -- `S:\Jobs` and `\\server\share\Jobs` are one folder, and a
comparison of the displayed forms would miss that.
"""

from __future__ import annotations

import json

from app.io import paths

#: The drag's own format. A MIME type rather than a flag on the URL list so a
#: drag from another program, whatever it carries, can never be mistaken for
#: one of these.
DRAG_FORMAT = "application/x-filemanager-rows"


def encode(sources: list[str]) -> bytes:
    return json.dumps({"sources": list(sources)}).encode("utf-8")


def decode(data: bytes | None) -> list[str]:
    """The dragged paths, or [] for anything that is not one of these drags.

    Malformed is the same as foreign. A drop is a request to write files, and
    one whose list cannot be read is one to decline, not to repair.
    """
    if not data:
        return []
    try:
        value = json.loads(bytes(data).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return []
    sources = value.get("sources") if isinstance(value, dict) else None
    if not isinstance(sources, list):
        return []
    return [item for item in sources if isinstance(item, str) and item]


def is_move(ctrl_held: bool) -> bool:
    """Ctrl moves; anything else copies. See the module docstring for why this
    is not Explorer's way round."""
    return bool(ctrl_held)


def _key(path: str) -> str:
    return paths.resolve(paths.normalize(path)).lower().rstrip("\\")


def refusal(sources: list[str], destination: str) -> str:
    """Why these cannot be dropped on `destination`, or "" when they can.

    Worded for the pane's status line, which is where it is shown while the
    pointer is over the place that refuses.
    """
    if not sources or not destination:
        return "nothing to drop"
    target = _key(destination)
    folders = {_key(paths.parent(source) or source) for source in sources}
    if folders == {target}:
        return "that is where they already are"
    for source in sources:
        own = _key(source)
        if target == own or target.startswith(own + "\\"):
            return f"{paths.leaf(source)} cannot go inside itself"
    return ""
