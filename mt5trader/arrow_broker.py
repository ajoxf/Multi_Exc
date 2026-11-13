"""Arrow session: the only module that touches the pyarrow-client SDK.

Leg B of a cross-exchange pair lives on MCX through Arrow, and this class
gives the rest of the system EXACTLY the methods `broker.BrokerSession`
gives it for an MT5 account — same names, same return shapes, same
conventions — so `LocalLeg`, the leg runner, the coordinator, the
executor, the quoter and the reconciler run unchanged on top of it.

What Arrow is NOT, and how each difference is absorbed here:

* NETTING, not hedging. An exchange holds one net position per symbol.
  The engine needs per-order TICKETS (closes target tickets; a hedging
  book can hold a long and a short spread at once). So every order this
  system sends is written to a LEDGER on disk before it is sent and
  updated after, each opening fill becomes a ticket (its order id), and
  a close by ticket is an opposite order for that ticket's volume —
  which, on a netting account, is precisely a close. `positions_by_magic`
  reports the ledger's tickets, CROSS-CHECKED against the broker's own
  net position: a ledger volume the broker does not hold is withheld so
  the reconciler sees a ghost rather than a position that is not there.

* NO MAGIC NUMBER. Ownership is the ledger. Anything at the broker the
  ledger cannot explain is not ours and is never reported as ours —
  which is the same rule MT5's magic gave: never touch what you cannot
  explain. It is surfaced by Diagnose as a netting mismatch instead.

* NO BROKER CLOCK IN THE FEED. Indian exchanges run on IST, so the
  broker's clock is UTC+05:30 and the offset is a fact, not a
  measurement.

* LOTS ARE UNITS. Arrow's `quantity` is units (lots x lot size). The
  engine speaks lots everywhere; this class multiplies at the wire.

* MARKET ORDERS need `mpp=True` and `price=0` — plain MKT is disabled on
  Arrow (learned on the live API in arrow-statarb).

THE ARROW SDK HAS HISTORICALLY NOT LISTED MCX. arrow-statarb refused MCX
because the SDK build it was written against had no MCX exchange. This
session asks the installed SDK for one and REFUSES TO START, in words,
if it is not there — a leg that cannot trade MCX must not pretend to.
Field names in Arrow's responses vary by build, so every read is
case-insensitive and tolerant, and anything that cannot be read is
UNKNOWN (None), never zero.
"""

import inspect
import logging
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from . import atomicfile
from .broker import OrderResult
from .contracts import parse_expiry, spec_for
from .models import OrderSide

#: IST is UTC+05:30 all year (India has no daylight saving).
IST_OFFSET_SEC = 19800

#: How long a market order is watched for its fill before the call
#: returns. MCX market orders fill in milliseconds; anything still
#: working after this is reported as working, never as filled.
MARKET_FILL_WAIT_SEC = 3.0
POLL_STEP_SEC = 0.15
#: Quotes are fetched for every subscribed symbol in ONE call, at most
#: this often, however many legs and pairs ask.
QUOTE_TTL_SEC = 0.25

_STATUS = {
    'COMPLETE': 'filled', 'COMPLETED': 'filled', 'FILLED': 'filled',
    'EXECUTED': 'filled', 'TRADED': 'filled',
    'REJECTED': 'rejected', 'REJECT': 'rejected',
    'CANCELLED': 'cancelled', 'CANCELED': 'cancelled',
    'EXPIRED': 'cancelled', 'LAPSED': 'cancelled',
    'PARTIALLY FILLED': 'partial', 'PARTIAL': 'partial',
    'OPEN': 'open', 'PENDING': 'open', 'VALIDATION PENDING': 'open',
    'PUT ORDER REQ RECEIVED': 'open', 'MODIFY VALIDATION PENDING': 'open',
    'TRIGGER PENDING': 'open', 'OPEN PENDING': 'open',
    'MODIFIED': 'open', 'NEW': 'open',
}
_WORKING = ('open', 'partial', 'sending')
_DONE = ('filled', 'rejected', 'cancelled')


def _dig(d, *keys, default=None):
    """Case-insensitive first-present lookup. Arrow answers in TitleCase
    on some endpoints and camelCase on others."""
    if not isinstance(d, dict):
        return default
    low = {str(k).lower().replace('_', ''): v for k, v in d.items()}
    for k in keys:
        v = low.get(k.lower().replace('_', ''))
        if v not in (None, ''):
            return v
    return default


def _num(value, default=None):
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out


def _load_sdk():
    try:
        import pyarrow_client                     # noqa: F401
        return pyarrow_client
    except ImportError:
        return None


