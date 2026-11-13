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
