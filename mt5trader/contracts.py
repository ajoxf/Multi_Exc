"""What one lot IS, on each exchange, and how two of them compare.

A cross-exchange spread puts two contracts side by side that agree on
almost nothing:

    MCX CRUDEOIL   INR per barrel     100 barrels a lot
    NYMEX CL       USD per barrel     1,000 barrels a lot
    MCX GOLD       INR per 10 grams   1 kilogram a lot
    COMEX GC       USD per troy oz    100 troy ounces a lot
    MCX SILVER     INR per kilogram   30 kilograms a lot
    COMEX SI       USD per troy oz    5,000 troy ounces a lot

Subtracting one price from the other means nothing until both are in
the SAME currency AND the SAME unit. This module owns the second half
of that — the units — and the reference specs the screen checks the
brokers' own figures against. The currency half (USDINR) is `fx.py`.

THE TABLE IS A CROSS-CHECK, NOT THE SOURCE. Every money figure still
runs through the contract size the BROKER reports (MT5's
`trade_contract_size`, Arrow's lot size), exactly as in the
single-currency terminal. This table is what Diagnose compares those
against, so a broker that reports a gold lot as 1 instead of 100 is
named on the screen before anybody sizes a trade off it. Exchanges do
revise lots (MCX has, more than once): when this table and the broker
disagree the broker wins, and the screen says they disagree.
"""

import math
import re
import time as time_mod
from datetime import datetime, timezone

#: Every unit a commodity is quoted in, in one base quantity per
#: dimension. Mass in GRAMS, volume in BARRELS, energy in mmBtu.
#: Only units of the same dimension convert into each other.
TROY_OUNCE_G = 31.1034768
POUND_G = 453.59237

UNITS = {
    # mass, in grams
    'g': ('mass', 1.0),
    '8g': ('mass', 8.0),
    '10g': ('mass', 10.0),
    '100g': ('mass', 100.0),
    'kg': ('mass', 1000.0),
    'oz': ('mass', TROY_OUNCE_G),        # TROY ounce, which is what metals trade in
    'lb': ('mass', POUND_G),
    'tonne': ('mass', 1_000_000.0),
    # volume, in barrels
    'bbl': ('volume', 1.0),
    # energy, in mmBtu
    'mmbtu': ('energy', 1.0),
}


#: How each unit is named on the screen, in the order the dropdown
#: offers them.
UNIT_LABELS = {
    'bbl': 'barrel (bbl)',
    'oz': 'troy ounce (oz t, 31.1035 g)',
    'g': 'gram (g)',
    '8g': '8 grams (MCX Gold Guinea)',
    '10g': '10 grams (MCX gold quote)',
    '100g': '100 grams',
    'kg': 'kilogram (kg, MCX silver quote)',
    'lb': 'pound (lb, 453.592 g)',
    'tonne': 'metric tonne',
    'mmbtu': 'million BTU (mmBtu)',
}
_BASE_NAMES = {'mass': 'g', 'volume': 'bbl', 'energy': 'mmBtu'}


def units_table():
    """Every unit the dropdown offers, with its size in its dimension's
    base quantity — what the screen needs to show a factor's working."""
    return [{'key': key, 'label': UNIT_LABELS.get(key, key),
             'dimension': UNITS[key][0], 'base': _BASE_NAMES[UNITS[key][0]],
             'size': UNITS[key][1]}
            for key in UNIT_LABELS]


def factor_steps(from_unit, to_unit):
    """The unit factor AND its working, line by line, so the number on
    the pair can be checked by hand rather than trusted:

        Leg A is priced per 1 oz = 31.1034768 g
        Leg B is priced per 10g  = 10 g
        a price per 10g is a price per oz x (10 / 31.1034768)
        unit factor = 0.32150747

    None when the two units are of different kinds (an ounce is not a
    barrel) — there is no factor, and the screen says so.
    """
    a = UNITS.get(_unit(from_unit))
    b = UNITS.get(_unit(to_unit))
    if a is None or b is None:
        return None
    if a[0] != b[0]:
        return {'factor': None, 'steps': [
            f'{from_unit} measures {a[0]} and {to_unit} measures {b[0]} — '
            f'they do not convert']}
    base = _BASE_NAMES[a[0]]
    factor = b[1] / a[1]
    return {'factor': factor, 'steps': [
        f'Leg A is priced per {from_unit} = {a[1]:.10g} {base}',
        f'Leg B is priced per {to_unit} = {b[1]:.10g} {base}',
        f'one {to_unit} is {b[1]:.10g} / {a[1]:.10g} = {factor:.8g} of a '
        f'{from_unit}, so a price per {to_unit} is a price per '
        f'{from_unit} x {factor:.8g}',
        f'unit factor = {factor:.10g}']}