class ArrowSession:
    """One Arrow login for one account, behind BrokerSession's methods."""

    def __init__(self, account, sdk=None, ledger_path=None,
                 clock=time.time, sleep=time.sleep):
        self.account = account
        self.sdk = sdk if sdk is not None else _load_sdk()
        self.clock = clock
        self.sleep = sleep
        self.connected = False
        self.client = None
        self.last_error = None
        self.last_visible = {}
        self.exchange_name = (getattr(account, 'exchange', None) or 'MCX').upper()
        self.product = (getattr(account, 'product', None) or 'NRML').upper()
        #: Arrow REST prices are rupees; set to 100 if a build answers
        #: in paise (the WebSocket feed does).
        self.price_divisor = float(getattr(account, 'price_divisor', None) or 1.0)
        #: Lots this leg will send in one order, at most. The engine
        #: refuses a click over it before either leg moves.
        self.max_lots = float(getattr(account, 'max_lots', None) or 50.0)
        safe = re.sub(r'[^A-Za-z0-9_.-]', '_', str(account.name))
        self.ledger_path = ledger_path or os.path.join(
            getattr(account, 'data_dir', None) or 'data',
            f'arrow-{safe}.ledger.json')
        self._lock = threading.RLock()
        self._instruments = {}           # TradingSymbol upper -> record
        self._quotes = {}                # symbol -> parsed quote
        self._quoted_at = 0.0
        self._subscribed = set()
        self._exchange_enum = None
        self.netting = {}                # symbol -> last cross-check
        self.ledger = {'orders': {}, 'positions': {}}

    # --- connection ------------------------------------------------------

    def initialize(self):
        acct = self.account
        if self.sdk is None:
            self.last_error = ('pyarrow-client is not installed — run: '
                               'pip install pyarrow-client')
            logging.error("[%s] %s", acct.name, self.last_error)
            return False
        # The SDK (1.8) lists MCX AND MCXFO, and says MCXFO is the one for
        # orders, quotes and margin — MCX is for permission checks and
        # the instrument download. So the futures segment wins.
        exchange = (getattr(self.sdk.Exchange, 'MCXFO', None)
                    if self.exchange_name == 'MCX' else None)
        if exchange is None:
            exchange = getattr(self.sdk.Exchange, self.exchange_name, None)
        if exchange is None:
            try:
                exchange = self.sdk.Exchange(self.exchange_name)
            except (ValueError, KeyError, TypeError):
                exchange = None
        if exchange is None:
            self.last_error = (
                f"the installed pyarrow-client has no {self.exchange_name} "
                f"exchange (it lists "
                f"{', '.join(e.name for e in self.sdk.Exchange)}). Arrow's SDK "
                f"did not support MCX in the build arrow-statarb was written "
                f"against — upgrade pyarrow-client or ask Arrow to enable "
                f"{self.exchange_name} on this account")
            logging.error("[%s] refusing to start: %s", acct.name,
                          self.last_error)
            return False
        self._exchange_enum = exchange

        app_id = getattr(acct, 'app_id', None)
        user_id = getattr(acct, 'user_id', None)
        password = getattr(acct, 'password', None)
        api_secret = getattr(acct, 'api_secret', None)
        totp = getattr(acct, 'totp_secret', None)
        missing = [n for n, v in (('app id', app_id), ('user id', user_id),
                                  ('password', password),
                                  ('API secret', api_secret),
                                  ('TOTP secret', totp)) if not v]
        if missing:
            self.last_error = (f"no {', '.join(missing)} for this Arrow "
                               f"account — enter them on the Exchanges page "
                               f"(secrets go to .env)")
            logging.error("[%s] refusing to run: %s", acct.name,
                          self.last_error)
            return False
        try:
            client = self.sdk.ArrowClient(app_id=app_id)
            # `api_secret`, NOT `app_secret`: the SDK docs are wrong, the
            # installed SDK is not (arrow-statarb fact 1). TOTP is the
            # base32 SEED, not a six-digit code.
            client.auto_login(user_id=user_id, password=password,
                              api_secret=api_secret, totp_secret=totp)
        except Exception as e:
            self.last_error = _sdk_error(e)
            logging.error("[%s] Arrow login failed: %s", acct.name,
                          self.last_error)
            logging.error("    fix: the host's static IP must be registered "
                          "with Arrow (SEBI rule), and the TOTP secret is "
                          "the base32 seed")
            return False
        self.client = client
        self.connected = True
        self._load_instruments()
        self._load_ledger()
        self._recover_ledger()
        logging.info("Connected [%s] to Arrow as %s (%s, %d instruments)",
                     acct.name, user_id, self.exchange_name,
                     len(self._instruments))
        return True

    def shutdown(self):
        if self.client is not None:
            try:
                self.client.invalidate_session()
            except Exception:
                pass
        self.client = None
        self.connected = False

    def is_alive(self):
        return bool(self.connected and self.client is not None)

    # --- the instrument master --------------------------------------------

    def _load_instruments(self):
        try:
            records = _parse_instruments(self.client.get_instruments())
        except Exception as e:
            logging.error("[%s] instrument master failed: %s",
                          self.account.name, _sdk_error(e))
            records = []
        found = {}
        for rec in records:
            if not isinstance(rec, dict):
                continue
            seg = str(_dig(rec, 'ExchSeg', 'exch_seg', 'Exchange',
                           default='')).upper()
            if self.exchange_name not in seg:
                continue
            tsym = str(_dig(rec, 'TradingSymbol', 'trading_symbol', 'tsym',
                            default='')).strip()
            if tsym:
                found[tsym.upper()] = rec
        self._instruments = found

    def _instrument(self, symbol):
        return self._instruments.get(str(symbol or '').upper())

    def _lot_size(self, symbol):
        rec = self._instrument(symbol)
        lot = _num(_dig(rec, 'LotSize', 'lot_size', 'lotqty', 'BoardLotQuantity'))
        return lot if lot and lot > 0 else None

    def _units_per_lot(self, symbol):
        """What the exchange wants in `quantity` for ONE lot."""
        if getattr(self.account, 'quantity_in_lots', False):
            return 1.0
        return self._lot_size(symbol)

    def _tick_size(self, symbol):
        rec = self._instrument(symbol)
        tick = _num(_dig(rec, 'TickSize', 'tick_size', 'ticksize'))
        if tick and tick > 0:
            # Some masters publish the tick in paise.
            spec = spec_for(symbol, 'MCX')
            if spec and tick >= 100 * spec.tick:
                tick /= 100.0
            return tick
        spec = spec_for(symbol, 'MCX')
        return spec.tick if spec else 1.0

    def contract_size(self, symbol):
        """(rupees per 1.00 of price per lot, note).

        The master's lot size is the QUANTITY the exchange counts, which
        is the contract size only when the price is quoted per one of
        those units (crude: 100 bbl, priced per bbl). MCX GOLD is priced
        per 10 g on a 1 kg lot, so its contract size is 100 whatever the
        master's LotSize says. The exchange spec decides where it knows
        the contract; the disagreement is reported, never hidden.
        """
        rec = self._instrument(symbol)
        lot = self._lot_size(symbol)
        mult = _num(_dig(rec, 'Multiplier', 'PriceMultiplier', 'mult'))
        spec = spec_for(symbol, 'MCX')
        from_master = (lot * mult) if (lot and mult) else lot
        if spec is not None and spec.contract_size:
            note = None
            if from_master and abs(from_master - spec.contract_size) > 1e-9:
                note = (f"Arrow's master gives {symbol} a lot of "
                        f"{from_master:g}; the {spec.description} spec is "
                        f"{spec.lot_quantity:g} {spec.lot_unit} priced per "
                        f"{spec.quote_unit} = {spec.contract_size:g}. Using "
                        f"the spec — check it against MCX's circular")
            return spec.contract_size, note
        return from_master, None

    # --- reads -------------------------------------------------------------

    def account_info(self):
        if not self.is_alive():
            return None
        raw = {}
        for meth in ('get_user_limits', 'get_limits', 'get_funds'):
            fn = getattr(self.client, meth, None)
            if fn is None:
                continue
            try:
                raw = fn() or {}
                break
            except Exception as e:
                logging.debug("Arrow %s failed: %s", meth, e)
        if isinstance(raw, list):
            raw = raw[0] if raw else {}

        def f(*keys):
            return _num(_dig(raw, *keys))

        available = f('availableMargin', 'availablecash', 'cashAvailable',
                      'marginAvailable', 'availableBalance', 'available', 'net')
        used = f('marginUsed', 'usedMargin', 'utilizedMargin',
                 'marginUtilized', 'utilisedMargin', 'span')
        balance = f('cashBalance', 'openingBalance', 'balance', 'cash', 'payin')
        equity = f('equity', 'netWorth', 'collateral')
        if equity is None and available is not None:
            equity = available + (used or 0.0)
        profit = None
        rows = self._broker_positions()
        if rows is not None:
            profit = sum((_num(_dig(p, 'pnl', 'unrealizedPnl', 'mtm',
                                    'netPnl', 'urmtom'), 0.0) or 0.0)
                         for p in rows)
        return SimpleNamespace(
            login=getattr(self.account, 'user_id', None),
            server=f'Arrow {self.exchange_name}',
            name=_dig(raw, 'name', 'clientName', default=''),
            currency='INR', leverage=None,
            balance=balance, credit=0.0, equity=equity,
            margin=used, margin_free=available,
            margin_level=(100.0 * equity / used if equity and used else None),
            margin_so_call=None, margin_so_so=None, profit=profit)

    def symbol_info(self, symbol):
        rec = self._instrument(symbol)
        if rec is None:
            return None
        tick = self._tick_size(symbol)
        size, _note = self.contract_size(symbol)
        freeze = _num(_dig(rec, 'FreezeQty', 'freeze_qty', 'MaxOrderQty'))
        units = self._units_per_lot(symbol)
        max_lots = self.max_lots
        if freeze and units:
            max_lots = min(max_lots, max(1.0, (freeze // units)))
        return SimpleNamespace(
            name=str(_dig(rec, 'TradingSymbol', default=symbol)),
            visible=True, volume_min=1.0, volume_step=1.0,
            volume_max=float(max_lots), point=tick, trade_tick_size=tick,
            trade_contract_size=size, digits=_digits(tick),
            expiration_time=int(parse_expiry(_dig(rec, 'Expiry', 'expiry_date'))
                                or 0))

    def ensure_symbol(self, symbol):
        info = self.symbol_info(symbol)
        if info is not None and info.name not in self._subscribed:
            self._subscribed.add(info.name)
            # A NEW symbol is quoted now, not at the next batch: waiting
            # out the batch made a just-added leg read as dark.
            self._quoted_at = 0.0
        return info

    def resubscribe(self, symbol):
        self._quoted_at = 0.0
        return self.symbol_tick(symbol)

    def _refresh_quotes(self, force=False):
        now = time.monotonic()
        if not force and now - self._quoted_at < QUOTE_TTL_SEC:
            return
        if not self._subscribed or not self.is_alive():
            return
        mode = None
        for name in ('FULL', 'QUOTE', 'DEPTH', 'MARKET_DEPTH', 'OHLCV'):
            mode = getattr(self.sdk.QuoteMode, name, None)
            if mode is not None:
                break
        if mode is None:
            # LTP alone is not a book. The spread is built from the MID
            # OF THE BOOK, never the last trade, so a feed with no bid and
            # ask is no price at all.
            self.last_error = ("this pyarrow-client offers only LTP quotes — "
                               "the spread needs a bid and an ask")
            self._quotes = {}
            self._quoted_at = now
            return
        pairs = [(sym, self._exchange_enum) for sym in sorted(self._subscribed)]
        try:
            reply = self.client.get_quotes(mode, pairs)
        except Exception as e:
            self.last_error = _sdk_error(e)
            logging.debug("Arrow quotes failed: %s", self.last_error)
            self._quotes = {}
            self._quoted_at = now
            return
        quotes = {}
        items = reply if isinstance(reply, list) else (
            reply.get('data') if isinstance(reply, dict)
            and isinstance(reply.get('data'), list) else
            [dict(v, tradingSymbol=k) for k, v in reply.items()
             if isinstance(v, dict)] if isinstance(reply, dict) else [])
        for item in items or ():
            sym = _dig(item, 'tradingSymbol', 'TradingSymbol', 'symbol', 'tsym')
            if sym:
                quotes[str(sym).upper()] = self._parse_quote(item)
        self._quotes = quotes
        self._quoted_at = now

    def _parse_quote(self, item):
        div = self.price_divisor
        depth = _dig(item, 'depth', 'marketDepth', 'Depth') or {}
        buys = _dig(depth, 'buy', 'bids', 'bid') or []
        sells = _dig(depth, 'sell', 'asks', 'ask') or []

        def first(rows):
            if isinstance(rows, list) and rows and isinstance(rows[0], dict):
                return _num(_dig(rows[0], 'price', 'p'))
            return None

        bid = _num(_dig(item, 'bestBidPrice', 'bidPrice', 'bestBuyPrice',
                        'bp', 'bp1', 'bid')) or first(buys)
        ask = _num(_dig(item, 'bestAskPrice', 'askPrice', 'bestSellPrice',
                        'sp', 'sp1', 'ask', 'offerPrice')) or first(sells)
        ltp = _num(_dig(item, 'ltp', 'lastPrice', 'lastTradedPrice', 'Ltp'))
        stamp = _dig(item, 'exchangeTimestamp', 'exchTime', 'lastTradeTime',
                     'ltt', 'timestamp', 'feedTime')
        stamp = parse_expiry(stamp) if stamp not in (None, '') else None

        def scaled(v):
            return None if v is None else v / div

        book = []
        for side, rows in (('bid', buys), ('ask', sells)):
            for row in rows if isinstance(rows, list) else ():
                price = _num(_dig(row, 'price', 'p'))
                qty = _num(_dig(row, 'quantity', 'qty', 'q', 'volume'))
                if price:
                    book.append({'type': side, 'price': price / div,
                                 'volume': qty or 0.0})
        return {'bid': scaled(bid), 'ask': scaled(ask), 'last': scaled(ltp),
                'time': stamp, 'depth': book,
                'open': scaled(_num(_dig(item, 'open', 'openPrice'))),
                'high': scaled(_num(_dig(item, 'high', 'highPrice'))),
                'low': scaled(_num(_dig(item, 'low', 'lowPrice'))),
                'volume': _num(_dig(item, 'volume', 'totalTradedVolume',
                                    'vtt'))}

    def _quote(self, symbol):
        info = self.ensure_symbol(symbol)
        if info is None:
            return None
        self._refresh_quotes()
        return self._quotes.get(info.name.upper())

    def symbol_tick(self, symbol):
        q = self._quote(symbol)
        if not q or not q.get('bid') or not q.get('ask'):
            # One-sided or empty: no mid, so no price. Not a zero.
            return None
        self.last_visible[symbol] = True
        stamp = q.get('time')
        # The EXCHANGE's stamp where Arrow gives one, else none at all:
        # stamping a quote with OUR clock would make every poll look
        # like a fresh quote, and the staleness guard would never fire.
        return SimpleNamespace(bid=q['bid'], ask=q['ask'], last=q.get('last'),
                               time=int(stamp) if stamp else 0,
                               time_msc=int(stamp * 1000) if stamp else None)

    def rates(self, symbol, timeframe_min, count):
        """History bars for the Algo's warm-up and the backtest, from
        Arrow's historical API — oldest first, in the shape MT5's are.

            client.candle_data(exchange, token, interval, from, to)
            rows: [timestamp (ISO, +0530), open, high, low, close, volume]

        THREE THINGS ARE NOT LIKE MT5, and each is handled where a wrong
        answer would build a band the Algo trades on:

        * PRICES IN PAISE. Divided by `candle_price_divisor` (100) — and
          the newest bar is CHECKED against the live book: off by more
          than 10%, it is not history, it is a scale nobody confirmed,
          and the answer is None.
        * THE CLOSE IS A TRADE, not a bid. There is no book in history,
          so it stands as the bar's mid (spread 0). The live spread is
          still built from the mid of the book; this only warms the band.
        * TIME is ISO with its offset; it is returned as the BROKER's
          wall clock encoded as an epoch (UTC + 05:30), the convention
          MT5 bars use, so the caller places both legs' bars alike.

        None — never [] — for anything that is not usable history: an
        interval Arrow has no name for, no token, a refused range, an
        empty answer, a scale that fails the check. The Algo then says
        "collecting from the live price", as it does for a dark MT5.
        """
        interval = CANDLE_INTERVALS.get(int(timeframe_min))
        fetch = getattr(self.client, 'candle_data', None) if self.client else None
        rec = self._instrument(symbol)
        token = _dig(rec, 'Token', 'token', 'instrument_token')
        if interval is None or fetch is None or token in (None, ''):
            return None
        # Enough calendar time for `count` bars on a market that trades
        # ~14.5 hours a weekday, plus a margin for holidays.
        trading_min_per_day = 14.5 * 60 * 5 / 7.0
        days = (int(count) + 2) * int(timeframe_min) / trading_min_per_day
        days = min(max(days * 1.3 + 2, 2), CANDLE_MAX_DAYS)
        now_ist = datetime.fromtimestamp(self.clock() + IST_OFFSET_SEC,
                                         tz=timezone.utc).replace(tzinfo=None)
        start = now_ist - timedelta(days=days)
        rows = None
        for exchange in self._candle_exchanges():
            try:
                reply = fetch(exchange, str(token), interval,
                              start.strftime('%Y-%m-%dT%H:%M:%S'),
                              now_ist.strftime('%Y-%m-%dT%H:%M:%S'))
            except Exception as e:
                logging.warning("%s: Arrow candle history refused on %s — %s",
                                symbol, getattr(exchange, 'value', exchange),
                                _sdk_error(e))
                continue
            rows = _candle_rows(reply)
            if rows:
                break
        if not rows:
            return None
        divisor = float(getattr(self.account, 'candle_price_divisor', None)
                        or CANDLE_PRICE_DIVISOR)
        bars = []
        for row in rows:
            stamp = _iso_epoch(row[0])
            close = _num(row[4])
            if stamp is None or close is None:
                continue
            bars.append({'time': int(stamp + IST_OFFSET_SEC),
                         'close': close / divisor, 'spread': 0,
                         'point': self._tick_size(symbol) or 0.0})
        bars.sort(key=lambda bar: bar['time'])
        if not bars:
            return None
        # THE SCALE CHECK: the newest close against the live book.
        live = self.symbol_tick(symbol)
        if live is not None:
            mid = (live.bid + live.ask) / 2.0
            if mid and abs(bars[-1]['close'] / mid - 1.0) > \
                    CANDLE_SCALE_TOLERANCE:
                logging.error(
                    "%s: Arrow's history ends at %g against a live mid of %g "
                    "— not the same scale (paise vs rupees?), so it is not "
                    "used; set candle_price_divisor on the account if Arrow "
                    "answers in rupees", symbol, bars[-1]['close'], mid)
                return None
        return bars[-int(count):]

    def _candle_exchanges(self):
        """MCXFO first (what the SDK asks for on a future), then MCX."""
        out = []
        for name in (self.exchange_name + 'FO', self.exchange_name):
            found = getattr(self.sdk.Exchange, name, None) if self.sdk else None
            if found is not None and found not in out:
                out.append(found)
        return out

    def depth(self, symbol):
        q = self._quote(symbol)
        return (q or {}).get('depth') or None

    def session_stats(self, symbol):
        q = self._quote(symbol)
        if q is None:
            return None
        return {'symbol': symbol, 'open': q.get('open') or None,
                'high': q.get('high') or None, 'low': q.get('low') or None,
                'volume': q.get('volume') or None}

    def find_symbols(self, pattern, limit=40):
        needle = (pattern or '').strip().upper()
        found = []
        rows = sorted(self._instruments.items(),
                      key=lambda kv: (parse_expiry(_dig(kv[1], 'Expiry')) or 9e12,
                                      kv[0]))
        for name, rec in rows:
            under = str(_dig(rec, 'Symbol', 'Underlying', default='')).upper()
            if needle and needle not in name and needle not in under:
                continue
            opt = str(_dig(rec, 'OptionType', default='')).upper()
            if opt in ('CE', 'PE'):
                continue                        # futures only
            info = self.symbol_info(name)
            found.append({
                'symbol': info.name, 'description': under,
                'path': f'Arrow\\{self.exchange_name}', 'visible': True,
                'contract_size': info.trade_contract_size,
                'volume_min': info.volume_min, 'volume_max': info.volume_max,
                'volume_step': info.volume_step, 'currency': 'INR',
                'expiry': info.expiration_time,
                'lot_size': self._lot_size(name)})
            if len(found) >= limit:
                break
        return found

    def margin_for(self, symbol, side, volume, price=None):
        if not self.is_alive():
            return None
        units = self._units_per_lot(symbol)
        if not units:
            return None
        for meth in ('margin_calculator', 'get_order_margin', 'order_margin',
                     'get_margin'):
            fn = getattr(self.client, meth, None)
            if fn is None:
                continue
            try:
                reply = fn(exchange=self._exchange_enum, symbol=symbol,
                           quantity=int(round(volume * units)),
                           transaction_type=self._side_enum(side),
                           price=float(price or 0.0))
            except Exception as e:
                logging.debug("Arrow %s failed: %s", meth, e)
                continue
            value = _num(_dig(reply if isinstance(reply, dict) else {},
                              'totalMargin', 'total', 'requiredMargin',
                              'margin', 'span'))
            if value is not None:
                return value
        return None        # unknown, rendered as an em dash

    def symbol_report(self, symbol):
        info = self.ensure_symbol(symbol)
        if info is None:
            return {'symbol': symbol, 'found': False,
                    'error': (f'{symbol} is not in Arrow\'s '
                              f'{self.exchange_name} instrument master')}
        q = self._quote(symbol) or {}
        size, note = self.contract_size(symbol)
        spec = spec_for(symbol, 'MCX')
        return {
            'symbol': info.name, 'found': True,
            'description': (spec.description if spec else
                            str(_dig(self._instrument(symbol), 'Symbol',
                                     default=''))),
            'visible': True, 'bid': q.get('bid'), 'ask': q.get('ask'),
            'tick_time': int(q['time']) if q.get('time') else None,
            'digits': info.digits, 'point': info.point,
            'tick_size': info.trade_tick_size, 'contract_size': size,
            'contract_size_note': note,
            'lot_size': self._lot_size(symbol),
            'units_per_lot': self._units_per_lot(symbol),
            'tick_value': (size * info.trade_tick_size if size else None),
            'volume_min': info.volume_min, 'volume_max': info.volume_max,
            'volume_step': info.volume_step, 'currency': 'INR',
            'quote_unit': spec.quote_unit if spec else None,
            'filling_mode': None, 'trade_mode': None, 'trade_allowed': True,
            'expiry': info.expiration_time,
            'depth_levels': len(q.get('depth') or ()),
            'swap_long': None, 'swap_short': None, 'swap_mode': None,
            'swap_rollover3days': None,
            'exchange': self.exchange_name, 'broker': 'arrow',
        }

    def terminal_report(self):
        report = {'library': self.sdk is not None, 'terminal': self.is_alive(),
                  'broker': 'arrow', 'exchange': self.exchange_name}
        if self.sdk is None:
            report['error'] = 'pyarrow-client is not installed'
            return report
        if not self.is_alive():
            report['error'] = self.last_error or 'not logged in to Arrow'
            return report
        info = self.account_info()
        mismatches = {s: n for s, n in self.netting.items()
                      if n.get('mismatch')}
        report.update({
            'terminal_name': 'Arrow', 'terminal_path': '',
            'terminal_connected': True,
            # There is no "Algo Trading" switch on Arrow; API trading is
            # enabled per app and per static IP, and a login proves both.
            'algo_trading': True, 'ping_ms': None,
            'logged_in': True, 'login': getattr(info, 'login', None),
            'server': getattr(info, 'server', ''),
            'name': getattr(info, 'name', ''), 'currency': 'INR',
            'leverage': None, 'balance': getattr(info, 'balance', None),
            'credit': 0.0, 'equity': getattr(info, 'equity', None),
            'margin_free': getattr(info, 'margin_free', None),
            'trade_allowed': True, 'trade_expert': True,
            'margin_mode': 'netting',
            # A NETTING account, honestly reported — and handled: the
            # ledger gives every order its own ticket, so the hedging
            # requirement is met above the exchange, not at it.
            'hedging': False, 'netting_ledger': True,
            'netting_mismatches': mismatches,
            'instruments': len(self._instruments),
        })
        return report

    def server_time_offset_sec(self):
        return IST_OFFSET_SEC

    def account_is_hedging(self):
        return False

    # --- the ledger -------------------------------------------------------

    def _load_ledger(self):
        data = atomicfile.read_json(self.ledger_path, default=None)
        if isinstance(data, dict):
            self.ledger = {'orders': dict(data.get('orders') or {}),
                           'positions': dict(data.get('positions') or {})}

    def _save_ledger(self):
        folder = os.path.dirname(self.ledger_path)
        if folder:
            os.makedirs(folder, exist_ok=True)
        if not atomicfile.write_json(self.ledger_path, self.ledger):
            logging.critical("[%s] the Arrow ledger could not be written to "
                             "%s — positions opened now cannot be recovered "
                             "after a restart", self.account.name,
                             self.ledger_path)

    def _recover_ledger(self):
        """Settle every order the ledger left unfinished.

        An order written as `sending` and never given an id is one that
        may or may not have reached the exchange: the process died in
        between. It is NOT assumed either way — it is kept, flagged, and
        named by Diagnose, so a person checks Arrow's order book.
        """
        for oid, order in list(self.ledger['orders'].items()):
            if order.get('state') == 'sending':
                order['state'] = 'unknown'
                logging.critical(
                    "[%s] order %s (%s %s %s) was being SENT when the last "
                    "process stopped — it may be at the exchange. Check "
                    "Arrow's order book; this system will not guess.",
                    self.account.name, oid, order.get('side'),
                    order.get('lots'), order.get('symbol'))
            elif order.get('state') in ('open', 'partial'):
                self._refresh_order(oid)
        self._save_ledger()

    def _position(self, ticket):
        return self.ledger['positions'].get(str(ticket))

    def _credit_fill(self, oid, order, filled_lots, price, stamp):
        """Book the NEW part of a fill exactly once — idempotent, because
        the same fill is read on every poll until the order is done."""
        before = float(order.get('booked_lots') or 0.0)
        delta = round(filled_lots - before, 9)
        if delta <= 1e-9:
            return
        order['booked_lots'] = filled_lots
        closes = order.get('closes')
        if closes:
            pos = self._position(closes)
            if pos is not None:
                pos['volume'] = max(0.0, round(pos['volume'] - delta, 9))
                if pos['volume'] <= 1e-9:
                    pos['closed_at'] = stamp
            return
        pos = self.ledger['positions'].setdefault(str(oid), {
            'ticket': str(oid), 'symbol': order['symbol'],
            'side': order['side'], 'volume': 0.0, 'price_open': price,
            'opened_at': stamp, 'comment': order.get('comment', '')})
        pos['volume'] = round(pos['volume'] + delta, 9)
        pos['price_open'] = price if price else pos.get('price_open')

    # --- the exchange's view of an order ---------------------------------

    def _order_record(self, oid):
        """(record, readable) for one order id from Arrow."""
        if not self.is_alive():
            return None, False
        for meth in ('get_order_status', 'get_order_history',
                     'single_order_history', 'order_history'):
            fn = getattr(self.client, meth, None)
            if fn is None:
                continue
            try:
                raw = fn(oid)
            except Exception as e:
                logging.debug("Arrow %s(%s) failed: %s", meth, oid, e)
                continue
            rec = raw[-1] if isinstance(raw, list) and raw else raw
            if isinstance(rec, dict) and rec:
                return rec, True
        for meth in ('get_order_book', 'get_orders', 'order_book'):
            fn = getattr(self.client, meth, None)
            if fn is None:
                continue
            try:
                book = fn() or []
            except Exception as e:
                logging.debug("Arrow %s failed: %s", meth, e)
                continue
            if isinstance(book, dict):
                book = book.get('data') or []
            for rec in book:
                if str(_order_id(rec)) == str(oid):
                    return rec, True
            return None, True            # read the book; it is not there
        return None, False

    def _refresh_order(self, oid):
        """Read the exchange's state for one of our orders into the
        ledger and return (order, readable)."""
        order = self.ledger['orders'].get(str(oid))
        if order is None:
            return None, False
        rec, readable = self._order_record(oid)
        if rec is None:
            return order, readable
        status = str(_dig(rec, 'status', 'orderStatus', 'ordStatus',
                          default='')).upper().strip()
        state = _STATUS.get(status, order.get('state') or 'open')
        units = self._units_per_lot(order['symbol']) or 1.0
        filled_units = _num(_dig(rec, 'filledQty', 'filledQuantity',
                                 'fillshares', 'cumQty', 'tradedQty'), 0.0)
        price = _num(_dig(rec, 'avgPrice', 'averagePrice', 'avgprc',
                          'tradedPrice', 'avgTradedPrice'))
        if price is not None:
            price /= self.price_divisor
        filled = round((filled_units or 0.0) / units, 9)
        if state == 'filled' and not filled:
            filled = float(order['lots'])      # some builds omit filledQty
        stamp = parse_expiry(_dig(rec, 'exchangeTimestamp', 'exchTime',
                                  'fillTime', 'updateTime')) or self.clock()
        order['state'] = 'partial' if (state == 'open' and filled) else state
        order['filled_lots'] = filled
        if price:
            order['price'] = price
        if state == 'rejected':
            order['error'] = str(_dig(rec, 'rejectionReason', 'rejectReason',
                                      'reason', 'statusMessage', 'text',
                                      'message', default=status))
        if filled:
            order.setdefault('filled_at', stamp)
            self._credit_fill(oid, order, filled, order.get('price'), stamp)
        return order, True

    # --- orders ------------------------------------------------------------

    def _side_enum(self, side):
        name = 'BUY' if str(getattr(side, 'value', side)).upper() == 'BUY' \
            else 'SELL'
        return getattr(self.sdk.TransactionType, name)

    def _enum(self, enum_name, *wanted):
        enum = getattr(self.sdk, enum_name)
        for name in wanted:
            found = getattr(enum, name, None)
            if found is not None:
                return found
            try:
                return enum(name)
            except (ValueError, KeyError, TypeError):
                continue
        return None

    def _place(self, symbol, side, lots, kind, price=0.0, comment='',
               closes=None):
        """Write the intent, send, record the id. Returns (oid, error)."""
        info = self.ensure_symbol(symbol)
        if info is None:
            return None, f'{symbol} is not in Arrow\'s instrument master'
        units = self._units_per_lot(symbol)
        if not units:
            return None, (f'{symbol} has no lot size in Arrow\'s master, so '
                          f'{lots:g} lots cannot be turned into a quantity')
        quantity = int(round(lots * units))
        local = f'local-{int(self.clock() * 1000)}-{len(self.ledger["orders"])}'
        intent = {'symbol': info.name, 'side': OrderSide(side).value,
                  'lots': float(lots), 'units': quantity, 'kind': kind,
                  'price': price or None, 'state': 'sending',
                  'comment': comment, 'created': self.clock(),
                  'closes': str(closes) if closes else None,
                  'booked_lots': 0.0}
        with self._lock:
            self.ledger['orders'][local] = intent
            self._save_ledger()          # BEFORE the send
            market = kind == 'market'
            kwargs = dict(
                exchange=self._exchange_enum, symbol=info.name,
                quantity=quantity, disclosed_quantity=0,
                product=self._enum('ProductType', self.product,
                                   {'NRML': 'M', 'MIS': 'I'}.get(self.product, 'M')),
                order_type=(self._enum('OrderType', 'MKT', 'MARKET') if market
                            else self._enum('OrderType', 'LMT', 'LIMIT')),
                variety=self._enum('Variety', 'REGULAR'),
                transaction_type=self._side_enum(side),
                # Plain MKT is disabled on Arrow: market = mpp + price 0.
                price=0.0 if market else float(price),
                validity=self._enum('Retention', 'DAY'),
                mpp=market)
            try:
                params = inspect.signature(self.client.place_order).parameters
                if 'tag' in params and comment:
                    kwargs['tag'] = str(comment)[:20]
            except (TypeError, ValueError):
                pass
            try:
                reply = self.client.place_order(**kwargs)
            except Exception as e:
                intent['state'] = 'rejected'
                intent['error'] = _sdk_error(e)
                self._save_ledger()
                return None, intent['error']
            oid = _order_id(reply) if isinstance(reply, dict) else reply
            if not oid:
                intent['state'] = 'unknown'
                intent['error'] = f'Arrow answered without an order id: {reply!r}'
                self._save_ledger()
                return None, intent['error']
            oid = str(oid)
            self.ledger['orders'].pop(local, None)
            intent['state'] = 'open'
            self.ledger['orders'][oid] = intent
            self._save_ledger()
        return oid, None

    def _await_fill(self, oid):
        deadline = time.monotonic() + MARKET_FILL_WAIT_SEC
        order, readable = self._refresh_order(oid)
        while order is not None and order.get('state') in _WORKING \
                and time.monotonic() < deadline:
            self.sleep(POLL_STEP_SEC)
            order, readable = self._refresh_order(oid)
        self._save_ledger()
        return order, readable

    def send_market_order(self, symbol, side, volume, slippage_points=1.0,
                          comment=""):
        tick = self.symbol_tick(symbol)
        requested = None
        if tick is not None:
            requested = tick.ask if OrderSide(side) is OrderSide.BUY else tick.bid
        oid, error = self._place(symbol, side, volume, 'market',
                                 comment=comment)
        if oid is None:
            return OrderResult(False, requested_price=requested,
                               error=f'Order failed: {error}')
        return self._market_result(oid, requested, volume)

    def _market_result(self, oid, requested, volume):
        order, readable = self._await_fill(oid)
        filled = float((order or {}).get('filled_lots') or 0.0)
        state = (order or {}).get('state')
        if state == 'filled' or (filled and state in _DONE):
            return OrderResult(True, requested_price=requested,
                               executed_price=order.get('price'), ticket=oid,
                               volume=filled or volume)
        if state == 'rejected':
            return OrderResult(False, requested_price=requested, ticket=oid,
                               error=f"Order failed: {order.get('error')}")
        # Still working, or unreadable. NOT a fill and NOT a refusal:
        # the caller reads order_fill_state and finds whatever is on.
        return OrderResult(False, requested_price=requested, ticket=oid,
                           error=(f'order {oid} is still {state or "unknown"} '
                                  f'at the exchange after '
                                  f'{MARKET_FILL_WAIT_SEC:g}s'
                                  + ('' if readable else
                                     ' — and Arrow could not be read')))

    def close_position_ticket(self, symbol, ticket, volume, entry_side,
                              slippage_points=1.0, comment=""):
        """Close OUR ticket: an opposite order for its volume, recorded
        against it. On a netting account that IS the close."""
        pos = self._position(ticket)
        if pos is None or pos.get('volume', 0.0) <= 1e-9:
            return OrderResult(False, error=(
                f'Close failed: ticket {ticket} is not an open position in '
                f'the Arrow ledger'))
        if pos['symbol'].upper() != str(symbol).upper():
            return OrderResult(False, error=(
                f'Close failed: ticket {ticket} is {pos["symbol"]}, not '
                f'{symbol}'))
        volume = min(float(volume), float(pos['volume']))
        close_side = OrderSide(getattr(entry_side, 'value', entry_side)).opposite
        tick = self.symbol_tick(symbol)
        requested = None
        if tick is not None:
            requested = tick.ask if close_side is OrderSide.BUY else tick.bid
        oid, error = self._place(symbol, close_side, volume, 'market',
                                 comment=comment, closes=ticket)
        if oid is None:
            return OrderResult(False, requested_price=requested,
                               error=f'Close failed: {error}')
        result = self._market_result(oid, requested, volume)
        if not result.success and result.error:
            result.error = result.error.replace('Order failed', 'Close failed')
        return result

    def legal_limit_price(self, symbol, side, price):
        """A limit that would CROSS is not a resting order. Kept a tick
        inside the touch, as MT5's stops-level rule does — so the quoter's
        assumption that a limit rests holds on this exchange too."""
        tick_size = self._tick_size(symbol) or 0.05
        wanted = round(round(price / tick_size) * tick_size, 10)
        t = self.symbol_tick(symbol)
        if t is None:
            return wanted, None
        side = OrderSide(getattr(side, 'value', side))
        if side is OrderSide.BUY and wanted >= t.ask:
            limit = round(round((t.ask - tick_size) / tick_size) * tick_size, 10)
            return limit, (f'buy limit moved {wanted:g} -> {limit:g}: it '
                           f'would have crossed the {t.ask:g} offer')
        if side is OrderSide.SELL and wanted <= t.bid:
            limit = round(round((t.bid + tick_size) / tick_size) * tick_size, 10)
            return limit, (f'sell limit moved {wanted:g} -> {limit:g}: it '
                           f'would have crossed the {t.bid:g} bid')
        return wanted, None

    def place_pending_limit(self, symbol, side, volume, price, comment=""):
        side = OrderSide(getattr(side, 'value', side))
        price, moved = self.legal_limit_price(symbol, side, price)
        oid, error = self._place(symbol, side, volume, 'limit', price=price,
                                 comment=comment)
        if oid is None:
            return {'ok': False, 'ticket': None, 'error': error}
        return {'ok': True, 'ticket': oid, 'error': None, 'price': price,
                'price_note': moved}

    def pending_orders_by_magic(self, symbol=None):
        """Our working limits. None when Arrow cannot be read — unknown
        is NOT "no orders", and a sweep must not believe otherwise."""
        if not self.is_alive():
            return None
        out = []
        for oid, order in list(self.ledger['orders'].items()):
            if order.get('kind') != 'limit' or order.get('state') not in \
                    ('open', 'partial'):
                continue
            if symbol and order['symbol'].upper() != str(symbol).upper():
                continue
            order, readable = self._refresh_order(oid)
            if not readable:
                return None
            if order.get('state') in ('open', 'partial'):
                out.append({'ticket': oid, 'symbol': order['symbol'],
                            'volume': round(order['lots']
                                            - (order.get('filled_lots') or 0.0),
                                            9),
                            'price': order.get('price')})
        self._save_ledger()
        return out

    def modify_pending(self, ticket, price):
        order = self.ledger['orders'].get(str(ticket))
        if order is None:
            return {'ok': False,
                    'error': f'order {ticket} is not in the Arrow ledger'}
        price, moved = self.legal_limit_price(order['symbol'], order['side'],
                                              price)
        fn = getattr(self.client, 'modify_order', None) or \
            getattr(self.client, 'amend_order', None)
        if fn is None:
            return {'ok': False, 'error': 'this pyarrow-client has no '
                                          'modify_order'}
        remaining = int(round((order['lots'] - (order.get('filled_lots') or 0))
                              * (self._units_per_lot(order['symbol']) or 1)))
        try:
            fn(str(ticket), exchange=self._exchange_enum,
               symbol=order['symbol'], quantity=remaining,
               price=float(price),
               order_type=self._enum('OrderType', 'LMT', 'LIMIT'),
               validity=self._enum('Retention', 'DAY'),
               transaction_type=self._side_enum(order['side']))
        except Exception as e:
            return {'ok': False, 'error': _sdk_error(e)}
        order['price'] = price
        self._save_ledger()
        if moved:
            logging.info("%s", moved)
        return {'ok': True, 'error': None}

    def cancel_pending(self, ticket):
        cancelled, error = False, None
        try:
            reply = self.client.cancel_order(str(ticket))
            # Some builds answer a refused cancel with a status body
            # instead of raising. Read it rather than assume success.
            status = str(_dig(reply, 'status', 'stat', default='')
                         if isinstance(reply, dict) else '').upper()
            if status in ('ERROR', 'FAILED', 'NOT_OK', 'REJECTED'):
                error = str(_dig(reply, 'message', 'emsg', 'reason',
                                 default=status))
            else:
                cancelled = True
        except Exception as e:
            error = _sdk_error(e)
        state = self.order_fill_state(ticket)
        state['cancelled'] = cancelled
        if error:
            state['error'] = error
        order = self.ledger['orders'].get(str(ticket))
        if cancelled and order is not None and order.get('state') in _WORKING:
            order['state'] = 'cancelled'
            self._save_ledger()
            state['still_open'] = False
        return state

    def order_fill_state(self, ticket):
        order, readable = self._refresh_order(ticket)
        self._save_ledger()
        if order is None:
            return {'ok': False, 'readable': readable, 'filled_volume': 0.0,
                    'price': None, 'position_tickets': [], 'filled_at': None,
                    'server_offset_sec': None, 'still_open': False,
                    'error': f'order {ticket} is not in the Arrow ledger'}
        filled = float(order.get('filled_lots') or 0.0)
        tickets = []
        if filled:
            tickets = [order['closes']] if order.get('closes') else [str(ticket)]
        stamp = order.get('filled_at')
        return {'ok': True, 'readable': readable, 'filled_volume': filled,
                'price': order.get('price') if filled else None,
                'position_tickets': tickets, 'from_position': False,
                'symbol': order['symbol'],
                # The broker's WALL clock as an epoch, as MT5 stamps deals.
                'filled_at': (int(stamp + IST_OFFSET_SEC)
                              if filled and stamp else None),
                'server_offset_sec': IST_OFFSET_SEC if filled else None,
                'still_open': order.get('state') in _WORKING,
                'error': order.get('error') if order.get('state') == 'rejected'
                else (None if readable else 'Arrow answered nothing about '
                      'this order')}

    # --- positions -----------------------------------------------------

    def _broker_positions(self):
        if not self.is_alive():
            return None
        try:
            raw = self.client.get_positions()
        except Exception as e:
            self.last_error = _sdk_error(e)
            return None
        if isinstance(raw, dict):
            raw = raw.get('data') or []
        return [p for p in (raw or []) if isinstance(p, dict)]

    def positions_by_magic(self, symbol=None):
        """Our tickets, cross-checked against what Arrow says is held.

        Per symbol, the ledger's NET (buys minus sells, in lots) must be
        what the exchange holds net — less whatever the desk holds by
        hand, which this system cannot see apart and does not try to.
        When the exchange holds LESS in our direction than the ledger,
        the missing volume is withheld from the newest tickets first, so
        the reconciler sees those as gone (and counts its strikes)
        instead of being told a position exists that the exchange does
        not have. When it holds MORE, the extra is not ours and is not
        reported as ours. Both are recorded in `netting` for Diagnose.

        None when Arrow cannot be read: unknown is not flat.
        """
        rows = self._broker_positions()
        if rows is None:
            return None
        broker_net = {}
        for p in rows:
            sym = str(_dig(p, 'tradingSymbol', 'symbol', default='')).upper()
            units = self._units_per_lot(sym) or 1.0
            net = _num(_dig(p, 'netQty', 'netQuantity', 'netqty', 'quantity'))
            if net is None:
                net = (_num(_dig(p, 'buyQty', 'cfBuyQty'), 0.0) or 0.0) - \
                      (_num(_dig(p, 'sellQty', 'cfSellQty'), 0.0) or 0.0)
            broker_net[sym] = broker_net.get(sym, 0.0) + net / units

        by_symbol = {}
        for ticket, pos in self.ledger['positions'].items():
            if pos.get('volume', 0.0) > 1e-9:
                by_symbol.setdefault(pos['symbol'].upper(), []).append(pos)

        out = []
        self.netting = {}
        for sym, positions in by_symbol.items():
            if symbol and sym != str(symbol).upper():
                continue
            positions.sort(key=lambda p: p.get('opened_at') or 0)
            ours = sum(p['volume'] * (1 if p['side'] == 'BUY' else -1)
                       for p in positions)
            held = broker_net.get(sym, 0.0)
            withheld = {}
            deficit = round(ours - held, 9)
            # Only a shortfall IN OUR DIRECTION removes our volume.
            if ours > 0 and deficit > 1e-9:
                short, side = min(deficit, ours), 'BUY'
            elif ours < 0 and deficit < -1e-9:
                short, side = min(-deficit, -ours), 'SELL'
            else:
                short, side = 0.0, None
            for pos in reversed(positions):
                if short <= 1e-9:
                    break
                if pos['side'] != side:
                    continue
                take = min(short, pos['volume'])
                withheld[pos['ticket']] = take
                short = round(short - take, 9)
            self.netting[sym] = {
                'ours_net_lots': round(ours, 9), 'exchange_net_lots': held,
                'mismatch': abs(round(ours - held, 9)) > 1e-9,
                'withheld': withheld}
            for pos in positions:
                volume = round(pos['volume'] - withheld.get(pos['ticket'], 0.0), 9)
                if volume <= 1e-9:
                    continue
                out.append({'ticket': pos['ticket'], 'symbol': pos['symbol'],
                            'side': pos['side'], 'volume': volume,
                            'price_open': pos.get('price_open'),
                            'comment': pos.get('comment', '')})
        for sym, held in broker_net.items():
            if sym not in by_symbol and abs(held) > 1e-9:
                self.netting[sym] = {'ours_net_lots': 0.0,
                                     'exchange_net_lots': held,
                                     'mismatch': True, 'withheld': {},
                                     'note': 'held at Arrow, not by this '
                                             'system — left alone'}
        return out

    # --- history -------------------------------------------------------

    def verify_ticket(self, ticket, attempts=3, delay=0.4):
        found = {'ticket': ticket, 'confirmed': False, 'deals': [],
                 'position_open': False}
        for attempt in range(attempts):
            rec, readable = self._order_record(ticket)
            if rec is not None:
                order, _ = self._refresh_order(ticket)
                found.update({
                    'confirmed': True, 'source': 'Arrow order book',
                    'symbol': _dig(rec, 'tradingSymbol', 'symbol'),
                    'volume': (order or {}).get('filled_lots'),
                    'price': (order or {}).get('price'),
                    'state': (order or {}).get('state'),
                    'comment': (order or {}).get('comment', '')})
                pos = self._position(ticket)
                found['position_open'] = bool(pos and pos['volume'] > 1e-9)
                break
            if attempt < attempts - 1:
                self.sleep(delay)
        if not found['confirmed']:
            found['error'] = 'not found in Arrow\'s order book'
        return found

    def order_log(self, hours=24):
        """This account's recent orders and fills, shaped like MT5's rows.

        Ours come from the ledger (it knows which are ours); the
        exchange's trade book adds the desk's own manual trades, marked
        not ours. Stamps are the broker's WALL clock as an epoch in ms,
        the convention MT5's deal history uses, so both accounts' rows
        sort into one table.
        """
        rows = []
        since = self.clock() - hours * 3600 if hours else 0
        ours = set()
        for oid, order in self.ledger['orders'].items():
            created = order.get('created') or 0
            if created < since and order.get('state') not in _WORKING:
                continue
            ours.add(str(oid))
            filled = float(order.get('filled_lots') or 0.0)
            stamp = order.get('filled_at') or created
            rows.append({
                'order_id': str(oid), 'deal_id': str(oid) if filled else '',
                'symbol': order['symbol'],
                'inst_type': ('DEAL' if filled else
                              'PENDING' if order.get('state') in _WORKING
                              else 'ORDER'),
                'side': order['side'].lower(),
                'pos_side': 'close' if order.get('closes') else 'open',
                'order_type': ('market ' if order['kind'] == 'market'
                               else '') + order['side'].lower()
                + (' limit' if order['kind'] == 'limit' else ''),
                'quantity': order['lots'], 'fill_qty': filled,
                'fill_price': order.get('price') or 0.0,
                'commission': None, 'swap': None, 'fee': 0.0,
                'fee_ccy': 'INR', 'pnl': 0.0,
                'state': {'open': 'working', 'partial': 'working'}.get(
                    order.get('state'), order.get('state')),
                'filled_at': int((stamp + IST_OFFSET_SEC) * 1000),
                'position_id': order.get('closes') or str(oid),
                'magic': None, 'is_bot': True,
                'comment': order.get('comment', ''),
                'server_offset_sec': IST_OFFSET_SEC})
        fn = getattr(self.client, 'get_trade_book', None) if self.client else None
        if fn is not None:
            try:
                trades = fn() or []
            except Exception as e:
                logging.debug("Arrow trade book failed: %s", e)
                trades = []
            for t in trades if isinstance(trades, list) else ():
                oid = str(_order_id(t) or '')
                if not oid or oid in ours:
                    continue
                sym = str(_dig(t, 'tradingSymbol', 'symbol', default=''))
                if self.exchange_name not in str(_dig(
                        t, 'exchange', 'exchSeg', default=self.exchange_name)).upper():
                    continue
                units = self._units_per_lot(sym) or 1.0
                qty = (_num(_dig(t, 'fillQty', 'tradedQty', 'quantity',
                                 'qty'), 0.0) or 0.0) / units
                price = (_num(_dig(t, 'fillPrice', 'tradedPrice', 'price',
                                   'avgPrice'), 0.0) or 0.0) / self.price_divisor
                stamp = parse_expiry(_dig(t, 'fillTime', 'exchangeTimestamp',
                                          'tradeTime')) or self.clock()
                side = str(_dig(t, 'transactionType', 'side', 'buySell',
                                default='')).lower()
                rows.append({
                    'order_id': oid, 'deal_id': str(_dig(t, 'tradeId',
                                                         'fillId', default=oid)),
                    'symbol': sym, 'inst_type': 'DEAL',
                    'side': 'buy' if side.startswith('b') else 'sell',
                    'pos_side': '-', 'order_type': 'manual',
                    'quantity': qty, 'fill_qty': qty, 'fill_price': price,
                    'commission': None, 'swap': None, 'fee': 0.0,
                    'fee_ccy': 'INR', 'pnl': 0.0, 'state': 'filled',
                    'filled_at': int((stamp + IST_OFFSET_SEC) * 1000),
                    'position_id': None, 'magic': None, 'is_bot': False,
                    'comment': '', 'server_offset_sec': IST_OFFSET_SEC})
        return rows


#: Arrow's interval names, by minutes. "min", never "1".
CANDLE_INTERVALS = {1: 'min', 3: '3min', 5: '5min', 10: '10min',
                    15: '15min', 30: '30min', 60: 'hour', 120: '2hours',
                    180: '3hours', 240: '4hours', 1440: 'day'}
#: Arrow's candle prices are paise.
CANDLE_PRICE_DIVISOR = 100.0
#: How far the newest bar may sit from the live mid before the history
#: is called a scale nobody confirmed.
CANDLE_SCALE_TOLERANCE = 0.10
#: The widest range asked for in one call.
CANDLE_MAX_DAYS = 60


def _candle_rows(reply):
    """The candle rows wherever this build put them: a bare list, or
    under data / candles. Each row a list [time, o, h, l, c, v, ...]."""
    if isinstance(reply, dict):
        for key in ('data', 'candles', 'result'):
            if key in reply:
                return _candle_rows(reply[key])
        return []
    if isinstance(reply, list):
        return [r for r in reply
                if isinstance(r, (list, tuple)) and len(r) >= 5]
    return []


def _iso_epoch(value):
    """A candle's stamp as a UTC epoch: ISO with an offset
    (2023-05-02T09:15:00+0530), or an epoch in seconds or ms."""
    if isinstance(value, (int, float)):
        return float(value) / (1000.0 if value > 1e11 else 1.0)
    text = str(value or '').strip()
    for fmt in ('%Y-%m-%dT%H:%M:%S%z', '%Y-%m-%d %H:%M:%S%z',
                '%Y-%m-%dT%H:%M:%S.%f%z'):
        try:
            return datetime.strptime(text, fmt).timestamp()
        except ValueError:
            continue
    try:
        # No offset given: the exchange's own clock, IST.
        naive = datetime.strptime(text[:19], '%Y-%m-%dT%H:%M:%S')
        return naive.replace(tzinfo=timezone.utc).timestamp() - IST_OFFSET_SEC
    except ValueError:
        return None


def _order_id(rec):
    if not isinstance(rec, dict):
        return rec
    return _dig(rec, 'orderNo', 'order_id', 'orderId', 'nestOrderNumber',
                'norenordno', 'id')


def _digits(tick):
    text = f'{tick:.10f}'.rstrip('0')
    return len(text.split('.')[1]) if '.' in text else 0


def _sdk_error(exc):
    """The broker's own words: Arrow's exceptions carry code, message,
    method and url, and all of them reach the screen."""
    code = getattr(exc, 'code', None)
    msg = getattr(exc, 'message', None) or str(exc)
    method, url = getattr(exc, 'method', None), getattr(exc, 'url', None)
    head = f'HTTP {code}: {msg}' if code else str(msg)
    if method and url:
        head += f'  [{method} {url.split("?")[0]}]'
    return head


def _parse_instruments(data):
    """Arrow's /all master: bytes (often gzip), JSON or CSV, ~223k rows
    (arrow-statarb facts 2 and 3)."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ('data', 'instruments', 'result', 'items'):
            if isinstance(data.get(key), list):
                return data[key]
        return [data]
    if isinstance(data, (bytes, bytearray, str)):
        raw = data.encode() if isinstance(data, str) else bytes(data)
        if raw[:2] == b'\x1f\x8b':
            import gzip
            raw = gzip.decompress(raw)
        text = raw.decode('utf-8', errors='replace').strip()
        if not text:
            return []
        if text[0] in '[{':
            import json
            return _parse_instruments(json.loads(text))
        import csv
        import io
        sample = text[:4096]
        delimiter = '\t' if sample.count('\t') > sample.count(',') else ','
        return [dict(r) for r in csv.DictReader(io.StringIO(text),
                                                delimiter=delimiter)]
    return []


def ist_now():
    return datetime.now(timezone.utc).timestamp() + IST_OFFSET_SEC
