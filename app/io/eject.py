"""Safely removing a USB drive: which letters can be ejected, and ejecting one.

0.27. Two questions, both Windows calls, both in `io` because both open a
device handle -- a local, instant open of `\\\\.\\E:` that reads nothing from
the disk, but a handle all the same, and handles belong in a worker.

**Which letters.** `GetDriveType` says "removable" for a stick and a card
reader, and says "fixed" for a USB hard drive or SSD, which is most of what
gets plugged into a laptop now. So a fixed disk is asked for its bus with
`IOCTL_STORAGE_QUERY_PROPERTY`; USB and 1394 count. A remote drive is never
asked anything -- the answer would be a network call, and a mapped drive is not
something to eject.

**Ejecting.** What Explorer's "Eject" does: find the disk device behind the
volume and ask Plug and Play to remove it with `CM_Request_Device_EjectW`. The
useful part is the veto. When something has a file open on the drive, Windows
refuses and says *who* -- a process path for an application, a service name for
a service -- and that sentence is worth ten times "the device is in use". The
volume is matched to its disk by device number (`IOCTL_STORAGE_GET_DEVICE_NUMBER`
on both), because a volume and a disk are separate device nodes and only the
disk can be ejected.

The application's own handles are the caller's job, not this module's: by the
time this runs, `core/eject.py` has moved every pane off the drive and refused
if a transfer touches it. Otherwise the veto names this application, which is
correct and embarrassing.

Every call here returns an answer and never raises, for the reason every other
io call does: a failure is a sentence for the status line.
"""

from __future__ import annotations

import ntpath
import os
from dataclasses import dataclass

_IOCTL_STORAGE_QUERY_PROPERTY = 0x002D1400
_IOCTL_STORAGE_GET_DEVICE_NUMBER = 0x002D1080
_FILE_SHARE_RW = 0x00000001 | 0x00000002
_OPEN_EXISTING = 3
_INVALID_HANDLE = -1
_BUS_USB = 7
_BUS_1394 = 4
_CR_SUCCESS = 0
_CM_GET_DEVICE_INTERFACE_LIST_PRESENT = 0
#: GUID_DEVINTERFACE_DISK
_DISK_GUID = "{53F56307-B6BF-11D0-94F2-00A0C91EFB8B}"
#: DEVPKEY_Device_InstanceId
_INSTANCE_ID_GUID = "{78C34FC8-104A-4ACA-9EA4-524D52996E57}"
_INSTANCE_ID_PID = 256

#: PNP_VETO_TYPE, as the words the status line uses.
VETO_REASONS = {
    0: "Windows would not say why",
    1: "a legacy device is using it",
    2: "it is still being closed",
    3: "{name} is using it",
    4: "the {name} service is using it",
    5: "a file on it is still open",
    6: "another device depends on it",
    7: "its driver will not let it go",
    8: "Windows refused the request",
    9: "removing it would cut power to something else",
    10: "Windows does not allow this device to be removed",
    11: "a legacy driver is using it",
    12: "this account is not allowed to remove it",
}


@dataclass(frozen=True, slots=True)
class Outcome:
    ok: bool
    message: str


def veto_sentence(letter: str, veto_type: int, veto_name: str) -> str:
    """"E: was not ejected: EXCEL.EXE is using it." Pure, so it is tested."""
    # ntpath, not os.path: the name is a Windows path on every machine this
    # runs on, including the one the tests run on.
    name = ntpath.basename(veto_name.rstrip("\\")) if veto_name else ""
    template = VETO_REASONS.get(veto_type, VETO_REASONS[0])
    if "{name}" in template and not name:
        template = VETO_REASONS[5] if veto_type == 3 else "a service is using it"
    return f"{letter} was not ejected: {template.format(name=name)}"


def _kernel():
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                     wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD,
                                     wintypes.HANDLE]
    kernel32.DeviceIoControl.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPVOID,
                                         wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
                                         ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    return ctypes, wintypes, kernel32


def _open(kernel32, wintypes, device: str):
    handle = kernel32.CreateFileW(device, 0, _FILE_SHARE_RW, None, _OPEN_EXISTING, 0, None)
    if handle is None or handle == wintypes.HANDLE(_INVALID_HANDLE).value:
        return None
    return handle


def _bus(letter: str) -> int | None:
    ctypes, wintypes, kernel32 = _kernel()
    handle = _open(kernel32, wintypes, "\\\\.\\" + letter.rstrip("\\"))
    if handle is None:
        return None
    try:
        # STORAGE_PROPERTY_QUERY { PropertyId=StorageDeviceProperty(0),
        # QueryType=PropertyStandardQuery(0), AdditionalParameters[1] }
        query = (ctypes.c_ubyte * 12)()
        out = (ctypes.c_ubyte * 1024)()
        got = wintypes.DWORD(0)
        if not kernel32.DeviceIoControl(handle, _IOCTL_STORAGE_QUERY_PROPERTY,
                                        query, 12, out, 1024, ctypes.byref(got), None):
            return None
        # STORAGE_DEVICE_DESCRIPTOR: BusType is the DWORD at offset 28.
        return int.from_bytes(bytes(out[28:32]), "little")
    finally:
        kernel32.CloseHandle(handle)