def unit_factor(from_unit, to_unit):
    """What to multiply a price PER `from_unit` by to get the price PER
    `to_unit`. None when the two cannot be compared.

        USD/oz -> USD/10g : x 10 / 31.1035  = 0.32151
        USD/oz -> USD/kg  : x 1000 / 31.1035 = 32.1507
        USD/bbl -> USD/bbl: x 1

    A price per unit scales with the SIZE of the unit, which is why
    this is to/from and not the other way round: ten grams cost about a
    third of an ounce.
    """
    a = UNITS.get(_unit(from_unit))
    b = UNITS.get(_unit(to_unit))
    if a is None or b is None or a[0] != b[0]:
        return None
    return b[1] / a[1]


def _unit(value):
    return str(value or '').strip().lower().replace(' ', '')


class Spec:
    """One exchange contract, as the exchange publishes it."""

    def __init__(self, root, exchange, currency, quote_unit, lot_quantity,
                 lot_unit, tick, description):
        self.root = root
        self.exchange = exchange
        self.currency = currency
        #: The unit the PRICE is per — 10g for MCX gold, oz for COMEX.
        self.quote_unit = quote_unit
        #: How much of the commodity one lot delivers, in `lot_unit`.
        self.lot_quantity = float(lot_quantity)
        self.lot_unit = lot_unit
        self.tick = tick
        self.description = description

    @property
    def contract_size(self):
        """Price units per lot: what one lot gains on a 1.00 move in
        the quoted price, in the quote currency. MCX GOLD is 1 kg a lot
        priced per 10 g, so a 1-rupee move is 100 rupees a lot."""
        factor = unit_factor(self.lot_unit, self.quote_unit)
        # quantity in quote units = quantity in lot units * (lot unit
        # size / quote unit size) = lot_quantity / factor
        return self.lot_quantity / factor if factor else None

    @property
    def lot_in_base(self):
        """The lot in the dimension's base quantity (grams, barrels,
        mmBtu), so two exchanges' lots can be compared directly."""
        dim = UNITS.get(_unit(self.lot_unit))
        return self.lot_quantity * dim[1] if dim else None

    def to_dict(self):
        return {'root': self.root, 'exchange': self.exchange,
                'currency': self.currency, 'quote_unit': self.quote_unit,
                'lot_quantity': self.lot_quantity, 'lot_unit': self.lot_unit,
                'contract_size': self.contract_size, 'tick': self.tick,
                'description': self.description}


