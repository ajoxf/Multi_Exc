"""A leg whose prices are restated in the OTHER leg's currency and unit.

The single-currency terminal subtracts two prices it can compare
directly. Across exchanges it cannot: NYMEX crude is dollars a barrel,
MCX crude is rupees a barrel; COMEX gold is dollars a troy ounce, MCX
gold is rupees per ten grams. So the dollar leg is wrapped:

    P_A' = P_A x FX x unit_factor          (e.g. USD/oz -> INR/10g)

and everything above this module — the spread, the ladder, the book,
the quoter, the reconciler, the slippage report — sees two legs priced
in the same currency and unit, and needs no idea that one of them is
not. That is deliberate: the engine is the one paid for on live
accounts, and every line of it that does not change is a line that
does not acquire a new bug.

WHAT IS CONVERTED, AND WHAT IS NOT

  prices      bid, ask, last, depth, fills, pendings, positions' open
              price, session O/H/L, tick size — all x m (m = FX x unit)
  prices IN   a limit price, a modify, a margin quote — all / m, then
              the broker rounds to ITS tick as it always did
  contract    contract_size / unit_factor, so `lots x C x dP'` is money
              in the converted currency: `k = L_B x C_B` still holds
  money       account equity, balance, margin, commission and swap
              stay in the ACCOUNT's own currency — they are the broker's
              figures, and a converted copy rides beside them where a
              total across both accounts needs one (`profit_converted`)

A fill is converted at the rate in force WHEN IT IS REPORTED, and the
book keeps that converted price. Marking it later at a different rate
therefore shows the FX translation of the dollar leg as part of the
spread's P&L — which is real: the rupee leg carries USDINR inside its
price and the dollar leg does not. The raw broker prices ride along
(`raw_*`) so nothing on the screen is unrecoverable.

UNKNOWN FX IS NO PRICE. A rate that cannot be read makes the converted
leg's tick None, the ladder goes dark and says why — never a spread
computed at a rate of 1 or 0.
"""

import logging

from .fx import parse_pair


class Conversion:
    """How one (account, symbol) is restated."""

    def __init__(self, symbol, fx=None, price_factor=1.0, target=None):
        self.symbol = symbol
        self.fx = (str(fx).upper() if fx else None)
        self.price_factor = float(price_factor or 1.0)
        pair = parse_pair(self.fx) if self.fx else None
        #: The currency prices come out in: the FX pair's quote side.
        self.target = target or (pair[1] if pair else None)
        self.source = pair[0] if pair else None

    def key(self):
        return (self.fx, round(self.price_factor, 12))

    def to_dict(self):
        return {'symbol': self.symbol, 'fx': self.fx,
                'price_factor': self.price_factor,
                'from_currency': self.source, 'to_currency': self.target}


def conversions_from_config(config):
    """{account: {symbol: Conversion}} for every leg any pair converts.

    Read off each pair's `leg_a` / `leg_b` (`fx`, `price_factor`). The
    same symbol on the same account converted two DIFFERENT ways by two
    pairs is a contradiction — one tick cannot carry two prices — and
    is returned in the second value so the caller can refuse it
    (config.conversion_clash refuses it at save time already).
    """
    out, clashes = {}, []
    for key, pair in (getattr(config, 'pairs', None) or {}).items():
        for leg in (pair.leg_a, pair.leg_b):
            fx = leg.get('fx')
            factor = leg.get('price_factor')
            if not fx and factor in (None, '', 1, 1.0):
                continue
            account, symbol = leg.get('account'), leg.get('symbol')
            conv = Conversion(symbol, fx, factor)
            seen = out.setdefault(account, {}).get(symbol)
            if seen is not None and seen.key() != conv.key():
                clashes.append(
                    f"{symbol} on '{account}' is converted as "
                    f"{seen.fx or 'no FX'} x {seen.price_factor:g} by one "
                    f"pair and {conv.fx or 'no FX'} x "
                    f"{conv.price_factor:g} by '{key}'")
                continue
            out[account][symbol] = conv
    return out, clashes


def wrap_legs(legs, config, fxbook):
    """Wrap every leg that has a converted symbol. Legs with none are
    returned as they are — a pair with no FX is exactly the old pair."""
    conversions, clashes = conversions_from_config(config)
    for clash in clashes:
        logging.error("conversion clash: %s — the second is ignored", clash)
    return {name: (ConvertedLeg(leg, conversions[name], fxbook)
                   if conversions.get(name) else leg)
            for name, leg in legs.items()}


