"""What a hover costs.

Reported as the window locking up at full screen on a large display while
working normally in a VM, and the size is the whole of it: moving the
highlight repainted the entire listing, and the pointer crosses a row every
twenty-two pixels. Measured at 3840x2160 over a folder of 20,000, crossing
forty rows cost 4.5 seconds before and a tenth of a second after -- an
application permanently behind the mouse, which is what a freeze looks like
from the chair.

The rectangle is asserted rather than the time, for the reason the column fit
tests give: a repaint of everything would pass a timing test on a fast enough
machine and is wrong on every machine.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QObject, Qt, Signal  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.core.config import Config  # noqa: E402
from app.io.protocol import Entry  # noqa: E402


class FakeBridge:
    def submit(self, op, path, *, timeout, on_reply, args=None):
        return 1

    def cancel(self, request_id):
        pass

    def forget(self, request_id):
        pass


class Volumes(QObject):
    changed = Signal()
    drives: list = []

    def letter_for(self, path):
        return None

    def refresh(self, *, rescan=False):
        pass


@pytest.fixture
def pane(tmp_path):
    from app.core.pane import Pane
    from app.theme import sheet
    from app.ui.pane import PaneWidget

    config = Config({"left.path": "C:\\Jobs"}, str(tmp_path / "config.json"))
    core = Pane(FakeBridge(), config, "left")
    widget = PaneWidget(core, Volumes(), sheet.metrics("normal"), None)
    widget.resize(1200, 800)
    widget.show()
    model = core.current.model
    model.begin(has_parent=False)
    model.add([Entry(name=f"file{i:03d}.txt", is_dir=False, size=i,
                     mtime=100.0 + i, attributes=0) for i in range(200)])
    model.finish()
    QApplication.processEvents()
    widget.core = core
    yield widget
    widget.hide()


def watch_updates(widget):
    """What the listing was asked to repaint, as (x, y, width, height)."""
    seen: list[tuple] = []
    viewport = widget._view.viewport()
    real = viewport.update

    def record(*args):
        seen.append(args)
        return real(*args)

    viewport.update = record
    return seen


def test_moving_the_hover_repaints_two_rows_and_not_the_listing(pane):
    model = pane._view.model()
    pane._on_row_entered(model.index(4, 0))
    seen = watch_updates(pane)
    pane._on_row_entered(model.index(5, 0))

    assert len(seen) == 2, "the row left and the row arrived at, and nothing else"
    height = pane._view.rowHeight(0)
    for rect in seen:
        assert rect != (), "a bare update() is the whole viewport"
        assert rect[3] == height
        assert rect[2] <= pane._view.viewport().width()


def test_the_rows_repainted_are_the_two_the_hover_moved_between(pane):
    model = pane._view.model()
    pane._on_row_entered(model.index(4, 0))
    seen = watch_updates(pane)
    pane._on_row_entered(model.index(5, 0))

    tops = sorted(rect[1] for rect in seen)
    assert tops == [pane._view.visualRect(model.index(4, 0)).y(),
                    pane._view.visualRect(model.index(5, 0)).y()]


def test_leaving_the_listing_repaints_only_the_row_that_was_hovered(pane):
    model = pane._view.model()
    pane._on_row_entered(model.index(7, 0))
    seen = watch_updates(pane)
    pane._clear_hover()

    assert len(seen) == 1
    assert seen[0][1] == pane._view.visualRect(model.index(7, 0)).y()


def test_leaving_when_nothing_was_hovered_repaints_nothing(pane):
    pane._clear_hover()
    seen = watch_updates(pane)
    pane._clear_hover()
    assert seen == []


def test_a_row_scrolled_out_of_sight_is_not_repainted(pane):
    """`visualRect` answers for a row that is not on screen too, and a
    rectangle off the top would be a repaint of nothing in particular."""
    model = pane._view.model()
    pane._on_row_entered(model.index(0, 0))
    pane._view.scrollTo(model.index(150, 0))
    QApplication.processEvents()
    seen = watch_updates(pane)
    pane._on_row_entered(model.index(150, 0))

    for rect in seen:
        assert rect[3] == pane._view.rowHeight(0)
    assert len(seen) <= 2
