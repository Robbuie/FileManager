"""What PyInstaller collects that this application never loads.

`filemanager.spec` already names the Qt modules to leave out, and that list
does what it says: no `PySide6.QtQuick` is bundled and no `PySide6.QtPdf`.
What it does not do is keep out the Qt *libraries* behind them, because the
two are collected by different routes. A module is excluded by name. A DLL
arrives because something PyInstaller did collect links to it -- and the
things doing the linking here are the **plugins**, which the PySide6 hook
gathers wholesale. `qtvirtualkeyboardplugin.dll` is one file of a few hundred
kilobytes, it is collected because it sits in `plugins/platforminputcontexts`,
and it drags Qt6VirtualKeyboard, Qt6Quick, Qt6Qml and Qt6QmlModels in behind
it: thirteen megabytes of a QML runtime that a widgets application will never
start, arriving in a build whose spec says twice over not to include QML.

So the filtering has to happen after `Analysis`, on the collected file lists,
and this is that filter. It is a pure function on a path taken out of the spec
for the reason `fit_popup` is out of the menu code: deciding what to drop is
string comparison and worth testing, while the thing it is deciding about
needs Windows, PyInstaller and four minutes.

**The list is short on purpose and every entry says why it is safe.** Getting
this wrong is not a build failure -- it is a build that works on the machine it
was made on and fails somewhere specific much later, the way an absent image
plugin means a folder of drawings quietly stops drawing. Two things were on
this list while it was being written and came off again after a look at what
the application actually does, which is the rate to expect:

  * **Qt6Pdf and `imageformats/qpdf.dll` stay.** `PREVIEW_IMAGE_KINDS` has
    `.pdf` in it, and `protocol.py` is explicit that this is deliberate: Qt's
    PDF plugin registers as an image format, so page one of a drawing set
    previews like a photograph. `PySide6.QtPdf` is excluded as a *module* and
    the plugin is what is used, which is exactly why reading the exclusion
    list and stopping there gets this wrong.
  * **Qt6Svg, `imageformats/qsvg.dll` and `pythonwin` stay.** `.svg` and
    `.svgz` are previewable kinds; `app/ui/glyphs.py` saying it needs no QtSvg
    is a statement about the chrome, not about what the previewer reads. And
    `win32ui` is imported by both `io/menu.py` and `io/worker.py`, which is
    how an `HBITMAP` from the shell becomes pixels.

The saving is around 46 MB of a 115 MB folder. `opengl32sw.dll` is more than
half of it and is the one entry here worth confirming by hand rather than by
reasoning -- see its note below.
"""

from __future__ import annotations

#: Files dropped by exact destination path, case-insensitively, forward slashes.
EXCLUDE_FILES = frozenset({
    # Mesa's software OpenGL, and 20 MB of the 34 MB installer on its own. Qt
    # loads it only when it needs a GL context it cannot get from the driver,
    # and a Qt Widgets application does not ask for one: the widget stack
    # paints through the raster engine, and every module that would have
    # wanted GL -- QtQuick, QtOpenGLWidgets, QtDataVisualization -- is already
    # excluded or dropped below.
    #
    # **This is the entry to check against a real session rather than against
    # this comment**, and specifically over a remote desktop connection, since
    # that is where Windows hands out a limited driver and where Qt is most
    # likely to go looking for a fallback. A window that opens locally proves
    # less than it looks.
    "pyside6/opengl32sw.dll",

    # The QML runtime, which arrives behind the on-screen keyboard plugin and
    # nothing else. That plugin is for tablets and kiosks; a dual-pane file
    # manager driven by the Norton keymap has no use for one, and a keyboard
    # appearing over a listing would be a bug rather than a feature. The
    # plugin is the entry that matters -- the four libraries above it are what
    # it pulls, and are listed as well so that a build which finds another
    # route to them still drops them.
    "pyside6/qt6virtualkeyboard.dll",
    "pyside6/qt6quick.dll",
    "pyside6/qt6qml.dll",
    "pyside6/qt6qmlmodels.dll",
    "pyside6/plugins/platforminputcontexts/qtvirtualkeyboardplugin.dll",

    # OpenGL's Qt module, a dependency of QtQuick rather than of anything here.
    # `PySide6.QtOpenGL` and `PySide6.QtOpenGLWidgets` are both already in the
    # spec's module exclusions; this is the library they would have used.
    "pyside6/qt6opengl.dll",

    # Qt's networking, which this application does not do. The one network call
    # it makes is `app/core/updates.py`, and that is `urllib` on a thread --
    # chosen there for reasons of its own, and the reason this is droppable.
    #
    # `libcrypto-3.dll` and `libssl-3.dll` are NOT here and must not be: they
    # sit at the root of `_internal` beside `_ssl.pyd` and `_hashlib.pyd`
    # rather than in `PySide6/`, because they are *Python's* OpenSSL. They are
    # what the update check's HTTPS and its SHA-256 verification run on.
    "pyside6/qtnetwork.pyd",
    "pyside6/qt6network.dll",
    "pyside6/plugins/tls/qcertonlybackend.dll",
    "pyside6/plugins/tls/qopensslbackend.dll",
    "pyside6/plugins/tls/qschannelbackend.dll",
    "pyside6/plugins/networkinformation/qnetworklistmanager.dll",

    # TUIO is a protocol for tracking fingers on a table-sized touch surface,
    # delivered over UDP. It is collected because it lives in `plugins/generic`.
    "pyside6/plugins/generic/qtuiotouchplugin.dll",
})

#: Whole folders dropped, matched as a prefix on the same normalised path.
#:
#: `translations/` is Qt's translations of Qt's own strings -- 96 `.qm` files
#: covering standard dialog buttons and shortcut names, 6.6 MB of them.
#: Nothing here installs a `QTranslator`, so they are not loaded in any locale.
#: This is not the application's own text; there is none to translate. If this
#: ever grows a UI language, the folder comes back.
EXCLUDE_FOLDERS = ("pyside6/translations/",)


def normalise(destination: str) -> str:
    """One spelling of a collected file's path, for comparing against the lists.

    PyInstaller writes destinations with the building platform's separator, and
    the DLL names are whatever case the wheel shipped -- `Qt6Quick.dll` on one
    machine is no guarantee about the next. Both are flattened here so the
    lists above can be written in one obvious form.
    """
    return destination.replace("\\", "/").lstrip("./").lower()


def keep(destination: str) -> bool:
    """Whether a collected file belongs in the build.

    Everything not named above, which is nearly everything: this refuses a
    known list rather than allowing one. An allow-list would be a build that
    breaks every time PySide6 renames a file, and it would break by leaving
    something out -- the failure that does not announce itself.
    """
    path = normalise(destination)
    if path in EXCLUDE_FILES:
        return False
    return not any(path.startswith(folder) for folder in EXCLUDE_FOLDERS)


def apply(analysis) -> list[tuple[str, int]]:
    """Drop the excluded entries from an `Analysis`, in place.

    Returns `(what, how many)` per list so the build can print what it did.
    A build that silently shipped 46 MB it meant to drop -- because a name
    changed, or because the hook started collecting plugins by another route
    -- would look exactly like a build that worked.
    """
    report: list[tuple[str, int]] = []
    for name in ("binaries", "datas"):
        entries = getattr(analysis, name, None)
        if entries is None:
            continue
        kept = [entry for entry in entries if keep(entry[0])]
        report.append((name, len(entries) - len(kept)))
        setattr(analysis, name, kept)
    return report
