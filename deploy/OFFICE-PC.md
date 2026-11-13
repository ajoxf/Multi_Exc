# Putting MT5-Trader on an office PC

One trader, one PC, their own two MT5 accounts. `RUNBOOK.md` is the
shared EC2 box; this is the other shape.

What the trader sees:

1. Double-click **Start-Setup.bat** — the only thing in the kit folder;
   installs everything, about ten minutes
2. A window asks **six questions** — two logins, two passwords, two servers, and which pair
3. Double-click **NEXUS Terminal** on the Desktop — the ladder opens

They never open MetaTrader 5, never edit a file, never see a port
number. `terminal_path` is set for both accounts, so the engine opens
each terminal and signs it in itself (`mt5trader/broker.py`).

Because every trader is on their own machine, **everyone uses the same
ports** — 9101, 9102, web 8000. Nothing to allocate, nothing to clash.
That is only true one-trader-per-PC; on a shared box read `## Ports` in
RUNBOOK.md instead.

---

## Before the first PC: build the golden terminal

Once, and again whenever the broker ships a new build. Three steps:

1. Install MetaTrader 5 with the **broker's own installer**
   (`mentomarkets5setup.exe`) into its own folder, e.g. `C:\MT5-A`.
2. **Do not log in.** Cancel the Open an Account dialog.
3. Close the terminal.

Then double-click **`deploy\MAKE-GOLDEN.BAT`**.

Result: `deploy\MT5-golden.zip`.

Use the `.BAT`, not the `.ps1` directly. Typing
`.\deploy\make-golden-terminal.ps1` into a **Command Prompt** does not
run it — cmd.exe cannot execute PowerShell, so Windows offers to *open*
the file and you get a "Select an app to open this .ps1 file" dialog
suggesting Notepad. The `.BAT` is the same script with that trap
removed, and it checks the terminal is closed before it starts.

That is genuinely all. Everything an earlier version of this page asked
for — logging in once, tidying Market Watch, closing charts, turning
News off, ticking Allow Algo Trading — is stored in
`%APPDATA%\MetaQuotes\Terminal\<hash>\`, **not** in the program
folder, so none of it is in the zip and none of it can be prepared
here.

Two things follow, both measured on a clean PC:

- **The broker's installer already knows its own servers.** A copied
  program folder logs in from `mt5.initialize(path, login, password,
  server)` with no manual sign-in. Verified: a fresh install connected
  as 10006, and a plain folder copy of it connected as 10007.
- **Allow Algo Trading does not travel.** It is per-installation, in
  AppData. It is pressed once in each terminal the first time it opens
  — the one manual step in the whole rollout, and SETUP says so.

Do not use `/portable` to pull those settings into the program folder:
a portable terminal refuses IPC from a normally-started Python
(`mt5trader/mt5_errors.py`), so the legs would never connect.

The script strips logs and refuses outright if it finds `accounts.dat`
— which a normal install keeps in AppData, so its presence means the
folder is not what it should be.

The broker's installer is **not** silent: `/auto` still shows the EULA.
That is why SETUP unpacks a zip rather than driving the installer.

## Changing the repository or branch

**`deploy\rollout.json` — that is the one place.**

```json
"repo_url": "https://github.com/nishant-patel-git/Mirror-MT5-Trader.git",
"branch":   "main",
```

Edit it in the rollout kit; new PCs are built from that copy. Anything
passed on the Start-Setup.bat command line still wins for that one machine.

A PC that is **already installed** does not re-read it — it has a git
remote of its own. To move one:

```
cd C:\MT5-Trader
git remote set-url origin <new url>
git checkout <new branch>
```

or delete `C:\MT5-Trader` and run Start-Setup.bat again. `config.json` and
`.env` are not in the repo, so a reinstall does not lose the accounts.

## The rollout kit

`MAKE-KIT.BAT` builds it. Copy the whole `ROLLOUT-KIT` folder onto a USB
stick or a share, and it looks like this:

```
ROLLOUT-KIT\
    Start-Setup.bat          <- the only thing to double-click
    setup-files\
        setup.ps1
        rollout.json
        MT5-golden.zip
