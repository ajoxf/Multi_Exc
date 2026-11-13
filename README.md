# Multi_Exc — the MT5-Trader ladder, across two exchanges

The same spread price-ladder terminal as **Mirror-MT5-Trader** — the same
ladder, Market Grid, positions monitor, Exchanges page, guards, journal,
slippage report and restart recovery, and the same optional per-ladder
**Algo** (Signals mode signals and you trade; LIVE, switched on and
confirmed by a person, trades that ladder by its rules) — with one
difference: **Leg A trades on an MT5 terminal (CME / NYMEX / COMEX) and
Leg B trades on MCX through Arrow**, the broker behind arrow-statarb.

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

### Arbitrage alerts — alert only, never an order

Each ladder can watch its own gap and say when it is worth looking at:
on the ladder (a purple ARBITRAGE line), as a banner with a sound of its
own, in the log, and on Telegram. It fires when BOTH are true:

* the **executable** gap, after costs, beyond this pair's normal, clears
  the edge needed — MCX rich: `MCX bid − CME ask × FX × factor − normal −
  costs`; MCX cheap: the mirror. Costs are commission on both legs both
  ends, the crossing still to pay on the way out, the **USDINR hedge**
  (the dollar leg's notional in NSE lots — $88,400 = 88 lots — times the
  round-trip cost per lot you type), and any per-unit extra (CTT, fees). "Normal" is typed per pair: about 0 for crude, the usual
  duty premium for gold and silver.
* the spread is unusual against its own last five minutes (z-score past
  the sigmas set; 0 turns this test off).

A stale leg, a desynced jump or a stale USDINR suppresses it — that is
how a fake gap looks. It fires once, and re-arms only when the edge has
fallen back below half the margin. It reads the snapshot and **never
sends an order**; the trader decides and clicks. Set up per ladder under
⚙ → Arbitrage alert; Telegram in `.env` (see docs/SETUP.md).

### Live charts

Each ladder has Mirror's chart: its **Chart** button on the title bar
opens and closes it — the Algo's z-score between its entry and stop
lines, in candles, and the spread over time. These are the live graphs
arrow-statarb shows. A reading only.

### Units and contract sizes on the pair form

"Leg A priced per" and "Leg B priced per" are dropdowns, and the unit
factor is worked out from them on screen. Each leg also has a contract
override — a lot quantity and its unit, e.g. "5 kg, priced per kg = 5"
— worked out the same way. Blank uses the broker's figure; the
exchange's published lot is shown beside it to check against. An
override is always in the leg's OWN units (100 oz for a COMEX gold
lot); the engine converts it for a converted leg.

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

Every command for a new machine — install, tests, a no-broker demo
(`python tools/demo.py`), first run and daily start — is in
[docs/SETUP.md](docs/SETUP.md).

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
| `mt5trader/quoter.py` | Working orders: held here and crossed on both legs when the spread reaches the level (default); the old one-leg quote behind `WORKING_ORDERS = QUOTE`; resting closes |
| `mt5trader/reconcile.py` | Orphans and ghosts, three strikes, contract-size-correct P&L |
| `mt5trader/session.py` | The one cutoff: DAY orders die, each ladder's overnight rule decides |
| `mt5trader/slippage.py` | The session window on the broker's clock, and the slippage report over it |
| `mt5trader/database.py` | SQLite (WAL, 30s busy timeout): crash-safe positions, the fill journal, the audit trail |
| `mt5trader/shutdown.py` | An unanswered prompt means NO |
| `mt5trader/commands.py` | The web↔coordinator bridge, primed at startup so a restart never replays a command |
| `mt5trader/bands.py` | Bollinger bands on the spread: candles, EMA middle, population sigma, MT5 bars matched across two brokers' clocks |
| `mt5trader/algo.py` | Fair spread, and the Algo signal: entry, gates, exits — decides, never acts |
| `mt5trader/algodesk.py` | The per-ladder Algo switch (off at every start), history backfill, and the sink that records intents |
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

