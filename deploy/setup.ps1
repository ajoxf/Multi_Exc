<#
    MT5-Trader: turn a bare Windows PC into a trading box.

    Launched by SETUP.bat, which has already asked for Administrator.
    Run it directly only if you know why:

        Set-ExecutionPolicy -Scope Process Bypass -Force
        .\deploy\setup.ps1 -RepoUrl https://github.com/OWNER/REPO.git

    The order is deliberate. Everything that can fail cheaply fails
    FIRST: the golden zip is checked before Git is downloaded, and the
    tests run before the trader is asked for a single password. A setup
    that is going to fail should fail in the first minute, in front of
    whoever started it - not at 9am the next morning.

    Plain ASCII and single quotes throughout, for the reason bootstrap.ps1
    gives: Windows PowerShell 5.1 reads a script with no byte-order mark
    as ANSI, and one curly quote from a word processor comes back as a
    parser error on a line that looks perfectly fine.
#>

[CmdletBinding()]
param(
    # Where the code lands. Short, no spaces, off the profile: a path
    # with a space in it is the thing that breaks a .bat six months later.
    #
    # These four default to whatever rollout.json says, and rollout.json
    # is THE place to change the repository and branch for the office.
    # A blank here means 'take it from the file'; passing one on the
    # command line overrides the file for this one machine.
    #
    [string] $Root = '',

    [string] $RepoUrl = '',
    [string] $Branch = '',

    # A fine-grained, READ-ONLY token for this one repository. Read-only
    # on purpose: a token that leaks off an office PC then reads code,
    # it cannot push. Leave it blank to be prompted, or to use a machine
    # that is already signed in to GitHub.
    [string] $Token = '',
    [string] $TokenUser = 'x-access-token',

    # The stripped MetaTrader 5 folder, zipped. Made once, by
    # make-golden-terminal.ps1, and copied beside this script.
    [string] $GoldenZip = '',

    # TWO folders, never one folder and a shortcut. A terminal holds a
    # single login, so two runners on one installation are one account
    # trading against itself.
    [string] $TerminalA = '',
    [string] $TerminalB = '',

    # What the Desktop icon is called. A NAME, and a parameter, because
    # a PC that runs two desks gets SETUP twice with two -Root folders,
    # and a fixed name would mean the second run silently repointed the
    # first desk's icon at the second desk's install.
    [string] $ShortcutName = '',

    # For a re-run on a machine that is already set up: keep its
    # config.json rather than asking the six questions again.
    [switch] $KeepConfig
)

$ErrorActionPreference = 'Stop'
$here = $PSScriptRoot

<#
    THE HOST THIS IS ABOUT TO RUN ON, made safe before anything else.

    Four settings, and every one of them is a bare-PC failure somebody
    else has already had.

    TLS. Windows PowerShell 5.1 on Windows 10 negotiates whatever
    .NET's default is, and on a machine that has not had its .NET
    defaults changed that can still be TLS 1.0/1.1. python.org and
    github.com both refuse those now, so the download dies with
    'Could not create SSL/TLS secure channel' - a message that says
    nothing about Python and sends the installer hunting for a
    firewall. Named protocols are OR-ed in rather than assigned, so a
    host that already has TLS 1.3 keeps it.

    THE PROGRESS BAR. Invoke-WebRequest renders one by default, and in
    5.1 that rewrite costs more time than the download: a 25 MB
    installer can take minutes instead of seconds. Off.

    NATIVE COMMANDS IN POWERSHELL 7.4+. There, a native program's
    non-zero exit code becomes a TERMINATING error whenever
    $ErrorActionPreference is 'Stop'. This script deliberately reads
    exit codes and decides - a failed login is a warning, a
    non-fast-forward is a warning, a cancelled wizard is a warning -
    and under 7.4 the first of them would kill the install instead.
    SETUP.bat starts Windows PowerShell, where this does not arise, so
    this line is for the day somebody runs the script by hand.

    THE PROXY. An office PC behind an authenticating proxy needs the
    signed-in user's credentials to fetch anything at all.
#>
try {
    [Net.ServicePointManager]::SecurityProtocol =
        [Net.ServicePointManager]::SecurityProtocol -bor
        [Net.SecurityProtocolType]::Tls12
} catch { }
$ProgressPreference = 'SilentlyContinue'
if (Test-Path variable:PSNativeCommandUseErrorActionPreference) {
    $PSNativeCommandUseErrorActionPreference = $false
}
try {
    if ([Net.WebRequest]::DefaultWebProxy) {
        [Net.WebRequest]::DefaultWebProxy.Credentials =
            [Net.CredentialCache]::DefaultCredentials
    }
} catch { }

<#
    Anything that throws lands here: the REASON in red, and exit 1 so
    SETUP.bat knows it failed.

    Without this the installer shows a PowerShell stack trace - which
    to the person standing at the machine is indistinguishable from
    success followed by noise - and, on some hosts, still exits 0.
#>
trap {
    Write-Host ''
    Write-Host ('  [X] ' + $_.Exception.Message) -ForegroundColor Red
    Write-Host ''
    exit 1
}

function Say    { param([string] $m) Write-Host ('  ' + $m) }
function Step   { param([string] $m) Write-Host ''; Write-Host ('== ' + $m) -ForegroundColor Cyan }
function Warn   { param([string] $m) Write-Host ('  [!] ' + $m) -ForegroundColor Yellow }
function Fail   { param([string] $m) throw $m }