```

**One file at the top level, on purpose.** A folder showing
`Start-Setup.bat` next to `setup.ps1` is a folder where somebody
double-clicks the `.ps1`, Windows offers to open it in Notepad, and the
install never starts. The three files that are not the entry point are
out of reach — and they stay together, because `setup.ps1` looks for
`rollout.json` and `MT5-golden.zip` beside itself.

`Start-Setup.bat` asks for Administrator itself and hands over to
`setup.ps1`, which:

- installs Git and Python 3.11 64-bit if they are missing, and
  **refuses loudly** if this PC has a version not in `python_versions`
  (`3.11` and `3.14` — the two the suite has actually passed on) or a
  32-bit build. MT5's handshake fails against 32-bit with an error that
  says nothing, and an untested version makes this the one desk that
  behaves differently
- clones the repo to `C:\MT5-Trader`
- unpacks the golden zip **twice**, to `C:\MT5-A` and `C:\MT5-B`
- installs the dependencies
- **runs the test suite, and stops if it fails** — before anything is
  configured and before a shortcut exists
- opens the six-question wizard
- **logs both accounts in and proves it** (`check_config.py`) — a wrong
  password or a mistyped login is found in front of whoever is
  installing, in the broker's own words, not by a trader at 9am
- puts **NEXUS Terminal** on the Desktop (rename it with
  `shortcut_name` in `rollout.json`, or `-ShortcutName`)

It exits non-zero if either account failed to connect, so a bad install
cannot look like a good one.

Private repo, so each PC needs read access:

```
Start-Setup.bat -Token github_pat_xxx
```

Use a **fine-grained, read-only token scoped to this one repository**.
It goes into the Windows Credential Manager, never into the clone URL —
a token in the URL is written to `.git\config` in plain text and is
then in every screenshot of that file forever. A leaked read-only token
reads code; it cannot push.

## When MetaTrader 5 asks to update itself

Every terminal from one golden zip is the same build, so when the broker
ships a new one **every PC gets the LiveUpdate box on the same day**.

Let them press **Restart**. It is the terminal updating itself in place;
nothing in `C:\MT5-Trader` is touched, the accounts stay signed in, and
the engine reconnects on its own. Nothing is lost by clicking it, and
nothing breaks by clicking **Later** either.

But do it once at the source too, or every PC installed from then on
starts on the old build and updates itself on first launch — in front of
whoever is installing it.

**Refreshing the template — the same three steps as the first time:**

1. On the build PC, open `C:\MT5-A` and let MetaTrader 5 finish its own
   LiveUpdate. Then **close it.**
2. Double-click `deploy\MAKE-KIT.BAT`. It rebuilds `MT5-golden.zip` from
   `C:\MT5-A` and re-gathers the kit.
3. Replace the `ROLLOUT-KIT` folder on the stick and in whatever you
   email out. Delete the old one rather than copying over it.

Two things worth knowing:

- **`C:\MT5-A` on the build PC must never be logged in.** If it has
  been, log out and clear the account before rebuilding — the template
  is refused if it finds saved account details, but a build zipped from
  a used terminal is a bad habit either way.
- **Machines already installed need nothing.** They update themselves
  from the LiveUpdate box. The rebuild is only so the NEXT PC starts on
  the current build.

---

## One PC, two shifts

A machine shared by two traders on different shifts — say 10001/10002 in
the morning and 10003/10004 in the evening, four MT5 terminals in all —
is **two installs, not one install with four accounts.**

That is not a preference. `config.json` keys a pair by its two symbols
(`XAUUSD.f|GCZ6`), so two desks trading the same instruments would be
one row, not two; and `ADD-PAIRS` reads which account is leg A off the
pairs already there and **refuses** a config whose pairs disagree,
rather than guessing a leg onto the wrong terminal.

Nothing in the rollout kit changes. Run the same `Start-Setup.bat`
twice, naming the second desk's folders on the command line:

```
Start-Setup.bat
```

then, for the second trader:

```
Start-Setup.bat -Root C:\MT5-Trader-2 -TerminalA C:\MT5-C -TerminalB C:\MT5-D -ShortcutName "NEXUS Terminal - Evening"
```

Four flags, one line, and each desk gets its own clone, its own
`config.json`, its own `.env`, its own two terminals and its own icon.
`-ShortcutName` is the one that is easy to forget: without it the second
run would rewrite the first desk's Desktop icon to point at the second
desk's install.

Two things to know:

- **Ports.** Both installs default to `9101`/`9102` and web `8000`. On
  true shifts — one trader at a time — that is fine and there is
  nothing to change. If the two ever run **at the same time**, give the
  second desk `9103`/`9104` on the Exchanges page and start it with
  `START-TRADING.bat` edited to `--port 8001`.
- **The roll list.** `ADD-PAIRS.BAT` works per install. Run it in
  `C:\MT5-Trader` and again in `C:\MT5-Trader-2`; the same
  `deploy\pairs.json` serves both, because each config has exactly one
  account on each leg.

---

## The six questions

`deploy\configure.py`, opened by SETUP or run again any time:

| | |
|---|---|
| Account A | login, password, server |
| Account B | login, password, server |
| Pair | pick a preset, then the contract month(s) — or type both symbols |

Both server boxes come pre-filled with `default_server` from
`presets.json` (`MentoMarkets-Server`) and both stay editable — they are
separate answers, so a desk running one leg at a second broker types
over the second box and nothing else changes.

The two terminal folders are **not** among the questions. SETUP made
them and passes them in — a trader asked to type a path is a trader who
can type the same path twice, and two accounts on one installation is
one account hedging against itself.

What it writes, and nothing else:

- `config.json` — the two accounts and the pair
- `.env` — the two passwords, quoted, and nowhere else

**The accounts are named for their logins** — `AC-10006`, `AC-10007`.
That is what the ladder header prints (`AC-10006 → AC-10007`) and what
the Positions and Fills tables show. `Account A` told a trader looking
at two ladders nothing; the login is the number they see in MetaTrader
5 and on the broker's statement. It is also the desk's existing
convention, so nothing new has to be learned.

Re-running the wizard with a corrected login writes a new name and
clears the row it replaces — along with any pair that named it. Left
behind, that old row would sit on the same MT5 folder, and one
installation holds one login, so the machine would refuse to start with
a terminal clash the trader never caused. Accounts on *other*
installations are untouched.

The `.env` key comes from the app's own `env_key_for()`. Nothing guesses
it: a key the loader does not read is a password that is silently never
there.

It **refuses to overwrite a machine that already has accounts.**
`--force` replaces it and keeps `config.json.bak`. A `config.json` that
is still the shipped example is replaced without asking, because
`start.py` copies the example on a first run — the file existing is not
evidence anyone configured anything.

Tick size, contract size and lot steps are left unset on purpose. The
app reads them from MT5 on the first connect; a number typed at setup
is a number that can be wrong, and every money figure on the ladder
runs through it.

## Every month: the roll

**`deploy\pairs.json`** — you edit it when the contract months change,
hand it out, and the trader double-clicks **`ADD-PAIRS.BAT`**. The new
ladders appear on their Exchanges page.

```json
{ "name": "Gold basis", "leg_a": "XAUUSD.f", "leg_b": "GCZ6",
  "pair_type": "SPOT_FUTURE" }