**Each ladder keeps its own hours** (ladder Settings: *Session open*,
*Session close*, *Break start*, *Break end*, all HH:MM broker time). The
close is that ladder's cutoff - DAY orders cancelled, the Overnight rule,
the Algo's last entry, the backtest and the END OF DAY message. Outside
the session and in the break the Algo takes no new entry (exits are never
held), and a silent feed is expected rather than warned about or
re-subscribed. Blank close is the desk-wide `OVERNIGHT_CLOSE_HOUR:MINUTE`
(16:55); blank open is any time before the close; an open after the close
is a session over midnight; the break takes both ends or neither. A
ladder with nothing set behaves exactly as before.

**Accounts** is the fifth monitor tab: equity, balance, credit, open
P&L, margin used and free, margin level against the broker's own call
and stop-out levels, and this system's own lots and units on each
account. With two brokers there is no combined margin — each posts its
own and the pair can only be carried by the **weaker** of the two, so
that account is named rather than averaged into a total that reads
comfortable.

**Working orders (LIMIT) are held by the desk.** A LIMIT click - or a
MARKET click away from the touch - puts NOTHING at the broker. The desk
holds the level, and when the EXECUTABLE spread reaches it (the Sell
spread at or over a SELL, the Buy spread at or under a BUY) both legs
cross at market together, through the same path a market click takes.
It fills when the SPREAD gets there. It used to be a real limit on one
leg with the other crossed on the fill; on two legs that move together
(spot gold against the gold future) that limit filled on a move in gold,
not in the spread, and the leg crossed after had moved the same way -
live, a SELL at 28.30 went on at 26.84, every fill the same direction.
Just before sending, both legs' prices are read AGAIN, fresh, and the
order goes only if the spread is still at the level - a level the poll
saw for an instant and the market has already left is not traded, and
the order keeps working (`RECHECK_BEFORE_SEND`, on; the Working orders
panel counts the flickers it skipped). A held order costs both legs' bid-ask and works only while the desk is
running (as the old one did - our pendings are swept at shutdown). A
stale or jumping price holds it; one that reaches its level and is
refused ends there, in the broker's words, and is journalled. The old
quote is still behind the desk setting `WORKING_ORDERS = QUOTE` for a
pair whose legs genuinely move apart.

**Each side can have its own exits.** Under the ATR settings, a "Per
side" block gives H to L and L to H their own SL by / TP by (% or ATR),
SL %, TP % and ATR multiples. Blank is the ladder's own value - never a
zero - and a trade keeps the levels it was given when it opened. The
entry check (a stop inside the bid-ask, an ATR not yet measured) is made
per side, so one side being held never holds the other; the backtest
sizes each trade from its own side.

**The Algo's take-profit is confirmed before it is sent.** Both legs are
read again and the close goes only if the fresh closing price still
reaches the target, and on those prices - one poll's bad print once
"reached" oil's target 0.16 past it and the close filled 0.235 back,
banking $0.10 of a $3.90 target. Held, it is tried again in 5 s while
the Algo still sees the target; the stop stands throughout. The stop
loss, the cutoff, a kill and every manual close are never re-checked
(`RECHECK_TAKE_PROFIT`, on).

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

## The Algo — a signal, not a trader

Per ladder, picked in the ladder's settings (**Algo: Off / Fair spread / Signals /
LIVE**). It is OFF after every restart. In Signals the ladder trades exactly
as before — it adds a reading, not a lock; LIVE takes the ladder over (below).

- **The band.** Candles of the spread `B - beta x A` from the mid (15-minute,
  N = 20 by default). Middle = EMA(N), Pine-style; sigma = population stdev of
  the last N closes; the forming candle counts. Turning the Algo on loads both
  legs' MT5 bars (moved from bid to mid with each bar's own spread, matched in
  UTC on each broker's measured clock), so the band is there at once. If the
  terminals will not give history, candles are built from the live price and
  the window says `candles 7/20`. Closed candles are saved, so a restart keeps
  them.
