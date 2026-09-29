"""Checks on the peek card's keys and on when Space peeks (0.36)."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, QRect, Qt  # noqa: E402
from PySide6.QtGui import QKeyEvent  # noqa: E402
from PySide6.QtWidgets import QWidget  # noqa: E402

from app.io.protocol import Entry, Preview, PreviewForm  # noqa: E402
from app.ui.peek import PeekCard  # noqa: E402


def _press(widget, key):
    widget.keyPressEvent(QKeyEvent(QEvent.KeyPress, key, Qt.NoModifier))


@pytest.fixture
def card():
    host = QWidget()
    host.resize(1000, 700)
    host.show()
    made = PeekCard(host)
    made.set_motion(False)
    yield made
    host.deleteLater()


def test_it_fills_most_of_the_window(card):
    card.open_from(QRect())
    assert card.geometry() == card.target_rect()
    assert card.width() > 600 and card.height() > 500


def test_space_and_escape_close_it(card):
    closed = []
    card.closed.connect(lambda: closed.append(1))
    for key in (Qt.Key_Space, Qt.Key_Escape):
        card.open_from(QRect())
        _press(card, key)
        assert not card.isVisible()
    assert len(closed) == 2


def test_arrows_step_and_enter_opens(card):
    steps, opened = [], []
    card.stepRequested.connect(steps.append)
    card.openRequested.connect(lambda: opened.append(1))
    card.open_from(QRect())
    _press(card, Qt.Key_Down)
    _press(card, Qt.Key_Up)
    assert steps == [1, -1]
    _press(card, Qt.Key_Return)
    assert opened == [1] and not card.isVisible()


def test_an_answer_for_another_file_is_ignored(card):
    card.open_from(QRect())
    card.waiting("C:\\a.txt", "a.txt")
    card.show_answer("C:\\b.txt", "b.txt",
                     Preview(form=PreviewForm.TEXT, text="other"))
    assert card.path == "C:\\a.txt"
    assert card._body._name.text() == "a.txt"


def test_space_peeks_only_on_a_file_and_only_when_set(tmp_path):
    from tests.test_window import FakeBridge, FakeVolumes
    from app.core.config import Config
    from app.core.pane import Pane
    from app.ui.pane import PaneWidget
    from app.ui.window import MainWindow  # noqa: F401 - the sheet's metrics
    from app.theme import sheet

    config = Config({"left.path": "C:\\Jobs"}, str(tmp_path / "c.json"))
    pane = Pane(FakeBridge(), config, "left")
    widget = PaneWidget(pane, FakeVolumes(), sheet.metrics("normal"), None)
    model = pane.current.model
    model.begin(has_parent=False)
    model.add([Entry("folder", True, 0, 1.0, 0), Entry("file.txt", False, 5, 1.0, 0)])
    model.finish()
    widget._view.setModel(model)
    widget._go_to(model.row_of("file.txt"))
    assert widget.peek_target()[1].name == "file.txt"
    widget._go_to(model.row_of("folder"))
    assert widget.peek_target() is None