```

What ships today — four pairs:

| Leg A | Leg B | Type |
|---|---|---|
| `XAUUSD.f` | `GCZ6` | SPOT_FUTURE |
| `XAGUSD.f` | `SIZ6` | SPOT_FUTURE |
| `USOIL.f` | `UKOIL.f` | RELATED |
| `USOILX6` | `UKOILX6` | RELATED |

It **only adds.** A pair already on the machine is reported and left
*exactly* as it is — not re-written, not re-enabled, not re-stamped —
because the trader may be holding a position on it and editing a pair
under a live position moves the ladder out from under the money.
Nothing is ever deleted or switched off, and accounts are never
touched. So it is safe to run twice and safe to run mid-session.

`ADD-PAIRS.BAT --dry-run` says what it would do and writes nothing.

If the engine is running it restarts itself within a few seconds when
the config changes (`mt5trader/config.py`, `restart_required`); if it
is not, the pairs are there at the next NEXUS Terminal start.

**One file serves every PC.** It does not name the two accounts —
names carry the login (`AC-100015`), so they differ on every desk.
`ADD-PAIRS` reads which account is leg A and which is leg B from the
pairs the machine already trades. That is not a guess: it is what that
config does every day.

Name them explicitly (`leg_a_account` / `leg_b_account`) only for a
machine with no pairs at all, or one whose existing pairs disagree
about which account is which. Both cases are refused with the reason
rather than guessed at, because picking one is how a leg ends up on the
wrong terminal.

## Adding an instrument to the setup wizard

`deploy\presets.json` seeds the **first** install only. Data, so a new
instrument is an edit rather than a release to every PC.

`{contract}` in a leg is the month-and-year code the trader types at
setup (`Z6`, `U6`, `V6`, `1226`). Put it in either leg, both, or
neither: a basis dates only the future, a calendar spread dates both
and gets **two** boxes. One box for a calendar spread would make both
legs the same symbol, and that spread is always zero.

Both the suffix and the contract code belong to the **liquidity
provider**, not to us. The same gold is `XAUUSD.f` on one LP and
`XAUUSD_` or `GOLD` on the next. When the LP changes, this file and
`pairs.json` change.

`pair_type` is not cosmetic:

| | |
|---|---|
| `SPOT_FUTURE` | spot against its own future — a basis, has a fair value |
| `FUTURE_FUTURE` | two contract months of one instrument |
| `RELATED` | two different instruments — **no** fair value |

A basis marked RELATED loses its fair value; two unrelated instruments
marked as a basis are given one they do not have. "Type my own" writes
RELATED, the safe way to be wrong — correct it on the Exchanges page.

A `presets.json` that will not parse leaves the wizard with no menu,
not a crash: the trader can still type both symbols.

## Every morning

NEXUS Terminal does the whole start:

1. finds a Python
2. installs any new dependencies
3. **preflight** — can this machine actually connect?
4. **the safety tests** — and it will not start the engine if they fail
5. starts the engine and opens the ladders

**It does not update itself.** A desk's version changes when somebody
decides it changes, not because a commit landed overnight. Step 2 is
allowed to fail — the office internet goes down, and a trader must
never arrive to a black window and no ladder. Step 4 is not allowed to
fail; that rule is what keeps a bad build away from a live account.

## Updating a PC

`deploy\UPDATE.BAT`, run deliberately by whoever maintains it, on the
machine being updated:

- refuses to run under a live engine (it asks, and the default is no)
- records the current commit **before** anything moves
- fast-forwards, updates dependencies, and **runs the tests on the new
  version**
- if those tests fail it puts the PC **back** where it was, reinstalls
  the old dependencies, and says so

`deploy\UPDATE.BAT --rollback` steps back one version and re-runs the
tests, for the case where a build passes its tests and still misbehaves
on the desk.

The cost of no auto-update, stated plainly: a fix pushed today is not
on any PC until somebody walks to it.

## What the preflight refuses

`deploy\preflight.py` refuses only what would trade **wrong**:

- one login, one port or one MT5 folder on both accounts — that is one
  account entered twice, and the pair would hedge against itself
- a `terminal_path` pointing at a file that is not there
- an account with a blank `terminal_path` when no terminal is open (it
  attaches to whatever is running, so something has to be running)

A configured machine with no terminal open is **fine** and starts — the
engine opens them. The old check refused exactly this case.

## The one manual step

**Press Algo Trading in both terminals**, once per PC, so the button
turns green.

It is stored per installation in AppData, so it cannot ride in the
golden zip. Left off, the ladder shows prices and refuses every order —
which looks like a broken feed rather than a switch. SETUP leaves both
terminals open and says so in a box you cannot miss.

## Checking a machine later

**`deploy\VERIFY.bat`** — double-click. Changes nothing, starts
nothing, places no order. It runs the preflight (one login, one port or
one terminal folder on both rows) and then logs each account in and
reports what the broker said.

That is the first thing to run when a desk says "it will not connect".

## When the liquidity provider changes

Symbols belong to the LP, and both parts move — the suffix (`.f`, `_`)
and the contract code (`GCZ6`, `GC1226`). Two files carry them, and
nothing else does:

| File | Used for | Reaches a PC by |
|---|---|---|
| `deploy\presets.json` | the setup wizard's menu | a fresh Start-Setup.bat |
| `deploy\pairs.json` | the monthly roll | ADD-PAIRS.BAT |

Check the spelling in Market Watch, or with **Find symbols** on the
Exchanges page, before editing either. A symbol the terminal does not
have cannot trade: the pair sits on the screen reading unknown.

## One thing this shape is not for

This is the "give them the DVD" model: the code lands on the PC and can
be copied. Fine for your own office, your staff, your machines. Do not
use it for paying subscribers — keep those hosted.