- **Warm-up.** History fills the band at once, but no entry is taken until the
  Algo has watched `Warm-up (min)` of LIVE prices since it was turned on — 90 by
  default, 0 = off. Time with no price does not count; Signals time does, so a
  ladder can warm up in DRY and go LIVE without waiting again. Turning the Algo
  off starts it over. A restart does not: switched back on within 15 minutes of
  the last live price it watched (an update, a quick restart), it carries on
  where it was; after a longer gap it starts again. The window shows `Live ... warming up 23/90 min`.
- **Staying out of a trend.** A mean-reversion Algo that sells every touch of
  the upper band is run over by a spread climbing along it. Two protections,
  on by default, each switchable per ladder:
  - *Re-entry* — a side is ARMED when its stretch reaches Entry z and enters
    only when the spread comes back inside by `Re-entry back by` (2.0 armed,
    1.5 enters). A spread riding the band never comes back, so it gives no
    entry. The entry window ends `Re-entry window %` of the way back to the
    mean (50: between 1.50 and 0.75); past that the side disarms, so a
    spread armed hours ago is never an "entry" sitting on the mean. 100 is
    the old window, all the way to the mean.
  - *Trend filter* — if the band's middle moved more than `Trend filter (σ)`
    over `Trend lookback (min)` (1σ over 120 min), no entry against it: no
    H to L while it rises, no L to H while it falls. The panel's Trend badge
    shows ↑ / ↓ / –.
- **Backtest.** *Run backtest* in the Algo window replays the last 3–10 days of
  MT5's own 15-minute bars through this ladder's Algo — the same decision
  code, filters, costs, break-even, target and stop — and lists what it would
  have traded, beside the same run without re-entry and the trend filter. It
  sends nothing. It sees one price per candle (its close) and today's bid-ask.
- **Analysis tab** (Trading Monitor → *Analysis*), for trades opened in the
  last day / 7 / 30 days or the whole record, one ladder or all:
  summary (trades, net P&L, win rate); drawdown and each trade's MAE / MFE
  (its worst and best net P&L while open, and when); take / hold calibration
  (where trades peaked, a suggested take near the 65th percentile and a
  max-hold at the median minute winners peaked); a *what-if-held* shadow that
  keeps marking a position for an hour after any exit that was not its target
  (did it come back to break-even? to the target?); the Algo's backtest for
  the chosen ladder; z-score excursions on the Algo's band (touches of ±2σ and
  ±3σ, and 2σ stretches that came back to the mean); and the trade journal
  with the Algo's z at both ends. Built from what is recorded — it sends
  nothing. Best/worst points and shadows are kept from this version on.
- **Direction.** Both (default), H to L only, or L to H only — entries only;
  an open position is always managed to its exit.
- **Entry.** SELL when the z of the bid-side spread is >= +2.5, BUY when the z
  of the offer-side spread is <= -2.5, held for 3 fresh quotes, only on a flat
  ladder. Held back — and the reason shown — by a stale or jumping price, too
  few candles, a 5-minute cooldown after an exit, the last 20 minutes before
  the session cutoff, or a |z| past 3.5.
- **Exit.** For each REAL position, from its own fill: the closing side
  reaching break-even after every cost plus the take-profit % of margin — the
  same TP the Exit panel shows — or falling to the **stop loss**, break-even
  less 2% of margin (on by default, per ladder). Optional and OFF by default: a
  z-stop, back to the mean in profit, and a time stop in candles. No gate ever
  holds an exit.
- **Stop and target sized by ATR** (Algo settings: *Stop sized by*, *Target
  sized by*: % of margin or ATR; *ATR period*, default 14; *Stop x ATR*, 2.0;
  *Target x ATR*, 1.5). ATR is the spread's average true range, close to
  close with Wilder's smoothing, on the Algo's own closed candles - a spread
  candle has no honest high or low, the legs make theirs at different
  moments. Measured from break-even and frozen when the trade opens (saved,
  so a restart keeps it). In ATR mode no entry is taken until the ATR is
  measured (period + 1 closed candles), and in either mode an entry whose
  stop would sit inside the spread's bid-ask - a trade that opens already
  stopped - is refused, with the reason. The Statistics panel shows the
  ATR; the backtest sizes each simulated trade from the ATR at its entry.
