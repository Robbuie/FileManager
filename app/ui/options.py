"""The Options dialog: every row of `app.core.options`, drawn and wired.

It applies each change the moment it is made and has no OK button. A settings
dialog that collects changes and applies them on OK is one where somebody
flips a switch, looks at the window behind it to see what it did, sees
nothing, and flips it back -- so this one hands each change to the window at
once and the window re-renders whatever it touches. That is also why it is not
modal: the point of a switch that applies at once is being able to look at the
window while flipping it.

The dialog owns no setting and writes none. `changed(key, value)` goes to
`MainWindow.apply_setting`, which is the one place that knows what a key means
-- the same method the View menu's own entries end up in -- so a setting
changed here and the same setting changed from a menu cannot disagree.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QAbstractButton,
    QButtonGroup,
    QGraphicsOpacityEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from app.core import options as core_options
from app.theme import sheet
from app.ui.dialogs import Dialog


class Switch(QAbstractButton):
    """An on/off switch, painted from the tokens.

    A checkbox would do the job and read as a form. A switch reads as a
    setting that is already in effect, which is what every row here is.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        # The default render until the dialog hands down the one in use, so
        # a switch is never drawn from a colour written here.
        self.apply_tokens(sheet.tokens())

    def apply_tokens(self, tokens: dict[str, str]) -> None:
        self._on = QColor(tokens["accent"])
        self._off = QColor(tokens["bg_4"])
        self._edge = QColor(tokens["line"])
        self._knob_on = QColor(tokens["bg_0"])
        self._knob_off = QColor(tokens["txt_1"])
        self._focus = QColor(tokens["accent_text"])
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt naming
        return QSize(40, 22)

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt naming
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        track = QRectF(0.5, 0.5, 39, 21)
        on = self.isChecked()
        painter.setPen(self._focus if self.hasFocus() else
                       (self._on if on else self._edge))
        painter.setBrush(self._on if on else self._off)
        painter.drawRoundedRect(track, 10.5, 10.5)
        painter.setPen(Qt.NoPen)
        painter.setBrush(self._knob_on if on else self._knob_off)
        painter.drawEllipse(QRectF(21 if on else 3, 3, 16, 16))


#: The most choices drawn side by side before a row wraps.
PER_ROW = 6


