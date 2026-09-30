"""Build the application, the installer, and the manifest the updater reads.

    pip install -r requirements.txt
    pip install -r packaging/requirements-build.txt
    python packaging/build.py

Three outputs land in `dist/`: the unpacked `FileManager/` folder, the
`FileManager-Setup-<version>.exe` that gets downloaded, and `latest.json`,
which is the file every installed copy fetches to find out a new version
exists. All three are release assets except the folder.

The one rule this file exists to enforce: **the build tool builds and `gh`
publishes, never both.** Redline PDF let its packager publish once per target,
two of them raced to create the same release, the loser's uploads were orphaned
and the update manifest never arrived -- and the run still exited green, so the
fault surfaced weeks later on a machine that could no longer update. Nothing
here touches the network. What it does instead is refuse to finish: a missing
installer, a manifest naming a file that is not there, or a version that
disagrees with `app/__init__.py` all stop the build, because every one of those
ships a release that cannot be updated to and says nothing about it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DIST = os.path.join(ROOT, "dist")
BUILD = os.path.join(ROOT, "build")

REPO = "Robbuie/FileManager"

#: Where Inno Setup puts itself when nobody has said otherwise. The per-user
#: path is the one winget uses, and it is not on PATH.
ISCC_CANDIDATES = (
    r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    r"C:\Program Files\Inno Setup 6\ISCC.exe",
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Inno Setup 6", "ISCC.exe"),
    os.path.join(os.environ.get("ProgramFiles(x86)", ""), "Inno Setup 6", "ISCC.exe"),
    os.path.join(os.environ.get("ProgramFiles", ""), "Inno Setup 6", "ISCC.exe"),
)

#: Inno Setup's own uninstall key, which records where it went. Asking Windows
#: beats guessing: the installer offers per-machine and per-user, winget picks
#: for itself, and a guessed list is a list that is wrong on somebody's machine.
ISCC_REGISTRY_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup 6_is1"


def version() -> str:
    """Read the version out of `app/__init__.py` without importing the app.

    Importing it would pull in Qt, which is a long way to go for a string and
    fails outright in a checkout that has not installed the dependencies yet.
    """
    source = os.path.join(ROOT, "app", "__init__.py")
    with open(source, "r", encoding="utf-8") as handle:
        for line in handle:
            if line.startswith("__version__"):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("no __version__ in app/__init__.py")


def run(command: list[str], *, cwd: str | None = None) -> None:
    print("+", " ".join(command))
    result = subprocess.run(command, cwd=cwd or ROOT)
    if result.returncode != 0:
        raise SystemExit(f"failed ({result.returncode}): {command[0]}")


def iscc_from_registry() -> str | None:
    """Where Inno Setup says it installed itself, if it is there at all."""
    if os.name != "nt":
        return None
    import winreg

    views = (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY)
    for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for view in views:
            try:
                with winreg.OpenKey(root, ISCC_REGISTRY_KEY, 0,
                                    winreg.KEY_READ | view) as key:
                    location, _ = winreg.QueryValueEx(key, "InstallLocation")
            except OSError:
                continue
            candidate = os.path.join(str(location), "ISCC.exe")
            if os.path.isfile(candidate):
                return candidate
    return None


def find_iscc() -> str:
    """ISCC.exe, from the four places it can reasonably be."""
    override = os.environ.get("INNO_SETUP")
    if override:
        if not os.path.isfile(override):
            raise SystemExit(f"INNO_SETUP points at {override}, which is not a file")
        return override
    found = shutil.which("iscc") or shutil.which("ISCC") or iscc_from_registry()
    if found:
        return found
    for candidate in ISCC_CANDIDATES:
        if candidate and os.path.isfile(candidate):
            return candidate
    raise SystemExit(
        "Inno Setup 6 not found on PATH, in the registry, or in the usual "
        "folders. Install it from https://jrsoftware.org/isdl.php, or set "
        "INNO_SETUP to the full path of ISCC.exe if it is somewhere unusual."
    )


def digest(path: str) -> tuple[str, int]:
    hasher = hashlib.sha256()
    size = 0
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            hasher.update(block)
            size += len(block)
    return hasher.hexdigest(), size


def freeze(clean: bool) -> None:
    if clean:
        for folder in (DIST, BUILD):
            shutil.rmtree(folder, ignore_errors=True)
    run([sys.executable, "-m", "PyInstaller", "--noconfirm",
         os.path.join("packaging", "filemanager.spec")])
    produced = os.path.join(DIST, "FileManager", "FileManager.exe")
    if not os.path.isfile(produced):
        raise SystemExit(f"PyInstaller did not write {produced}")
    check_links(os.path.join(DIST, "FileManager"))


def check_links(folder: str) -> None:
    """Refuse a build in which a kept binary links to a DLL that is not there.

    0.39. `trim.py` removes files by name, and a name removed while something
    kept still links to it does not fail the freeze -- it fails on the user's
    machine, the first time that module is imported, as a feature that has
    quietly stopped working. Every `.pyd`, `.dll` and `.exe` in the folder is
    read with pefile (PyInstaller's own dependency, so nothing new) and its
    import table compared against the folder and this machine's System32.

    It cannot see what Qt loads by name at run time -- plugins, and OpenSSL
    for Qt's TLS -- which is why those entries in `trim.py` each carry their
    own argument. It does see every static link, which is the kind of mistake
    that reads as right in a list.

    The folder's size is printed too, so a release that grew is noticed in
    the build log rather than on somebody's disk.
    """
    import pefile  # noqa: PLC0415 - build-time only, and PyInstaller brings it

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import trim  # noqa: PLC0415

    present: set[str] = set()
    binaries: list[str] = []
    total = 0
    for base, _dirs, files in os.walk(folder):
        for name in files:
            path = os.path.join(base, name)
            present.add(name)
            total += os.path.getsize(path)
            if name.lower().endswith((".pyd", ".dll", ".exe")):
                binaries.append(path)
    system_root = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
    system = set(os.listdir(system_root)) if os.path.isdir(system_root) else set()

    imports: dict[str, list[str]] = {}
    for path in binaries:
        try:
            image = pefile.PE(path, fast_load=True)
            image.parse_data_directories(directories=[
                pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"]])
        except pefile.PEFormatError:
            continue
        names = [entry.dll.decode("ascii", "replace")
                 for entry in getattr(image, "DIRECTORY_ENTRY_IMPORT", [])]
        image.close()
        imports[os.path.relpath(path, folder)] = names

    problems = trim.missing_links(imports, present, system)
    if problems:
        lines = [f"  {binary}: {', '.join(names)}" for binary, names in sorted(problems.items())]
        raise SystemExit("these link to DLLs the build does not contain -- "
                         "check packaging/trim.py:\n" + "\n".join(lines))
    print(f"links checked: {len(imports)} binaries, none missing a DLL")
    print(f"frozen folder: {total / 1e6:.1f} MB in {len(present)} files")


def package(release: str) -> str:
    run([find_iscc(), f"/DAppVersion={release}",
         os.path.join("packaging", "installer.iss")])
    installer = os.path.join(DIST, f"FileManager-Setup-{release}.exe")
    if not os.path.isfile(installer):
        raise SystemExit(f"Inno Setup did not write {installer}")
    return installer


def manifest(release: str, installer: str) -> str:
    """Write the file the shipped updater reads.

    Deliberately not the GitHub API. The API is rate limited without a token,
    returns several kilobytes to answer one question, and changes shape at
    GitHub's convenience. A static asset at
    `releases/latest/download/latest.json` is one unauthenticated GET that
    always resolves to the newest release, and its contents are this file's
    problem rather than a third party's.
    """
    name = os.path.basename(installer)
    sha256, size = digest(installer)
    payload = {
        "version": release,
        "notes": f"https://github.com/{REPO}/blob/main/CHANGELOG.md",
        "release": f"https://github.com/{REPO}/releases/tag/v{release}",
        "installer": {
            "name": name,
            "url": f"https://github.com/{REPO}/releases/download/v{release}/{name}",
            "size": size,
            "sha256": sha256,
        },
    }
    target = os.path.join(DIST, "latest.json")
    with open(target, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")
    return target


def verify(release: str, target: str) -> None:
    """Read back what was written and check it describes what is on disk.

    This is the step that would have caught the Redline PDF release: the
    manifest is only useful if the file it names is really there, under exactly
    that name, with exactly that hash.
    """
    with open(target, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if payload.get("version") != release:
        raise SystemExit("latest.json version does not match app/__init__.py")
    installer = payload["installer"]
    beside = os.path.join(DIST, installer["name"])
    if not os.path.isfile(beside):
        raise SystemExit(f"latest.json names {installer['name']}, which is not in dist/")
    if " " in installer["name"]:
        raise SystemExit(
            f"{installer['name']} has a space in it; GitHub will rename it on "
            "upload and the url in latest.json will 404"
        )
    sha256, size = digest(beside)
    if (sha256, size) != (installer["sha256"], installer["size"]):
        raise SystemExit("latest.json does not match the installer beside it")
    if not installer["url"].endswith("/" + installer["name"]):
        raise SystemExit("latest.json url does not end in the installer's name")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--skip-installer", action="store_true",
                        help="freeze only; no Inno Setup, no manifest")
    parser.add_argument("--no-clean", action="store_true",
                        help="keep dist/ and build/ from the last run")
    args = parser.parse_args(argv)

    release = version()
    print(f"File Manager {release}")

    freeze(clean=not args.no_clean)
    if args.skip_installer:
        print(f"built {os.path.join(DIST, 'FileManager')}")
        return 0

    installer = package(release)
    target = manifest(release, installer)
    verify(release, target)

    print()
    print("release assets:")
    for path in (installer, target):
        print(f"  {os.path.relpath(path, ROOT)}  ({os.path.getsize(path):,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
