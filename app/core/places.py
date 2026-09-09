"""The fixed places at the top of the rail, worked out without touching a disk.

Home, Desktop, Documents, Downloads, Pictures. Every one of them is built from
an environment variable and `paths.join`, which is string work in this process
and nothing else.

What is deliberately missing is a check that any of them exists. That check is
`os.path.isdir` five times at startup, and on a machine whose profile is
redirected to a share it is five blocking calls before the window has drawn --
the exact failure this application was written to escape, reintroduced by the
most reasonable-looking line in it. So the list is what the environment says
and a place that is not there fails the way any other bad path fails: the pane
lists it, the listing comes back GONE, and the status line says so. One clear
failure at the moment somebody asks is worth more than five probes nobody
asked for.

The labels are the user's own words for these folders only insofar as Windows
is consistent about them. A redirected Documents keeps its label and gets its
real target from the variable, which is the case that matters.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from app.io import paths


@dataclass(frozen=True, slots=True)
class Place:
    """A name in the rail and where it goes."""

    label: str
    path: str


#: The folders under the profile, and what to call them. Ordered by how often
#: a file manager is pointed at them rather than alphabetically.
_UNDER_PROFILE = (
    ("Desktop", "Desktop"),
    ("Documents", "Documents"),
    ("Downloads", "Downloads"),
    ("Pictures", "Pictures"),
)


def home() -> str:
    """The profile folder. `USERPROFILE` on Windows, the expansion elsewhere.

    `expanduser` reads the environment on Windows and the password database on
    POSIX; neither opens a file on the volume being named, which is why this is
    allowed to run here at all.
    """
    return paths.normalize(os.environ.get("USERPROFILE") or os.path.expanduser("~"))


def places() -> list[Place]:
    """The rail's fixed places, in order, with no volume touched.

    A profile that cannot be worked out at all gives an empty list rather than
    a row pointing at `~`, which on Windows is a folder called `~`.
    """
    root = home()
    if not root:
        return []
    out = [Place(_leaf_label(root), root)]
    out.extend(Place(label, paths.join(root, name))
               for label, name in _UNDER_PROFILE)
    return out


def _leaf_label(root: str) -> str:
    """What to call the profile itself. The account name, which is what is on
    the folder, falling back to a word when the path has no leaf to read."""
    return paths.leaf(root) or "Home"
