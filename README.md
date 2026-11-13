# Multi_Exc — the MT5-Trader ladder, across two exchanges

The same spread price-ladder terminal as **Mirror-MT5-Trader** — the same
ladder, Market Grid, positions monitor, Exchanges page, guards, journal,
slippage report and restart recovery, unchanged — with one difference:
**Leg A trades on an MT5 terminal (CME / NYMEX / COMEX) and Leg B trades on
MCX through Arrow**, the broker behind arrow-statarb.

It is built for watching and trading spreads like these:

| Ladder | Leg A (MT5, USD) | Leg B (Arrow, INR) | Leg A restated as |
|---|---|---|---|
| Crude | NYMEX CL — USD/bbl, 1,000 bbl a lot | MCX CRUDEOIL — INR/bbl, 100 bbl a lot | CL × USDINR × 1 |
| Gold | COMEX GC — USD/troy oz, 100 oz a lot | MCX GOLD — INR/10 g, 1 kg a lot | GC × USDINR × 0.32151 |
| Silver | COMEX SI — USD/troy oz, 5,000 oz a lot | MCX SILVER — INR/kg, 30 kg a lot | SI × USDINR × 32.1507 |

It is still a manual tool: no strategy, no signals, no automatic entries or
exits.

## What is different from Mirror-MT5-Trader

Everything is the Mirror code, and everything above the brokers is
untouched. The differences sit underneath it:

