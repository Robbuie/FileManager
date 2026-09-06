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
