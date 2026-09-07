@echo off
rem Collect everything a session needs to see about the context menu, without
rem anybody retyping it. Double-click it, or drag a folder onto it to ask about
rem that folder instead of this repository -- a working copy with TortoiseSVN
rem in it is the interesting one.
rem
rem It writes "Claude outputs\menu-diagnosis.txt", which is where a session can
rem read it from. Nothing here changes a file: every command below builds a
rem menu, prints it and releases it.
setlocal
cd /d "%~dp0.."

set "TARGET=%CD%"
set "ITEM=README.md"
if not "%~1"=="" (
  set "TARGET=%~1"
  set "ITEM="
)

set "PY=python"
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"

set "OUT=Claude outputs\menu-diagnosis.txt"
if not exist "Claude outputs" mkdir "Claude outputs"

echo Asking the shell about %TARGET%
echo This writes %OUT%
echo.

(
  echo ==== version ====
  "%PY%" -c "import app; print(app.__version__)"
  echo.
  echo ==== menu for one item: %TARGET% %ITEM% ====
  "%PY%" -m app.io.harness menu "%TARGET%" %ITEM%
  echo.
  echo ==== menu for the folder background: %TARGET% ====
  "%PY%" -m app.io.harness menu "%TARGET%"
  echo.
  echo ==== the same item, with the entries Explorer hides behind Shift ====
  "%PY%" -m app.io.harness menu "%TARGET%" %ITEM% --extended
  echo.
  echo ==== overlays on the first 20 rows ====
  "%PY%" -m app.io.harness overlays "%TARGET%" --rows 20
) > "%OUT%" 2>&1

echo Done. %OUT%
echo.
pause
