<#
    Build MT5-golden.zip - the MetaTrader 5 folder every office PC gets
    two copies of.

    You run this ONCE, and again whenever the broker ships a new
    terminal build. SETUP.bat then unpacks the result twice on every PC,
    which is faster than running the installer twice and - the part that
    matters - identical every time. No installer dialogs, no 'did I tick
    the same boxes on both'.

    PREPARE THE SOURCE, and it is three steps, not ten:

      1. Install MetaTrader 5 once with the BROKER'S installer
         (mentomarkets5setup.exe), into its own folder such as C:\MT5-A.
      2. Do NOT log in. Cancel the Open an Account dialog if it appears.
      3. Close the terminal.

    That is all. Everything a previous version of this script fussed
    over - the servers list, Market Watch, closing charts, turning News
    off, Allow Algo Trading - lives in
    %APPDATA%\MetaQuotes\Terminal\<hash>\, NOT in the program folder, so
    none of it is in this zip and none of it can be prepared here.

    Two things follow from that, and both were measured on a clean PC:

      * The broker's installer already knows its own servers, so a copy
        of the program folder logs in from
        mt5.initialize(path, login, password, server) with no manual
        sign-in at all. That is the whole reason this approach works.

      * Allow Algo Trading does NOT travel. It is a per-installation
        setting in AppData, so it is pressed once in each terminal the
        first time it opens. SETUP says so at the end.

    Do not reach for /portable to pull those settings into the program
    folder: a terminal started portable refuses IPC from a normally
    started Python, so the legs would never connect. mt5_errors.py
    already records that.

        .\deploy\make-golden-terminal.ps1 -Source 'C:\MT5-A'

    Plain ASCII and single quotes throughout: Windows PowerShell 5.1
    reads a script with no byte-order mark as ANSI, and one curly quote
    from a word processor comes back as a parser error on a line that
    looks perfectly fine.
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string] $Source,

    # Blank, and worked out AFTER the param block. $PSScriptRoot is
    # EMPTY while a param default is being evaluated under -File, so
    # (Join-Path $PSScriptRoot '...') here threw before a single line of
    # this script ran: "Cannot bind argument to parameter 'Path'
    # because it is an empty string."
    [string] $Output = '',

    # For the one case where you have looked at the refusal below and
    # you are sure. It is not a switch to reach for casually: what it
    # overrides is the check that no account details are in the zip.
    [switch] $Force
)

$ErrorActionPreference = 'Stop'

if (-not $Output) {
    # $PSScriptRoot is set by the time the body runs. The fallback is
    # for the hosts where it is not - dot-sourcing, and some ISE and
    # remoting cases - because a default that can be empty is what
    # broke this script in the first place.
    $here = $PSScriptRoot
    if (-not $here) {
        $here = Split-Path -Parent $MyInvocation.MyCommand.Path
    }
    if (-not $here) { $here = (Get-Location).Path }
    $Output = Join-Path $here 'MT5-golden.zip'
}

if (-not (Test-Path (Join-Path $Source 'terminal64.exe'))) {
    throw ($Source + ' has no terminal64.exe in it. Point -Source at the ' +
           'MetaTrader 5 FOLDER, not the shortcut and not the installer.')
}
if (Get-Process -Name 'terminal64' -ErrorAction SilentlyContinue) {
    throw ('MetaTrader 5 is running. Close it first - a zip taken while ' +
           'the terminal is open is a snapshot of half-written files.')
}

# Everything is copied to a staging folder and cleaned THERE. The source
# is a working terminal and this script must not be able to delete
# anything in it.
$stage = Join-Path $env:TEMP ('mt5-golden-' + [Guid]::NewGuid().ToString('N'))
Write-Host ('Staging in ' + $stage)
Copy-Item -Recurse -Force -Path $Source -Destination $stage

<#
    A fresh, never-signed-in install has nothing in here worth removing,
    and that is the point: this is a belt-and-braces pass for the case
    where somebody builds the zip from a terminal that HAS been used.

    Logs name an account number and a server, so a template carrying
    them would ship one trader's details to every PC in the office.
#>
foreach ($relative in @('logs', 'Logs', 'MQL5\Logs', 'MQL5\Files',
                        'bases', 'history')) {
    $path = Join-Path $stage $relative
    if (Test-Path $path) {
        Write-Host ('  removing ' + $relative)
        Remove-Item -Recurse -Force $path
    }
}

# Credentials, by name. On a normal install these live in AppData and
# are not here at all - so finding one means this folder is not what it
# is supposed to be, and that is worth stopping for.
$secrets = Get-ChildItem -Path $stage -Include 'accounts.dat', 'accounts.ini' `
                         -Recurse -Force -ErrorAction SilentlyContinue
if ($secrets -and -not $Force) {
    $names = ($secrets | ForEach-Object {
        $_.FullName.Substring($stage.Length + 1) }) -join ', '
    Remove-Item -Recurse -Force $stage
    throw ('This folder contains saved account details (' + $names + '), ' +
           'which a normal installation keeps in AppData and not here. ' +
           'Something has made this a portable or hand-assembled copy. ' +
           'Refusing to build a template that would ship one login to ' +
           'every PC in the office. Start from a fresh install of the ' +
           "broker's own setup.exe, do not sign in, and try again.")
}
foreach ($file in $secrets) {
    Write-Host ('  removing ' + $file.Name + ' (-Force)')
    Remove-Item -Force $file.FullName
}

if (Test-Path $Output) { Remove-Item -Force $Output }
Write-Host ('Compressing to ' + $Output + ' ...')
# The CONTENTS, not the folder: SETUP unpacks straight into C:\MT5-A,
# and a zip of the folder would land terminal64.exe one level too deep.
Compress-Archive -Path (Join-Path $stage '*') -DestinationPath $Output
Remove-Item -Recurse -Force $stage

$mb = [Math]::Round((Get-Item $Output).Length / 1MB, 1)
Write-Host ''
Write-Host ('Done: ' + $Output + '  (' + $mb + ' MB)') -ForegroundColor Green
Write-Host 'Run MAKE-KIT.BAT to build the rollout kit around it.'