Write-Host ''
Write-Host '  =====================================================' -ForegroundColor Cyan
Write-Host '    MT5-Trader - setting up this PC' -ForegroundColor Cyan
Write-Host '  =====================================================' -ForegroundColor Cyan

# --- 0. The cheap checks, before anything is downloaded -----------------

Step 'Checking what this setup was given'

<#
    rollout.json holds the repository, the branch and the folders, so
    moving the office from the demo repo to the live one is an edit to
    one file in the rollout kit rather than a flag somebody has to
    remember on every machine.

    A missing file is not fatal - the defaults below keep a bare
    SETUP.bat working - but it IS said out loud, because a setup that
    silently installed from the wrong repository would be found out
    weeks later.
#>
$rolloutPath = Join-Path $here 'rollout.json'
$rollout = $null
if (Test-Path $rolloutPath) {
    try {
        $rollout = Get-Content -Raw -Path $rolloutPath | ConvertFrom-Json
        Say ('Rollout settings: ' + $rolloutPath)
    } catch {
        Fail ('rollout.json is next to this script but will not parse (' +
              $_.Exception.Message + '). Fix the file rather than ' +
              'deleting it - installing from a guessed repository is ' +
              'worse than not installing.')
    }
} else {
    Warn ('No rollout.json beside this script - using built-in defaults. ' +
          'Copy the whole deploy folder into the rollout kit so the ' +
          'repository and branch come from one place.')
}

function Setting {
    param([string] $Given, [string] $Key, [string] $Default)
    if ($Given) { return $Given }
    if ($rollout -and $rollout.PSObject.Properties.Name -contains $Key) {
        $value = [string] $rollout.$Key
        if ($value) { return $value }
    }
    return $Default
}

$RepoUrl   = Setting $RepoUrl   'repo_url' 'https://github.com/nishant-patel-git/Mirror-MT5-Trader.git'
$Branch    = Setting $Branch    'branch'   'main'
$Root      = Setting $Root      'root'       'C:\MT5-Trader'
$TerminalA = Setting $TerminalA 'terminal_a' 'C:\MT5-A'
$TerminalB = Setting $TerminalB 'terminal_b' 'C:\MT5-B'
$ShortcutName = Setting $ShortcutName 'shortcut_name' 'NEXUS Terminal'
# What the person actually double-clicked. The repository calls the
# shim SETUP.bat; the rollout kit hands it over as Start-Setup.bat,
# and a refusal that names the wrong file is a refusal a trader
# cannot act on. The shim passes its own name in; the default is for
# the case where this script is run directly.
$SetupName = $env:MT5_SETUP_NAME
if (-not $SetupName) { $SetupName = 'SETUP.bat' }
# A LIST, not one number: 3.11 and 3.14 have both run the whole suite
# green, and refusing a machine that already has a proven interpreter
# would mean installing a second Python on every desk for nothing.
# Anything not on the list is still refused, so a stray 3.9 cannot
# quietly become the one PC that behaves differently.
$PyVersions = @()
if ($rollout -and $rollout.PSObject.Properties.Name -contains 'python_versions') {
    $PyVersions = @($rollout.python_versions)
}
if (-not $PyVersions) { $PyVersions = @('3.11', '3.14') }

Say ('Repository:        ' + $RepoUrl)
Say ('Branch:            ' + $Branch)

if (-not $GoldenZip) {
    $candidate = Join-Path $here 'MT5-golden.zip'
    if (Test-Path $candidate) { $GoldenZip = $candidate }
}
if (-not $GoldenZip -or -not (Test-Path $GoldenZip)) {
    Fail ('MT5-golden.zip was not found beside this script. That zip is ' +
          'the stripped MetaTrader 5 folder every PC gets a copy of; ' +
          'make it once with make-golden-terminal.ps1 and copy it here. ' +
          'Without it this machine would have no terminals to trade ' +
          'through.')
}
if ($TerminalA.TrimEnd('\') -ieq $TerminalB.TrimEnd('\')) {
    Fail ('TerminalA and TerminalB are the same folder. One MetaTrader 5 ' +
          'installation holds ONE login, so both legs would end up on the ' +
          'same account and the pair would hedge against itself.')
}
Say ('Terminal template: ' + $GoldenZip)
Say ('Code folder:       ' + $Root)

<#
    64-BIT WINDOWS, refused here rather than three downloads later.

    Everything this script fetches is 64-bit by necessity: the terminal
    is terminal64.exe, and MetaTrader5's IPC handshake fails against a
    32-bit Python with an error that says nothing. On 32-bit Windows
    the Git installer refuses, the Python installer refuses, and the
    operator is left reading two unrelated complaints instead of the
    one fact that matters.
#>
if (-not [Environment]::Is64BitOperatingSystem) {
    Fail ('This is 32-bit Windows. The terminal is terminal64.exe and ' +
          'MetaTrader 5 will not talk to a 32-bit Python, so this PC ' +
          'cannot run a leg. Nothing has been downloaded or changed.')
}

<#
    ROOM ON THE DISK, for the same reason.

    Two MetaTrader 5 folders, Git, Python and the dependencies come to
    a little over 1.5 GB. Running out halfway leaves a half-unpacked
    terminal and an error from Expand-Archive about a file, which reads
    like a corrupt zip and is not.

    Measured on the drive the CODE goes to; the terminals normally sit
    on the same one. A drive that cannot be measured is NOT treated as
    full - unmeasured is not zero - it is simply not checked.
#>
$needGb = 3
$driveLetter = (Split-Path -Qualifier $Root).TrimEnd(':')
$drive = Get-PSDrive -Name $driveLetter -ErrorAction SilentlyContinue
if ($drive -and $null -ne $drive.Free) {
    $freeGb = [Math]::Round($drive.Free / 1GB, 1)
    if ($drive.Free -lt ($needGb * 1GB)) {
        Fail ('Only ' + $freeGb + ' GB free on ' + $driveLetter + ': and ' +
              'this install needs about ' + $needGb + ' GB - two ' +
              'MetaTrader 5 folders, Git, Python and the dependencies. ' +
              'Free some space and run ' + $SetupName + ' again. Nothing ' +
              'has been downloaded or changed.')
    }
    Say ('Free on ' + $driveLetter + ':      ' + $freeGb + ' GB')
}

# --- 1. Git --------------------------------------------------------------

Step 'Git'

function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') +
                ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
}

