@echo off
REM  MT5-Trader - put a new version on this PC.
REM
REM  Run BY WHOEVER MAINTAINS IT, deliberately, on the machine being
REM  updated. NEXUS Terminal does not do this: a desk's version changes
REM  when somebody decides it changes, not because a commit landed
REM  overnight.
REM
REM  The order matters. The new code is fetched, the tests are run ON
REM  IT, and only if they pass does the update stand. A version that
REM  fails its own tests is put back before anyone can trade on it -
REM  which is the whole reason this is a script and not two git
REM  commands typed from memory.
REM
REM    deploy\UPDATE.BAT              go to the latest on this branch
REM    deploy\UPDATE.BAT --rollback   go back to the version before it
REM
REM  Plain ASCII on purpose: a console running the default code page
REM  turns anything else into mojibake in the one message that matters.

setlocal
title MT5-Trader - update
cd /d "%~dp0.."
color 0F

echo.
echo   =====================================================
echo     MT5-Trader - updating this PC
echo   =====================================================
echo.

REM --- The engine must not be running -----------------------------------
REM  Swapping files under a live engine is how a half-old, half-new
REM  process ends up holding positions.
REM
REM  Written with a goto rather than a parenthesised block on purpose:
REM  %GOON% inside the block it is SET in expands to what it held when
REM  the block was parsed - nothing - so the answer would be ignored and
REM  the update would always abort. Delayed expansion would also fix it;
REM  a label is harder to get wrong six months from now.
tasklist /fi "imagename eq python.exe" /nh 2>nul | find /i "python.exe" >nul
if errorlevel 1 goto :engine_is_not_running
echo   [!] Python is running on this machine.
echo.
echo       If that is MT5-Trader, close its window first. Updating
echo       underneath a running engine leaves it half on the old code
echo       and half on the new.
echo.
set "GOON="
set /p "GOON=      Type YES to carry on anyway: "
if /i not "%GOON%"=="YES" (
  echo   Nothing was changed.
  echo.
  pause
  exit /b 1
)
:engine_is_not_running

REM --- Python and Git ---------------------------------------------------
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
  echo   [X] No Python was found on this machine.
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
where git >nul 2>&1
if errorlevel 1 (
  echo   [X] Git is not installed, so this machine cannot fetch an
  echo       update. Re-run deploy\SETUP.bat.
  pause
  exit /b 1
)

REM --- Where we are now, so we can come back ---------------------------
REM  Recorded BEFORE anything moves. A rollback that has to work out
REM  where it was going is a rollback that cannot run when it is needed.
set "WAS="
for /f %%h in ('git rev-parse HEAD 2^>nul') do set "WAS=%%h"
if defined WAS goto :know_where_we_are

REM  TWO DIFFERENT FAULTS, and this used to blame the wrong one.
REM
REM  git rev-parse can come back empty because there is no clone here -
REM  a folder somebody unzipped instead of installing - or because git
REM  CAN SEE the clone and is refusing to touch it. Saying 'not a git
REM  clone' for both sent an operator off to reinstall a machine whose
REM  clone was perfectly fine.
REM
REM  The refusal that actually happens on these PCs is dubious
REM  ownership. SETUP asks for Administrator and clones as that
REM  account; UPDATE is double-clicked as the trader. When those are
REM  different accounts git refuses the repository outright, and its
REM  complaint went to the 2>nul above, so nobody ever saw it.
if not exist ".git" (
  echo   [X] There is no .git folder here, so this is not a clone -
  echo       it is a copy of the files. UPDATE works by fetching, so
  echo       there is nothing for it to fetch into.
  echo.
  echo       Re-run deploy\SETUP.bat, or clone over it. config.json
  echo       and .env are NOT in the repository, so copy those two
  echo       aside first and put them back afterwards.
  echo.
  pause
  exit /b 1
)
echo   [!] There IS a clone here, but git will not read it. Its own
echo       words:
echo.
git rev-parse HEAD
echo.

