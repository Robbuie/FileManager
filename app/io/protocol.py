"""The wire format between the UI process and the workers.

Everything crossing the process boundary is defined here and nowhere else, and
everything defined here is plain and picklable: no `Path`, no Qt types, no open
handles. Two rules make late replies harmless, which matters because a tab can
navigate away while a 50,000-file listing is still arriving:

  * every request carries an id, and every reply carries the id it answers, so
    a stale reply can be dropped without special-casing;
  * every call returns a `Reply` whether it succeeded or not. A timeout is an
    ordinary result with `status=TIMEOUT`, not an exception the caller has to
    catch. Callers that must handle failure anyway are better off handling it
    on the same path as success.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Status(str, Enum):
    """Outcome of a request. `str` so it survives a repr in a log unchanged."""

    OK = "ok"
    PARTIAL = "partial"      # a batch of a streamed reply; more are coming
    TIMEOUT = "timeout"      # the operation exceeded its deadline
    CANCELLED = "cancelled"  # the caller withdrew it
    DENIED = "denied"        # access refused; retrying will not help
    GONE = "gone"            # path or volume no longer reachable
    ERROR = "error"          # anything else, with detail in `message`


class Op(str, Enum):
    """Requests a worker understands."""

    LIST = "list"
    STAT = "stat"
    ICON = "icon"
    DIR_SIZE = "dir_size"
    RESOLVE = "resolve"
    PING = "ping"


@dataclass(frozen=True, slots=True)
class Entry:
    """One row of a listing.

    Populated entirely from `os.scandir`, whose `DirEntry` carries size, mtime
    and attributes from the directory enumeration itself. Adding a field that
    needs `os.stat` turns a 50,000-row listing into 50,000 SMB round trips —
    under a second becomes over a minute. Anything that expensive is a separate
    lazy request (`ICON`, `DIR_SIZE`), never a column filled in here.
    """

    name: str
    is_dir: bool
    size: int
    mtime: float
    attributes: int
    is_link: bool = False


@dataclass(frozen=True, slots=True)
class Request:
    """A unit of work for a worker.

    `timeout` is not optional and has no default of `None`. An operation
    without a deadline is an incomplete operation, not a simpler one.
    """

    id: int
    op: Op
    path: str                       # always resolved UNC, never a drive letter
    timeout: float                  # seconds
    args: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Reply:
    """The answer to exactly one `Request`.

    A streamed listing arrives as a series of replies with `status=PARTIAL`,
    each carrying up to `BATCH_SIZE` entries, terminated by one final reply
    with a settled status. `seq` orders them.
    """

    id: int
    status: Status
    payload: Any = None
    message: str = ""
    seq: int = 0


#: Rows per streamed batch. Large enough that the queue is not the bottleneck,
#: small enough that the first rows paint while the rest are still arriving.
BATCH_SIZE = 1000