function Find-Git {
    <#
        git, as a command if PATH has it and as a FILE if it does not.

        The second half is not belt-and-braces, it is the lesson Python
        taught this script over three separate rounds: $env:Path in a
        console that was ALREADY OPEN does not learn about an install,
        and rebuilding it from the registry does not always reach
        PowerShell's command lookup either. A path on disk has neither
        problem, and Git for Windows only ever installs to one of these.
    #>
    if (Get-Command git -ErrorAction SilentlyContinue) { return 'git' }
    foreach ($candidate in @(
            (Join-Path $env:ProgramFiles 'Git\cmd\git.exe'),
            (Join-Path ${env:ProgramFiles(x86)} 'Git\cmd\git.exe'),
            (Join-Path $env:LOCALAPPDATA 'Programs\Git\cmd\git.exe'))) {
        if ($candidate -and (Test-Path $candidate)) { return $candidate }
    }
    return $null
}

$git = Find-Git
if ($git) {
    Say ('Already installed: ' + (& $git --version))
} else {
    Say 'Installing Git for Windows...'
    $gitExe = Join-Path $env:TEMP 'git-setup.exe'
    Invoke-WebRequest -UseBasicParsing -OutFile $gitExe -Uri (
        'https://github.com/git-for-windows/git/releases/download/' +
        'v2.45.2.windows.1/Git-2.45.2-64-bit.exe')
    # -PassThru, so the installer's OWN exit code is read rather than
    # discarded. Without it a refused install looked exactly like a
    # successful one that could not be found, and the message sent the
    # operator hunting through PATH for a Git that was never there.
    #   0    installed        3010 installed, wants a reboot
    #   1602 cancelled        1603 fatal        1618 another install running
    $run = Start-Process -Wait -PassThru -FilePath $gitExe -ArgumentList (
        '/VERYSILENT /NORESTART /NOCANCEL /SP- /SUPPRESSMSGBOXES ' +
        '/COMPONENTS="icons,ext\shellhere,assoc,assoc_sh"')
    if ($run.ExitCode -eq 1618) {
        Fail ('Another Windows installer is running, so Git could not be ' +
              'installed (exit 1618). Wait for it to finish - Windows ' +
              'Update is the usual one - and run ' + $SetupName + ' again.')
    }
    if ($run.ExitCode -ne 0 -and $run.ExitCode -ne 3010) {
        Fail ('The Git installer failed with exit code ' + $run.ExitCode +
              '. Nothing else has been changed. Install Git for Windows ' +
              'by hand from https://git-scm.com/download/win - the ' +
              'defaults are right - and run ' + $SetupName + ' again.')
    }
    Refresh-Path
    $git = Find-Git
    if (-not $git) {
        Fail ('Git installed - the installer returned ' + $run.ExitCode +
              ' - but this window still cannot find it, and it is not in ' +
              'any folder Git for Windows installs to. Close this window, ' +
              'open a new one, and run ' + $SetupName + ' again.')
    }
    Say ('Installed: ' + (& $git --version))
}

# --- 2. Python -----------------------------------------------------------

Step 'Python'