#: Published specs as at 2026. VERIFY against the exchange circular
#: before trading size: these are what Diagnose checks the broker's
#: figures against, not what sizes a trade.
SPECS = [
    # --- MCX, rupees ---------------------------------------------------
    Spec('CRUDEOILM', 'MCX', 'INR', 'bbl', 10, 'bbl', 1.0, 'MCX Crude Oil Mini'),
    Spec('CRUDEOIL', 'MCX', 'INR', 'bbl', 100, 'bbl', 1.0, 'MCX Crude Oil'),
    Spec('NATGASMINI', 'MCX', 'INR', 'mmbtu', 250, 'mmbtu', 0.1, 'MCX Natural Gas Mini'),
    Spec('NATURALGAS', 'MCX', 'INR', 'mmbtu', 1250, 'mmbtu', 0.1, 'MCX Natural Gas'),
    Spec('GOLDPETAL', 'MCX', 'INR', 'g', 1, 'g', 1.0, 'MCX Gold Petal'),
    Spec('GOLDGUINEA', 'MCX', 'INR', '8g', 8, 'g', 1.0, 'MCX Gold Guinea'),
    Spec('GOLDTEN', 'MCX', 'INR', '10g', 10, 'g', 1.0, 'MCX Gold Ten'),
    Spec('GOLDM', 'MCX', 'INR', '10g', 100, 'g', 1.0, 'MCX Gold Mini'),
    Spec('GOLD', 'MCX', 'INR', '10g', 1, 'kg', 1.0, 'MCX Gold'),
    # 100 g, quoted per 10 g like gold — NOT per kg like the other
    # silvers (launched 1 June 2026). Without its own row, longest-root
    # matching handed SILVER100 to SILVER's 30 kg spec: money out 3x and
    # the unit factor out 100x.
    Spec('SILVER100', 'MCX', 'INR', '10g', 100, 'g', 1.0, 'MCX Silver 100'),
    Spec('SILVERMIC', 'MCX', 'INR', 'kg', 1, 'kg', 1.0, 'MCX Silver Micro'),
    Spec('SILVERM', 'MCX', 'INR', 'kg', 5, 'kg', 1.0, 'MCX Silver Mini'),
    Spec('SILVER', 'MCX', 'INR', 'kg', 30, 'kg', 1.0, 'MCX Silver'),
    Spec('COPPER', 'MCX', 'INR', 'kg', 2500, 'kg', 0.05, 'MCX Copper'),
    # --- CME group, dollars ---------------------------------------------
    Spec('MCL', 'NYMEX', 'USD', 'bbl', 100, 'bbl', 0.01, 'Micro WTI Crude'),
    Spec('QM', 'NYMEX', 'USD', 'bbl', 500, 'bbl', 0.025, 'E-mini WTI Crude'),
    Spec('CL', 'NYMEX', 'USD', 'bbl', 1000, 'bbl', 0.01, 'WTI Crude Oil'),
    Spec('BZ', 'NYMEX', 'USD', 'bbl', 1000, 'bbl', 0.01, 'Brent Crude (last day)'),
    Spec('QG', 'NYMEX', 'USD', 'mmbtu', 2500, 'mmbtu', 0.005, 'E-mini Natural Gas'),
    Spec('NG', 'NYMEX', 'USD', 'mmbtu', 10000, 'mmbtu', 0.001, 'Henry Hub Natural Gas'),
    Spec('MGC', 'COMEX', 'USD', 'oz', 10, 'oz', 0.1, 'Micro Gold'),
    Spec('GC', 'COMEX', 'USD', 'oz', 100, 'oz', 0.1, 'Gold'),
    Spec('SIL', 'COMEX', 'USD', 'oz', 1000, 'oz', 0.005, 'Micro Silver'),
    Spec('SI', 'COMEX', 'USD', 'oz', 5000, 'oz', 0.005, 'Silver'),
    Spec('MHG', 'COMEX', 'USD', 'lb', 2500, 'lb', 0.0005, 'Micro Copper'),
    Spec('HG', 'COMEX', 'USD', 'lb', 25000, 'lb', 0.0005, 'Copper'),
]

#: Spot / CFD names an MT5 broker commonly uses for the same commodity.
#: Their CONTRACT SIZE is the broker's choice (XAUUSD is 100 oz at most
#: brokers, 1 oz at some), so only the quote unit is taken from here.
CFD_QUOTES = {
    'XAUUSD': ('USD', 'oz', 'Gold spot'),
    'GOLD': ('USD', 'oz', 'Gold spot'),
    'XAGUSD': ('USD', 'oz', 'Silver spot'),
    'SILVER': ('USD', 'oz', 'Silver spot'),
    'USOIL': ('USD', 'bbl', 'WTI crude CFD'),
    'WTI': ('USD', 'bbl', 'WTI crude CFD'),
    'XTIUSD': ('USD', 'bbl', 'WTI crude CFD'),
    'UKOIL': ('USD', 'bbl', 'Brent crude CFD'),
    'BRENT': ('USD', 'bbl', 'Brent crude CFD'),
    'XBRUSD': ('USD', 'bbl', 'Brent crude CFD'),
    'XNGUSD': ('USD', 'mmbtu', 'Natural gas CFD'),
    'NATGAS': ('USD', 'mmbtu', 'Natural gas CFD'),
    'COPPER': ('USD', 'lb', 'Copper CFD'),
    'XCUUSD': ('USD', 'lb', 'Copper CFD'),
}

_MCX_BY_LENGTH = sorted((s for s in SPECS if s.exchange == 'MCX'),
                        key=lambda s: -len(s.root))
_CME_BY_LENGTH = sorted((s for s in SPECS if s.exchange != 'MCX'),
                        key=lambda s: -len(s.root))

#: A CME root followed by what brokers put after it: a month code and
#: a year (CLZ5, CLZ25), a month and year (CL-DEC25, CL_Z25), digits
#: (GC1226), or a dot suffix (CL.f). The root must be followed by one
#: of those, or "CL" would match "CLOUD".
_CME_TAIL = re.compile(r'^[-_.\s]?([FGHJKMNQUVXZ]\d{1,2}|\d{2,4}|'
                       r'(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)'
                       r'[-_]?\d{2,4}|\.[A-Z]+|[-_][A-Z0-9]+)?$')


