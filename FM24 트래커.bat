@echo off
rem ============================================================
rem  FM24 Tracker  --  just double-click this file.
rem
rem  Keep this file ASCII-only: cmd.exe reads .bat in the OEM
rem  codepage (949 here), so UTF-8 Korean inside would be
rem  mojibake. Korean output comes from the Python side, which
rem  forces UTF-8 on stdout.
rem ============================================================
setlocal
cd /d "%~dp0"
title FM24 Tracker

echo.
echo   FM24 Tracker
echo   ------------------------------------------------
echo   Starting... a browser tab will open in a moment.
echo   Close this window to stop the tracker.
echo.

call "%~dp0scripts\_findpython.bat"
if errorlevel 1 (
  pause
  exit /b 1
)

"%PY%" "scripts\serve.py" %*
set "CODE=%ERRORLEVEL%"

rem Code 0 means a clean shutdown, so there is nothing to read.
if not "%CODE%"=="0" (
  echo.
  echo   [!] Stopped with an error ^(code %CODE%^).
  echo       Copy the message above if you need help.
  echo.
  pause
)
exit /b %CODE%
