"""Tell the trader when a cross-exchange gap is worth looking at.

AN ALERT, NEVER AN ORDER. Nothing in here sends, rests, cancels or
closes anything: it reads the snapshot the ladder already shows and
says, on the screen and on the phone, "this gap is unusually wide —
here are the numbers". The trader decides and clicks. That is the
line this terminal holds (CLAUDE.md: no automatic entries or exits);
the alert sits on the trader's side of it.

THE GAP IS THE EXECUTABLE ONE, after costs, against this pair's own
normal — never the last-price difference:

    MCX rich   sell the spread: sell B at its BID, buy A at its ASK
               edge = short_spread - normal - costs
    MCX cheap  buy the spread:  buy B at its ASK, sell A at its BID
               edge = normal - long_spread - costs

`normal` is typed per pair, because it is not zero everywhere: MCX crude
converges to NYMEX x the RBI rate (normal ~0), while MCX gold and silver
carry import duty and sit a few percent above COMEX all the time. It is
`arb_normal` (in spread units) plus `arb_normal_pct` of leg B's price.

BOTH TESTS must pass ("significantly larger" means both):

  1. the net edge is at least `arb_margin` (spread units, e.g. Rs/bbl)
  2. the spread is unusual against its own recent history: its z-score
     over the last `ARB_Z_WINDOW` samples is past `arb_z` sigmas in the
     same direction (0 turns this test off)

and the price must be one the market is offering: a stale leg, a
desynced jump or a stale USDINR is exactly how a FAKE gap appears, so
any guard reason suppresses the alert and says so.

Fired once when it becomes true; re-armed only once the edge has fallen
back below half the margin, so a gap hovering at the threshold is one
alert, not fifty.
"""

import time as time_mod

#: Samples (one a second) the z-score is measured over: five minutes.
Z_WINDOW = 300
#: Alerts kept for the screen.
KEEP_EVENTS = 50



def last_z(rows, window=Z_WINDOW):
    """The newest point's z against the last `window` points that had a
    price. None until there are 30 — unmeasured is not zero."""
    values = [r['spread'] for r in rows[-window:]
              if r.get('spread') is not None]
    if len(values) < 30 or rows[-1].get('spread') is None:
        return None
    n = len(values)
    mean = sum(values) / n
    var = sum((v - mean) ** 2 for v in values) / (n - 1)
    sd = var ** 0.5
    return None if sd <= 0 else (values[-1] - mean) / sd