function Test-Python {
    <#
        Is this interpreter one this project can actually run on?

        Two things are checked and NEITHER is negotiable:

        64-BIT. MetaTrader5's IPC handshake fails against a 32-bit
        Python with an error that says nothing useful, and the symptom
        on the desk is a leg that never connects.

        THE VERSION. The suite is tested on 3.11. A machine carrying
        the company's Python 3.9, or a 3.13 that no wheel exists for
        yet, must be told so - not quietly used, which turns one PC
        into the one that behaves differently.
    #>
    param([string[]] $Command)
    <#
        NO DOUBLE QUOTE ANYWHERE IN THIS PROBE, and that is not a
        style choice.

        Windows PowerShell 5.1 - which is what SETUP.bat starts, and
        what every one of these office PCs has - rebuilds the command
        line for a native program by wrapping any argument containing a
        space in double quotes, and it does NOT escape the double
        quotes already inside it. So the old probe,

            'import sys, struct; print("%d.%d %d" % (...))'

        reached python.exe as

            -c "import sys, struct; print(%d.%d %d" % (...))"

        which Windows then split at those quotes into several
        arguments. Python got a fragment, raised SyntaxError, exited
        non-zero, and its complaint went to the stderr this function
        deliberately swallows. Test-Python therefore returned $null for
        a PERFECTLY GOOD Python 3.11 - on every machine, every time.

        That is the whole bug behind 'Python installed but this window
        still cannot find it': the interpreter was found, run, and then
        judged missing because it could not parse a mangled one-liner.
        Installing Python again could never fix it.

        Written with no quote character anywhere in it, the argument
        survives whatever quoting a host puts around it, so 5.1, 7.x
        and cmd.exe all deliver it whole. chr(80) is 'P'; three numbers
        come back separated by spaces - major, minor, bits - and the
        version is put back together on this side.
    #>
    $probe = 'import sys,struct;print(sys.version_info[0],sys.version_info[1],struct.calcsize(chr(80))*8)'
    <#
        Run it with errors NOT fatal, and swallow stderr.

        A fresh Windows ships zero-byte python.exe and python3.exe
        stubs in %LOCALAPPDATA%\Microsoft\WindowsApps that open the
        Microsoft Store. Running one prints "Python was not found; run
        without arguments to install from the Microsoft Store" to
        STDERR - and PowerShell 7 turns a native command's stderr into
        a TERMINATING error while $ErrorActionPreference is 'Stop'.

        So on a genuinely bare PC this probe killed the whole install
        at the exact moment it had established that Python was missing
        and was about to go and install it.
    #>
    $out = $null
    $previous = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $out = Invoke-Python $Command @('-c', $probe) 2>$null
    } catch {
        return $null
    } finally {
        $ErrorActionPreference = $previous
    }
    if ($LASTEXITCODE -ne 0 -or -not $out) { return $null }
    <#
        THE LAST LINE, and it must be three plain numbers.

        Two things were wrong with reading this as 'whatever came back,
        split on spaces'.

        A wrapper gets to speak first. Plenty of things that answer to
        'python' on an office PC are not python.exe - a shim, a
        launcher, an antivirus or endpoint agent wrapping the
        executable - and they print a line of their own before the
        program runs. One PC answered with a line beginning
        'Extracting:', and the version came out as that word.

        And the cast was outside the try. [int] on a word is a
        TERMINATING error, so instead of 'this is not a Python I can
        use', the whole install died on the spot with a .NET conversion
        message and nothing about Python in it.

        So: take the last non-empty line, and accept it only if it is
        exactly three integers. Anything else is not an interpreter
        this script can trust - it is SAID, so the next person sees
        what the machine actually answered, and looking continues
        elsewhere.
    #>
    $text = (@($out) | ForEach-Object { [string] $_ }) -join "`n"
    $lines = @($text -split "`r?`n" | Where-Object { $_.Trim() })
    if (-not $lines) { return $null }
    $last = $lines[-1].Trim()
    $match = [regex]::Match($last, '^(\d+)\s+(\d+)\s+(\d+)$')
    if (-not $match.Success) {
        Warn ('Ignoring ' + ($Command -join ' ') + ': asked for its version ' +
              'and it answered ' + $last + ' - that is not a Python this ' +
              'script can use.')
        return $null
    }
    return @{ Version = ($match.Groups[1].Value + '.' +
                         $match.Groups[2].Value);
              Bits = [int] $match.Groups[3].Value;
              Command = $Command }
}

function Test-StoreStub {
    <#
        Is this command one of Windows' Microsoft Store aliases rather
        than a real interpreter? They live under WindowsApps, are zero
        bytes, and exist on every fresh install - so Get-Command finds
        a 'python' that is not Python.
    #>
    param([string] $Name)
    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if (-not $command) { return $false }
    $source = [string] $command.Source
    return $source -and ($source -like '*\WindowsApps\*')
}

function Test-Usable {
    <#
        Is this interpreter one this project can actually run on?

        64-BIT, because MetaTrader5's IPC handshake fails against a
        32-bit Python with an error that says nothing.

        A VERSION ON THE LIST, because requirements.txt is not
        negotiable: Flask 3, pytest 8, playwright and python-dotenv 1
        every one require 3.8 or newer, so a machine's stray 3.7 could
        not install the dependencies even if this script let it try.

        Used as a FILTER, not as a refusal. See Find-Python.
    #>
    param($Found)
    return $Found -and $Found.Bits -eq 64 -and
           ($PyVersions -contains $Found.Version)
}