| Module | What it adds |
|---|---|
| `mt5trader/arrow_broker.py` | `ArrowSession` — the only module that imports `pyarrow_client`. It gives the leg runner **exactly** the methods `BrokerSession` gives it for MT5, so the coordinator, executor, quoter and reconciler run unchanged on an Arrow leg |
| `mt5trader/convert.py` | `ConvertedLeg` — restates a dollar leg in the rupee leg's currency **and** unit (`A' = A × FX × unit factor`): ticks, depth, fills, pendings, positions and contract size. Limit prices and modifies go back to the broker in its own dollars |
| `mt5trader/fx.py` | The exchange rate. USDINR is typed by hand on the Exchanges page (stamped with the time, shown with its age), or read live from the mid of a symbol on either account |
| `mt5trader/contracts.py` | The MCX and CME contract specs (quote unit, lot, tick), unit factors, the lot-for-lot comparison of the two legs, and the expiry watch |

### The spread across two currencies

```
spread = Leg B − β × (Leg A × USDINR × unit factor)
```

Both legs are built from the **mid of the book**, exactly as before. Leg A's
contract size is restated per converted unit (100 oz of COMEX gold is 311.03
"10-gram" units), so `k = L_B × C_B` is still the one multiplier every
spread-to-money conversion uses, and every figure on the ladder is in rupees.
Each converted tick carries leg A's own dollar price beside it, and the ladder
shows it on a strip under the quote: `A x USDINR 88.50 (2.1h old) x 0.3215 ·
A raw 2650.4/2650.6 · expiry A 12.0d · B 22.0d`.

**The rate is part of the price.** A rate that cannot be read makes leg A
dark (unmeasured is not zero — never a rate of 1). A **typed** rate older than
its `max_age_hours` (24h by default) still draws the spread, but withholds
**orders** with the reason, the same as a stale leg, and the feed badge reads
`FX stale`. It never withholds a **close**: a close goes to the broker in its
own units and needs no rate.

A fill is restated at the rate in force when it is reported, and the book
keeps that price. Marking it later at a different rate shows the dollar leg's
FX translation as part of the spread's P&L — which is real: the rupee leg has
USDINR inside its price and the dollar leg does not. Hedge that with a USDINR
future if you need to; this terminal shows it rather than hiding it.

### Lots, contracts and expiries — monitored, not assumed

* **Lot sizes differ, so the two legs are compared in the commodity itself.**
  One CL (1,000 bbl) is exactly ten MCX CRUDEOIL (100 bbl): clip 1 : 10.
  One GC (100 oz = 3.11 kg) against three 1 kg MCX GOLD is 3.5% short, and
  that 3.5% is an **outright** position in gold, not part of the spread. The
  Exchanges page and Diagnose print the match and the closest small clip.
* **Contract sizes are read from each broker** (MT5's `trade_contract_size`,
  Arrow's instrument master) and cross-checked against the exchange spec in
  `contracts.py`. A disagreement is named by Diagnose — e.g. Arrow's master
  giving MCX GOLD a lot of 1 (kg) when the price is per 10 g (contract 100).
  Verify the table against the current MCX circular; exchanges revise lots.
* **Expiries are watched on every ladder.** Both legs' expiries (MT5's
  `expiration_time`, Arrow's master, or the date typed on the pair) are shown
  with days to go, a warning inside `EXPIRY_WARN_DAYS` (5), and a warning when
  the two expire `EXPIRY_GAP_DAYS` (3) or more apart — the leg that goes first
  leaves the other unhedged. An expired leg is a red alert. MCX and CME
  contracts of the "same" month expire on different days.

### The Arrow leg

* **Arrow is a netting account; the engine needs tickets.** Every order is
  written to a ledger (`data/arrow-<account>.ledger.json`) **before** it is
  sent; each opening fill becomes a ticket (its order id), and a close by
  ticket is an opposite order for that ticket's volume — which on a netting
  exchange *is* the close. A long and a short spread can stand side by side,
  as on a hedging account.
* **Positions are cross-checked against Arrow's own net.** Volume the
  exchange does not hold is withheld from the report (so the reconciler sees
  it as gone and counts its strikes); extra volume at Arrow that the ledger
  cannot explain is not ours and is never touched. Both show on Diagnose as a
  net-position mismatch. Do not trade the same MCX symbol by hand on this
  account — it nets against this system's.
* **An order lost mid-send** (process killed between the ledger write and
  Arrow's answer) comes back as `unknown`, is named in the log, and is not
  guessed either way.
* **Market orders** are `MKT` with `mpp=True` and `price=0` — plain MKT is
  disabled on Arrow (arrow-statarb fact 6). `quantity` is units (lots × lot
  size); set `quantity_in_lots` on the account only if a one-lot test proves
  Arrow wants lots for MCX.
* **The broker's clock is IST** (UTC+05:30, fixed), so the session cutoff
  and the journal run on the exchange's day.

> **MCX on Arrow must be verified first.** arrow-statarb refused MCX because
> the `pyarrow-client` build it used had no MCX exchange. `ArrowSession`
> asks the installed SDK for one and **refuses to start, in words**, if it is
> not there. The quote and order field names are read tolerantly, but they
> have not been tested against a live MCX account: run the leg runner, press
> **Diagnose**, and place a one-lot order on each symbol before trading size.
> If a build answers prices in paise, set `price_divisor: 100` on the account.

### Setting it up

1. **Exchanges → accounts**: add the MT5 account (kind *MT5*) and the Arrow
   account (kind *Arrow (MCX)*: app id, user id, password, API secret and the
   TOTP **seed**; the secrets go to `.env`). Each gets its own runner port.
2. **Exchanges → Exchange rates**: type today's USDINR and press *Set*.
3. **Pairs → New pair**: leg A on the MT5 account (e.g. `GCG7`), leg B on the
   Arrow account (e.g. `GOLD05FEB27FUT` — *Find* searches Arrow's MCX master).
   Press **Read both legs** — it offers the FX and unit factor from the
   contract specs, the lot match and both expiries — then Save.
4. Press **Diagnose** on each account before the first trade of the day.

`config.example.json` has the crude, gold and silver pairs set up.
`pip install pyarrow-client` on the machine that runs the Arrow leg (it must
be the static IP registered with Arrow).

---

The rest of this README is Mirror-MT5-Trader's, and all of it still applies.

## The specification

The full build specification is held privately, outside this repository.
Ask the repository owner for a copy — it is required reading before
writing any code.

## What is built so far

The spec's build order, in order. Steps 1-8 are in:

| Module | What it does |
|---|---|
| `mt5trader/config.py` | Accounts, pairs, settings; the three clash refusals; atomic saves; `.env` secrets |
| `mt5trader/broker.py` | The ONLY module that imports MetaTrader5 — every MT5 quirk lives here |
| `mt5trader/leg_runner.py`, `ipc.py`, `legs.py` | One process per account, JSON-lines over localhost, `LocalLeg`/`RemoteLeg` behind one interface |
| `mt5trader/spread.py` | The spread, the executable touches, the staleness and jump guards |
| `mt5trader/sizing.py` | `L_B = L_A x C_A / (beta x C_B)` and the one `k` |
| `mt5trader/hedgeratio.py` | Beta stamped with the pair it was computed for |
| `mt5trader/costs.py` | The round trip, split into crossing (already in the prices) and commission (not) |
| `mt5trader/executor.py` | MARKET entry both legs, the 2.0s escalation, unwind by ticket, ticket-based closes |
| `mt5trader/book.py` | Synthetic orders one-per-click, positions, net and average |
| `mt5trader/coordinator.py` | The poll loop, the guards, the sweeps, one status snapshot for every panel |
| `mt5trader/quoter.py` | The synthetic LIMIT path: quote one leg, re-peg off the OTHER leg by MODIFY, cross on fill |
| `mt5trader/reconcile.py` | Orphans and ghosts, three strikes, contract-size-correct P&L |
| `mt5trader/session.py` | The one cutoff: DAY orders die, each ladder's overnight rule decides |
| `mt5trader/slippage.py` | The session window on the broker's clock, and the slippage report over it |
| `mt5trader/database.py` | SQLite (WAL, 30s busy timeout): crash-safe positions, the fill journal, the audit trail |
| `mt5trader/shutdown.py` | An unanswered prompt means NO |
| `mt5trader/commands.py` | The web↔coordinator bridge, primed at startup so a restart never replays a command |
| `mt5trader/webapp.py` | The Flask process: it renders and it asks; it never trades |
| `mt5trader/static/`, `templates/` | The ladder, the Market Grid, the positions monitor and the settings page — self-hosted, no CDN |

The UI is the reference TT screen: window chrome and a bottom taskbar,
the quote strip, the left control rail, and the five-column grid
(`Work | Bids | Price | Asks | LTQ`) with the black inside-market rule,
bid blue and ask red. MARKET mode re-tints the click columns and
confirms through the one shared modal — there are no native dialogs
anywhere, and a test fails the build if they come back.

Settings (accounts and pairs) is a page in the browser: it saves each
account with the clash refusals on the row, tests a terminal (naming
Algo Trading being off, or a netting account, in words), searches each
broker's symbols, and derives β, the increment, the matched-minimum
clip and the pair's minimum notional from what MT5 says — each shown
with its derivation and offered as a one-click correction, never
applied silently. It talks to the leg runners directly, so it works
with the coordinator down.

**Restart recovery.** Open positions are written to the database on
every change and recovered at startup with their own ids, fills and
tickets. Until recovery has completed against both accounts the book is
marked incomplete and the reconciler auto-closes **nothing** — an
orphan is only an orphan if we are sure it is not ours. Anything at the
broker carrying our magic that recovery cannot explain is listed as
UNCLAIMED on a red banner, never closed automatically, with *adopt* and
*close it* for a person to choose.

**Fills — the trade journal.** Every fill the broker reports, read back
from MT5's own deal history rather than from our intentions, keyed by
(account, deal id) so two brokers' identical deal numbers cannot
overwrite each other. It carries the trader's own terminal clicks too,
marked as not ours, with the broker's commission, swap and P&L and the
broker's own clock beside the offset from ours. Exportable as CSV.

**Exchanges** is the setup page: each account with **Connect** (is the
runner there and the terminal logged in), **Test** (can it trade — Algo
Trading, permissions, hedging) and **Diagnose** (everything, including
whether the two legs fit each other: symbols, contract sizes, expiry,
and a beta stamped for the pair it is actually used on). Every failure
carries the step that fixes it. At the top, one line says whether the
system is **CONNECTED** or names the single thing standing in the way,
and it is said out loud once when it becomes true.

**Times are the broker's.** The session cutoff runs on the broker's
clock, measured from the terminal rather than configured, and the page
shows the broker's time and its offset from the machine. Unmeasured is
not zero: with no measurement the cutoff does not fire, and says so.

**Accounts** is the fifth monitor tab: equity, balance, credit, open
P&L, margin used and free, margin level against the broker's own call
and stop-out levels, and this system's own lots and units on each
account. With two brokers there is no combined margin — each posts its
own and the pair can only be carried by the **weaker** of the two, so
that account is named rather than averaged into a total that reads
comfortable.

**Slippage — the report over a real session.** The sixth monitor tab
reports the session you are in, cut at the cutoff on the **broker's**
clock so it lines up with the day MT5 stamps its deals in. Entries are
measured against the touch the click was taken at, exits against the
touch the close was sent at — and the exit is now anchored on the
CLOSING fills rather than on the touch it aimed at, which is what makes
an exit's slippage measurable at all. Positive is a cost at both ends,
in spread points and in money through the same `k`. MARKET and LIMIT
are reported side by side, because that is the split the peg has to
justify itself against, and the worst entries are ranked in money
rather than points. A fill that could not be priced is counted as
**unmeasured**, never averaged in as zero, and the journal is counted
over the same window as a check on coverage. Exportable as CSV, with
empty cells rather than zeros where nothing was measured.

## One click is one order

That is the product, so it is the default and it is fast:

- A **market click crosses both accounts immediately** — no confirmation
  dialog. The arming carries the weight instead: the mode badge, the
  tinted click columns, the crosshair cursor. A desk that wants the
  extra gesture turns on *ask before crossing* in Settings → Trading.
- The clicked price is still the **slippage guard**: a fill worse than
  it by more than `MARKET_PROTECTION_TICKS` is refused, with the reason
  on the ladder.
- Clicks are drained on their **own thread** (~20 ms), not on the price
  poll — a click never waits for the next 300 ms pass.
- The order appears on the ladder **before the engine answers**: the row
  flashes and a hatched ghost sits in the Work column until the real
  order arrives. A trader who cannot see their click clicks again.
- **The Asks column BUYS the spread** (buy leg B, sell leg A); the Bids
  column sells it. The rail's BUY button is red and SELL is blue for
  the same reason — a button takes the colour of the column it acts on.
- **A click away from the touch RESTS.** A buy under the offer cannot
  cross at any price, so it becomes a working order at that level and
  the toast says so. `CLICK_AWAY_RESTS` turns that off and the click is
  refused instead.
- **BUY / SELL / FLATTEN** on the rail hit the touch and get flat
  without hunting for a row.
- **The ladder centres on the mid**, re-centres every `RECENTRE_SEC`
  (5s by default; 0 = only when the market leaves the window), and
  leaves a hand-scrolled ladder alone for four seconds so an order can
  be placed twenty rows away. Lock stops it entirely.
- **Sound**: one blip when an order is placed, two rising notes when
  one fills, a low note when one is cancelled — generated in the page
  with WebAudio, so nothing can be silenced by a blocked network. Off
  is one click in the taskbar, and so is turning the **keyboard** off
  entirely: B and S are orders.
- **One line says whether it is working**, always on screen: green only
  when both accounts answered AND every enabled ladder is quoting.
- **The Bids and Asks columns carry the implied size** — how many
  spreads the two order books can actually do, matched leg against leg
  and counted once. Where a broker publishes no depth the columns stay
  empty rather than inventing a size from one leg.
- **The exit price on the rail**: break-even (the entry price plus
  commission, both legs, both ends) and break-even plus a target that
  is a percentage of the MARGIN one spread ties up — priced by the two
  terminals with `order_calc_margin`, not derived from notional. Both
  directions, because a long leaves on the bid and a short buys back on
  the offer. A position that is on gets its own line, anchored on the
  price it was ENTERED at. Nothing is sent to the broker: it is a price
  on the screen, and no leg ever carries a broker-side stop.
- **Fair spread on the rail**: `swap per day × days to expiry`, with
  the market named rich or cheap against it. Set the futures leg's
  expiry and swap on the Exchanges page; without both there is no fair
  value and the box shows an em dash.
- **The mid carries a heavy rule** and is what the ladder centres on;
  the inside-market rule stays where it was, between the two touches.
- **Keyboard**: `B` buy at the offer, `S` sell at the bid, `F` flatten,
  `X` cancel this ladder, `1`–`5` arm 1/5/10/50/100, `0` clear, `L`
  lock the scroll, `M` switch LIMIT↔MARKET, `Tab` next ladder, `?` the
  list. Keys never fire while a field has focus.

Flatten and the global kill still ask once — they are irreversible and
they are the buttons pressed in a hurry.

## More than one spread

Every pair is its own ladder, and they trade side by side on one
desktop: Spot silver vs the silver future, WTI vs Brent, gold basis —
as many as the two accounts carry symbols for.

Add one on **Exchanges → Pairs → New pair**: pick the account and
symbol for each leg (Find lists what each broker actually offers),
press **Read both legs from MT5** to derive β, the increment, the
matched-minimum clip and the minimum notional from the contract specs,
then Save. The key is built from the two symbols. The launcher restarts
the engine within a few seconds and the ladder appears.

A pair saved on the Exchanges page gets its ladder **by itself**, beside
the ones already open, as soon as the engine picks it up — no reload. A
ladder you close stays closed. The **+** button in the taskbar lists
every ladder, the Market Grid and the Positions window, to open one
again. Windows move by their title bar and resize from
the corner grip, and where you put them is where they are after a
reload; **Tidy** puts them back in a row.

## Running it — one file

```
python start.py
```

Or `py -3.11 start.py` where the python.org launcher is installed — a
conda or venv prompt has `python` and usually no launcher at all, so
use whichever of the two answers on your box. (`python -3.11` is not a
thing: `-3.11` is the LAUNCHER's argument, and `python` reads it as an
option and exits.)

That is the whole thing. No arguments, nothing to copy, nothing to
edit: it creates `config.json` and `.env` on a first run, brings up the
web UI **first** (the accounts are entered on that screen, so it has to
be reachable before there are any), starts a leg runner per account and
the coordinator, opens the browser, and restarts a crashed child with
backoff.

Save an account on the Exchanges page and the launcher notices within a
few seconds and restarts the engine to pick it up — accounts, symbols
and β are read at startup, so it has to come back to see them, and that
should not be the operator's job to remember. It watches only those
fields: changing a display setting or a per-ladder knob does not
interrupt trading.

**Credentials are entered in the UI**, never in a file. The password
goes to `.env` under a sanitised key; `config.json` holds only the name
of that key.

On the trading box, double-clicking **START-TRADING** does the same
thing with a few guard rails first (Python present, a terminal running,
the test suite passing — it refuses to start the engine on a failing
suite). `deploy/bootstrap.ps1` prepares a fresh Windows box and puts
that shortcut on the desktop; `deploy/RUNBOOK.md` is the page to hand
to whoever runs it day to day.

## Running it (the pieces)

The terminals and the leg runners live on the Windows box; everything
else runs anywhere.

```
cp config.example.json config.json     # then edit accounts and pairs
cp .env.example .env                   # passwords go here, nowhere else
python start.py --config config.json   # spawns the runners + coordinator
```

Or run the pieces separately while setting up:

```
python run_leg.py --config config.json --account leg_a
python run_leg.py --config config.json --account leg_b
python run_coordinator.py --config config.json
```

## Tests

```
pytest tests/ -q
```

262 of them, including an end-to-end suite that runs two leg runners on
real sockets, a coordinator with a real database, the Flask process and
Chromium — a click crosses every boundary except MT5 itself, survives a
coordinator restart, and lands in the journal. Twenty-one more drive
the UI under Playwright and read `pageerror` — Python tests cannot see a temporal-dead-zone
`ReferenceError` that aborts a script block and silently unregisters a
handler, and that is exactly what happened in the system this is ported
from. They skip cleanly where no browser is installed — and nothing else
skips or fails with them: a box with no Chromium runs 231 and skips 31,
because every test about what happens to a live position clicks through
the command bridge when there is no browser to click with. (To get the
browser ones too: `python -m playwright install chromium`.)

They must pass before any commit, and LIVE mode must never be run
without them. Everything is faked — `FakeBroker` keeps a real
hedging-mode book, so an opposite market order opens a SECOND position
exactly as it does on the real accounts. No MT5, no network, no clock.

## Reference screen

The UI target is a TT inter-product ladder screen supplied by the operator.
Drop it at `docs/reference/tt_ladder.png` — §3.3 transcribes it, and the
Playwright layout test checks against it.
