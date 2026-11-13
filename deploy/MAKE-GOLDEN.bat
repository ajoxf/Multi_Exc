@echo off
REM  MT5-Trader - build the MetaTrader 5 template. Double-click it.
REM
REM  Everything else in this folder is a .bat for a reason: typing
REM  .\deploy\make-golden-terminal.ps1 into a Command Prompt does not
REM  run it. cmd.exe cannot execute PowerShell, so Windows offers to
REM  OPEN the file instead - a "Select an app to open this .ps1 file"
REM  dialog offering Notepad - and nothing happens. This wrapper is the
REM  same script with that trap removed.
REM
REM  Prepare the source first, and it is three steps:
REM
REM    1. Install MetaTrader 5 with the BROKER'S installer into its own
REM       folder, C:\MT5-A by default.
REM    2. Do NOT log in. Cancel the Open an Account dialog.
REM    3. Close the terminal.
REM
REM  Everything else - Market Watch, charts, News, Allow Algo Trading -
REM  is kept in AppData, not the program folder, so it is not in this
REM  zip and cannot be prepared here.
REM
REM      MAKE-GOLDEN.BAT                 uses C:\MT5-A
REM      MAKE-GOLDEN.BAT D:\MT5-Source   uses that folder instead
REM
REM  Plain ASCII on purpose: a console running the default code page
REM  turns anything else into mojibake in the one message that matters.

setlocal
title MT5-Trader - build the terminal template
cd /d "%~dp0"
color 0F

set "SOURCE=%~1"
if not defined SOURCE set "SOURCE=C:\MT5-A"

echo.
echo   =====================================================
echo     MT5-Trader - building the terminal template
echo   =====================================================
echo.
echo   Source: %SOURCE%
echo.

if not exist "%SOURCE%\terminal64.exe" (
  echo   [X] There is no terminal64.exe in %SOURCE%.
  echo.
  echo       Install MetaTrader 5 there first with the broker's own
  echo       setup.exe, do NOT log in, and close it. Or pass the folder
  echo       you did install into:
  echo.
  echo           MAKE-GOLDEN.BAT D:\wherever
  echo.
  pause
  exit /b 1
)

REM  Said here rather than only in the script: closing the terminal is
REM  the step people skip, and a zip of a running terminal is a
REM  snapshot of half-written files.
tasklist /fi "imagename eq terminal64.exe" /nh 2>nul | find /i "terminal64.exe" >nul
if not errorlevel 1 (
  echo   [X] MetaTrader 5 is running. Close it and run this again.
  echo.
  pause
  exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0make-golden-terminal.ps1" -Source "%SOURCE%" -Output "%~dp0MT5-golden.zip"
set "RC=%ERRORLEVEL%"

echo.
if not "%RC%"=="0" (
  echo   [X] The template was not built - the reason is above.
) else (
  echo   Built. Copy these four onto the USB stick or share:
  echo.
  echo       SETUP.bat   setup.ps1   rollout.json   MT5-golden.zip
  echo.
  echo   That folder is what every new PC is built from.
)
echo.
pause
exit /b %RC%