function Find-Python {
    <#
        The command that runs Python here, as a list: the program and
        any arguments it needs. Three ways a working Python turns up on
        these boxes and all three are normal - the py launcher, a plain
        'python' on PATH (what a conda or venv prompt has), or neither.
        Assuming the launcher is why a machine that already had Python
        was told it had none.

        The exact version is asked for FIRST. 'py -3.11' on a box that
        also has 3.9 on PATH is the good outcome, and looking at PATH
        first would miss it.

        AN INTERPRETER THIS PROJECT CANNOT USE IS NOT A FIND. It is
        said out loud and stepped over.

        That distinction is the whole of this function's job, and
        getting it wrong cost a fresh EC2 box its install: the machine
        had Python 3.7 on PATH, this returned it as 'the Python', and
        the check below refused the entire setup - telling an operator
        to go and install 3.11 by hand on a script whose next line
        would have installed 3.11 by itself. A wrong Python on PATH is
        a fact about the machine, not a reason to stop; 3.11 is put on
        beside it and the launcher picks the right one from then on.
    #>
    # 1. The launcher, by name and then by its known home. py.exe goes
    #    to C:\Windows, which is always on PATH - but only for a
    #    console started AFTER the install, which this one was not.
    $launchers = @()
    if (Get-Command py -ErrorAction SilentlyContinue) { $launchers += 'py' }
    $inWindows = Join-Path $env:WINDIR 'py.exe'
    if (Test-Path $inWindows) { $launchers += $inWindows }
    foreach ($launcher in $launchers) {
        foreach ($version in $PyVersions) {
            $found = Test-Python @($launcher, ('-' + $version))
            # Asked for BY version, so only the bitness can be wrong
            # here - and a 32-bit build of the right version is still
            # one the terminal will not talk to.
            if (Test-Usable $found) { return $found }
            if ($found) {
                Warn ('Ignoring ' + ($found.Command -join ' ') + ': it is ' +
                      $found.Version + ' ' + $found.Bits + '-bit, and ' +
                      'MetaTrader 5 needs 64-bit.')
            }
        }
    }

    # 2. Where the installer actually puts it, looked up as a FILE.
    #    This is the step that matters: $env:Path in a console that was
    #    already open does not learn about an install, and refreshing it
    #    from the registry does not always reach PowerShell's command
    #    lookup either. A path on disk has neither problem.
    # Test-Usable again, though Find-PythonInFolders has already
    # applied it. The rule this function has to keep is 'nothing
    # unusable leaves here', and a rule enforced at every exit is one a
    # later edit cannot quietly break.
    $found = Find-PythonInFolders
    if (Test-Usable $found) { return $found }

    # 3. Whatever 'python' means here - a conda prompt, a venv - as long
    #    as it is not the Microsoft Store stub.
    if ((Get-Command python -ErrorAction SilentlyContinue) -and
        -not (Test-StoreStub 'python')) {
        $found = Test-Python @('python')
        if (Test-Usable $found) { return $found }
        if ($found) {
            # SAID, and then left alone. Nothing is uninstalled and
            # nothing on this machine changes because of it: whatever
            # else on this PC uses that Python goes on using it.
            Warn ('This machine has Python ' + $found.Version + ' ' +
                  $found.Bits + '-bit on PATH. The suite runs on ' +
                  ($PyVersions -join ' and ') + ' 64-bit, and ' +
                  'requirements.txt needs 3.8 or newer, so 3.11 is being ' +
                  'installed alongside it. The existing Python is left ' +
                  'exactly as it is.')
        }
    }
    return $null
}

function Find-PythonInFolders {
    <#
        The places python.org's installer puts an interpreter, checked
        as files rather than through PATH.

        3.11 lands in Python311, so the dots come out of the version to
        make the folder name.
    #>
    $candidates = @()
    foreach ($version in $PyVersions) {
        $tag = 'Python' + ($version -replace '\.', '')
        foreach ($base in @($env:ProgramFiles,
                            ${env:ProgramFiles(x86)},
                            (Join-Path $env:LOCALAPPDATA 'Programs\Python'),
                            'C:\')) {
            if ($base) {
                $candidates += (Join-Path $base (Join-Path $tag 'python.exe'))
            }
        }
    }
    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) {
            $found = Test-Python @($candidate)
            # Filtered here too, and not only for tidiness: one of the
            # folders looked in is Program Files (x86), where a 32-bit
            # build of exactly the right version lives. The terminal
            # will not talk to it.
            if (Test-Usable $found) { return $found }
            if ($found) {
                Warn ('Ignoring ' + $candidate + ': it is ' + $found.Version +
                      ' ' + $found.Bits + '-bit.')
            }
        }
    }
    return $null
}

function Assert-Python {
    <#
        The loud refusal. A wrong Python is a machine that will behave
        differently from every other desk, so it stops the install and
        names what to do about it.
    #>
    param($Found)
    if ($Found.Bits -ne 64) {
        Fail ('This machine''s Python is ' + $Found.Bits + '-bit (' +
              ($Found.Command -join ' ') + ', version ' + $Found.Version +
              '). MetaTrader 5 will not talk to it: the handshake fails ' +
              'with an error that says nothing, and the leg simply never ' +
              'connects. Uninstall it, or install Python ' +
              $PyVersions[0] + ' 64-bit from python.org alongside it, and ' +
              'run ' + $SetupName + ' again.')
    }
    if ($PyVersions -notcontains $Found.Version) {
        Fail ('This machine has Python ' + $Found.Version + ' (' +
              ($Found.Command -join ' ') + '), and the suite has only been ' +
              'run on ' + ($PyVersions -join ' and ') + '. Refusing to ' +
              'install onto an untested one rather than making this the ' +
              'PC that behaves differently from every other desk. Install ' +
              $PyVersions[0] + ' 64-bit from python.org - ticking "Add ' +
              'python.exe to PATH" and "py launcher" - and run ' + $SetupName + ' ' +
              'again. The existing Python can stay; the launcher picks ' +
              'the right one. If this version HAS been proven, add it to ' +
              'python_versions in rollout.json.')
    }
}

function Invoke-Python {
    param([string[]] $Command, [string[]] $Arguments)
    $exe = $Command[0]
    $argv = @()
    if ($Command.Count -gt 1) { $argv += $Command[1..($Command.Count - 1)] }
    $argv += $Arguments
    & $exe @argv
}