def spec_for(symbol, exchange=None):
    """The exchange spec this symbol trades, or None.

    MCX trading symbols are regular (`CRUDEOIL25NOVFUT`, `GOLDM05DEC25FUT`)
    so their root is reliable — longest root first, or GOLD would claim
    GOLDM's symbol. CME names on an MT5 broker are whatever the broker
    chose, so a match there is a GUESS and the caller labels it one.
    """
    text = str(symbol or '').strip().upper()
    if not text:
        return None
    if exchange is None or str(exchange).upper() == 'MCX':
        for spec in _MCX_BY_LENGTH:
            if text.startswith(spec.root) and \
                    re.match(r'^\d', text[len(spec.root):] or '0'):
                return spec
        if exchange is not None:
            return None
    for spec in _CME_BY_LENGTH:
        if text.startswith(spec.root) and \
                _CME_TAIL.match(text[len(spec.root):]):
            return spec
    return None


def quote_for(symbol, exchange=None):
    """(currency, quote_unit, description) this symbol is PRICED in, or
    None. A futures spec where one matches, else a known CFD name."""
    spec = spec_for(symbol, exchange)
    if spec is not None:
        return spec.currency, spec.quote_unit, spec.description
    text = str(symbol or '').strip().upper()
    for name, quote in sorted(CFD_QUOTES.items(), key=lambda kv: -len(kv[0])):
        if text.startswith(name):
            return quote
    return None


def suggest_conversion(symbol_a, symbol_b, exchange_b='MCX'):
    """How leg A's price has to be restated to be subtracted from leg B's.

    Returns a dict for the pair's `leg_a`:

        {'fx': 'USDINR', 'price_factor': 0.32150747, 'why': '...'}

    or one with `fx`/`price_factor` None and a `why` that says what
    could not be matched. The result is OFFERED on the Exchanges page,
    never applied silently — a unit factor somebody did not look at is
    a spread nobody can check.
    """
    qa = quote_for(symbol_a)
    qb = quote_for(symbol_b, exchange_b)
    if qa is None or qb is None:
        missing = symbol_a if qa is None else symbol_b
        return {'fx': None, 'price_factor': None,
                'why': f'{missing} is not a contract this terminal knows '
                       f'the quote unit of — set the unit factor by hand'}
    cur_a, unit_a, _ = qa
    cur_b, unit_b, _ = qb
    factor = unit_factor(unit_a, unit_b)
    if factor is None:
        return {'fx': None, 'price_factor': None,
                'why': f'{symbol_a} is priced per {unit_a} and {symbol_b} '
                       f'per {unit_b} — those do not convert'}
    fx = None if cur_a == cur_b else f'{cur_a}{cur_b}'
    return {'fx': fx, 'price_factor': round(factor, 10),
            'unit_a': unit_a, 'unit_b': unit_b,
            'why': (f'A is {cur_a}/{unit_a}, B is {cur_b}/{unit_b}: '
                    f'A x {fx + " x " if fx else ""}{factor:.8g} '
                    f'is A in B\'s {cur_b}/{unit_b}')}


def lot_match(spec_a, spec_b, lots_a=1.0, lots_b=1.0):
    """How well `lots_a` of A covers `lots_b` of B in the commodity
    itself, and the clip that would cover it best.

    One CL is 1,000 barrels and one MCX CRUDEOIL is 100, so 1:10 is
    exact; one GC is 3.11 kg against a 1 kg MCX GOLD, so no small whole
    clip is exact and the residual is said out loud — that residual is
    an OUTRIGHT position in the metal, not part of the spread.
    """
    base_a = spec_a.lot_in_base if spec_a else None
    base_b = spec_b.lot_in_base if spec_b else None
    if not base_a or not base_b or \
            UNITS[_unit(spec_a.lot_unit)][0] != UNITS[_unit(spec_b.lot_unit)][0]:
        return None
    qty_a = lots_a * base_a
    qty_b = lots_b * base_b
    mismatch = (qty_b - qty_a) / qty_a if qty_a else None
    best = None
    for la in range(1, 11):
        lb = round(la * base_a / base_b)
        if lb < 1:
            continue
        err = abs(lb * base_b - la * base_a) / (la * base_a)
        if best is None or err < best[2] - 1e-12:
            best = (la, lb, err)
    return {'qty_a': qty_a, 'qty_b': qty_b,
            'base_unit': {'mass': 'g', 'volume': 'bbl',
                          'energy': 'mmBtu'}[UNITS[_unit(spec_a.lot_unit)][0]],
            'mismatch_pct': None if mismatch is None else 100.0 * mismatch,
            'best_clip_a': best[0] if best else None,
            'best_clip_b': best[1] if best else None,
            'best_mismatch_pct': 100.0 * best[2] if best else None}


