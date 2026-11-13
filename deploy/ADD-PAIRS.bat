@echo off
REM  MT5-Trader - add this month's contracts.
REM
REM  Double-click it. The pairs listed in pairs.json beside this file
REM  are added to this machine, and the new ladders appear on the
REM  Exchanges page.
REM
REM  It only ADDS. A pair you already have is left exactly as it is -
REM  including any position you are holding on it - and nothing is ever
REM  deleted or switched off. So it is safe to run twice, and safe to
REM  run in the middle of the day.
REM
REM  To see what it WOULD do without changing anything:
REM      ADD-PAIRS.BAT --dry-run
REM
REM  Plain ASCII on purpose: a console running the default code page
REM  turns anything else into mojibake in the one message that matters.

setlocal
title MT5-Trader - add pairs
cd /d "%~dp0.."
color 0F

echo.
echo   =====================================================
echo     MT5-Trader - adding this month's contracts
echo   =====================================================
echo.

REM  FOUR ways a working Python turns up on these boxes, and all four
REM  are normal.
REM
REM  The launcher by exact version first: py -3.11 on a machine that
REM  also has 3.9 on PATH is the good outcome, and looking at PATH
REM  first would miss it. Then 3.14, which rollout.json also allows and
REM  the suite has passed on - asking only for 3.11 told a perfectly
REM  good 3.14 desk it had no Python at all. Then py -3 for a version
REM  nobody listed here. Then plain "python", which is what a conda or
REM  venv prompt has and a python.org install without the launcher.
REM
REM  A test with NO assert in it, deliberately. "python" on a bare
REM  Windows is the Microsoft Store stub, which exits non-zero without
REM  running anything, so a plain import is enough to tell a real
REM  interpreter from the stub - and what version it is gets decided
REM  below, where it can be SAID rather than hidden in an exit code.
set "PY="
py -3.11 -c "import sys" >nul 2>&1
if not errorlevel 1 set "PY=py -3.11"
if not defined PY (
  py -3.14 -c "import sys" >nul 2>&1
  if not errorlevel 1 set "PY=py -3.14"
)
if not defined PY (
  py -3 -c "import sys" >nul 2>&1
  if not errorlevel 1 set "PY=py -3"
)
if not defined PY (
  python -c "import sys" >nul 2>&1
  if not errorlevel 1 set "PY=python"
)
if not defined PY (
  echo   [X] No Python was found on this machine, so this PC has not
  echo       been set up yet. Run deploy\SETUP.bat first.
  echo.
  pause
  exit /b 1
)

REM --- Is it a Python this can actually trade through? ------------------
REM  32-BIT IS FATAL. MetaTrader5's IPC handshake fails against a 32-bit
REM  Python with an error that says nothing, and the symptom on the desk
REM  is a leg that reads unknown forever. Starting would be worse than
REM  refusing, because it looks like it worked.
REM
REM  AN UNTESTED VERSION IS A WARNING, not a refusal. It may well be
REM  fine, the safety tests are the real gate, and stopping a trader at
REM  9am over a version number the suite then passes would be the wrong
REM  trade-off.
REM
REM  chr(80) is "P" - there is no quote character inside the quoted
REM  argument, because cmd would end the argument on it.
%PY% -c "import struct,sys;sys.exit(0 if struct.calcsize(chr(80))*8==64 else 1)" >nul 2>&1
if errorlevel 1 (
  echo   [X] This machine's Python is 32-bit.
  echo.
  echo       MetaTrader 5 will not talk to it: the handshake fails with
  echo       an error that says nothing, and the leg simply never
  echo       connects. Install Python 3.11 64-bit from python.org and
  echo       run this again.
  echo.
  pause
  exit /b 1
)
%PY% -c "import sys;sys.exit(0 if sys.version_info[:2] in ((3,11),(3,14)) else 1)" >nul 2>&1
if errorlevel 1 (
  echo   [!] This Python is not one of the versions this build has been
  echo       tested on ^(3.11 and 3.14^). Carrying on.
)

if not exist config.json (
  echo   [X] This machine has no config.json, so it has not been set up
  echo       yet. Run deploy\SETUP.bat first - there is nothing for a
  echo       pair to hang on until the two accounts exist.
  echo.
  pause
  exit /b 1
)

%PY% deploy\add_pairs.py --config config.json --pairs deploy\pairs.json %*
set "RC=%ERRORLEVEL%"

echo.
if not "%RC%"=="0" (
  echo   [X] Nothing was changed - the reason is above.
) else (
  echo   Done.
)
echo.
pause
exit /b %RC%