$found = Find-Python
if ($null -eq $found) {
    if ($PyVersions -notcontains '3.11') {
        Fail ('This machine has no Python, and rollout.json does not list ' +
              '3.11 - the only version this script knows how to fetch ' +
              'unattended. Install one of ' + ($PyVersions -join ', ') +
              ' 64-bit by hand and run ' + $SetupName + ' again.')
    }
    # 64-bit, and it must match the 64-bit terminal: a 32-bit Python
    # fails the MT5 IPC handshake with an error that says nothing.
    # Include_tcltk: the setup wizard is a tkinter window.
    Say 'Installing Python 3.11, 64-bit...'
    $pyExe = Join-Path $env:TEMP 'python-3.11.exe'
    Invoke-WebRequest -UseBasicParsing -OutFile $pyExe -Uri (
        'https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe')
    # -PassThru, so the installer's own exit code is READ rather than
    # discarded. Without it a failed install looked exactly like a
    # successful one that could not be found, and the message sent the
    # operator hunting through PATH for a Python that was never there.
    #   0    installed
    #   3010 installed, wants a reboot
    #   1602 cancelled   1603 fatal   1618 another install is running
    $run = Start-Process -Wait -PassThru -FilePath $pyExe -ArgumentList (
        '/quiet InstallAllUsers=1 PrependPath=1 Include_test=0 ' +
        'Include_tcltk=1 Include_launcher=1 InstallLauncherAllUsers=1')
    if ($run.ExitCode -eq 1618) {
        Fail ('Another Windows installer is running, so Python could not ' +
              'be installed (exit 1618). Wait for it to finish - Windows ' +
              'Update is the usual one - and run ' + $SetupName + ' again.')
    }
    if ($run.ExitCode -ne 0 -and $run.ExitCode -ne 3010) {
        Fail ('The Python installer failed with exit code ' +
              $run.ExitCode + '. Nothing else has been changed. Install ' +
              'Python ' + $PyVersions[0] + ' 64-bit from python.org by ' +
              'hand - tick "Add python.exe to PATH" and "py launcher" - ' +
              'and run ' + $SetupName + ' again.')
    }
    if ($run.ExitCode -eq 3010) {
        Warn 'Python installed and asked for a reboot; carrying on.'
    }
    Refresh-Path
    $found = Find-Python
    if ($null -eq $found) {
        # Say WHAT WAS LOOKED AT before refusing. Two rounds of this
        # failure were diagnosed by guessing, and both guesses were
        # wrong; a refusal that lists the evidence ends that.
        Write-Host ''
        Write-Host '  Where this looked, and what it found:' -ForegroundColor Yellow
        Write-Host ('    installer exit code : ' + $run.ExitCode)
        foreach ($probe in @((Join-Path $env:WINDIR 'py.exe'),
                             'C:\Program Files\Python311\python.exe',
                             'C:\Program Files\Python314\python.exe',
                             (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python311\python.exe'),
                             'C:\Python311\python.exe')) {
            $mark = if (Test-Path $probe) { 'PRESENT' } else { 'missing' }
            Write-Host ('    ' + $mark.PadRight(8) + ' ' + $probe)
            # What the interpreter ACTUALLY says when asked. A file that
            # is PRESENT and still not used means the probe below it
            # failed, and its answer is the only thing that says why.
            if (Test-Path $probe) {
                $answer = $null
                $previous = $ErrorActionPreference
                try {
                    $ErrorActionPreference = 'Continue'
                    $answer = & $probe '-c' 'import sys,struct;print(sys.version_info[0],sys.version_info[1],struct.calcsize(chr(80))*8)' 2>&1
                } catch {
                    $answer = $_.Exception.Message
                } finally {
                    $ErrorActionPreference = $previous
                }
                Write-Host ('             it answers: ' +
                            (([string] $answer).Trim()))
            }
        }
        foreach ($name in @('py', 'python')) {
            $cmd = Get-Command $name -ErrorAction SilentlyContinue
            $where = if ($cmd) { [string] $cmd.Source } else { '(not on PATH)' }
            Write-Host ('    ' + $name.PadRight(8) + ' ' + $where)
        }
        Write-Host ''
        Fail ('Python installed but this window still cannot find it. ' +
              'Close this window, open a new one, and run ' + $SetupName + ' ' +
              'again - a PATH set by an installer does not reach a ' +
              'console that was already open. If it still fails, turn ' +
              'OFF the python.exe and python3.exe App execution aliases ' +
              'in Settings > Apps > Advanced app settings > App ' +
              'execution aliases: those are Microsoft Store shortcuts ' +
              'that shadow a real Python.')
    }
}
Assert-Python $found
$python = $found.Command
Say ('Using Python: ' + ($python -join ' ') + '  (version ' +
     $found.Version + ', ' + $found.Bits + '-bit)')

# tkinter is what the six-question window is drawn with. Checked here,
# where it is still fixable, rather than at the moment the wizard opens.
Invoke-Python $python @('-c', 'import tkinter') 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    Warn ('This Python has no tkinter, so the setup window cannot open. ' +
          'The rest of the install will run and you can configure the ' +
          'machine from the Exchanges page instead.')
}

# --- 3. The code ---------------------------------------------------------

Step 'The code'

if ($Token) {
    <#
        Into the Windows Credential Manager, NOT into the clone URL.
        A token in the URL is written to .git\config in plain text and
        is then in every screenshot of that file forever; this way the
        remote stays clean and Windows holds the secret.
    #>
    $uri = [Uri] $RepoUrl
    $blob = ("protocol=" + $uri.Scheme + "`nhost=" + $uri.Host +
             "`nusername=" + $TokenUser + "`npassword=" + $Token + "`n`n")
    & $git config --global credential.helper manager | Out-Null
    $blob | & $git credential approve
    Say 'Repository token stored in Windows Credential Manager.'
}