REM  OFFERED, NOT PRINTED FOR SOMEBODY TO TYPE.
REM
REM  The fix is one command and this window can run it: --global writes
REM  to the profile of whoever is sitting here, which is the account
REM  that runs UPDATE and the account git is refusing. No elevation,
REM  nothing shared, nothing that can fail on a locked-down PC.
REM
REM  --system was the first advice and it was the wrong one to lead
REM  with: it writes into C:\Program Files\Git\etc\gitconfig, so a
REM  Command Prompt that is not truly elevated answers 'access denied -
REM  cannot lock config file', which is a second dead end on top of the
REM  first. SETUP still does --system, because SETUP is already
REM  Administrator and that covers every account on the PC at once.
REM
REM  Backslashes become forward slashes: that is the form git prints
REM  and the form it matches against.
set "HERE=%CD:\=/%"
echo       If that says "dubious ownership", this clone was made by a
echo       different Windows account - SETUP runs as Administrator and
echo       this does not. Nothing is broken and nothing is lost; git is
echo       just refusing to touch a repository it did not expect.
echo.
echo       This window can fix it for your account, right now:
echo           git config --global --add safe.directory %HERE%
echo.
set "FIXIT="
set /p "FIXIT=      Type YES to do that and carry on: "
if /i not "%FIXIT%"=="YES" goto :not_fixed
git config --global --add safe.directory "%HERE%"
if errorlevel 1 goto :not_fixed
for /f %%h in ('git rev-parse HEAD 2^>nul') do set "WAS=%%h"
if defined WAS (
  echo.
  echo   Fixed - git can read this clone now. Carrying on.
  goto :know_where_we_are
)

:not_fixed
echo.
echo   [X] Nothing has been changed. If you would rather fix it for
echo       EVERY account on this PC at once, open a Command Prompt as
echo       Administrator and run:
echo.
echo           git config --system --add safe.directory %HERE%
echo.
pause
exit /b 1

:know_where_we_are
echo   This PC is on %WAS%

if /i "%~1"=="--rollback" goto :rollback

REM --- Fetch ------------------------------------------------------------
echo   Fetching...
git fetch --quiet
if errorlevel 1 (
  echo   [X] Could not reach GitHub. Check this machine's internet, and
  echo       that its access token has not expired.
  pause
  exit /b 1
)

git merge --ff-only @{u}
if errorlevel 1 (
  echo.
  echo   [X] This machine cannot fast-forward. Either it has local
  echo       changes, or its branch has moved sideways. Nothing has
  echo       been changed. Whoever maintains this needs to look at it.
  echo.
  pause
  exit /b 1
)

set "NOW="
for /f %%h in ('git rev-parse HEAD 2^>nul') do set "NOW=%%h"
if "%NOW%"=="%WAS%" (
  echo   Already on the latest version - nothing to do.
  echo.
  pause
  exit /b 0
)
echo   Now on %NOW%

REM --- Dependencies the new version may need ---------------------------
echo   Updating dependencies...
%PY% -m pip install --quiet --disable-pip-version-check --no-warn-script-location -r requirements.txt
if errorlevel 1 (
  echo   [!] The dependencies could not be updated. The tests below will
  echo       say whether that matters.
)

REM --- The tests decide whether this update stands ----------------------
echo   Running the safety tests on the new version...
%PY% -m pytest tests -q
if errorlevel 1 (
  echo.
  echo   [X] THE NEW VERSION FAILS ITS OWN TESTS.
  echo.
  echo       Putting this machine back on %WAS%.
  git reset --hard --quiet %WAS%
  %PY% -m pip install --quiet --disable-pip-version-check --no-warn-script-location -r requirements.txt
  echo.
  echo       This PC is back where it was and is safe to trade on.
  echo       Send the lines above to whoever maintains it - do NOT try
  echo       the update again until they say so.
  echo.
  pause
  exit /b 1
)

echo.
echo   Updated, and the safety tests pass.
echo   Nothing else to do - NEXUS Terminal as usual.
echo.
echo   If this version misbehaves, deploy\UPDATE.BAT --rollback puts
echo   this PC back on %WAS%.
echo.
pause
exit /b 0

:rollback
REM  Back one commit. Deliberately ONE: a trader on the phone can say
REM  "put it back", and a script that walks further than that is a
REM  script nobody can predict the result of.
echo   Rolling back one version...
git reset --hard --quiet HEAD~1
if errorlevel 1 (
  echo   [X] Could not roll back. Whoever maintains this needs to look.
  pause
  exit /b 1
)
set "NOW="
for /f %%h in ('git rev-parse HEAD 2^>nul') do set "NOW=%%h"
echo   This PC is now on %NOW%
%PY% -m pip install --quiet --disable-pip-version-check --no-warn-script-location -r requirements.txt
%PY% -m pytest tests -q
if errorlevel 1 (
  echo.
  echo   [X] The version this rolled back TO also fails its tests. Do
  echo       not trade on this machine. Call whoever maintains it.
  echo.
  pause
  exit /b 1
)
echo.
echo   Rolled back, and the safety tests pass.
echo.
pause
exit /b 0
