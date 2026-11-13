"""The recent life of each spread, for the live chart windows.

arrow-statarb has a window of live graphs — the spread and its z-score
over the session — and so does this terminal, one per ladder. This is
where the series comes from: the coordinator samples every pair's
snapshot at most once a second and keeps the last hour, and the web
process serves it to whichever chart windows are open.

It is a picture, NOT an input. Nothing reads it to decide anything: no
signal, no entry, no exit. The z-score is drawn because a trader reads
a spread against its own range, and the bands are labelled in sigmas,
never as entry or stop levels.

UNMEASURED IS NOT ZERO. A pair with no price this second gets a GAP in
its series (a point with spread None), not a zero and not a straight
line drawn across the hole — a chart that bridges a dark minute shows a
market that did not happen.
"""

import collections
import time as time_mod

#: Seconds between samples, and how many are kept: an hour at one a
#: second, which is what a chart window can usefully show at its width.
SAMPLE_SEC = 1.0
KEEP = 3600


def _r(value, digits=6):
    if value is None:
        return None
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None


class SpreadHistory:
    def __init__(self, sample_sec=SAMPLE_SEC, keep=KEEP,
                 clock=time_mod.time):
        self.sample_sec = float(sample_sec)
        self.keep = int(keep)
        self.clock = clock
        self._series = {}
        self._last = {}

    def observe(self, key, md, now=None):
        """Record this pair's snapshot if a sample is due. `md` None is
        a dark pair, recorded as a gap."""
        now = self.clock() if now is None else now
        last = self._last.get(key)
        if last is not None and now - last < self.sample_sec:
            return False
        self._last[key] = now
        series = self._series.get(key)
        if series is None:
            series = self._series[key] = collections.deque(maxlen=self.keep)
        md = md or {}
        series.append({
            't': round(now, 3),
            'spread': _r(md.get('spread')),
            # The EXECUTABLE band: buying the spread pays `long`,
            # selling it gets `short`. The mid sits between them.
            'long': _r(md.get('long_spread')),
            'short': _r(md.get('short_spread')),
            'a': _r(md.get('leg_a_mid')),
            'b': _r(md.get('leg_b_mid')),
            'a_raw': _r(None if md.get('leg_a_raw_bid') is None
                        or md.get('leg_a_raw_ask') is None
                        else (md['leg_a_raw_bid'] + md['leg_a_raw_ask']) / 2),
            'fx': _r(md.get('leg_a_fx_rate')),
            'sigma': _r(md.get('spread_sigma')),
            'guard': bool(md.get('guard_reason')),
        })
        return True

    def forget(self, key):
        self._series.pop(key, None)
        self._last.pop(key, None)

    def series(self, key, since=None):
        rows = list(self._series.get(key) or ())
        if since is not None:
            rows = [r for r in rows if r['t'] > since]
        return rows

    def payload(self):
        return {'at': self.clock(), 'sample_sec': self.sample_sec,
                'pairs': {k: list(v) for k, v in self._series.items()}}


def with_zscore(rows, window=300):
    """Each point's distance from the rolling mean in rolling sigmas,
    over the last `window` points that HAD a price. None until there are
    30 of them — a z-score of a dozen points is noise with a decimal
    point, and unmeasured is not zero."""
    out = []
    recent = collections.deque(maxlen=int(window))
    for row in rows:
        row = dict(row)
        spread = row.get('spread')
        if spread is not None:
            recent.append(spread)
        n = len(recent)
        if spread is None or n < 30:
            row['mean'] = row['z'] = None
        else:
            mean = sum(recent) / n
            var = sum((v - mean) ** 2 for v in recent) / (n - 1)
            sd = var ** 0.5
            row['mean'] = round(mean, 6)
            row['sd'] = round(sd, 6)
            row['z'] = round((spread - mean) / sd, 4) if sd > 0 else None
        out.append(row)
    return out