if (Test-Path (Join-Path $Root '.git')) {
    Say 'Already cloned - fetching the latest instead.'
    Push-Location $Root
    & $git fetch --quiet origin $Branch
    # --ff-only: if this machine somehow has local commits, say so
    # rather than starting a merge nobody is here to finish.
    & $git merge --ff-only ('origin/' + $Branch)
    if ($LASTEXITCODE -ne 0) {
        Warn ('This clone has local changes that cannot fast-forward. ' +
              'Carrying on with the code already here.')
    }
    Pop-Location
} else {
    if (Test-Path $Root) {
        $existing = Get-ChildItem -Force $Root
        if ($existing) {
            Fail ($Root + ' already exists and is not a clone. Move it ' +
                  'aside and run ' + $SetupName + ' again - overwriting it might ' +
                  'destroy a config or a book this desk still needs.')
        }
    }
    Say ('Cloning into ' + $Root + ' ...')
    & $git clone --quiet --branch $Branch $RepoUrl $Root
    if ($LASTEXITCODE -ne 0) {
        Fail ('The clone failed. If the repository is private, re-run ' +
              $SetupName + ' with -Token followed by a fine-grained ' +
              'read-only token for it.')
    }
}

<#
    THE CLONE MUST BE READABLE BY THE ACCOUNT THAT USES IT.

    This script runs as Administrator - it has to, to install Git and
    Python for all users - so the clone it just made is owned by that
    account. The trader then double-clicks UPDATE.bat as themselves,
    git sees a repository owned by somebody else, and refuses it
    outright with 'detected dubious ownership'. Nothing is broken; git
    will not touch it.

    --system, not --global: --global would write it into the
    ADMINISTRATOR's own config, which is the one account that did not
    need it. System scope covers every user of this PC, which is the
    set of people who might run UPDATE here.

    Forward slashes, which is the form git itself prints and matches
    against. Not fatal if it fails: the install is complete either way,
    and UPDATE now names this fix in its own refusal.
#>
$safe = $Root -replace '\\', '/'
try {
    & $git config --system --add safe.directory $safe 2>$null | Out-Null
    Say ('Registered ' + $safe + ' as safe for every account on this PC.')
} catch {
    Warn ('Could not mark ' + $safe + ' as a safe directory (' +
          $_.Exception.Message + '). Nothing is broken - the install ' +
          'is complete. If UPDATE.bat later says "dubious ownership", ' +
          'it offers to fix it there and then, for the account sitting ' +
          'at the machine, with no Administrator needed: git config ' +
          '--global --add safe.directory ' + $safe)
}

Step 'Dependencies'
Push-Location $Root
# --no-warn-script-location: pip prints a yellow paragraph per console
# script when Scripts\ is not on PATH, and on a per-user Python that is
# a dozen of them. Nothing here ever runs pytest, flask or playwright by
# name - every call in this repo goes through -m - so the warning is
# noise that reads like a failure to the person watching the install.
Invoke-Python $python @('-m', 'pip', 'install', '--upgrade', '--quiet',
                        '--no-warn-script-location', 'pip')
Invoke-Python $python @('-m', 'pip', 'install', '--quiet',
                        '--no-warn-script-location', '-r',
                        'requirements.txt')
if ($LASTEXITCODE -ne 0) { Pop-Location; Fail 'The dependencies could not be installed.' }
Pop-Location

# --- 4. Two terminals ----------------------------------------------------

Step 'MetaTrader 5'

function Install-Terminal {
    param([string] $Zip, [string] $Destination)
    if (Test-Path (Join-Path $Destination 'terminal64.exe')) {
        Say ($Destination + ' already has a terminal - left alone.')
        return
    }
    Say ('Unpacking ' + $Destination + ' ...')
    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
    Expand-Archive -Path $Zip -DestinationPath $Destination -Force
    if (-not (Test-Path (Join-Path $Destination 'terminal64.exe'))) {
        # A zip made of the FOLDER rather than its contents lands one
        # level deep. Flatten it, because every path in config.json is
        # about to be written assuming terminal64.exe is right here.
        $inner = Get-ChildItem -Directory $Destination |
                 Where-Object { Test-Path (Join-Path $_.FullName 'terminal64.exe') } |
                 Select-Object -First 1
        if ($inner) {
            Get-ChildItem -Force $inner.FullName |
                Move-Item -Destination $Destination -Force
            Remove-Item -Recurse -Force $inner.FullName
        }
    }
    if (-not (Test-Path (Join-Path $Destination 'terminal64.exe'))) {
        Fail ('No terminal64.exe under ' + $Destination + ' after ' +
              'unpacking. The golden zip is not a MetaTrader 5 folder.')
    }
}

Install-Terminal -Zip $GoldenZip -Destination $TerminalA
Install-Terminal -Zip $GoldenZip -Destination $TerminalB

# --- 5. The safety tests -------------------------------------------------
#
#     Before the trader is asked for a password, and before anything is
#     put on the Desktop. A broken install must fail HERE, in front of
#     whoever is installing it.

Step 'Safety tests'
Push-Location $Root
Invoke-Python $python @('-m', 'pytest', 'tests', '-q')
$testsFailed = ($LASTEXITCODE -ne 0)
Pop-Location
if ($testsFailed) {
    Fail ('The test suite failed on this machine. Nothing has been ' +
          'configured and no shortcut was made. Do not trade on this ' +
          'build - send the lines above to whoever maintains it.')
}

# --- 6. The six questions ------------------------------------------------

