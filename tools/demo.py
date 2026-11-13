"""See the whole terminal working with NO broker attached.

    python tools/demo.py              # then open http://127.0.0.1:8001

Simulated brokers — a fake MT5 account carrying CL, GC and SI, and a
fake Arrow MCX login behind the REAL ArrowSession — are served on real
sockets to the real coordinator and the real web UI, so every screen
(ladders, live charts, Exchanges, Diagnose, the positions monitor) can
be seen and clicked. Prices drift by themselves. Nothing here reaches a
real broker: there is no MetaTrader5 and no pyarrow-client involved.

Everything it writes goes into ./demo-run (config, database, ledger,
logs), never into the real config.json. Ctrl+C stops it.
"""

import json
import os
import random
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, 'tests'))

from conftest import FakeBroker, FakeSymbol                    # noqa: E402
from mt5trader.arrow_broker import ArrowSession                # noqa: E402
from mt5trader.config import TraderConfig                      # noqa: E402
from mt5trader.leg_runner import LegServer                     # noqa: E402
from test_arrow_session import FakeExchange, make_sdk          # noqa: E402

RUN = os.path.join(os.getcwd(), 'demo-run')
WEB_PORT = int(os.environ.get('DEMO_PORT', '8001'))


def epoch(day):
    return int(datetime.strptime(day, '%Y-%m-%d').replace(
        tzinfo=timezone.utc).timestamp())


def main():
    os.makedirs(RUN, exist_ok=True)
    # Placeholders for the fake Arrow login — never real secrets.
    os.environ.update({'ARROW_PASSWORD_MCX': 'demo',
                       'ARROW_API_SECRET_MCX': 'demo',
                       'ARROW_TOTP_SECRET_MCX': 'demo'})
    config = json.load(open(os.path.join(REPO, 'config.example.json')))
    config['fx']['USDINR'] = {'source': 'manual', 'rate': 88.52,
                              'set_at': time.time(), 'max_age_hours': 24}
    # The arbitrage alert, on, for the crude ladder — with a USDINR
    # hedge cost so the alert shows it in its cost.
    crude_key = next(k for k in config['pairs'] if k.startswith('CL'))
    config['pairs'][crude_key].update({
        'arb_alert': True, 'arb_margin': 15.0, 'arb_normal': 0.0,
        'arb_extra_cost': 1.0, 'arb_z': 2.0, 'fx_hedge_cost_per_lot': 40.0})
    config_path = os.path.join(RUN, 'config.json')
    with open(config_path, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=2)
    cfg = TraderConfig.from_file(config_path)

    cme = FakeBroker('cme', [
        FakeSymbol('CLF7', 70.12, 70.14, contract_size=1000.0,
                   volume_min=1.0, volume_step=1.0, tick_size=0.01,
                   expiry=epoch('2026-12-17')),
        FakeSymbol('GCG7', 4012.3, 4012.6, contract_size=100.0,
                   volume_min=1.0, volume_step=1.0, tick_size=0.1,
                   expiry=epoch('2027-01-27')),
        FakeSymbol('SIH7', 48.215, 48.225, contract_size=5000.0,
                   volume_min=1.0, volume_step=1.0, tick_size=0.005,
                   expiry=epoch('2027-03-26')),
    ], login=5000001)
    cme.initialize()

    exchange = FakeExchange()
    exchange.instruments = [
        {'ExchSeg': 'MCXFO', 'TradingSymbol': 'CRUDEOIL18DEC26FUT',
         'Symbol': 'CRUDEOIL', 'Expiry': '18-Dec-2026', 'LotSize': '100',
         'TickSize': '1', 'Token': '1'},
        {'ExchSeg': 'MCXFO', 'TradingSymbol': 'GOLD05FEB27FUT',
         'Symbol': 'GOLD', 'Expiry': '05-Feb-2027', 'LotSize': '1',
         'TickSize': '1', 'Token': '2'},
        {'ExchSeg': 'MCXFO', 'TradingSymbol': 'SILVER05MAR27FUT',
         'Symbol': 'SILVER', 'Expiry': '05-Mar-2027', 'LotSize': '30',
         'TickSize': '1', 'Token': '3'},
    ]
    exchange.book = {'CRUDEOIL18DEC26FUT': (6221.0, 6223.0),
                     'GOLD05FEB27FUT': (121452.0, 121470.0),
                     'SILVER05MAR27FUT': (145810.0, 145842.0)}
    arrow = ArrowSession(cfg.accounts['mcx'], sdk=make_sdk(),
                         ledger_path=os.path.join(RUN, 'data',
                                                  'arrow-mcx.ledger.json'))
    if not arrow.initialize():
        sys.exit(f'demo Arrow leg did not start: {arrow.last_error}')

    for broker, account in ((cme, cfg.accounts['cme']),
                            (arrow, cfg.accounts['mcx'])):
        host, port = account.endpoint.split(':')
        server = LegServer(broker, host, int(port))
        threading.Thread(target=server.serve_forever, daemon=True).start()

    rng = random.Random(7)

    started = time.time()

    def drift():
        dislocated = False
        while True:
            time.sleep(0.5)
            # A DISLOCATION, scripted, so the arbitrage alert can be seen:
            # after 90s MCX crude jumps Rs 45 rich for 30s, then returns.
            age = time.time() - started
            jump = 45.0 if 90 <= age < 120 else 0.0
            if (jump > 0) != dislocated:
                dislocated = jump > 0
                bid, ask = exchange.book['CRUDEOIL18DEC26FUT']
                shift = 45.0 if dislocated else -45.0
                exchange.book['CRUDEOIL18DEC26FUT'] = (bid + shift, ask + shift)
            for sym in cme.symbols.values():
                step = sym.tick_size * rng.choice((-2, -1, 0, 1, 2))
                width = sym.ask - sym.bid
                sym.quote(round(sym.bid + step, 4),
                          round(sym.bid + step + width, 4))
            for name, (bid, ask) in list(exchange.book.items()):
                step = rng.choice((-3, -1, 0, 1, 3)) * \
                    (1 if bid < 10000 else 5)
                exchange.book[name] = (bid + step, ask + step)

    threading.Thread(target=drift, daemon=True).start()

    env = dict(os.environ, PYTHONPATH=REPO)
    children = [
        subprocess.Popen([sys.executable,
                          os.path.join(REPO, 'run_coordinator.py'),
                          '--config', config_path], cwd=RUN, env=env),
        subprocess.Popen([sys.executable, '-m', 'mt5trader.webapp',
                          '--config', config_path, '--port', str(WEB_PORT)],
                         cwd=RUN, env=env),
    ]
    print(f'\n  Demo running — open http://127.0.0.1:{WEB_PORT}\n'
          f'  MCX crude jumps Rs 45 rich after 90s for 30s: watch the '
          f'arbitrage alert.\n'
          f'  Simulated brokers only. Files in {RUN}. Ctrl+C to stop.\n',
          flush=True)
    try:
        while all(child.poll() is None for child in children):
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        for child in children:
            child.terminate()


if __name__ == '__main__':
    main()
