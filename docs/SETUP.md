# Setting up Multi_Exc on a new machine

Leg A runs on an **MT5 terminal** (Windows only — the MetaTrader5 Python
package is Windows-only). Leg B runs on **Arrow**, which accepts API
logins only from the **static IP registered with Arrow** (SEBI rule). The
simplest layout is one Windows machine on that static IP running
everything.

## 1. Install the prerequisites

- Python 3.11 from python.org (tick "Add python.exe to PATH")
- Git for Windows
- The MT5 terminal for your CME broker, logged in, with **Algo Trading**
  switched on in its toolbar

## 2. Get the code

```bash
git clone -b claude/sharp-cannon-9zvff9 https://github.com/ajoxf/Multi_Exc.git
cd Multi_Exc
```

## 3. Install the Python packages

```bash
py -3.11 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
pip install pyarrow-client
```

## 4. Run the tests (required before any live use)

```bash
python -m playwright install chromium
pytest tests/ -q
```

## 5. See it working with simulated brokers first (optional)

```bash
python tools/demo.py
```

Open http://127.0.0.1:8001. Nothing reaches a broker. Ctrl+C stops it.

## 6. Start it for real

```bash
python start.py
```

The first run copies `config.example.json` to `config.json` (the crude,
gold and silver pairs) and `.env.example` to `.env`, starts the UI, and
opens the browser at http://127.0.0.1:8000. Then, on the **Exchanges**
page:

1. **Accounts** — fill in `cme` (kind MT5: terminal path, login, server,
   password) and `mcx` (kind Arrow (MCX): app id, user id, password, API
   secret, TOTP **seed**). Secrets go to `.env`, never `config.json`.
   Press **Save**; the launcher restarts the engine by itself.
2. **Exchange rates** — type today's USDINR and press **Set**.
3. **Pairs** — for each pair, set this month's symbols (Find searches
   each account), press **Read both legs**, check the units, the unit
   factor, the contract sizes and the expiries, then **Save pair**.
4. Press **Diagnose** on both accounts. Everything must be PASS before
   the first trade.
5. Place a **one-lot** test on each MCX symbol before trading size.

## Arbitrage alerts

The terminal can tell you when a gap is unusually wide — it never trades
it for you. Per ladder, open ⚙ and fill in **Arbitrage alert**:

| Field | Crude (MCX vs NYMEX) | Gold / silver |
|---|---|---|
| Arb alert | ticked | ticked |
| Normal gap | 0 | 0 |
| Normal gap % | 0 | the usual MCX premium, e.g. 6 |
| Edge needed | e.g. 20 (Rs per bbl, after costs) | e.g. 300 (Rs per 10 g) / 400 (Rs per kg) |
| Other cost/unit | CTT and fees per unit, e.g. 1 | likewise |
| Sigmas | 2 (0 = off) | 2 |
| USDINR hedge/lot | your round-trip cost per NSE USDINR lot, in Rs | same |
| USDINR lot $ | blank (= $1,000) | blank |

Commission per lot (also on ⚙) and the exit crossing are counted
automatically. So is the USDINR hedge: the dollar leg's notional is
turned into NSE USDINR lots (1 CL at $88.40 = $88,400 = 88 lots) and
multiplied by the cost per lot you type. With no cost typed the alert
still fires but says the hedge is NOT costed. The alert also says which
way to hedge: MCX rich (sell MCX / buy CME) → buy USDINR; MCX cheap →
sell USDINR. An alert shows on the ladder, as a banner and a sound, in
the log, and — if set up — on Telegram.

To get alerts (and the desk's Telegram remote) on your phone:

1. In Telegram, message **@BotFather**, send `/newbot`, and copy the token.
2. Message **@userinfobot** to get your numeric Telegram user id.
3. Put both in `.env` (never in config.json). Only the ids listed get
   an answer:

```bash
TELEGRAM_BOT_TOKEN="123456:ABC..."
TELEGRAM_ALLOWED_USERS="123456789"
```

4. Restart `start.py` and send the bot a message.

## Algo — Signals or LIVE, per ladder (as in Mirror-MT5-Trader)

Each ladder's title bar has an **ALGO** switch:

- **Off** — manual: you click, as always.
- **Signals** — the Algo works out entries and exits from the spread's
  z-score and tells you (screen, sound, Telegram). It sends nothing;
  you trade.
- **LIVE** — the Algo trades that ladder by its rules: MARKET both legs
  in, closes by ticket. Confirmed every time it is switched on, OFF
  after every restart, refused on a ladder with a manual position or
  working order, and while it is LIVE the ladder takes no manual
  orders (closes still work).

Its settings (entry z, candle timeframe, filters, stops) are on the
ladder's ⚙. Run it in **Signals** first, and LIVE only after
`pytest tests/ -q` passes.

On these pairs the Algo's price history comes from MT5 for leg A
(restated in rupees at today's USDINR) but **not yet from Arrow** for
leg B: until Arrow's candle endpoint has been confirmed on a live
login, the Algo builds its candles from the live price after it is
switched on, and says so ("collecting from the live price").

## Running the pieces separately (while setting up)

```bash
python run_leg.py --config config.json --account cme
```

```bash
python run_leg.py --config config.json --account mcx
```

```bash
python run_coordinator.py --config config.json
```

```bash
python -m mt5trader.webapp --config config.json
```

## Every day

```bash
cd Multi_Exc
.venv\Scripts\activate
python start.py
```

Arrow's session token lasts about a day, so start fresh each morning,
and re-enter USDINR — a typed rate older than 24 hours withholds new
orders (never a close) until it is updated.

## Updating the code

```bash
git pull
pytest tests/ -q
```