- **In position.** The window names it — `in BUY @ 59.11 — TP 59.31 · SL
  58.93` — and draws an SL ◄ entry ► TP bar with the closing price on it.
- **The filters** (from the stat-arb system's algo), every one judged before an
  entry in Signals and LIVE alike — and one that cannot be priced blocks:
  - *Edge*: expected capture (0.5 x |z| x sigma, in money) at least 1.5x the
    round-trip cost — the spread's bid-ask crossed both ways, commission both
    legs both ways, and the slippage budget.
  - *Regime*: no entry while the spread is TRENDING (Kaufman efficiency ratio
    >= 0.6 and <= 4 crossings of its mean).
  - *Half-life band*: off until bounds (minutes) are typed.
  - *Ready*: collecting candles, or a warm-up not yet done, always blocks.
- **The switch** is on each ladder's title bar — ALGO OFF / ALGO DRY / ALGO
  LIVE; click it for the menu. LIVE still asks to confirm.
- **The Algo window** opens while the Algo is on, with the stat-arb dashboard's
  three panels: *Signal & Position* (sell/buy spread tiles with their z and
  entry line, FLAT/LONG/SHORT, and in a position the entry, each leg's fill and
  close-now price, BE/TP/SL and the SL-entry-TP bar), *Statistics* (EMA mean,
  sigma, half-life, regime, candles, band, data progress) and *Filters* (Edge /
  Regime / Ready badges, capture / cost against the requirement, round
  trip, the day's counts, and the last signal blocked and why).
  Closing it turns the Algo off.
- **Record.** Every signal goes to the audit trail; *Algo signals CSV* on the
  Fills tab exports them, with what an exit would have made after costs.
- **Signals or LIVE.** *Algo: Signals* records signals and sends nothing; you
  trade. An entry it would take, and YOUR position ("YOUR SHORT" in its
  window) reaching its target or stop, is told once: a toast that stays until
  dismissed, a chime, the ladder flashing, and on Telegram. ENTRY / BE / TP /
  SL are tagged on the ladder's prices.
  *Algo: LIVE* (confirmed every time) trades: MARKET both legs in, closes by
  ticket, through the same executor a click uses, at its own **Algo qty**
  (spreads; one spread = the ladder's Leg A / Leg B lots — 0.01 for testing).
  One position at a time.
- **Algo or Manual, never both.** While a ladder is LIVE a new manual order on
  it is refused on the engine, in words; CLOSE ALL and the positions list still
  close. LIVE is refused while the ladder holds a manual position or a working
  order. Turning LIVE off with a position asks: close it now, or hand it to
  manual. A restart comes back OFF; the Algo's position is shown as not
  managed until LIVE adopts it again.
- **The day's limits** stop entries (never exits) for the rest of the day: max
  trades (10), losses in a row (3), and a daily loss limit (off until set). A
  refused entry waits out the cooldown; a failed exit is retried every 5s.

## Telegram — the desk from a phone

Optional. With a bot token in `.env` the web process also runs a Telegram
bot, written the way Stat_Arb_W3 writes its own: a heading with the time
(`OPEN POSITIONS  ·  14:02:11 UTC`), then a bold label and its value in a box
for every figure, plain words, no icons and no monospace blocks. `/start` is
the menu, with buttons and the commands (tap one to run it). The same
commands are registered as Telegram's own menu: type `/` and each one is
listed with what it does.

- `/status` (or `/dashboard`): everything to watch on one screen - the system (engine,
  broker time, cutoff, each account), then per ladder its feed, prices, Algo
  mode and state, warm-up, stretch, entry and what is armed, filters, the
  open position with TP / SL / BE, today, and the last blocked signal.
- `/positions`: each open position in full - size and lots per leg,
  notional, margin and leverage, entry and now per leg (with % change),
  entry and current spread with z, the move with or against, net P&L, the
  BE / TP / SL levels and what each is worth, age, slippage. **Close all**
  per ladder.
- `/trades`: the five most recent closed trades in full - exit reason,
  time, duration, Algo or manual, each leg in, the spread in and out with z,
  net P&L and the running total.
- `/pnl`: closed trades, win rate, average win and loss, best and worst,
  today's net, all-time net, max drawdown, and what is open now.
- `/balance`: each account's balance, equity, margin used and free, margin
  level, floating P&L.
- `/settings`: every setting of every ladder with the key `/set` takes;
  `/set <key> <value>` (or `/set <ladder> <key> <value>` with more than one
  ladder) changes one, `/set mode off | dry | live` switches the Algo. The
  **Settings** button does the same by sections: Algo mode, Entry, Filters,
  Exits, Daily limits, Session and costs. A ladder's hours are
  `/set session_open 01:05`, `/set session_close 23:45` and
  `/set break 23:55-01:05` (or `none`), broker time.
- `/alerts` on / off, `/ping`, and **KILL ALL**.

Every message is laid out row for row as Stat_Arb_W3's bot lays it out
- a bold label, the value in code type, blank lines between the same
groups, W3's labels (Orders at / Filled at / Latency, Gross PnL / Est.
Fees / Net PnL, Spread SD, Spread Mean, Half-Life, Regime) and W3's
number styles (`$+3.85`, `-$0.40`, `$4,131.0300`, z to four places) -
with the desk's own rows (the exits it holds, slippage, the analysis)
after W3's, and the desk's words for a side (H to L / L to H). No
monospace block anywhere.

Alerts go to every allowed user, and only for what needs a person:
**TRADE ENTRY** when a position opens (legs, notional, margin, entry spread
and z, TP / SL / BE and what each is worth), **TRADE EXIT** when it closes
(each leg in and out, the spread both ways, gross, commission, net,
slippage, then an ANALYSIS: outcome, peak and trough with when, how much of
the best it kept, hold time, z path), **ORDER FAILED / ORDER REFUSED** with
the broker's words, **SYSTEM ERROR** (the engine stalling, an account
dropping out), **ERROR**, **RECONCILER**, **FEED WARNING / RESTORED** (a
stale or jumping price that lasts 20 s), **DAILY LIMIT**, **ALGO MODE**
when a ladder goes into or out of LIVE (a restart included), and **END OF
DAY** at each ladder's close. Signals - taken or held back - are not sent:
in LIVE the trade is the news, in a dry run nothing happened, and the desk
and the Dashboard keep every one.

Every action asks Confirm / Cancel first; LIVE asks twice. Buttons expire
after ten minutes. It goes through the web app's own routes, so every
check and refusal is the one the screen gets — and it can switch, save
and CLOSE, never open a position.

Setting it up:

1. In Telegram, talk to **@BotFather**, send `/newbot`, and copy the
   token it gives you.
2. Get your numeric user id from **@userinfobot**.
3. In `.env` (never in `config.json`):
   ```
   TELEGRAM_BOT_TOKEN="123456:ABC..."
   TELEGRAM_ALLOWED_USERS="111111111, 222222222"
   ```
4. Restart `start.py`, open your bot in Telegram and send `/start`.

Only the listed ids get an answer; anyone else is ignored and their id
is written to the log once, so a colleague can be added by reading it.
The bot connects OUT to Telegram — no port is opened on the machine.

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

## Logs, and "why did the Algo not trade?"

Every process writes to `logs/` beside `config.json`: `coordinator.log`,
`web.log`, `leg-<account>.log`, `launcher.log`. Each also has a
**`<name>.problems.log`** with warnings and errors only — open that one
first. The screen's own polling is not logged (it was a line three times a
second); button presses and failed requests still are.

For the Algo, the journal answers it directly:

```
python tools/algo_report.py              # the last 24 hours
python tools/algo_report.py --hours 72
```

Per ladder: when it was switched, every signal it acted on and what became
of the order (done, or refused in the refusal's own words), and every
signal it held back with a count per reason — the cutoff, the warm-up, a
filter, an Algo qty the broker cannot trade.

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
