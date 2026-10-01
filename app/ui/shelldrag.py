"""Dragging files out of the window the way Explorer does.

0.50.3. A drag out of a listing was Qt's own: `QMimeData.setUrls` with file
URLs, which Qt hands to Windows as `CF_HDROP` -- and also, from the same
converter, as `UniformResourceLocator`, the format a browser uses for a link.
Outlook's message editor takes the link over the file, so a file dragged into
an email arrived as its name (a `file://` link) rather than as an attachment.
Qt offers no way to leave the link format out.

So a drag that starts in a listing is handed to the shell instead: a data
object built by `SHCreateDataObject` from the files' item ID lists -- the same
kind of object Explorer drags, carrying `CF_HDROP`, the shell ID list and the
file descriptors Outlook looks for -- and `SHDoDragDrop` runs the drag. Only a
copy is offered, as before, so nothing dropped elsewhere can take the files
away (`ListingModel.supportedDragActions` gives the reason).

Two rules this keeps:

- **The UI thread does not touch the files.** The ID lists come from
  `SHSimpleIDListFromPath`, which builds them from the path string and does
  not go to the disk -- a drag of files on a share that stopped answering must
  not hang the window. Whatever program the files are dropped on reads them,
  on its own time.
- **A drop on one of this application's own panes still works.** The shell's
  data object does not carry `drops.DRAG_FORMAT`, so while the shell drag is
  running the dragged paths are kept in `drops.outgoing`, and a pane takes a
  drop whose file list is exactly those paths. See `drops.sources_from`.

Everything here is ctypes against shell32 and ole32, and a no-op that says why
anywhere else -- in which case the pane falls back to Qt's drag.
"""

from __future__ import annotations

import sys

DROPEFFECT_COPY = 1

_IID_IDATAOBJECT = "{0000010e-0000-0000-C000-000000000046}"


def available() -> bool:
    return sys.platform == "win32"


def drag(hwnd: int, files: list[str]) -> tuple[bool, str]:
    """Run an Explorer drag of `files` from window `hwnd`.

    Returns `(ran, problem)`. `ran` is False when the drag could not be
    started at all, which is the caller's cue to use Qt's drag instead; once it
    has started, its outcome is the drop target's business.
    """
    if not available():
        return False, "not Windows"
    if not files:
        return False, "nothing to drag"
    try:
        import ctypes
        from ctypes import wintypes
    except ImportError as exc:  # pragma: no cover - ctypes is always there
        return False, str(exc)

    shell32 = ctypes.windll.shell32
    ole32 = ctypes.windll.ole32
    shell32.SHSimpleIDListFromPath.restype = ctypes.c_void_p
    shell32.SHSimpleIDListFromPath.argtypes = [wintypes.LPCWSTR]
    shell32.ILFree.argtypes = [ctypes.c_void_p]
    shell32.SHCreateDataObject.argtypes = [
        ctypes.c_void_p, wintypes.UINT, ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    shell32.SHCreateDataObject.restype = ctypes.c_long
    shell32.SHDoDragDrop.argtypes = [wintypes.HWND, ctypes.c_void_p, ctypes.c_void_p,
                                     wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    shell32.SHDoDragDrop.restype = ctypes.c_long

    pidls: list[int] = []
    data_object = ctypes.c_void_p()
    try:
        for path in files:
            pidl = shell32.SHSimpleIDListFromPath(path)
            if not pidl:
                return False, f"no shell item for {path}"
            pidls.append(pidl)
        iid = _guid(ole32, _IID_IDATAOBJECT)
        folder = common_folder(files)
        if folder:
            # The usual case: every file in one folder, given the way
            # Explorer gives them -- the folder, and each file's last ID
            # relative to it. `ILFindLastID` points into the full list, which
            # stays alive until the finally below.
            parent = shell32.SHSimpleIDListFromPath(folder)
            if not parent:
                return False, f"no shell item for {folder}"
            pidls.append(parent)
            shell32.ILFindLastID.restype = ctypes.c_void_p
            shell32.ILFindLastID.argtypes = [ctypes.c_void_p]
            children = [shell32.ILFindLastID(p) for p in pidls[:-1]]
            folder_pidl = parent
        else:
            # Files from several folders (a flat view, search results): the
            # desktop's own ID list is empty -- two zero bytes -- and every
            # absolute ID list is relative to it.
            desktop = ctypes.create_string_buffer(2)
            children = list(pidls)
            folder_pidl = ctypes.addressof(desktop)
        array = (ctypes.c_void_p * len(children))(*children)
        result = shell32.SHCreateDataObject(folder_pidl, len(children), array,
                                            None, ctypes.addressof(iid),
                                            ctypes.byref(data_object))
        if result < 0 or not data_object.value:
            return False, f"SHCreateDataObject {result & 0xFFFFFFFF:#x}"
        effect = wintypes.DWORD(0)
        # A null drop source is the shell's own: the usual cursors, and Esc
        # or the right button cancels.
        result = shell32.SHDoDragDrop(hwnd, data_object, None, DROPEFFECT_COPY,
                                      ctypes.byref(effect))
        if result < 0:
            return True, f"SHDoDragDrop {result & 0xFFFFFFFF:#x}"
        return True, ""
    except OSError as exc:
        return False, str(exc)
    finally:
        if data_object.value:
            _release(data_object)
        for pidl in pidls:
            shell32.ILFree(pidl)


def common_folder(files: list[str]) -> str:
    """The folder every file is directly in, or "" when they are not all in
    one. Compared case-blind, the way Windows names are."""
    folders = {f.rstrip("\\").rpartition("\\")[0].lower() for f in files}
    if len(folders) != 1:
        return ""
    first = files[0].rstrip("\\").rpartition("\\")[0]
    if not first:
        return ""
    # A drive root keeps its separator: "C:" alone means the current folder
    # on C:, not its root.
    return first + "\\" if first.endswith(":") else first


def _guid(ole32, text: str):
    import ctypes

    class GUID(ctypes.Structure):
        _fields_ = [("a", ctypes.c_ulong), ("b", ctypes.c_ushort),
                    ("c", ctypes.c_ushort), ("d", ctypes.c_ubyte * 8)]

    guid = GUID()
    ole32.CLSIDFromString(ctypes.c_wchar_p(text), ctypes.byref(guid))
    return guid


def _release(pointer) -> None:
    import ctypes

    vtable = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
    release = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtable[2])
    release(pointer)


def start_from_view(view) -> bool:
    """`QAbstractItemView.startDrag`, handed to the shell when it can be.

    True when the shell ran the drag. False leaves it to Qt: off Windows, with
    `listing.shell_drag` off (the view's `shell_drag` attribute), for rows
    inside an archive -- which are not files Windows can take, so their drag
    carries only this application's own format -- or if the shell would not
    start one.
    """
    from app.core import drops

    if not getattr(view, "shell_drag", False) or not available():
        return False
    model = view.model()
    picker = view.selectionModel()
    if model is None or picker is None:
        return False
    indexes = [index for index in picker.selectedIndexes() if index.column() == 0] \
        or picker.selectedIndexes()
    if not indexes:
        return False
    data = model.mimeData(indexes)
    if data is None or not data.hasUrls():
        return False
    files = drops.decode(bytes(data.data(drops.DRAG_FORMAT).data()))
    if not files:
        return False
    drops.outgoing = list(files)
    try:
        ran, problem = drag(int(view.window().winId()), files)
    finally:
        drops.outgoing = None
    if problem:
        status = getattr(view.window(), "statusBar", None)
        if callable(status):
            status().showMessage(f"drag: {problem}", 8000)
    return ran