Step 'Accounts'
if ($KeepConfig -and (Test-Path (Join-Path $Root 'config.json'))) {
    Say 'Keeping the config already on this machine, as asked.'
} else {
    Push-Location $Root
    Invoke-Python $python @('deploy\configure.py',
                            '--root', $Root,
                            '--terminal-a', (Join-Path $TerminalA 'terminal64.exe'),
                            '--terminal-b', (Join-Path $TerminalB 'terminal64.exe'))
    if ($LASTEXITCODE -ne 0) {
        Pop-Location
        Warn ('The accounts were not saved. Everything else is installed - ' +
              'run deploy\configure.py again, or enter them on the ' +
              'Exchanges page after starting.')
    } else {
        Pop-Location
    }
}

# --- 7. Prove it -------------------------------------------------------
#
#     The install is not finished when the files are in place. It is
#     finished when this machine has actually logged both accounts in.
#
#     Doing it HERE means a wrong password, a typo'd login or a server
#     name that does not resolve is found in front of whoever is
#     installing, with the broker's own words on screen. The alternative
#     is a trader discovering it at 9am with nobody around.
#
#     It also opens both terminals, which is what the Algo Trading step
#     below needs - so the two are done in one visit rather than two.

Step 'Proving both accounts connect'
$verified = $false
if (Test-Path (Join-Path $Root 'config.json')) {
    Push-Location $Root
    Invoke-Python $python @('deploy\check_config.py', '--config',
                            'config.json')
    $verified = ($LASTEXITCODE -eq 0)
    Pop-Location
    if (-not $verified) {
        Warn ('At least one account did not connect - the reason is above, ' +
              'in the broker''s own words. Everything is installed; fix the ' +
              'account on the Exchanges page after starting, or re-run ' +
              'deploy\configure.py. Do not hand this machine over until ' +
              'both legs connect.')
    }
} else {
    Warn 'No config.json, so there is nothing to prove yet.'
}

# --- 8. The shortcut -----------------------------------------------------

Step 'Desktop shortcut'
<#
    A WARNING IF IT FAILS, NEVER A FAILURE.

    By this point the code is cloned, both terminals are unpacked, the
    suite has passed and both accounts have logged in. Killing that
    over an icon would be absurd - and there are two ordinary Windows
    reasons it can happen:

      * Controlled folder access (Windows Security > Ransomware
        protection) blocks writes to Desktop folders by programs it
        does not recognise.
      * A name with a character Windows forbids in a filename, from a
        -ShortcutName somebody typed.

    The name is cleaned first, and anything still wrong is said with
    what to double-click instead.
#>
$safeName = ($ShortcutName -replace '[\\/:*?"<>|]', '-').Trim()
if (-not $safeName) { $safeName = 'NEXUS Terminal' }
if ($safeName -ne $ShortcutName) {
    Warn ('The icon name had characters Windows does not allow in a file ' +
          'name; using "' + $safeName + '".')
    $ShortcutName = $safeName
}
$target = Join-Path $Root 'START-TRADING.bat'
try {
    $desktop = [Environment]::GetFolderPath('CommonDesktopDirectory')
    $link = (New-Object -ComObject WScript.Shell).CreateShortcut(
        (Join-Path $desktop ($ShortcutName + '.lnk')))
    $link.TargetPath = $target
    $link.WorkingDirectory = $Root
    $link.Description = 'Start MT5-Trader and open the ladders'
    $link.IconLocation = (Join-Path $TerminalA 'terminal64.exe') + ',0'
    $link.Save()
    Say ($ShortcutName + ' is on the Desktop.')
} catch {
    Warn ('The Desktop icon could not be created (' + $_.Exception.Message +
          '). Everything else IS installed. Start the app by ' +
          'double-clicking ' + $target + ', and make a shortcut to it by ' +
          'hand. If this keeps happening, check Windows Security > ' +
          'Ransomware protection > Controlled folder access.')
}

Write-Host ''
if ($verified) {
    Write-Host '  Done - and both accounts logged in from this machine.' `
               -ForegroundColor Green
} else {
    Write-Host '  Installed, but NOT proven - see the account errors above.' `
               -ForegroundColor Yellow
}

<#
    The one manual step in the whole rollout, and it gets its own box
    because it is the one that produces a silent failure. Algo Trading
    is a per-INSTALLATION setting living in AppData, so it cannot ride
    in the golden zip; a fresh terminal has it off, and the symptom is
    a ladder that shows prices and refuses every order.

    Step 7 left both terminals open, so this is two clicks now rather
    than a second visit.
#>
Write-Host ''
Write-Host '  ###########################################################'
Write-Host '  #  ONE THING LEFT, BY HAND, ONCE ON THIS PC:              #'
Write-Host '  #                                                         #'
Write-Host '  #  In BOTH MetaTrader 5 windows, press ALGO TRADING so    #'
Write-Host '  #  the button turns GREEN.                                #'
Write-Host '  #                                                         #'
Write-Host '  #  It is stored per installation, so it cannot be shipped #'
Write-Host '  #  in the template. Left off, the ladder shows prices and #'
Write-Host '  #  refuses every order.                                   #'
Write-Host '  ###########################################################'
Write-Host ''
Write-Host '  Do NOT run the terminals as Administrator. A terminal started'
Write-Host '  elevated will not accept a connection from a normally-started'
Write-Host '  Python, and the leg reads as unknown with no obvious reason.'
Write-Host ''
Write-Host ('  Then: double-click ' + $ShortcutName + ' on the Desktop.')
Write-Host '  Any time later, deploy\VERIFY.bat re-checks this machine.'
Write-Host ''
if (-not $verified) { exit 1 }
exit 0