def _num(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


#: Dollars in one NSE USDINR futures lot.
USDINR_LOT_USD = 1000.0


def fx_hedge(pair, md, meta_a):
    """The USDINR hedge one Qty of this pair needs, and what it costs.

    The rupee leg carries USDINR inside its price and the dollar leg does
    not, so a move in the rate moves the spread: on CL at $88.40, a 0.10
    rise in USDINR is Rs 8.84 a barrel, more than most gaps. Holding
    USDINR futures the size of the dollar leg's notional takes that out:

        notional = leg A lots x leg A contract (own units) x leg A price ($)
        lots     = notional / dollars per lot      (88,400 / 1,000 = 88)
        cost     = lots x the trader's round-trip cost per lot

    `cost` is None when no cost per lot is typed — unmeasured is not
    zero, and the alert then says the hedge is not costed.
    """
    if not (pair.leg_a or {}).get('fx'):
        return {'needed': False, 'lots': None, 'notional_usd': None,
                'cost': 0.0, 'cost_per_lot': None}
    md = md or {}
    bid, ask = md.get('leg_a_raw_bid'), md.get('leg_a_raw_ask')
    price = (bid + ask) / 2.0 if (bid and ask) else None
    contract = meta_a.get('contract_size_raw') or meta_a.get('contract_size')
    if meta_a.get('contract_size_raw') and meta_a.get('contract_size_mt5') \
            and meta_a.get('contract_size'):
        # The trader's override, back in leg A's own units.
        contract = (meta_a['contract_size'] * meta_a['contract_size_raw']
                    / meta_a['contract_size_mt5'])
    lot_usd = float(getattr(pair, 'fx_hedge_lot_usd', None)
                    or USDINR_LOT_USD)
    notional = (float(pair.clip_lots_a or 1.0) * float(contract) * price
                if (contract and price) else None)
    lots = round(notional / lot_usd) if notional else None
    per_lot = _num(getattr(pair, 'fx_hedge_cost_per_lot', None))
    cost = (lots * per_lot) if (lots is not None and per_lot is not None) \
        else None
    return {'needed': True, 'lots': lots, 'notional_usd': notional,
            'cost': cost, 'cost_per_lot': per_lot, 'lot_usd': lot_usd}


class ArbWatch:
    def __init__(self, clock=time_mod.time):
        self.clock = clock
        self.status = {}          # pair key -> the latest reading
        self.events = []          # newest last
        self._active = {}         # pair key -> direction while armed off

    def evaluate(self, pair, md, settings=None, history=None,
                 costs_per_unit=0.0, cost_detail=None):
        """Read one pair's snapshot. Returns (status, fired event or None)."""
        key = pair.key
        if not getattr(pair, 'arb_alert', False):
            self.status[key] = {'enabled': False}
            self._active.pop(key, None)
            return self.status[key], None
        margin = _num(getattr(pair, 'arb_margin', None))
        z_needed = _num(getattr(pair, 'arb_z', None), 2.0)
        status = {'enabled': True, 'margin': margin, 'z_needed': z_needed,
                  'active': False, 'direction': None, 'edge': None,
                  'edge_pct': None, 'z': None, 'normal': None,
                  'cost': None, 'why_not': None}
        self.status[key] = status
        if margin is None:
            status['why_not'] = ('no margin set — type the edge this pair '
                                 'must clear, after costs')
            return status, None
        if not md or md.get('spread') is None:
            status['why_not'] = 'no price on this pair'
            return status, None

        mid_b = _num(md.get('leg_b_mid'))
        normal = (_num(getattr(pair, 'arb_normal', None), 0.0)
                  + _num(getattr(pair, 'arb_normal_pct', None), 0.0)
                  / 100.0 * (mid_b or 0.0))
        cost = (costs_per_unit or 0.0) + _num(
            getattr(pair, 'arb_extra_cost', None), 0.0)
        short, long_ = _num(md.get('short_spread')), _num(md.get('long_spread'))
        rich = None if short is None else short - normal - cost
        cheap = None if long_ is None else normal - long_ - cost
        if rich is None and cheap is None:
            status['why_not'] = 'no executable price'
            return status, None
        direction, edge = (('MCX_RICH', rich) if (cheap is None or
                           (rich is not None and rich >= cheap))
                           else ('MCX_CHEAP', cheap))
        z = last_z(history or ()) if history else None
        hedge = (cost_detail or {}).get('hedge') or {}
        status.update(direction=direction, edge=edge, normal=normal,
                      cost=cost, z=z, hedge_lots=hedge.get('lots'),
                      hedge_notional_usd=hedge.get('notional_usd'),
                      hedge_cost=hedge.get('cost'),
                      hedge_needed=bool(hedge.get('needed')),
                      hedge_per_unit=(cost_detail or {}).get('hedge_per_unit'),
                      edge_pct=(100.0 * edge / mid_b) if mid_b else None)

        reasons = []
        if edge < margin:
            reasons.append(f'net edge {edge:,.2f} is under the {margin:,.2f} '
                           f'margin')
        if z_needed and z_needed > 0:
            if z is None:
                reasons.append('not enough history for z yet')
            elif (direction == 'MCX_RICH' and z < z_needed) or \
                    (direction == 'MCX_CHEAP' and z > -z_needed):
                reasons.append(f'z {z:+.2f} is inside ±{z_needed:g}')
        if hedge.get('needed') and hedge.get('cost') is None:
            # Not a reason to stay silent — the gap is real — but the
            # trader is told the hedge was not in the cost.
            status['hedge_note'] = ('USDINR hedge not costed: type the cost '
                                    'per lot on the ladder')
        if md.get('guard_reason'):
            # A stale leg, a jump or a stale rate is how a FAKE gap looks.
            reasons.append(f"price not trusted: {md['guard_reason']}")

        if reasons:
            status['why_not'] = '; '.join(reasons)
            # Re-armed only once the edge is well back in: hysteresis, so
            # a gap hovering at the line is one alert, not fifty.
            if self._active.get(key) and (edge < margin / 2.0
                                          or direction != self._active[key]):
                self._active.pop(key, None)
            return status, None

        status['active'] = True
        if self._active.get(key) == direction:
            return status, None              # already said
        self._active[key] = direction
        event = self._event(pair, md, status)
        self.events.append(event)
        del self.events[:-KEEP_EVENTS]
        return status, event

    def _event(self, pair, md, status):
        rich = status['direction'] == 'MCX_RICH'
        action = ('SELL leg B (MCX) at its bid / BUY leg A at its ask'
                  if rich else
                  'BUY leg B (MCX) at its ask / SELL leg A at its bid')
        price = md.get('short_spread') if rich else md.get('long_spread')
        z = status['z']
        hedge = ''
        if status.get('hedge_needed') and status.get('hedge_lots'):
            # The rupee leg is SOLD when MCX is rich: hold USD against
            # the dollar leg's notional -> BUY USDINR. Mirror when cheap.
            hedge = (f" Hedge: {'BUY' if rich else 'SELL'} "
                     f"{status['hedge_lots']} USDINR lots "
                     f"(~${status['hedge_notional_usd']:,.0f})"
                     + (f", cost {status['hedge_cost']:,.2f} included."
                        if status.get('hedge_cost') is not None
                        else ' — NOT costed: no cost per lot typed.'))
        text = (f"{pair.name}: MCX {'RICH' if rich else 'CHEAP'} — net edge "
                f"{status['edge']:,.2f} per unit"
                + (f" ({status['edge_pct']:+.3f}%)"
                   if status['edge_pct'] is not None else '')
                + f" after costs {status['cost']:,.2f}, vs normal "
                f"{status['normal']:,.2f}"
                + (f", z {z:+.2f}" if z is not None else '')
                + f". Executable spread {price:,.2f}. {action}.{hedge} "
                f"Alert only — nothing was sent.")
        # The id is a millisecond timestamp, so it keeps rising across an
        # engine restart: a page that had already seen alert 5 would
        # otherwise ignore the restarted engine's 1 to 5 in silence.
        now_ms = int(self.clock() * 1000)
        self._last_id = max(getattr(self, '_last_id', 0) + 1, now_ms)
        return {'id': self._last_id, 'at': self.clock(), 'pair': pair.key,
                'name': pair.name, 'direction': status['direction'],
                'edge': status['edge'], 'edge_pct': status['edge_pct'],
                'z': z, 'normal': status['normal'], 'cost': status['cost'],
                'spread': price, 'leg_a_bid': md.get('leg_a_bid'),
                'leg_a_ask': md.get('leg_a_ask'),
                'leg_b_bid': md.get('leg_b_bid'),
                'leg_b_ask': md.get('leg_b_ask'),
                'fx': md.get('leg_a_fx_rate'), 'text': text}