def is_ejectable(letter: str, kind: str) -> bool:
    """A removable drive, or a fixed one on the USB or 1394 bus."""
    if os.name != "nt":
        return False
    if kind == "removable":
        return True
    if kind != "fixed":
        return False
    try:
        return _bus(letter) in (_BUS_USB, _BUS_1394)
    except Exception:  # noqa: BLE001 - an answer, never a failure
        return False


def _device_number(kernel32, wintypes, ctypes, device: str) -> tuple[int, int] | None:
    handle = _open(kernel32, wintypes, device)
    if handle is None:
        return None
    try:
        # STORAGE_DEVICE_NUMBER { DeviceType, DeviceNumber, PartitionNumber }
        out = (ctypes.c_ulong * 3)()
        got = wintypes.DWORD(0)
        if not kernel32.DeviceIoControl(handle, _IOCTL_STORAGE_GET_DEVICE_NUMBER, None, 0,
                                        out, ctypes.sizeof(out), ctypes.byref(got), None):
            return None
        return int(out[0]), int(out[1])
    finally:
        kernel32.CloseHandle(handle)


def eject(letter: str) -> Outcome:
    letter = letter.rstrip("\\").upper()
    if os.name != "nt":
        return Outcome(False, "ejecting a drive is a Windows feature")
    try:
        return _eject(letter)
    except Exception as exc:  # noqa: BLE001
        return Outcome(False, f"{letter} was not ejected: {exc}")


def _eject(letter: str) -> Outcome:
    ctypes, wintypes, kernel32 = _kernel()
    cfg = ctypes.WinDLL("cfgmgr32")
    ole32 = ctypes.WinDLL("ole32")

    wanted = _device_number(kernel32, wintypes, ctypes, "\\\\.\\" + letter)
    if wanted is None:
        return Outcome(False, f"{letter} was not ejected: the drive would not answer")

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort),
                    ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8)]

    class DEVPROPKEY(ctypes.Structure):
        _fields_ = [("fmtid", GUID), ("pid", ctypes.c_ulong)]

    disk = GUID()
    ole32.CLSIDFromString(_DISK_GUID, ctypes.byref(disk))
    size = ctypes.c_ulong(0)
    if cfg.CM_Get_Device_Interface_List_SizeW(ctypes.byref(size), ctypes.byref(disk), None,
                                              _CM_GET_DEVICE_INTERFACE_LIST_PRESENT) != _CR_SUCCESS:
        return Outcome(False, f"{letter} was not ejected: the disks could not be listed")
    listing = ctypes.create_unicode_buffer(max(1, size.value))
    if cfg.CM_Get_Device_Interface_ListW(ctypes.byref(disk), None, listing, size,
                                         _CM_GET_DEVICE_INTERFACE_LIST_PRESENT) != _CR_SUCCESS:
        return Outcome(False, f"{letter} was not ejected: the disks could not be listed")
    interfaces = [part for part in ctypes.wstring_at(listing, size.value).split("\0") if part]

    key = DEVPROPKEY()
    ole32.CLSIDFromString(_INSTANCE_ID_GUID, ctypes.byref(key.fmtid))
    key.pid = _INSTANCE_ID_PID
    for interface in interfaces:
        if _device_number(kernel32, wintypes, ctypes, interface) != wanted:
            continue
        prop_type = ctypes.c_ulong(0)
        buffer = ctypes.create_unicode_buffer(512)
        length = ctypes.c_ulong(ctypes.sizeof(buffer))
        if cfg.CM_Get_Device_Interface_PropertyW(interface, ctypes.byref(key),
                                                 ctypes.byref(prop_type), buffer,
                                                 ctypes.byref(length), 0) != _CR_SUCCESS:
            continue
        node = ctypes.c_ulong(0)
        if cfg.CM_Locate_DevNodeW(ctypes.byref(node), buffer.value, 0) != _CR_SUCCESS:
            continue
        return _request(cfg, ctypes, node, letter)
    return Outcome(False, f"{letter} was not ejected: its disk could not be found")


def _request(cfg, ctypes, node, letter: str) -> Outcome:
    """Eject the disk; if Windows declines without a veto, try its parent, which
    is the USB storage device some adapters want asked instead."""
    for attempt in range(2):
        veto = ctypes.c_int(0)
        name = ctypes.create_unicode_buffer(260)
        result = cfg.CM_Request_Device_EjectW(node, ctypes.byref(veto), name, 260, 0)
        if result == _CR_SUCCESS and veto.value == 0:
            return Outcome(True, f"{letter} can be removed")
        if veto.value != 0:
            return Outcome(False, veto_sentence(letter, veto.value, name.value))
        if attempt == 0:
            parent = ctypes.c_ulong(0)
            if cfg.CM_Get_Parent(ctypes.byref(parent), node, 0) != _CR_SUCCESS:
                break
            node = parent
    return Outcome(False, f"{letter} was not ejected: Windows refused the request")
