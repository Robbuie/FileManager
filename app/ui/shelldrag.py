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

0.56.2: that did not fix it on the machine it was reported from -- the file
still arrived in Outlook as its name -- and nothing about the drop says whether
the shell drag ran and Outlook declined it, or failed and Qt's drag ran in its
place. So there is a third drag, and the default: `drag_file_list` hands
Windows a data object of this application's own that carries the file list
(`CF_HDROP`) and the preferred effect and nothing else. That is the shape of
Double Commander's drag, which Outlook attaches from, and with no link and no
text in it there is nothing else for a target to take. `listing.drag_out`
picks between the three, so they can be tried against Outlook one after the
other without a build in between, and a drag that fell back to Qt says so on
the status bar.
"""

from __future__ import annotations

import struct
import sys

DROPEFFECT_COPY = 1

#: The values `listing.drag_out` takes, in the order Options offers them.
FILE_LIST = "files"
SHELL = "shell"
QT = "qt"
DRAG_OUT = (FILE_LIST, SHELL, QT)

CF_HDROP = 15
#: What `IDropSource` answers. Success codes, not failures, which is why they
#: are returned rather than raised.
DRAGDROP_S_DROP = 0x00040100
DRAGDROP_S_CANCEL = 0x00040101
DRAGDROP_S_USEDEFAULTCURSORS = 0x00040102
MK_LBUTTON = 0x0001
MK_RBUTTON = 0x0002

#: The size of `DROPFILES`: the offset of the names, a point, and two BOOLs.
_DROPFILES_SIZE = 20

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


def dropfiles(files: list[str]) -> bytes:
    """`CF_HDROP` as the bytes Windows expects: a `DROPFILES` header, then
    every path in UTF-16, each ended by a null and the list by one more.

    Pure, so it is tested off Windows. The paths are the strings the listing
    already holds; nothing here goes near the files.
    """
    header = struct.pack("<IiiII", _DROPFILES_SIZE, 0, 0, 0, 1)
    names = "".join(f"{path}\0" for path in files) + "\0"
    return header + names.encode("utf-16-le")


def drag_file_list(files: list[str]) -> tuple[bool, str]:
    """Run a drag of `files` carrying only the file list (0.56.2).

    Returns `(ran, problem)` the way `drag` does. A data object and a drop
    source of this application's own, served through pywin32's COM gateways;
    `DoDragDrop` runs the drag on this thread, which Qt has already initialised
    for OLE because its own drags need the same.
    """
    if not available():
        return False, "not Windows"
    if not files:
        return False, "nothing to drag"
    try:
        import ctypes

        import pythoncom
        from win32com.server.exception import COMException
        from win32com.server.util import NewEnum, wrap
    except ImportError as exc:
        return False, f"no pywin32: {exc}"

    # Registered by name, so the value is whatever this session gave it.
    preferred = ctypes.windll.user32.RegisterClipboardFormatW("Preferred DropEffect")
    payload = {CF_HDROP: dropfiles(files)}
    if preferred:
        payload[preferred] = struct.pack("<I", DROPEFFECT_COPY)

    E_NOTIMPL = -2147467263            # 0x80004001
    DV_E_FORMATETC = -2147221404       # 0x80040064
    DV_E_TYMED = -2147221399           # 0x80040069
    DV_E_DVASPECT = -2147221397        # 0x8004006B
    OLE_E_ADVISENOTSUPPORTED = -2147221501  # 0x80040003

    class FileList:
        _com_interfaces_ = [pythoncom.IID_IDataObject]
        _public_methods_ = ["GetData", "GetDataHere", "QueryGetData",
                            "GetCanonicalFormatEtc", "SetData", "EnumFormatEtc",
                            "DAdvise", "DUnadvise", "EnumDAdvise"]

        formats = [(cf, None, pythoncom.DVASPECT_CONTENT, -1, pythoncom.TYMED_HGLOBAL)
                   for cf in payload]

        def _check(self, fe):
            cf, _target, aspect, _index, tymed = fe
            if cf not in payload:
                raise COMException(hresult=DV_E_FORMATETC)
            if not aspect & pythoncom.DVASPECT_CONTENT:
                raise COMException(hresult=DV_E_DVASPECT)
            if not tymed & pythoncom.TYMED_HGLOBAL:
                raise COMException(hresult=DV_E_TYMED)
            return cf

        def GetData(self, fe):  # noqa: N802 - COM naming
            medium = pythoncom.STGMEDIUM()
            medium.set(pythoncom.TYMED_HGLOBAL, payload[self._check(fe)])
            return medium

        def QueryGetData(self, fe):  # noqa: N802
            self._check(fe)

        def EnumFormatEtc(self, direction):  # noqa: N802
            if direction != pythoncom.DATADIR_GET:
                raise COMException(hresult=E_NOTIMPL)
            return NewEnum(self.formats, iid=pythoncom.IID_IEnumFORMATETC)

        # A target may offer things back (Explorer writes what it did, and
        # its drag image helper writes pictures); declining them all is what
        # a data object with nothing to remember does.
        def GetDataHere(self, fe):  # noqa: N802
            raise COMException(hresult=E_NOTIMPL)

        def GetCanonicalFormatEtc(self, fe):  # noqa: N802
            raise COMException(hresult=E_NOTIMPL)

        def SetData(self, fe, medium, release):  # noqa: N802
            raise COMException(hresult=E_NOTIMPL)

        def DAdvise(self, fe, flags, sink):  # noqa: N802
            raise COMException(hresult=OLE_E_ADVISENOTSUPPORTED)

        def DUnadvise(self, connection):  # noqa: N802
            raise COMException(hresult=OLE_E_ADVISENOTSUPPORTED)

        def EnumDAdvise(self):  # noqa: N802
            raise COMException(hresult=OLE_E_ADVISENOTSUPPORTED)

    class Source:
        _com_interfaces_ = [pythoncom.IID_IDropSource]
        _public_methods_ = ["QueryContinueDrag", "GiveFeedback"]

        def QueryContinueDrag(self, escape, keys):  # noqa: N802
            # Esc, or the right button joining in, cancels -- Explorer's rule.
            if escape or keys & MK_RBUTTON:
                return DRAGDROP_S_CANCEL
            if not keys & MK_LBUTTON:
                return DRAGDROP_S_DROP
            return 0

        def GiveFeedback(self, effect):  # noqa: N802
            return DRAGDROP_S_USEDEFAULTCURSORS

    try:
        data = wrap(FileList(), iid=pythoncom.IID_IDataObject, useDispatcher=0)
        source = wrap(Source(), iid=pythoncom.IID_IDropSource, useDispatcher=0)
    except Exception as exc:  # noqa: BLE001 - any failure here means use Qt
        return False, f"data object: {exc}"
    try:
        pythoncom.DoDragDrop(data, source, DROPEFFECT_COPY)
    except pythoncom.com_error as exc:
        # It started, so it is not Qt's to run again; say what happened.
        return True, f"DoDragDrop {exc}"
    return True, ""


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
    """`QAbstractItemView.startDrag`, handed to Windows when it can be.

    True when Windows ran the drag. False leaves it to Qt: off Windows, with
    `listing.drag_out` set to Qt (the view's `drag_out` attribute), for rows
    inside an archive -- which are not files Windows can take, so their drag
    carries only this application's own format -- or if Windows would not
    start one, in which case the status bar says why. That last is the line
    worth reading when a file still arrives in Outlook as its name: Qt's drag
    is the one that carries a link.
    """
    from app.core import drops

    how = getattr(view, "drag_out", QT)
    if how not in (FILE_LIST, SHELL) or not available():
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
        if how == FILE_LIST:
            ran, problem = drag_file_list(files)
        else:
            ran, problem = drag(int(view.window().winId()), files)
    finally:
        drops.outgoing = None
    if problem:
        said = f"drag: {problem}" if ran else \
            f"drag: {problem} -- used Qt's drag, which Outlook takes as a link"
        status = getattr(view.window(), "statusBar", None)
        if callable(status):
            status().showMessage(said, 15000)
    return ran
