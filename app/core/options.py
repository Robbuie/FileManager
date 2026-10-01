"""What the Options dialog offers: every setting a person can change, and how.

A table rather than code in the dialog, for the command table's reason. The
dialog draws whatever is listed here, the window applies whatever key comes
back, and a setting added later is one row -- with no second place that has to
remember it exists. It also makes the part worth testing testable without a
screen: that every row names a real setting, that a choice's current value is
always one of the choices offered, and that the search finds what it should.

Two rules decide what goes in the table. A setting somebody would reasonably
want to change without opening a JSON file goes in; a setting that only makes
sense as a number somebody measured (a deadline, a walk's ceiling) stays in the
file, where the comment beside it explains the number. And every feature added
from 0.33 on that changes what the window looks like or does by itself arrives
with a row here -- the user's rule, and the reason this dialog exists: a lot of
it should be a choice rather than forced on.

Nothing here touches Qt or the filesystem.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core.config import DEFAULTS
from app.io.protocol import THUMB_SIZES
from app.theme.tokens import ACCENT_LABELS, DENSITY_LABELS, FONTS, THEME_LABELS

#: The pages, in the order the dialog lists them.
SECTIONS: tuple[tuple[str, str], ...] = (
    ("look", "Look"),
    ("listing", "Listing"),
    ("rail", "Rail and network"),
    ("previews", "Previews"),
    ("transfers", "Transfers"),
    ("general", "General"),
)

TOGGLE = "toggle"
CHOICE = "choice"


@dataclass(frozen=True)
class Option:
    """One row of the dialog.

    `choices` is `(value, label)` pairs for a CHOICE and empty for a TOGGLE.
    `needs` is `(key, value)`: the row is drawn faded while that other setting
    is not `value`, because the switch does nothing then -- but it stays
    changeable, so somebody can set it up before turning the other one on.
    `restart` says the change waits for the next start, and the dialog says so.
    `new` marks a row added with the release that introduced it, which the
    dialog tags so the new things can be found among the old.
    """

    key: str
    section: str
    label: str
    help: str = ""
    kind: str = TOGGLE
    choices: tuple[tuple[Any, str], ...] = ()
    heading: str = ""
    needs: tuple[str, Any] | None = None
    restart: bool = False
    new: bool = False
    words: tuple[str, ...] = field(default_factory=tuple)


def _choice(key, section, label, choices, help="", **more) -> Option:
    return Option(key, section, label, help, CHOICE, tuple(choices), **more)


def _seconds(values) -> tuple[tuple[float, str], ...]:
    out = []
    for value in values:
        if value == 0:
            out.append((0.0, "Never"))
        elif value < 60:
            out.append((float(value), f"{value:g} s"))
        else:
            out.append((float(value), f"{value / 60:g} min"))
    return tuple(out)


OPTIONS: tuple[Option, ...] = (
    # ---------------------------------------------------------------- look
    _choice("theme", "look", "Theme", tuple(THEME_LABELS.items()),
            "The greys. Accent and density are separate on purpose.",
            heading="Theme"),
    Option("look.blueprint_grid", "look", "Drafting grid behind Blueprint",
           "A faint grid on the window's backdrop, only in the Blueprint "
           "theme.", needs=("theme", "blueprint"), new=True),
    _choice("theme.follow", "look", "Theme changes by itself",
            (("off", "Never: always the theme above"),
             ("windows", "With Windows' light and dark mode"),
             ("schedule", "By time of day")),
            "Switches between the two themes below. The theme above is the "
            "one used when this is off.", new=True,
            words=("dark mode", "light mode", "night", "automatic")),
    _choice("theme.light", "look", "Light theme", tuple(THEME_LABELS.items()),
            "Used in Windows' light mode, or during the day.", new=True),
    _choice("theme.dark", "look", "Dark theme", tuple(THEME_LABELS.items()),
            "Used in Windows' dark mode, or in the evening.", new=True),
    _choice("theme.day_from", "look", "Day theme from",
            tuple((h, f"{h:02d}:00") for h in range(4, 13)),
            needs=("theme.follow", "schedule"), new=True),
    _choice("theme.night_from", "look", "Evening theme from",
            tuple((h, f"{h:02d}:00") for h in range(15, 24)),
            needs=("theme.follow", "schedule"), new=True),
    _choice("density", "look", "Density", tuple(DENSITY_LABELS.items()),
            "Row height and the size of the chrome."),
    _choice("look.font", "look", "Font",
            tuple((key, label) for key, (_stack, label) in FONTS.items()),
            "Cascadia Mono throughout suits the Phosphor theme.", new=True,
            words=("typeface", "monospace")),
    _choice("look.corners", "look", "Corners",
            (("round", "Rounded"), ("square", "Square")),
            "Square suits the Ink theme.", new=True),
    _choice("accent.source", "look", "Accent comes from",
            (("named", "A colour picked here"), ("windows", "Windows' accent"),
             ("wallpaper", "The wallpaper")),
            "Windows' and the wallpaper's are made lighter or darker until "
            "they read well; the hue stays.", heading="Accent", new=True),
    _choice("accent", "look", "Accent", tuple(ACCENT_LABELS.items()),
            "Selection, focus, the active pane and everything else that "
            "says where you are.", needs=("accent.source", "named")),
    _choice("accent.right", "look", "Right pane's accent",
            (("same", "The same as the left"),) + tuple(ACCENT_LABELS.items()),
            "A colour of its own for the right-hand pane, so a glance says "
            "which side has the keyboard.", new=True, words=("pane colour",)),
    _choice("window.backdrop", "look", "Glass backdrop",
            (("auto", "Automatic"), ("glass", "Glass"), ("solid", "Solid")),
            "Automatic is glass where Windows can draw it and solid over "
            "Remote Desktop or Hyper-V, where transparency is turned off.",
            heading="Window", restart=True),
    _choice("window.frame", "look", "Title bar",
            (("custom", "This application's"), ("system", "Windows' own")),
            "Windows' own brings the menu bar back. The way out if the drawn "
            "one misbehaves on a machine it was not tried on.",
            restart=True),
    Option("look.pane_glow", "look", "Glow around the active pane",
           "Off leaves the accent border on its own.", heading="Motion",
           new=True),
    Option("look.motion", "look", "Animations",
           "Folders fade in, the active pane's glow moves across, and the "
           "transfer readout slides in and out."),

    # ------------------------------------------------------------- listing
    Option("listing.hidden", "listing", "Hidden files",
           "Shown dimmed when on. Off leaves them out of the listing; they "
           "are still there, and a new name still avoids them.",
           heading="Files shown", new=True),
    Option("listing.system", "listing", "System files",
           "desktop.ini, thumbs.db and the rest. Separate from hidden, "
           "because most of those are both and a person usually wants one "
           "without the other.", new=True),
    _choice("icons.style", "listing", "Each row starts with",
            (("badges", "Type badge"), ("icons", "Windows icon")),
            "A badge is a tag with the extension, coloured by kind of file.",
            heading="Rows"),
    _choice("listing.column_edges", "listing", "Column edges",
            (("header", "Lines between the headings"),
             ("ruled", "Lines down the whole listing"),
             ("banded", "Every other column shaded"),
             ("off", "Nothing")),
            "Shows where each column ends, so the place to drag a width is "
            "easy to find. The header also takes a drag a few pixels either "
            "side of a line, and shows the width while dragging.",
            new=True, words=("divider", "grid", "border", "resize", "width")),
    _choice("listing.size_bar", "listing", "Size comparison",
            (("behind", "A bar behind the size"),
             ("under", "A line under the size"),
             ("off", "None")),
            "How big each file is against the largest in the folder. Behind "
            "keeps the figure clear of the bar on every theme.",
            new=True, words=("bar", "size")),
    Option("pane.header", "listing", "Folder header",
           "The folder's name above the listing, and a bar of what it holds "
           "by kind of file."),
    Option("icons.shell", "listing", "Windows icons",
           "Off draws every row as its kind. The switch for a shell "
           "extension that misbehaves.", heading="Icons",
           needs=("icons.style", "icons")),
    Option("icons.overlays", "listing", "Icon overlays",
           "Shared folders, OneDrive and source control badges. The one icon "
           "lookup that asks about a file rather than its type.",
           needs=("icons.style", "icons")),
    Option("icons.per_file", "listing", "Icons from the file itself",
           "Programs, shortcuts and .ico files draw their own icon. Reads the "
           "file, for the rows on screen only.",
           needs=("icons.style", "icons")),
    _choice("listing.recency", "listing", "Recently changed files",
            (("off", "Plain"), ("chip", "Age chip"), ("glow", "Glow")),
            "Age chip tints the Age column green for the last day, week and "
            "month. Glow also lights the edge of every row changed today.",
            heading="Age", new=True),
    _choice("listing.fade_days", "listing", "Fade files older than",
            ((0.0, "Never"), (30.0, "1 month"), (90.0, "3 months"),
             (365.0, "1 year")),
            "Old files step back so this week's work stands out. Folders "
            "are left alone.", new=True),
    _choice("listing.space", "listing", "Space on a file",
            (("size", "Counts it, as always"), ("peek", "Peeks at it")),
            "Peek opens a large preview over the window; Up and Down step "
            "through the folder behind it. Space on a folder always counts it.",
            heading="Keys", new=True),
    Option("listing.scrollmap", "listing", "Scrollbar map",
           "Ticks on the scrollbar for marked rows, today's rows and the "
           "rows matching a quick search -- the ones not on screen.",
           heading="Extras", new=True),
    Option("archives.browse", "listing", "Open archives like folders",
           "Enter on a .zip or a .tar (also .tar.gz, .tgz, .tar.bz2, .tar.xz) "
           "goes into it. Inside, files can be viewed, opened and copied out "
           "with F5; nothing inside can be changed. Off hands them to Windows.",
           heading="Archives", new=True, words=("zip", "tar", "extract")),
    Option("listing.remember_sort", "listing", "Remember each folder's sort",
           "Clicking a column heading keeps that order for that folder, and "
           "it comes back the next time the folder is opened. Folders never "
           "sorted keep the order last clicked in the tab. Right-click the "
           "header to forget one.",
           new=True, words=("order", "column", "per folder")),
    Option("listing.folder_bars", "listing", "Bars on counted folders",
           "Once Space has counted folders, each gets a bar against the "
           "largest of them, in a colour of its own.", new=True),
    Option("git.badges", "listing", "Git badges",
           "Inside a git repository on a local disk: a letter on each changed "
           "file (M, A, ?, D) and a dot on a folder with changes under it. "
           "Read by running git once per folder shown.", new=True),
    Option("labels.shown", "listing", "Colour labels and notes",
           "Right-click a row to give it a colour or a note. Kept in this "
           "application's settings, by path: a file renamed or moved outside "
           "this application leaves its label behind. Off hides them; they "
           "are not forgotten.", new=True),
    _choice("flat.layout", "listing", "Flat view shows",
            (("column", "A Location column"), ("groups", "A heading per folder")),
            "Ctrl+B: every file under a folder in one list.",
            heading="Flat view"),
    _choice("refresh.local_seconds", "listing", "Check local folders every",
            _seconds((0, 1, 2, 5, 10)),
            "The folder on screen is listed again to pick up changes made "
            "by other programs.", heading="Live folders"),
    _choice("refresh.network_seconds", "listing", "Check network folders every",
            _seconds((0, 5, 10, 30, 60)),
            "A slow folder is checked less often than this, never more."),

    # ----------------------------------------------------------- rail
    Option("rail.shown", "rail", "Navigation rail",
           "Places, drives, network locations and saved folders down the "
           "left. Ctrl+Shift+B.", heading="Rail"),
    _choice("rail.capacity", "rail", "Drive free space",
            (("rings", "Rings"), ("bars", "Bars"), ("off", "Off")),
            "Only for drives that have been measured: local disks by "
            "themselves, anything else when asked.", new=True),
    Option("favorites.bar", "rail", "Favorites bar",
           "Saved folders as buttons under each tab strip. The rail already "
           "lists them."),
    Option("network.ping", "rail", "Ping network locations",
           "A dot and the milliseconds beside each share in the rail: green, "
           "amber when slow, red when not answering. Only shares a pane has "
           "been to this session are measured.", heading="Share health",
           new=True),
    _choice("network.ping_seconds", "rail", "Check every",
            _seconds((5, 15, 60)), "Longer is quieter on a busy network.",
            needs=("network.ping", True), new=True),
    _choice("network.amber_ms", "rail", "Amber above",
            ((50.0, "50 ms"), (100.0, "100 ms"), (250.0, "250 ms")),
            "Red always means no reply within five seconds.",
            needs=("network.ping", True), new=True),

    # -------------------------------------------------------- previews
    Option("preview.thumbnails", "previews", "Pictures in the grid",
           "Off draws the icon for each kind, which is still a grid and "
           "costs no reads.", heading="Thumbnail grid"),
    _choice("preview.thumb_size", "previews", "Cell size",
            tuple((size, f"{size} px") for size in THUMB_SIZES),
            "Each step is a fresh read of every file on screen."),
    Option("preview.shell", "previews", "Windows thumbnail handlers",
           "Pictures of the kinds this application cannot decode itself: "
           "video frames, Office documents, .psd. The one part of the "
           "previewer that runs somebody else's code.", heading="Decoders"),
    Option("preview.logix", "previews", "Read Logix exports",
           "An .L5X shows its controller, processor, firmware, export date, "
           "counts of tags, programs, routines and modules, its tasks and "
           "its I/O -- instead of its first screen of XML.", new=True),
    Option("preview.peek_motion", "previews", "Peek grows out of the row",
           "Off opens the card in place. Also off whenever animations are.",
           heading="Peek", needs=("listing.space", "peek"), new=True),

    # ------------------------------------------------------- transfers
    Option("transfers.speedline", "transfers", "Speed line in the pill",
           "The running job's speed as a line beside its readout. Click the "
           "pill for the last minute in full.", heading="The pill", new=True),
    Option("basket.enabled", "transfers", "Basket",
           "Alt+Ins puts the marked files into a basket that stays while the "
           "pane goes elsewhere; Copy here and Move here take them all, after "
           "the usual prompt. Off frees the key.", heading="Basket", new=True),
    _choice("notify.after", "transfers", "Say a job finished when it ran longer than",
            _seconds((0, 10, 20, 60, 120)),
            "Only while the window is not in front: the taskbar button "
            "flashes and Windows shows a notification.", heading="When a job ends"),
    _choice("compare.tolerance", "transfers", "Compare treats times as equal within",
            ((0.0, "Exact"), (1.0, "1 s"), (2.0, "2 s"), (5.0, "5 s")),
            "Two seconds is FAT's resolution; an exact compare calls half the "
            "files on a USB stick newer every time.", heading="Compare and sync"),

    # --------------------------------------------------------- general
    Option("menu.shell", "general", "Explorer context menu",
           "The shell's own entries after this application's. Off is the "
           "answer when an extension misbehaves.", heading="Shell"),
    Option("general.single_instance", "general", "One window",
           "Starting File Manager again -- or Open in File Manager from "
           "Explorer -- opens the folder in a new tab of the window already "
           "open, rather than a second window.", heading="Starting",
           restart=True, new=True, words=("instance", "explorer")),
    Option("updates.check_on_launch", "general", "Check for updates on launch",
           "One request to this application's release feed, shortly after "
           "start. Nothing is downloaded without asking.", heading="Updates"),
)


def by_key() -> dict[str, Option]:
    return {option.key: option for option in OPTIONS}


def in_section(section: str) -> list[Option]:
    return [option for option in OPTIONS if option.section == section]


def current(config, option: Option) -> Any:
    """The value to draw for `option`, as one of its own choices.

    A settings file can hold a value no choice matches -- a hand-edited
    number, or one from a later version. The dialog must still show *something*
    selected rather than nothing, so an unmatched value is returned as it is
    and `choices_for` adds it to the row.
    """
    value = config.get(option.key)
    if option.kind == TOGGLE:
        return bool(value)
    for choice, _label in option.choices:
        if _same(choice, value):
            return choice
    return value


def choices_for(config, option: Option) -> tuple[tuple[Any, str], ...]:
    """The choices to draw, with the stored value added if it is not one."""
    value = config.get(option.key)
    if any(_same(choice, value) for choice, _label in option.choices):
        return option.choices
    return (*option.choices, (value, f"{value:g}" if isinstance(value, (int, float))
                              and not isinstance(value, bool) else str(value)))


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) \
            and not isinstance(a, bool) and not isinstance(b, bool):
        return abs(float(a) - float(b)) < 1e-9
    return a == b


def live(config, option: Option) -> bool:
    """Whether the row does anything right now (see `Option.needs`)."""
    if option.needs is None:
        return True
    key, wanted = option.needs
    return _same(config.get(key), wanted)


def matches(option: Option, text: str) -> bool:
    """Whether a search for `text` should show this row.

    Every word has to appear somewhere in the label, the help, the heading or
    the page's name, so "hidden" finds the row and "grid pictures" finds the
    thumbnail switch without anybody having to know which page it is on.
    """
    words = [word for word in (text or "").lower().split() if word]
    if not words:
        return True
    section = dict(SECTIONS).get(option.section, "")
    haystack = " ".join((option.label, option.help, option.heading, section,
                         *option.words)).lower()
    return all(word in haystack for word in words)


def check() -> list[str]:
    """What is wrong with the table, for the test that keeps it honest."""
    problems = []
    sections = dict(SECTIONS)
    seen = set()
    for option in OPTIONS:
        if option.key in seen:
            problems.append(f"{option.key} is listed twice")
        seen.add(option.key)
        if option.key not in DEFAULTS:
            problems.append(f"{option.key} is not a setting")
        if option.section not in sections:
            problems.append(f"{option.key} is on an unknown page {option.section}")
        if option.kind == CHOICE:
            if not option.choices:
                problems.append(f"{option.key} offers nothing")
            elif not any(_same(c, DEFAULTS.get(option.key)) for c, _ in option.choices):
                problems.append(f"{option.key}'s default is not one of its choices")
        elif option.kind == TOGGLE:
            if not isinstance(DEFAULTS.get(option.key), bool):
                problems.append(f"{option.key} is a switch over a value that is not")
        else:
            problems.append(f"{option.key} has an unknown kind {option.kind}")
        if option.needs is not None and option.needs[0] not in DEFAULTS:
            problems.append(f"{option.key} depends on an unknown setting")
    return problems
