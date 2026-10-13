"""A worker process ends when the window's process does, however that ends.

0.56.5. The pool's workers are `daemon` processes, which multiprocessing ends
when the parent *exits*. A parent that crashes does not exit; it is gone, and
nothing runs to end them. Their inbox does not tell them either: a worker holds
both ends of its own queue, so a parent that vanished looks exactly like a
parent with nothing to ask. Every crash therefore left one `FileManager.exe`
per volume running with nothing to answer to -- and holding the files in the
install folder open, which is why the update after a crash rolled itself back
until they were ended from Task Manager.

So each worker watches its parent. On Windows multiprocessing already hands
the child a handle to the parent process (`parent_process().sentinel`); a
thread waits on it and ends this process when it is signalled. Waiting on a
process handle costs nothing and needs no polling.

Standard library only, and a no-op wherever there is no parent to watch.
"""

from __future__ import annotations

import multiprocessing
import os
import threading


def follow_parent() -> bool:
    """Start the thread that ends this process with its parent.

    Returns whether it was started: False when this is not a multiprocessing
    child (the tests call the entry points directly) or when the platform
    gives no handle to wait on.
    """
    parent = multiprocessing.parent_process()
    if parent is None:
        return False
    sentinel = getattr(parent, "sentinel", None)
    if sentinel is None:
        return False

    def wait() -> None:
        from multiprocessing.connection import wait as wait_on

        try:
            wait_on([sentinel])
        except Exception:  # noqa: BLE001 - a watcher that fails ends nothing
            return
        # Not sys.exit: the main thread may be inside a call that never
        # returns -- a share that stopped answering -- which is the reason
        # this is a separate process in the first place.
        os._exit(0)

    threading.Thread(target=wait, name="fm-parent-watch", daemon=True).start()
    return True
