"""One Qt application for the whole run, built before any test asks for one.

Qt allows exactly one application object per process and will not let a wider
one replace a narrower one, so the first test to want an event loop decides
what every test after it can have. A test that only needs signals is happy
with `QCoreApplication`; anything that turns bytes into a pixmap needs
`QGuiApplication` underneath it. Left to collection order, adding a test could
break an unrelated one by getting there first.

So it is made once, here, as the widest of the three. Offscreen, because none
of this has a screen and the tests are meant to run anywhere -- which they do:
this file is also why they pass on a machine with no display at all.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session", autouse=True)
def qt_app():
    """The application, or None where Qt is not installed at all.

    Returned rather than merely created: the fixture's own reference is what
    keeps it alive for the session, and an application that is garbage
    collected halfway through a run fails in ways that look like the test.
    """
    try:
        from PySide6.QtWidgets import QApplication
    except Exception:  # noqa: BLE001 - the Qt tests skip themselves anyway
        return None
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def collected():
    """Collect garbage after every test, because Qt will not survive it later.

    Found on 15 September while adding the 0.17 tests, and the way it was found
    is the reason this is here rather than in a note. Two new test files of
    pure Python -- no Qt in either of them -- made an unrelated test in
    `test_previews.py` segfault inside `QApplication.processEvents()`. The
    crash was in `QTimerInfoList::activateTimers` delivering to an object
    whose C++ half had gone.

    What is actually happening: a test builds Qt objects, some of them with
    debounce timers running, and drops them when it ends. Python collects those
    at whatever moment the generational collector next runs, which can be in
    the middle of *another* test's event loop -- and a timer being activated
    while its owner is being destroyed is a crash rather than a missed
    callback. Nothing was wrong with either new file; they moved the
    collector's schedule by a few allocations and it landed inside a
    `processEvents`.

    So the collection is made to happen at a moment when no event loop is
    running. It is a fixture rather than a fix in `app/` on purpose: nothing in
    the application drops a `Previews` or an `Icons` while it is running -- they
    live as long as the window -- so there is nothing there to repair. This is
    the test harness declining to be the only thing that does it.
    """
    yield
    import gc

    gc.collect()
