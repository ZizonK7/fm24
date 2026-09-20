@echo off
rem ============================================================
rem  Shared helper: locate a working Python and set PY.
rem  Call with:  call "%~dp0scripts\_findpython.bat"
rem
rem  Why this exists: "python" on PATH is usually the Windows
rem  Store stub, which prints nothing and returns an error code.
rem  So every candidate is executed before we trust it.
rem
rem  Keep this file ASCII-only: cmd.exe reads .bat in the OEM
rem  codepage (949 here), so UTF-8 Korean would be mojibake.
rem ============================================================

set "PY="

call :probe "py"
if not defined PY (
  for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do call :probe "%%D\python.exe"
)
if not defined PY (
  for /d %%D in ("%ProgramFiles%\Python3*") do call :probe "%%D\python.exe"
)
if not defined PY (
  for /d %%D in ("%ProgramFiles(x86)%\Python3*") do call :probe "%%D\python.exe"
)
if not defined PY call :probe "python"

if not defined PY (
  echo.
  echo   [!] Python was not found on this PC.
  echo.
  echo       Install it, then run this file again:
  echo         winget install Python.Python.3.12
  echo.
  echo       Or get it from https://www.python.org/downloads/
  echo       ^(tick "Add python.exe to PATH" during setup^)
  echo.
  exit /b 1
)
exit /b 0

:probe
if defined PY exit /b 0
%~1 -c "import sys" >nul 2>&1
if errorlevel 1 exit /b 0
set "PY=%~1"
exit /b 0
