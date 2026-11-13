@echo off
REM  MT5-Trader - check this machine can actually trade.
REM
REM  Double-click it. Nothing is changed, nothing is started, no order
REM  is ever placed: it logs each configured account in, reports what
REM  the broker said, and stops.
REM
REM  Run it when a desk says "it will not connect", and at the end of an
REM  install. It is the difference between a report you can act on and
REM  a trader saying the screen looks wrong.
REM
REM  Plain ASCII on purpose: a console running the default code page
REM  turns anything else into mojibake in the one message that matters.

setlocal
title MT5-Trader - verify
cd /d "%~dp0.."
color 0F

echo.
echo   =====================================================
echo     MT5-Trader - checking this machine
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
  echo   [X] No Python on this machine - it has not been set up.
  echo       Run deploy\SETUP.bat.
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
  echo   [X] No config.json - this machine has not been set up.
  echo       Run deploy\SETUP.bat.
  echo.
  pause
  exit /b 1
)

REM --- 1. Is the config itself sane? ------------------------------------
REM  Cheap, offline, and catches the faults that make a connection
REM  pointless: one login, one port or one terminal folder on both rows.
set "TERMINALS=0"
for /f %%c in ('tasklist /fi "imagename eq terminal64.exe" /nh 2^>nul ^| find /c /i "terminal64.exe"') do set "TERMINALS=%%c"
if exist deploy\preflight.py (
  %PY% deploy\preflight.py --config config.json --terminals-running %TERMINALS%
  echo.
)

REM --- 2. Can it actually log in? ---------------------------------------
%PY% deploy\check_config.py --config config.json
set "RC=%ERRORLEVEL%"

echo.
if not "%RC%"=="0" (
  echo   [X] This machine is NOT ready to trade - see above.
) else (
  echo   This machine can reach every account it is configured for.
  echo.
  echo   If a ladder still reads unknown, check Algo Trading is green
  echo   in BOTH terminals - it is a per-installation setting and a
  echo   fresh terminal has it off.
)
echo.
pause
exit /b %RC%
