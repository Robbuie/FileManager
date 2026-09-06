"""Running one operation as administrator, and nothing more than one.

Windows refuses a rename in `C:\\Program Files` to a process that is not
elevated, and it refuses it in a way that cannot be worked around from inside
this application: there is no flag, no handle and no API that makes an
unelevated process able to write there. The only route is a second process,
started with the `runas` verb, which puts the consent prompt on the screen.

The shape of it here is deliberate and narrow.

  * **One operation.** The plan says exactly which operation on exactly which
    paths. The elevated process does that and exits. It does not stay
    running, it does not listen on anything, and it never becomes a general
    way to make this application do work with administrator rights.
  * **Only ever after a refusal, and only ever after a person said so.** The
    window offers this when Windows has already answered ACCESS DENIED, and
    the offer is a dialog with a button on it. Nothing here is reached by a
    program deciding on its own that elevation would be convenient.
  * **The same code, with a different token.** The elevated process runs the
    worker's own handler for the operation rather than a second
    implementation of it. A copy of `delete` that only runs when elevated is
    a copy that is only tested when elevated.

The plan travels as a file in the user's temp folder rather than on a command
line, because a command line has a length limit and a quoting problem and this
carries a list of names. It is written by this user, read by this user's
elevated process, and deleted afterwards. Worth being plain about the limit of
that: anything already running as this user could rewrite the file between
those two moments. It could also simply raise its own consent prompt, so this
is not a door that was closed before.
"""

from __future__ import annotations

import json
import os
import queue
import sys
import tempfile
from typing import Any

from app.io.protocol import Op, Reply, Request, Status

#: The operations that may be elevated, and the only ones this file will run.
#: Listing is not among them and neither is anything that streams: an elevated
#: process exists to make one change and stop.
ACTIONS = {
    "mkdir": Op.MKDIR,
    "rename": Op.RENAME,
    "delete": Op.DELETE,
}

#: The switch the frozen executable and `python -m app` both recognise.
FLAG = "--elevated"

#: How long the parent waits for the elevated process, in seconds, on top of
#: the operation's own deadline. It is mostly the consent prompt: the user has
#: to see it, read it and answer it, and a timeout short enough to lose that
#: race would be worse than no timeout at all.
CONSENT_GRACE = 120.0


def plan_for(op: Op, path: str, args: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """The plan for an operation that was refused, or None if it cannot be one.

    Here rather than in the window so that there is one place that decides
    what is elevatable, and it is next to the code that runs it.
    """
    for action, known in ACTIONS.items():
        if known is op:
            return {"action": action, "path": path, "args": dict(args or {})}
    return None


def describe(plan: dict[str, Any]) -> str:
    """What the plan does, in the words the confirmation dialog uses."""
    action = str(plan.get("action") or "")
    args = plan.get("args") or {}
    path = str(plan.get("path") or "")
    if action == "mkdir":
        return f"create {path}"
    if action == "rename":
        return f"rename {path} to {args.get('name', '')}"
    if action == "delete":
        names = args.get("names") or []
        kind = "delete permanently" if args.get("permanent") else "delete"
        return f"{kind} {len(names)} item(s) in {path}"
    return action or "the operation"


# --------------------------------------------------------------------------
# The elevated process itself.
# --------------------------------------------------------------------------


class _Collector:
    """Stands in for a worker's outbox. Keeps the last reply and nothing else."""

    def __init__(self) -> None:
        self.reply: Reply | None = None

    def put(self, reply: Reply) -> None:
        self.reply = reply


class _NoControl:
    """Stands in for a worker's control queue. There is nothing to cancel."""

    def get_nowait(self) -> Any:
        raise queue.Empty


def perform(plan_path: str) -> int:
    """Run the plan at `plan_path` and write the result beside it.

    The entry point of the elevated process, and the whole of it. It imports
    the worker rather than reimplementing anything, validates the plan against
    `ACTIONS` before it does, and exits non-zero on anything it will not do.
    """
    from app.io import worker

    try:
        with open(plan_path, "r", encoding="utf-8") as handle:
            plan = json.load(handle)
    except (OSError, ValueError) as exc:
        return _finish(plan_path, Status.ERROR, f"the plan could not be read: {exc}")

    if not isinstance(plan, dict):
        return _finish(plan_path, Status.ERROR, "the plan is not an operation")
    op = ACTIONS.get(str(plan.get("action") or ""))
    path = str(plan.get("path") or "")
    args = plan.get("args") if isinstance(plan.get("args"), dict) else {}
    if op is None or not path:
        return _finish(plan_path, Status.ERROR, "that operation cannot be elevated")

    request = Request(id=1, op=op, path=path,
                      timeout=float(plan.get("timeout") or 300.0), args=dict(args))
    outbox = _Collector()
    try:
        worker._handle(request, outbox, _NoControl(), set())
    except Exception as exc:  # noqa: BLE001 - the reply is the only output
        return _finish(plan_path, Status.ERROR, str(exc) or exc.__class__.__name__)

    reply = outbox.reply
    if reply is None:
        return _finish(plan_path, Status.ERROR, "the operation produced no result")
    return _finish(plan_path, reply.status, reply.message, reply.payload)


def result_path(plan_path: str) -> str:
    return plan_path + ".result"


def _finish(plan_path: str, status: Status, message: str = "",
            payload: Any = None) -> int:
    """Write what happened where the parent will look, and say so in the exit code.

    Both, rather than one of them: the exit code survives a result file that
    could not be written, and the file carries the sentence a person needs to
    read when the exit code only says "no".
    """
    try:
        with open(result_path(plan_path), "w", encoding="utf-8") as handle:
            json.dump({"status": status.value, "message": message,
                       "payload": payload if _plain(payload) else None}, handle)
    except OSError:
        pass
    return 0 if status is Status.OK else 1


def _plain(value: Any) -> bool:
    """Whether a payload will survive JSON. A reply's payload is a plain dict
    for the operations here, but the check is cheap and the alternative is an
    elevated process that fails while writing down that it succeeded.
    """
    try:
        json.dumps(value)
        return True
    except (TypeError, ValueError):
        return False


# --------------------------------------------------------------------------
# The unelevated side: writing the plan out and starting the process.
# --------------------------------------------------------------------------


def write_plan(plan: dict[str, Any], timeout: float) -> str:
    """Put the plan somewhere the elevated process can read it. Returns the path."""
    handle, path = tempfile.mkstemp(prefix="filemanager-elevate-", suffix=".json")
    with os.fdopen(handle, "w", encoding="utf-8") as stream:
        json.dump(dict(plan, timeout=float(timeout)), stream)
    return path


def read_result(plan_path: str) -> dict[str, Any]:
    try:
        with open(result_path(plan_path), "r", encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def clean_up(plan_path: str) -> None:
    for path in (result_path(plan_path), plan_path):
        try:
            os.remove(path)
        except OSError:
            pass


def command() -> tuple[str, str, str]:
    """How to start this application again: `(executable, prefix, directory)`.

    A frozen build is its own executable and takes the switch directly. Run
    from a source tree it is the interpreter, which needs `-m app` in front of
    the switch and the folder holding the package to start in -- and that
    folder is worked out from where this file is rather than from the current
    directory, which by then belongs to whatever the user last navigated to.
    """
    if getattr(sys, "frozen", False):
        return sys.executable, "", os.path.dirname(sys.executable)
    package = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return sys.executable, "-m app ", os.path.dirname(package)