# --- expiries ----------------------------------------------------------

_MONTHS = {m: i for i, m in enumerate(
    ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN',
     'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC'], start=1)}


def parse_expiry(value):
    """An expiry as a UTC epoch, or None. Accepts what the two brokers
    actually send: MT5's epoch seconds, Arrow's `30-Jun-2026`, ISO
    dates, `19NOV25`, and millisecond epochs. 0 and '' are "none" —
    MT5 reports 0 for a symbol that does not expire."""
    if value in (None, '', 0, '0'):
        return None
    if isinstance(value, (int, float)) or str(value).strip().isdigit():
        v = float(value)
        if v > 1e11:
            v /= 1000.0
        return v if v > 0 else None
    s = str(value).strip().upper()
    m = re.search(r'(\d{4})-(\d{1,2})-(\d{1,2})', s)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    else:
        m = re.search(r'(\d{1,2})[-\s]?(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP'
                      r'|OCT|NOV|DEC)[A-Z]*[-\s]?(\d{2,4})', s)
        if not m:
            return None
        d, mo, y = int(m.group(1)), _MONTHS[m.group(2)], int(m.group(3))
        y += 2000 if y < 100 else 0
    try:
        return datetime(y, mo, d, tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None


def expiry_watch(expiry_a, expiry_b, warn_days=5.0, gap_days=3.0,
                 now=None):
    """The two legs' expiries side by side, and what is wrong with them.

    A cross-exchange spread is two DIFFERENT contracts with two
    different last days. MCX crude, gold and silver each expire on
    their own calendar, a few days either side of the CME month they
    track — so a pair can be one leg short of a hedge for those days,
    and a leg can go into its delivery period while the other trades
    on. Unmeasured is not zero: an expiry nobody could read is None
    and is said to be unknown, not taken to be far away.
    """
    now = time_mod.time() if now is None else now
    ea, eb = parse_expiry(expiry_a), parse_expiry(expiry_b)

    def days(e):
        return None if e is None else (e - now) / 86400.0

    da, db = days(ea), days(eb)
    warnings = []
    for label, d in (('Leg A', da), ('Leg B', db)):
        if d is None:
            continue
        if d < 0:
            warnings.append(f'{label} has EXPIRED ({-d:.1f} days ago) — '
                            f'roll it before trading')
        elif d <= warn_days:
            warnings.append(f'{label} expires in {d:.1f} days')
    gap = None
    if ea is not None and eb is not None:
        gap = (eb - ea) / 86400.0
        if abs(gap) >= gap_days:
            first = 'Leg A' if gap > 0 else 'Leg B'
            warnings.append(f'the legs expire {abs(gap):.0f} days apart — '
                            f'{first} goes first and the other leg is '
                            f'unhedged from then on')
    unknown = [label for label, e in (('Leg A', ea), ('Leg B', eb))
               if e is None]
    return {
        'expiry_a': ea, 'expiry_b': eb,
        'days_a': None if da is None else round(da, 2),
        'days_b': None if db is None else round(db, 2),
        'gap_days': None if gap is None else round(gap, 2),
        'unknown': unknown,
        'warnings': warnings,
        'level': ('alert' if any('EXPIRED' in w for w in warnings) else
                  'warn' if warnings else 'ok'),
    }


def fmt_expiry(epoch):
    if epoch is None:
        return '—'
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime('%d-%b-%Y')


CURRENCY_SIGNS = {'USD': '$', 'INR': '\u20b9', 'EUR': '\u20ac',
                  'GBP': '\u00a3', 'JPY': '\u00a5'}


def money_text(value, currency=None, digits=2):
    """A money figure in ITS OWN currency: Rs. for a rupee leg, $ for a
    dollar one, the code for anything else. Unknown currency is written
    as dollars only because that is what the single-currency terminal
    always meant by it."""
    code = (currency or 'USD').upper()
    mark = CURRENCY_SIGNS.get(code, code + ' ')
    sign = '-' if value < 0 else ''
    return f'{sign}{mark}{abs(value):,.{digits}f}'


def is_finite(value):
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False