class ConvertedLeg:
    """Same methods as LocalLeg / RemoteLeg; prices restated per symbol.

    Anything not overridden passes straight through, so a method added
    to the leg interface later still works — unconverted, which for a
    method that carries no price is correct, and which a test pins for
    every method that does.
    """

    def __init__(self, inner, conversions, fxbook):
        self.inner = inner
        self.name = inner.name
        self.conversions = dict(conversions or {})
        self.fxbook = fxbook
        self._warned = set()
        #: ticket -> symbol for every order sent through this leg, so a
        #: fill state (which MT5 reports without a symbol) can be
        #: restated at the right symbol's rate and unit.
        self._ticket_symbol = {}

    def __getattr__(self, attr):
        # Only reached for attributes not defined here.
        return getattr(self.inner, attr)

    # --- the rate ------------------------------------------------------

    def _conv(self, symbol):
        return self.conversions.get(symbol)

    def multiplier(self, symbol):
        """(m, info) for this symbol: m is None when the rate is unknown;
        (1.0, None) for a symbol that is not converted at all."""
        conv = self._conv(symbol)
        if conv is None:
            return 1.0, None
        if not conv.fx:
            return conv.price_factor, {'conversion': conv.to_dict(),
                                       'fx_rate': None, 'fx_reason': None,
                                       'multiplier': conv.price_factor}
        status = self.fxbook.status(conv.fx) if self.fxbook else \
            {'rate': None, 'stale': f'no FX book for {conv.fx}'}
        rate = status.get('rate')
        info = {'conversion': conv.to_dict(), 'fx_rate': rate,
                'fx_age_sec': status.get('age_sec'),
                'fx_source': status.get('source'),
                'fx_reason': status.get('stale'),
                'multiplier': None if rate is None
                else rate * conv.price_factor}
        if rate is None:
            if (symbol, 'dark') not in self._warned:
                self._warned.add((symbol, 'dark'))
                logging.error("%s on '%s': %s — the leg has no price until "
                              "it is", symbol, self.name,
                              status.get('stale') or 'FX unknown')
            return None, info
        self._warned.discard((symbol, 'dark'))
        return rate * conv.price_factor, info

    def _out(self, value, m):
        if value is None or m is None:
            return None if m is None else value
        try:
            return float(value) * m
        except (TypeError, ValueError):
            return value

    def _in(self, value, m):
        if value is None or not m:
            return value
        return float(value) / m

    # --- prices out -----------------------------------------------------

    def tick(self, symbol):
        t = self.inner.tick(symbol)
        m, info = self.multiplier(symbol)
        if t is None or info is None:
            return t
        if m is None:
            return None
        out = dict(t)
        for field in ('bid', 'ask', 'last'):
            out[f'raw_{field}'] = t.get(field)
            out[field] = self._out(t.get(field), m)
        out.update(info)
        return out

    def resubscribe(self, symbol):
        t = self.inner.resubscribe(symbol)
        m, info = self.multiplier(symbol)
        if t is None or info is None:
            return t
        if m is None:
            return None
        out = dict(t)
        for field in ('bid', 'ask', 'last'):
            out[field] = self._out(t.get(field), m)
        return out

    def rates(self, symbol, timeframe_min, count):
        """History bars, restated like every other price.

        The Algo builds its band and the backtest builds its trades from
        these. Passed through unconverted — which is what `__getattr__`
        would have done — a dollar leg's bars would be subtracted from
        rupee bars: a spread that never existed, which in LIVE the Algo
        would trade. So `close` and `point` (the bar's spread is counted
        in points) go through the same multiplier as a tick.

        AT TODAY'S RATE: the rate each bar was struck at is not known
        here, so a long history carries USDINR's drift over it as part
        of the spread. Unknown rate is no history at all (None), which
        the Algo already reads as "collect from the live price".
        """
        bars = self.inner.rates(symbol, timeframe_min, count)
        m, info = self.multiplier(symbol)
        if bars is None or info is None:
            return bars
        if m is None:
            return None
        out = []
        for bar in bars:
            row = dict(bar)
            row['close_raw'] = bar.get('close')
            row['close'] = self._out(bar.get('close'), m)
            if bar.get('point') is not None:
                row['point'] = self._out(bar.get('point'), m)
            out.append(row)
        return out

    def ensure_symbol(self, symbol):
        reply = self.inner.ensure_symbol(symbol)
        m, info = self.multiplier(symbol)
        if not reply or info is None or m is None:
            return reply
        out = dict(reply)
        for field in ('point', 'tick_size'):
            out[f'raw_{field}'] = reply.get(field)
            out[field] = self._out(reply.get(field), m)
        return out

    def depth(self, symbol):
        rows = self.inner.depth(symbol)
        m, info = self.multiplier(symbol)
        if not rows or info is None:
            return rows
        if m is None:
            return None
        return [dict(r, price=self._out(r.get('price'), m)) for r in rows]

    def session_stats(self, symbol):
        stats = self.inner.session_stats(symbol)
        m, info = self.multiplier(symbol)
        if not stats or info is None:
            return stats
        out = dict(stats)
        for field in ('open', 'high', 'low'):
            out[field] = None if m is None else self._out(stats.get(field), m)
        return out

    def symbol_report(self, symbol):
        report = self.inner.symbol_report(symbol)
        conv = self._conv(symbol)
        if not report or conv is None:
            return report
        m, info = self.multiplier(symbol)
        out = dict(report)
        for field in ('bid', 'ask', 'point', 'tick_size'):
            out[f'raw_{field}'] = report.get(field)
            out[field] = None if m is None else self._out(report.get(field), m)
        size = report.get('contract_size')
        out['contract_size_raw'] = size
        # Money per 1.00 of the CONVERTED price, per lot: the FX cancels
        # (a rupee move of the converted price is a rupee of P&L), only
        # the unit is left. 100 oz of COMEX gold is 311.03 lots of 10 g.
        out['contract_size'] = (float(size) / conv.price_factor
                                if size else size)
        if report.get('tick_value') is not None and conv.fx:
            rate = (info or {}).get('fx_rate')
            out['tick_value_raw'] = report.get('tick_value')
            out['tick_value'] = (None if rate is None
                                 else float(report['tick_value']) * rate)
        out['currency_raw'] = report.get('currency')
        if conv.target:
            out['currency'] = conv.target
        out['conversion'] = dict((info or {}), symbol=symbol)
        return out

    def positions(self, symbol=None):
        rows = self.inner.positions(symbol)
        if rows is None:
            return None             # unknown stays unknown
        out = []
        for row in rows:
            m, info = self.multiplier(row.get('symbol'))
            if info is None:
                out.append(row)
                continue
            converted = dict(row, price_open_raw=row.get('price_open'))
            converted['price_open'] = (None if m is None else
                                       self._out(row.get('price_open'), m))
            out.append(converted)
        return out

    def pending_orders(self, symbol=None):
        rows = self.inner.pending_orders(symbol)
        if rows is None:
            return None
        out = []
        for row in rows:
            m, info = self.multiplier(row.get('symbol'))
            if info is None:
                out.append(row)
                continue
            out.append(dict(row, price_raw=row.get('price'),
                            price=None if m is None
                            else self._out(row.get('price'), m)))
        return out

    def order_log(self, hours=24):
        rows = self.inner.order_log(hours)
        if rows is None:
            return None
        out = []
        for row in rows:
            m, info = self.multiplier(row.get('symbol'))
            if info is None:
                out.append(row)
                continue
            # Converted at the rate NOW, which for a fill from hours ago
            # is an approximation — so the broker's own price rides along
            # and the row says which rate it was restated at.
            out.append(dict(row, fill_price_raw=row.get('fill_price'),
                            fill_price=None if m is None
                            else self._out(row.get('fill_price'), m),
                            fx_rate=(info or {}).get('fx_rate')))
        return out

    def verify_order(self, ticket):
        found = self.inner.verify_order(ticket)
        m, info = self.multiplier((found or {}).get('symbol'))
        if not found or info is None:
            return found
        out = dict(found, price_raw=found.get('price'),
                   price=None if m is None else self._out(found.get('price'), m))
        out['deals'] = [dict(d, price_raw=d.get('price'),
                             price=None if m is None
                             else self._out(d.get('price'), m))
                        for d in (found.get('deals') or ())]
        return out

    def account_info(self):
        info = self.inner.account_info()
        if not info:
            return info
        # The broker's money stays the broker's. Where every converted
        # symbol on this account goes through ONE rate from this
        # account's currency, a converted copy of the floating P&L rides
        # beside it, so a check that totals P&L across two accounts in
        # two currencies adds like with like.
        convs = {c.fx for c in self.conversions.values() if c.fx}
        if len(convs) == 1:
            fx = next(iter(convs))
            pair = parse_pair(fx)
            status = self.fxbook.status(fx) if self.fxbook else {}
            rate = status.get('rate')
            if pair and pair[0] == info.get('currency') and rate:
                info = dict(info, profit_converted=(
                    None if info.get('profit') is None
                    else float(info['profit']) * rate),
                    converted_currency=pair[1], fx_rate=rate)
        return info

    # --- fills: prices come back converted ------------------------------

    def _fill_out(self, symbol, reply):
        if not isinstance(reply, dict):
            return reply
        if reply.get('ticket') is not None and symbol:
            self._ticket_symbol[str(reply['ticket'])] = symbol
        m, info = self.multiplier(symbol)
        if info is None:
            return reply
        out = dict(reply, price_raw=reply.get('price'))
        if m is None:
            # The order went; the rate did not come back. The fill is
            # real and so is its raw price — the converted one is
            # unknown, and the book is told so rather than given a 0.
            out['price'] = None
            out['fx_reason'] = (info or {}).get('fx_reason') or 'FX unknown'
        else:
            out['price'] = self._out(reply.get('price'), m)
            out['fx_rate'] = info.get('fx_rate')
        return out

    def order(self, symbol, side, volume, slippage_points=1.0, comment=""):
        m, info = self.multiplier(symbol)
        # Slippage is stated in SPREAD points by the engine, i.e. in
        # converted units; the broker wants its own.
        slip = slippage_points if (info is None or not m) \
            else slippage_points / m
        reply = self.inner.order(symbol, side, volume, slippage_points=slip,
                                 comment=comment)
        return self._fill_out(symbol, reply)

    def close_ticket(self, symbol, ticket, volume, entry_side,
                     slippage_points=1.0, comment=""):
        m, info = self.multiplier(symbol)
        slip = slippage_points if (info is None or not m) \
            else slippage_points / m
        # A CLOSE GOES WHATEVER THE RATE. It is sent in the broker's own
        # units and needs no conversion to be correct; only the price
        # reported back does. A guard must never prevent a close.
        reply = self.inner.close_ticket(symbol, ticket, volume, entry_side,
                                        slippage_points=slip, comment=comment)
        return self._fill_out(symbol, reply)

    def place_limit(self, symbol, side, volume, price, comment=""):
        m, info = self.multiplier(symbol)
        if info is not None and m is None:
            return {'ok': False, 'ticket': None,
                    'error': (f'{symbol}: {(info or {}).get("fx_reason")} — '
                              f'a limit cannot be priced without the rate')}
        reply = self.inner.place_limit(symbol, side, volume,
                                       self._in(price, m) if info else price,
                                       comment=comment)
        return self._fill_out(symbol, reply) if info else reply

    def modify_order(self, ticket, price, symbol=None):
        symbol = (symbol or self._ticket_symbol.get(str(ticket))
                  or self._symbol_of_pending(ticket))
        m, info = self.multiplier(symbol)
        if info is not None and m is None:
            return {'ok': False,
                    'error': f'{symbol}: FX unknown — re-peg withheld'}
        return self.inner.modify_order(
            ticket, self._in(price, m) if info else price)

    def _symbol_of_pending(self, ticket):
        if len(self.conversions) == 0:
            return None
        rows = self.inner.pending_orders(None) or ()
        for row in rows:
            if str(row.get('ticket')) == str(ticket):
                return row.get('symbol')
        # Not found: a pending that is not listed cannot be re-pegged
        # correctly by guessing its symbol. One converted symbol on the
        # account is not a guess.
        if len(self.conversions) == 1:
            return next(iter(self.conversions))
        return None

    def order_state(self, ticket):
        return self._state_out(ticket, self.inner.order_state(ticket))

    def cancel_order(self, ticket):
        symbol = self._ticket_symbol.get(str(ticket)) \
            or self._symbol_of_pending(ticket)
        return self._state_out(ticket, self.inner.cancel_order(ticket),
                               symbol)

    def _state_out(self, ticket, state, symbol=None):
        if not isinstance(state, dict) or state.get('price') is None:
            return state
        symbol = (symbol or state.get('symbol')
                  or self._ticket_symbol.get(str(ticket))
                  or self._symbol_of_pending(ticket))
        if symbol is None:
            # Not ours to guess: an unconvertible price is unknown.
            return dict(state, price_raw=state.get('price'), price=None)
        return self._fill_out(symbol, state)

    def margin_for(self, symbol, side, volume, price=None):
        m, info = self.multiplier(symbol)
        if price is not None and info is not None:
            if m is None:
                return None
            price = self._in(price, m)
        return self.inner.margin_for(symbol, side, volume, price)
