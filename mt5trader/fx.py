"""The exchange rate that puts a dollar leg next to a rupee leg.

MCX crude is quoted in rupees and NYMEX crude in dollars, so the spread

    spread = P_B - beta * (P_A * USDINR * unit_factor)

has a third price in it. This module is where that price comes from,
and it says honestly how good it is.

Two sources, set per rate in the config's top-level `fx` section:

    "fx": {
      "USDINR": {"source": "manual", "rate": 84.25, "set_at": 1731400000,
                 "max_age_hours": 24}
    }

    "fx": {
      "USDINR": {"source": "symbol", "account": "leg_b",
                 "symbol": "USDINR25NOVFUT", "invert": false}
    }

MANUAL is the normal case — the desk types the rate it is working to on
the Exchanges page. Its AGE is shown beside every converted price, and
once it is older than `max_age_hours` it is STALE: the spread is still
drawn (a trader watching a spread must see it), but orders are withheld
with the reason, the same as a stale leg. A guard never withholds a
close.

SYMBOL reads the MID of the book of a live quote on one of the
accounts — an NSE currency future on Arrow, or a USDINR symbol on an MT5
broker that carries one. The mid, never `last`, for the same reason the
spread is built from mids.

UNMEASURED IS NOT ZERO. A rate nobody could read is None, the converted
leg then has no price, and the ladder goes dark rather than dividing by
something it made up.
"""

import json
import logging
import os
import time as time_mod

DEFAULT_MAX_AGE_HOURS = 24.0
#: A live FX quote is read at most this often per rate, however many
#: legs convert through it: every tick of every converted leg asks.
LIVE_TTL_SEC = 0.5


def parse_pair(name):
    """'USDINR' -> ('USD', 'INR'). None for anything that is not two
    three-letter codes."""
    text = str(name or '').strip().upper().replace('/', '')
    if len(text) != 6 or not text.isalpha():
        return None
    return text[:3], text[3:]


class FxBook:
    """Every configured rate, read on demand.

    `raw` is the config's `fx` section, or `config_path` names the file
    to re-read it from — the coordinator and the web process both hold
    one, and a rate saved on the Exchanges page has to reach the engine
    without a restart (it is not structural: it changes the SPREAD, not
    which instruments are traded). The file is re-read only when its
    modification time moves.

    `leg_for(account)` returns an UNCONVERTED leg for the `symbol`
    source; without it a symbol-sourced rate is unknown.
    """

    def __init__(self, raw=None, config_path=None, leg_for=None,
                 clock=time_mod.time, monotonic=time_mod.monotonic):
        self._raw = dict(raw or {})
        self.config_path = config_path
        self.leg_for = leg_for
        self.clock = clock
        self.monotonic = monotonic
        self._mtime = None
        self._live = {}          # name -> (read at, value, error)
        self._warned = set()

    # --- configuration -----------------------------------------------

    def _refresh(self):
        if not self.config_path:
            return
        try:
            mtime = os.path.getmtime(self.config_path)
        except OSError:
            return
        if mtime == self._mtime:
            return
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                self._raw = dict((json.load(f) or {}).get('fx') or {})
            self._mtime = mtime
        except (OSError, ValueError) as e:
            # Keep the LAST GOOD rates: a file caught half-written by
            # the web process is not a reason to lose USDINR mid-poll.
            logging.warning("fx: could not re-read %s (%s) — keeping the "
                            "rates already loaded", self.config_path, e)

    def names(self):
        self._refresh()
        return sorted(self._raw)

    def entry(self, name):
        self._refresh()
        return dict(self._raw.get(str(name or '').upper()) or {})

    # --- reading -------------------------------------------------------

    def rate(self, name):
        """The rate to convert with, or None when it is unknown."""
        return self.status(name)['rate']

    def status(self, name):
        """Everything the screen needs about one rate.

        `rate`        the value, or None
        `stale`       a one-line reason orders must be withheld, or None
        `age_sec`     how old a manual rate is (None for a live one)
        `source`      manual | symbol | missing
        """
        name = str(name or '').upper()
        entry = self.entry(name)
        out = {'name': name, 'rate': None, 'source': entry.get('source')
               or ('manual' if entry else 'missing'),
               'age_sec': None, 'stale': None, 'error': None,
               'set_at': entry.get('set_at'), 'symbol': entry.get('symbol'),
               'account': entry.get('account')}
        if not entry:
            out['error'] = (f'no {name} rate is configured — set one on the '
                            f'Exchanges page')
            out['stale'] = out['error']
            return out

        if out['source'] == 'symbol':
            value, error = self._live_rate(name, entry)
            out['rate'], out['error'] = value, error
            if value is None:
                out['stale'] = error
            return out

        try:
            value = float(entry.get('rate'))
        except (TypeError, ValueError):
            value = None
        if value is None or value <= 0:
            out['error'] = f'the manual {name} rate is not a positive number'
            out['stale'] = out['error']
            return out
        out['rate'] = value
        set_at = entry.get('set_at')
        try:
            age = self.clock() - float(set_at)
        except (TypeError, ValueError):
            age = None
        out['age_sec'] = age
        limit = entry.get('max_age_hours', DEFAULT_MAX_AGE_HOURS)
        try:
            limit = float(limit)
        except (TypeError, ValueError):
            limit = DEFAULT_MAX_AGE_HOURS
        if age is None:
            # A rate with no stamp could be from last year. It is shown,
            # and it does not price an order until somebody re-saves it.
            out['stale'] = (f'the manual {name} rate {value:g} has no time '
                            f'stamp — re-enter it to confirm it is current')
        elif limit > 0 and age > limit * 3600:
            out['stale'] = (f'the manual {name} rate {value:g} is '
                            f'{age / 3600:.1f}h old (limit {limit:g}h) — '
                            f'update it on the Exchanges page')
        return out

    def _live_rate(self, name, entry):
        now = self.monotonic()
        cached = self._live.get(name)
        if cached and now - cached[0] < LIVE_TTL_SEC:
            return cached[1], cached[2]
        value, error = None, None
        account, symbol = entry.get('account'), entry.get('symbol')
        leg = self.leg_for(account) if (self.leg_for and account) else None
        if leg is None:
            error = (f'{name} is read from {symbol} on {account}, and that '
                     f'account is not connected')
        else:
            try:
                tick = leg.tick(symbol)
            except Exception as e:             # a dead leg is not fatal
                tick, error = None, str(e)
            bid = (tick or {}).get('bid')
            ask = (tick or {}).get('ask')
            if bid and ask and bid > 0 and ask > 0:
                value = (float(bid) + float(ask)) / 2.0     # the MID
                if entry.get('invert'):
                    value = 1.0 / value
            elif error is None:
                error = (f'{symbol} on {account} has no two-sided quote, so '
                         f'{name} is unknown')
        self._live[name] = (now, value, error)
        return value, error


def manual_entry(rate, clock=time_mod.time, max_age_hours=None):
    """The config entry a rate typed on the Exchanges page becomes —
    stamped NOW, because the stamp is what makes its age honest."""
    entry = {'source': 'manual', 'rate': float(rate), 'set_at': clock()}
    if max_age_hours is not None:
        entry['max_age_hours'] = float(max_age_hours)
    return entry
