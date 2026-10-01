"""0.50.4: the status line no longer sets how narrow a pane can be.

Reported as the left pane being locked to its size: dragging the divider to
give the right pane the room stopped wherever the status sentence ended.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402


def test_a_long_status_asks_for_no_width() -> None:
    from app.ui.fitlabel import FitLabel

    label = FitLabel("3 folders, 12 files selected, 1.4 GB  ·  48 folders, 1,204 files")
    assert label.minimumSizeHint().width() == 0
    label.resize(80, 20)
    label.show()
    shown = label.shown_text()
    assert shown != label.text() and shown.endswith("…")
    assert label.text().startswith("3 folders")
    assert label.toolTip() == label.text()


def test_right_aligned_text_keeps_its_end() -> None:
    from app.ui.fitlabel import FitLabel, elide_mode

    assert elide_mode(Qt.AlignRight | Qt.AlignVCenter) == Qt.ElideLeft
    assert elide_mode(Qt.AlignLeft) == Qt.ElideRight
    label = FitLabel("212 GB free of 476 GB")
    label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    label.resize(60, 20)
    label.show()
    assert label.shown_text().startswith("…")


def test_a_fitting_text_has_no_tooltip() -> None:
    from app.ui.fitlabel import FitLabel

    label = FitLabel("ok")
    label.resize(200, 20)
    label.show()
    label.setText("ok")
    assert label.shown_text() == "ok" and label.toolTip() == ""
