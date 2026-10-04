"""Quick view (0.52): the other pane shows the file under the cursor."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, Qt  # noqa: E402
from PySide6.QtGui import QKeyEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from app.io.protocol import Op, Preview, PreviewForm  # noqa: E402
from app.ui.quickview import QuickView  # noqa: E402
from tests.test_previews import fill, window  # noqa: E402,F401 - the fixture


def _press(widget, key):
    widget.keyPressEvent(QKeyEvent(QEvent.KeyPress, key, Qt.NoModifier))


@pytest.fixture
def panel():
    host = QWidget()
    host.resize(800, 600)
    host.show()
    made = QuickView(host)
    made.show()
    yield made
    host.deleteLater()


def test_keys_go_back_step_and_open(panel):
    back, steps, opened = [], [], []
    panel.backRequested.connect(lambda: back.append(1))
    panel.stepRequested.connect(steps.append)
    panel.openRequested.connect(lambda: opened.append(1))
    for key in (Qt.Key_Escape, Qt.Key_Tab):
        _press(panel, key)
    _press(panel, Qt.Key_Down)
    _press(panel, Qt.Key_Up)
    _press(panel, Qt.Key_Return)
    assert back == [1, 1] and steps == [1, -1] and opened == [1]


def test_an_answer_for_another_file_is_ignored(panel):
    panel.waiting("C:\\a.txt", "a.txt")
    panel.show_answer("C:\\b.txt", "b.txt", Preview(form=PreviewForm.TEXT, text="b"))
    assert panel.path == "C:\\a.txt" and panel._name.text() == "a.txt"
    panel.show_answer("C:\\a.txt", "a.txt", Preview(form=PreviewForm.TEXT, text="a"))
    assert panel._text.toPlainText() == "a"


def test_a_folder_says_so_rather_than_keeping_the_last_file(panel):
    panel.waiting("C:\\a.txt", "a.txt")
    panel.show_answer("C:\\a.txt", "a.txt", Preview(form=PreviewForm.TEXT, text="a"))
    panel.not_a_file("Drawings")
    assert panel.path == "" and panel._stack.currentWidget() is panel._note


def test_ctrl_q_covers_the_other_pane_and_follows_the_cursor(window):  # noqa: F811
    fill(window._panes[0], ["a.jpg", "b.txt"])
    widget = window._widgets[0]
    widget._view.setModel(window._panes[0].current.model)
    window._set_active(0)
    widget._go_to(0)
    bridge = window._previews._bridge
    window.toggle_quick_view()
    QApplication.processEvents()
    assert window.quick_view_on
    assert window._quick.geometry().topLeft() == \
        window._widgets[1].mapTo(window, window._widgets[1].rect().topLeft())
    window._previews.fire_now()
    asked = [sent["path"] for sent in bridge.sent if sent["op"] is Op.PREVIEW]
    assert asked and asked[-1].endswith("a.jpg")

    widget._go_to(1)
    window._previews.fire_now()
    asked = [sent["path"] for sent in bridge.sent if sent["op"] is Op.PREVIEW]
    assert asked[-1].endswith("b.txt")

    window.toggle_quick_view()
    assert not window.quick_view_on


def test_tab_goes_into_quick_view_and_back_without_changing_the_active_pane(window):  # noqa: F811
    window._set_active(0)
    window.toggle_quick_view()
    window._switch_pane()
    QApplication.processEvents()
    assert window._active == 0
    window._switch_pane()
    QApplication.processEvents()
    assert window._active == 0


def test_when_the_covered_pane_becomes_active_quick_view_moves_over(window):  # noqa: F811
    window._set_active(0)
    window.toggle_quick_view()
    assert window._quick_over == 1
    window._set_active(1)
    assert window._quick_over == 0


def test_quit_is_alt_f4_and_ctrl_q_is_quick_view(window):  # noqa: F811
    keys = {action.text().replace("&", ""): action.shortcut().toString()
            for menu in window.menuBar().findChildren(type(window.menuBar().actions()[0].menu()))
            for action in menu.actions()}
    assert keys.get("Quit") == "Alt+F4"
    assert keys.get("Quick view in the other pane") == "Ctrl+Q"
