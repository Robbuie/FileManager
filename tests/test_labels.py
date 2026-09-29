"""Checks on colour labels and notes (0.38)."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from app.core.config import Config  # noqa: E402
from app.core.labels import Labels  # noqa: E402
from app.io.protocol import Entry  # noqa: E402


def test_a_label_is_kept_by_path_and_saved():
    config = Config({})
    labels = Labels(config)
    labels.set_colour("C:\\Jobs", ["Riverside"], 1)
    labels.set_note("C:\\Jobs", "Riverside", "sent 9/12")
    assert labels.label("c:\\jobs", "RIVERSIDE") == (1, "sent 9/12")
    assert "c:\\jobs\\riverside" in config.get("labels")
    # Read back by a fresh store, the way the next start reads it.
    assert Labels(config).label("C:\\Jobs", "Riverside") == (1, "sent 9/12")


def test_clearing_both_forgets_the_entry():
    config = Config({})
    labels = Labels(config)
    labels.set_colour("C:\\", ["a"], 3)
    labels.set_colour("C:\\", ["a"], 0)
    assert labels.label("C:\\", "a") is None
    assert len(labels) == 0


def test_a_rename_here_carries_it():
    labels = Labels(Config({}))
    labels.set_colour("C:\\Jobs", ["old"], 2)
    labels.moved("C:\\Jobs", "old", "C:\\Jobs", "new")
    assert labels.label("C:\\Jobs", "new") == (2, "")
    assert labels.label("C:\\Jobs", "old") is None


def test_the_model_answers_the_label_and_the_note_as_a_tooltip():
    from PySide6.QtCore import Qt

    from app.core.listing import Column, ListingModel

    labels = Labels(Config({}))
    labels.set_colour("C:\\Jobs", ["a.txt"], 5)
    labels.set_note("C:\\Jobs", "a.txt", "check rev")
    model = ListingModel()
    model.set_folder("C:\\Jobs")
    model.set_labels(labels)
    model.begin(has_parent=False)
    model.add([Entry("a.txt", False, 1, 1.0, 0), Entry("b.txt", False, 1, 1.0, 0)])
    model.finish()
    a = model.index(model.row_of("a.txt"), int(Column.NAME))
    b = model.index(model.row_of("b.txt"), int(Column.NAME))
    assert model.data(a, ListingModel.LabelRole) == (5, "check rev")
    assert model.data(a, Qt.ToolTipRole) == "check rev"
    assert model.data(b, ListingModel.LabelRole) is None
    model.set_labels(None)
    assert model.data(a, ListingModel.LabelRole) is None
