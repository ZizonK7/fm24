@echo off
rem ============================================================
rem  Creates a Desktop shortcut to the FM24 Tracker launcher.
rem  Run this once; after that just use the Desktop icon.
rem
rem  Keep this file ASCII-only (see scripts\_findpython.bat).
rem  The Korean shortcut name lives in scripts\make_shortcut.py,
rem  where UTF-8 is safe.
rem ============================================================
setlocal
cd /d "%~dp0"
title FM24 Tracker - shortcut

echo.

call "%~dp0scripts\_findpython.bat"
if errorlevel 1 (
  pause
  exit /b 1
)

"%PY%" "scripts\make_shortcut.py" %*
set "CODE=%ERRORLEVEL%"

echo.
pause
exit /b %CODE%