class Segments(QWidget):
    """A row of exclusive choices, drawn as one control.

    Buttons in a group rather than a combo box: the choices here are two to
    five words long, and a combo box hides all but one of them behind a click
    -- which, for a setting somebody is exploring, is the one thing not wanted.
    """

    chosen = Signal(object)

    def __init__(self, choices, current, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("role", "segments")
        self.setAttribute(Qt.WA_StyledBackground, True)
        # 0.48: wrapped into rows of `PER_ROW`. Eleven themes on one line ran
        # past the edge of the page, and a sideways scroll inside a dialog is
        # the combo box's hiding again in another form.
        row = QGridLayout(self)
        row.setContentsMargins(3, 3, 3, 3)
        row.setSpacing(2)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._values: list[Any] = []
        for index, (value, label) in enumerate(choices):
            button = QPushButton(label)
            button.setProperty("role", "segment")
            button.setCheckable(True)
            button.setAutoDefault(False)
            button.setCursor(Qt.PointingHandCursor)
            button.setChecked(core_options._same(value, current))
            self._group.addButton(button, index)
            self._values.append(value)
            row.addWidget(button, index // PER_ROW, index % PER_ROW)
        self._group.idClicked.connect(
            lambda index: self.chosen.emit(self._values[index]))

    def value(self) -> Any:
        index = self._group.checkedId()
        return self._values[index] if index >= 0 else None

    def set_value(self, value: Any) -> None:
        for index, known in enumerate(self._values):
            if core_options._same(known, value):
                self._group.button(index).setChecked(True)


class OptionRow(QWidget):
    """One setting: what it is, what it does, and the control that sets it."""

    changed = Signal(str, object)

    def __init__(self, option: core_options.Option, config,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.option = option
        self.setProperty("role", "optionrow")
        self.setAttribute(Qt.WA_StyledBackground, True)

        title = QLabel(option.label)
        title.setProperty("role", "optionlabel")
        heading = QHBoxLayout()
        heading.setSpacing(8)
        heading.addWidget(title)
        if option.new:
            tag = QLabel("new")
            tag.setProperty("role", "newtag")
            heading.addWidget(tag)
        if option.restart:
            tag = QLabel("after a restart")
            tag.setProperty("role", "restarttag")
            heading.addWidget(tag)
        heading.addStretch(1)

        words = QVBoxLayout()
        words.setSpacing(3)
        words.addLayout(heading)
        if option.help:
            about = QLabel(option.help)
            about.setProperty("role", "note")
            about.setWordWrap(True)
            words.addWidget(about)

        if option.kind == core_options.TOGGLE:
            self.control = Switch()
            self.control.setChecked(bool(core_options.current(config, option)))
            self.control.setAccessibleName(option.label)
            self.control.toggled.connect(
                lambda on: self.changed.emit(option.key, bool(on)))
        else:
            self.control = Segments(core_options.choices_for(config, option),
                                    core_options.current(config, option))
            self.control.setAccessibleName(option.label)
            self.control.chosen.connect(
                lambda value: self.changed.emit(option.key, value))

        # A switch sits at the end of its line; a choice goes under its
        # words, left-aligned, because five choices beside a paragraph leave
        # the paragraph three words wide.
        if isinstance(self.control, Switch):
            line = QHBoxLayout(self)
            line.setContentsMargins(0, 10, 0, 10)
            line.setSpacing(20)
            line.addLayout(words, 1)
            line.addWidget(self.control, 0, Qt.AlignVCenter)
        else:
            line = QVBoxLayout(self)
            line.setContentsMargins(0, 10, 0, 10)
            line.setSpacing(8)
            line.addLayout(words)
            line.addWidget(self.control, 0, Qt.AlignLeft)

        self._fade = QGraphicsOpacityEffect(self)
        self._fade.setOpacity(1.0)
        self.setGraphicsEffect(self._fade)

    def set_live(self, live: bool) -> None:
        """Faded, not disabled: see `core.options.Option.needs`."""
        self._fade.setOpacity(1.0 if live else 0.45)

    def show_value(self, value: Any) -> None:
        """Put the control at `value` without saying it changed.

        For a change made somewhere else -- the View menu -- while the dialog
        is open. Signals blocked, or the dialog would hand the window back the
        change it was just told about.
        """
        self.control.blockSignals(True)
        try:
            if isinstance(self.control, Switch):
                self.control.setChecked(bool(value))
            else:
                self.control.set_value(value)
        finally:
            self.control.blockSignals(False)


class OptionsDialog(Dialog):
    """The pages, the rows on them, and a search across all of them."""

    changed = Signal(str, object)

    def __init__(self, config, parent: QWidget | None = None,
                 section: str = "look") -> None:
        super().__init__(parent)
        self.setWindowTitle("Options")
        self.setModal(False)
        self.setProperty("role", "options")
        self.resize(900, 640)
        self._config = config
        self._rows: list[OptionRow] = []
        self._headings: list[tuple[QLabel, str, str]] = []
        self._pages: dict[str, QWidget] = {}

        self._find = QLineEdit()
        self._find.setPlaceholderText("Find a setting")
        self._find.setClearButtonEnabled(True)
        self._find.setProperty("role", "optionsfind")
        self._find.textChanged.connect(self._search)

        self._nav = QListWidget()
        self._nav.setProperty("role", "optionsnav")
        self._nav.setFixedWidth(200)
        self._nav.setFocusPolicy(Qt.StrongFocus)

        self._stack = QStackedWidget()
        for key, label in core_options.SECTIONS:
            fresh = sum(1 for option in core_options.in_section(key) if option.new)
            item = QListWidgetItem(label + (f"   {fresh} new" if fresh else ""))
            item.setData(Qt.UserRole, key)
            self._nav.addItem(item)
            page = self._build_page(key, label)
            self._pages[key] = page
            self._stack.addWidget(page)
        self._nav.currentRowChanged.connect(self._stack.setCurrentIndex)

        side = QVBoxLayout()
        side.setSpacing(10)
        side.addWidget(self._find)
        side.addWidget(self._nav, 1)
        note = QLabel("Every change applies at once.")
        note.setProperty("role", "note")
        note.setWordWrap(True)
        side.addWidget(note)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(18)
        layout.addLayout(side)
        layout.addWidget(self._stack, 1)

        self._refresh_live()
        self.show_section(section)

    # --------------------------------------------------------------- building

    def _build_page(self, key: str, label: str) -> QWidget:
        body = QWidget()
        body.setProperty("role", "optionsbody")
        column = QVBoxLayout(body)
        column.setContentsMargins(4, 0, 12, 12)
        column.setSpacing(0)
        title = QLabel(label)
        title.setProperty("role", "optionpage")
        column.addWidget(title)
        for option in core_options.in_section(key):
            if option.heading:
                heading = QLabel(option.heading.upper())
                heading.setProperty("role", "optionhead")
                column.addWidget(heading)
                self._headings.append((heading, key, option.heading))
            row = OptionRow(option, self._config)
            row.changed.connect(self._on_changed)
            column.addWidget(row)
            self._rows.append(row)
        column.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setWidget(body)
        return scroll

    def apply_tokens(self, tokens: dict[str, str]) -> None:
        for row in self._rows:
            if isinstance(row.control, Switch):
                row.control.apply_tokens(tokens)

    # ------------------------------------------------------------- behaviour

    def show_section(self, key: str) -> None:
        for index, (known, _label) in enumerate(core_options.SECTIONS):
            if known == key:
                self._nav.setCurrentRow(index)
                return

    def _on_changed(self, key: str, value: Any) -> None:
        self.changed.emit(key, value)
        self._refresh_live()

    def _refresh_live(self) -> None:
        for row in self._rows:
            row.set_live(core_options.live(self._config, row.option))

    def sync(self, key: str) -> None:
        """A setting changed somewhere else; show it here."""
        for row in self._rows:
            if row.option.key == key:
                row.show_value(core_options.current(self._config, row.option))
        self._refresh_live()

    def _search(self, text: str) -> None:
        """Hide what does not match, on every page, and the pages left empty.

        The first page with anything on it comes to the front, so typing a
        word and looking is the whole of finding a setting.
        """
        hits: dict[str, int] = {}
        for row in self._rows:
            shown = core_options.matches(row.option, text)
            row.setVisible(shown)
            if shown:
                hits[row.option.section] = hits.get(row.option.section, 0) + 1
        for heading, section, _name in self._headings:
            heading.setVisible(not text.strip() or hits.get(section, 0) > 0)
        first = None
        for index, (key, _label) in enumerate(core_options.SECTIONS):
            empty = bool(text.strip()) and not hits.get(key)
            self._nav.item(index).setHidden(empty)
            if first is None and not empty:
                first = index
        if first is not None and (self._nav.currentRow() < 0 or
                                  self._nav.item(self._nav.currentRow()).isHidden()):
            self._nav.setCurrentRow(first)

    def rows(self) -> list[OptionRow]:
        """For the tests: every row the dialog drew."""
        return list(self._rows)
