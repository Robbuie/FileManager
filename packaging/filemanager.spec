# PyInstaller spec. Built by `packaging/build.py`, not by hand.
#
#     python packaging/build.py
#
# One directory rather than one file. A onefile build unpacks itself into a
# temp folder on every launch, which costs a second or two and leaves the
# working set on disk; this is an application somebody opens twenty times a day
# and closes again, so the folder wins. The installer packs the folder into a
# single setup exe, which is the thing that actually gets downloaded.

import os
import sys

from PyInstaller.utils.hooks import collect_dynamic_libs

# `SPECPATH` is where this file lives; the application is its parent.
ROOT = os.path.dirname(SPECPATH)
sys.path.insert(0, ROOT)

from app import __version__  # noqa: E402

sys.path.insert(0, SPECPATH)
import trim  # noqa: E402 - beside this file, not on the path until now

# The Windows version resource: what the file's Properties tab shows and what
# the installer reads back to check it packed what it thinks it did. Written
# here rather than committed so it cannot disagree with `app/__init__.py` --
# a stale one is invisible until somebody looks at a property sheet and
# concludes they are running last month's build.
PARTS = tuple(int(part) for part in (__version__.split("-")[0].split(".") + ["0", "0", "0"])[:4])
VERSION_RESOURCE = os.path.join(SPECPATH, "version_info.txt")
with open(VERSION_RESOURCE, "w", encoding="utf-8") as handle:
    handle.write(f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={PARTS}, prodvers={PARTS}, mask=0x3f, flags=0x0,
                    OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'Robbuie'),
      StringStruct('FileDescription', 'File Manager'),
      StringStruct('FileVersion', '{__version__}'),
      StringStruct('InternalName', 'FileManager'),
      StringStruct('LegalCopyright', 'MIT'),
      StringStruct('OriginalFilename', 'FileManager.exe'),
      StringStruct('ProductName', 'File Manager'),
      StringStruct('ProductVersion', '{__version__}'),
    ])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])]),
  ],
)
""")

# Qt ships a great deal that a file manager has no use for. Excluding it is
# worth roughly a hundred megabytes, and more to the point it keeps the
# installer down to something worth downloading over a phone tether.
EXCLUDE_QT = [
    "PySide6.Qt3DAnimation", "PySide6.Qt3DCore", "PySide6.Qt3DExtras",
    "PySide6.Qt3DInput", "PySide6.Qt3DLogic", "PySide6.Qt3DRender",
    "PySide6.QtBluetooth", "PySide6.QtCharts", "PySide6.QtDataVisualization",
    "PySide6.QtDesigner", "PySide6.QtHelp", "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets", "PySide6.QtNfc", "PySide6.QtOpenGL",
    "PySide6.QtOpenGLWidgets", "PySide6.QtPdf", "PySide6.QtPdfWidgets",
    "PySide6.QtPositioning", "PySide6.QtQml", "PySide6.QtQuick",
    "PySide6.QtQuick3D", "PySide6.QtQuickControls2", "PySide6.QtQuickWidgets",
    "PySide6.QtRemoteObjects", "PySide6.QtScxml", "PySide6.QtSensors",
    "PySide6.QtSerialBus", "PySide6.QtSerialPort", "PySide6.QtSpatialAudio",
    "PySide6.QtSql", "PySide6.QtStateMachine", "PySide6.QtSvgWidgets",
    "PySide6.QtTest", "PySide6.QtTextToSpeech", "PySide6.QtWebChannel",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineQuick",
    "PySide6.QtWebEngineWidgets", "PySide6.QtWebSockets",
]

# The test tools and the development-only dependencies. A build that quietly
# picked up pytest would ship it.
#
# PIL came off this list in 0.29.11 and that is the whole of what adding
# libheif cost the installer: Pillow is pi-heif's dependency, so excluding
# it would freeze a build whose HEIC rung imports something that is not there,
# and the failure would be a folder of photographs quietly falling through to
# the rung below rather than an error anybody would see.
EXCLUDE_DEV = ["pytest", "_pytest", "tkinter", "unittest", "pydoc"]

analysis = Analysis(
    [os.path.join(SPECPATH, "entry.py")],
    pathex=[ROOT],
    # libheif and its decoders, which pi-heif ships beside its extension
    # module. Both names are asked for because `decode.py` takes either build,
    # and a name that is not installed answers with an empty list -- which is
    # also the answer on a wheel that links its libraries statically, the usual
    # shape on Windows. A missing decoder here would show up only as HEIC files
    # that stopped drawing once frozen, so both are cheap insurance.
    binaries=(collect_dynamic_libs("pi_heif")
              + collect_dynamic_libs("pillow_heif")),
    datas=[],
    # win32api and friends are imported through `app/io/paths.py` behind a
    # try/except so the tests can run off Windows, and an import PyInstaller
    # cannot see is an import it does not bundle.
    hiddenimports=[
        "win32api", "win32file", "win32wnet", "win32com.shell.shell",
        "win32com.shell.shellcon", "pywintypes", "pythoncom",
        # Imported inside `_heif` rather than at module level, for the reason
        # every import in `decode.py` is, so PyInstaller cannot see them.
        "pi_heif", "pillow_heif", "PIL.Image", "PIL.ImageOps",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=EXCLUDE_QT + EXCLUDE_DEV,
    noarchive=False,
)

# `excludes` above keeps modules out. It does not keep out the Qt libraries
# behind them, because those arrive as dependencies of the plugins the PySide6
# hook collects wholesale -- which is how a build whose spec excludes QtQuick
# and QtQml twice over still shipped 13 MB of QML runtime. That has to be done
# here, on what `Analysis` actually collected. `packaging/trim.py` holds the
# list and the reasoning, and is a separate module so the deciding can be
# tested without a four-minute Windows build.
for _what, _dropped in trim.apply(analysis):
    print(f"trim: {_dropped} entries dropped from {_what}")

pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="FileManager",
    icon=os.path.join(SPECPATH, "icon.ico"),
    debug=False,
    strip=False,
    upx=False,          # UPX and antivirus heuristics are a bad pairing.
    console=False,      # A GUI application; the harness is run from source.
    version=VERSION_RESOURCE,
)

collect = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name="FileManager",
)
