@echo off
REM  MT5-Trader - run this ONCE on a new office PC.
REM
REM  It installs Git and Python, fetches the code, unpacks two separate
REM  MetaTrader 5 folders, runs the safety tests, asks you six questions
REM  and puts NEXUS Terminal on the Desktop. About ten minutes, most of
REM  it unattended.
REM
REM  This file is only a shim. It does two things batch is good at -
REM  ask Windows for Administrator, and hand over to PowerShell - and
REM  nothing else. The work is in setup.ps1 beside it, where a failure
REM  can stop the script instead of scrolling past.
REM
REM  On a new PC there is nothing to choose: the kit folder holds this
REM  file and a setup-files folder, and this is the one to double-click.

setlocal
cd /d "%~dp0"

REM --- Administrator ----------------------------------------------------
REM  Needed to install Git and Python for all users. Asking here means
REM  the trader never has to know to right-click.
REM
REM  The arguments are carried across. Elevating without them is how a
REM  -Token typed on the command line disappears and the clone then
REM  fails on a private repository for no visible reason.
net session >nul 2>&1
if errorlevel 1 (
  echo   Asking for Administrator...
  REM  Two branches, because -ArgumentList '' is an error rather than
  REM  an empty list - and the no-arguments case is the normal one.
  if "%*"=="" (
    powershell -NoProfile -Command "Start-Process -Verb RunAs -FilePath '%~f0'"
  ) else (
    powershell -NoProfile -Command "Start-Process -Verb RunAs -FilePath '%~f0' -ArgumentList '%*'"
  )
  exit /b 0
)

REM --- Where the engine room is -----------------------------------------
REM  TWO places are looked in, and that is deliberate.
REM
REM  In the REPOSITORY, setup.ps1 sits right beside this file.
REM
REM  In the ROLLOUT KIT it does not. The kit a trader is handed has ONE
REM  thing at the top level - this file - and everything else tucked
REM  into setup-files\, because a folder showing SETUP.bat next to
REM  setup.ps1 is a folder where somebody double-clicks the .ps1,
REM  Windows offers to open it in Notepad, and nothing happens. The
REM  fix is to not put the wrong file in front of them.
REM
REM  rollout.json and MT5-golden.zip travel WITH setup.ps1, because that
REM  script looks for both beside itself.
set "PS1=%~dp0setup.ps1"
if not exist "%PS1%" set "PS1=%~dp0setup-files\setup.ps1"
if not exist "%PS1%" (
  echo   [X] setup.ps1 was not found - not in this folder, and not in
  echo       the setup-files folder beside it.
  echo.
  echo       Copy the WHOLE kit folder, not the one file. This shim
  echo       cannot do anything on its own.
  echo.
  pause
  exit /b 1
)

REM  The name of THIS file, handed to the script so a refusal says
REM  "run Start-Setup.bat again" on a kit and "run SETUP.bat again"
REM  in the repository - whichever the person actually clicked.
set "MT5_SETUP_NAME=%~nx0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%PS1%" %*
set "RC=%ERRORLEVEL%"

echo.
if not "%RC%"=="0" (
  echo   [X] Setup did not finish. The reason is above.
) else (
  echo   Setup finished. Double-click NEXUS Terminal on the Desktop.
)
echo.
pause
exit /b %RC%
