"""The arbitrage alert: when it speaks, when it must not, and that it
never sends an order — each refusal with the control that proves the
test is the thing withholding the alert."""

import json
import logging

import pytest

from conftest import FakeBroker, FakeLeg, FakeSymbol
from mt5trader.arbalert import ArbWatch, last_z
from mt5trader.config import PairConfig, TraderConfig
from mt5trader.convert import ConvertedLeg, conversions_from_config
from mt5trader.coordinator import Coordinator
from mt5trader.fx import FxBook, manual_entry
from mt5trader.notify import TelegramNotifier


def crude(**arb):
    settings = dict(arb_alert=True, arb_margin=10.0, arb_z=2.0)
    settings.update(arb)
    return PairConfig('CL|CRUDE', name='Crude', **settings)


def md(short, long_, mid_b=8343.0, guard=None):
    return {'spread': (short + long_) / 2, 'short_spread': short,
            'long_spread': long_, 'leg_b_mid': mid_b, 'guard_reason': guard,
            'leg_a_bid': 88.39, 'leg_a_ask': 88.41, 'leg_b_bid': 8342.0,
            'leg_b_ask': 8344.0, 'leg_a_fx_rate': 94.30}


def quiet_then(spread, n=60):
    """A quiet history around 0, then `spread` — z far out."""
    rows = [{'t': i, 'spread': (i % 5) - 2.0} for i in range(n)]
    rows.append({'t': n, 'spread': spread})
    return rows


def test_a_wide_rich_gap_alerts_with_its_numbers():
    watch = ArbWatch(clock=lambda: 1000.0)
    status, event = watch.evaluate(crude(), md(25.0, 28.0), history=quiet_then(26.5),
                                   costs_per_unit=3.0)
    assert status['active'] and event is not None
    assert event['direction'] == 'MCX_RICH'
    assert event['edge'] == pytest.approx(25.0 - 0.0 - 3.0)
    assert 'SELL leg B' in event['text'] and 'nothing was sent' in event['text']


def test_a_cheap_gap_alerts_the_other_way():
    watch = ArbWatch()
    status, event = watch.evaluate(crude(), md(-30.0, -27.0),
                                   history=quiet_then(-28.5))
    assert event['direction'] == 'MCX_CHEAP'
    assert event['edge'] == pytest.approx(27.0)
    assert 'BUY leg B' in event['text']


def test_an_edge_under_the_margin_does_not_alert():
    watch = ArbWatch()
    status, event = watch.evaluate(crude(arb_margin=10.0), md(8.0, 11.0),
                                   history=quiet_then(9.5))
    assert event is None and 'under the' in status['why_not']


def test_control_a_lower_margin_lets_the_same_gap_alert():
    watch = ArbWatch()
    _status, event = watch.evaluate(crude(arb_margin=5.0), md(8.0, 11.0),
                                    history=quiet_then(9.5))
    assert event is not None


def test_a_gap_that_is_normal_for_its_history_does_not_alert():
    """Wide, but it has been this wide all along: z is small."""
    watch = ArbWatch()
    steady = [{'t': i, 'spread': 25.0 + (i % 5) * 0.5} for i in range(60)]
    status, event = watch.evaluate(crude(), md(25.0, 28.0), history=steady)
    assert event is None and 'inside' in status['why_not']


def test_control_with_the_sigma_test_off_the_same_gap_alerts():
    watch = ArbWatch()
    steady = [{'t': i, 'spread': 25.0 + (i % 5) * 0.5} for i in range(60)]
    _status, event = watch.evaluate(crude(arb_z=0), md(25.0, 28.0),
                                    history=steady)
    assert event is not None


def test_no_history_yet_is_not_unusual():
    watch = ArbWatch()
    status, event = watch.evaluate(crude(), md(25.0, 28.0), history=[])
    assert event is None and 'not enough history' in status['why_not']


def test_a_stale_or_desynced_price_never_alerts():
    """A lagging leg is exactly how a FAKE gap appears."""
    watch = ArbWatch()
    status, event = watch.evaluate(
        crude(), md(25.0, 28.0, guard="Leg A's quote has not moved"),
        history=quiet_then(26.5))
    assert event is None and 'not trusted' in status['why_not']


def test_control_the_same_gap_on_a_trusted_price_alerts():
    watch = ArbWatch()
    _status, event = watch.evaluate(crude(), md(25.0, 28.0),
                                    history=quiet_then(26.5))
    assert event is not None


def test_the_normal_gap_is_taken_off_first():
    """MCX gold sits ~6% above COMEX: a 6% gap is normal, not an edge."""
    watch = ArbWatch()
    gold = crude(arb_normal_pct=6.0, arb_margin=100.0, arb_z=0)
    status, event = watch.evaluate(gold, md(7260.0, 7290.0, mid_b=121000.0))
    assert event is None
    assert status['normal'] == pytest.approx(7260.0)
    assert status['edge'] == pytest.approx(0.0)
    _s, event = watch.evaluate(gold, md(7500.0, 7530.0, mid_b=121000.0))
    assert event is not None and event['edge'] == pytest.approx(240.0)


def test_one_alert_while_it_lasts_and_again_only_after_it_has_gone():
    watch = ArbWatch()
    pair = crude(arb_z=0)
    assert watch.evaluate(pair, md(25.0, 28.0))[1] is not None
    assert watch.evaluate(pair, md(26.0, 29.0))[1] is None      # still on
    assert watch.evaluate(pair, md(8.0, 11.0))[1] is None       # 8 > 5: armed off
    assert watch.evaluate(pair, md(25.0, 28.0))[1] is None      # not re-armed
    assert watch.evaluate(pair, md(2.0, 5.0))[1] is None        # under half
    assert watch.evaluate(pair, md(25.0, 28.0))[1] is not None  # again


def test_a_pair_with_the_alert_off_says_nothing():
    watch = ArbWatch()
    status, event = watch.evaluate(crude(arb_alert=False), md(500.0, 503.0))
    assert event is None and status == {'enabled': False}


def test_no_margin_is_not_a_margin_of_zero():
    watch = ArbWatch()
    status, event = watch.evaluate(crude(arb_margin=None, arb_z=0),
                                   md(25.0, 28.0))
    assert event is None and 'no margin set' in status['why_not']


def test_last_z_is_unmeasured_until_thirty_prices():
    assert last_z([{'spread': 1.0}] * 10) is None
    assert last_z(quiet_then(26.5)) > 5


# --- the engine: it alerts, and it never orders --------------------------

class Phone:
    def __init__(self):
        self.messages = []

    def send(self, text):
        self.messages.append(text)
        return True


@pytest.fixture
def engine(timeline):
    now = [1_700_000_000.0]
    clock = lambda: now[0]
    cme = FakeBroker('cme', [FakeSymbol('CLZ6', 88.39, 88.41,
                                        contract_size=1000.0, volume_min=1.0,
                                        volume_step=1.0)], timeline)
    mcx = FakeBroker('mcx', [FakeSymbol('CRUDEOIL19NOV26FUT', 8336.0, 8338.0,
                                        contract_size=100.0, volume_min=1.0,
                                        volume_step=1.0, tick_size=1.0)],
                     timeline)
    pair = PairConfig('CLZ6|CRUDEOIL19NOV26FUT', name='Crude',
                      leg_a={'account': 'cme', 'symbol': 'CLZ6',
                             'fx': 'USDINR', 'price_factor': 1.0},
                      leg_b={'account': 'mcx', 'symbol': 'CRUDEOIL19NOV26FUT'},
                      hedge_ratio=1.0, hedge_ratio_for='CLZ6|CRUDEOIL19NOV26FUT',
                      clip_lots_a=1.0, clip_lots_b=10.0,
                      arb_alert=True, arb_margin=10.0, arb_z=0)
    config = TraderConfig(pairs={pair.key: pair})
    config.settings['AUTO_REFRESH_STALE_SEC'] = 0
    book = FxBook(raw={'USDINR': manual_entry(94.30, clock=clock)},
                  clock=clock)
    convs, _ = conversions_from_config(config)
    legs = {'cme': ConvertedLeg(FakeLeg(cme), convs['cme'], book),
            'mcx': FakeLeg(mcx)}
    coordinator = Coordinator(config, legs, clock=clock,
                              sleep=lambda s: None)
    coordinator.notifier = Phone()
    coordinator.resolve_symbols()
    return coordinator, pair, cme, mcx, now


def test_the_engine_alerts_on_a_wide_gap_and_sends_no_order(engine, caplog):
    coordinator, pair, cme, mcx, now = engine
    coordinator.poll_once()
    assert coordinator.market[pair.key]['arb']['active'] is False
    # MCX jumps rich: bid 8,380 against CL ask 88.41 x 94.30 = 8,337.06.
    mcx.symbols['CRUDEOIL19NOV26FUT'].quote(8380.0, 8382.0)
    now[0] += 1
    with caplog.at_level(logging.WARNING):
        coordinator.poll_once()
    arb = coordinator.market[pair.key]['arb']
    assert arb['active'] and arb['direction'] == 'MCX_RICH'
    assert len(coordinator.notifier.messages) == 1
    assert 'MCX RICH' in coordinator.notifier.messages[0]
    assert any('ARBITRAGE ALERT' in r.message for r in caplog.records)
    snap = coordinator.snapshot()
    assert snap['arb_events'][-1]['pair'] == pair.key
    # AN ALERT, NEVER AN ORDER.
    assert cme.sent == [] and mcx.sent == []
    assert coordinator.book.positions() == []


def test_control_with_the_alert_off_the_engine_says_nothing(engine):
    coordinator, pair, cme, mcx, now = engine
    pair.arb_alert = False
    mcx.symbols['CRUDEOIL19NOV26FUT'].quote(8380.0, 8382.0)
    coordinator.poll_once()
    assert coordinator.notifier.messages == []


# --- Telegram ----------------------------------------------------------------

class Reply:
    def __init__(self, body):
        self.body = body

    def read(self):
        return json.dumps(self.body).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_telegram_unset_is_quiet_and_sends_nothing():
    sender = TelegramNotifier(token='', chat_id='')
    assert not sender.configured
    assert sender.send('x') is False


def test_telegram_delivers_and_rate_limits():
    calls = []
    clock = [0.0]

    def opener(request, timeout):
        calls.append(json.loads(request.data))
        return Reply({'ok': True})
    sender = TelegramNotifier(token='T0K3N', chat_id='42', opener=opener,
                              clock=lambda: clock[0], background=False)
    assert sender.send('one') is True
    assert sender.send('two') is False           # inside the gap
    clock[0] += 5
    assert sender.send('three') is True
    assert [c['text'] for c in calls] == ['one', 'three']
    assert calls[0]['chat_id'] == '42'


def test_a_telegram_failure_never_leaks_the_token(caplog):
    import urllib.error

    def opener(request, timeout):
        raise urllib.error.URLError(f'refused by {request.full_url}')
    sender = TelegramNotifier(token='S3CR3T-TOKEN', chat_id='42',
                              opener=opener, background=False)
    with caplog.at_level(logging.WARNING):
        assert sender.send('x') is False
    assert 'S3CR3T-TOKEN' not in caplog.text
    assert 'S3CR3T-TOKEN' not in (sender.last_error or '')


# --- the USDINR hedge, in the cost --------------------------------------------

def hedged(cost_per_lot=None, lot_usd=None):
    pair = PairConfig('CL|CRUDE', name='Crude',
                      leg_a={'account': 'cme', 'symbol': 'CLZ6',
                             'fx': 'USDINR', 'price_factor': 1.0},
                      clip_lots_a=1.0, clip_lots_b=10.0,
                      fx_hedge_cost_per_lot=cost_per_lot,
                      fx_hedge_lot_usd=lot_usd)
    return pair


def test_the_hedge_is_sized_from_the_dollar_legs_notional():
    """1 CL at $88.40 is $88,400: 88 NSE lots of $1,000."""
    from mt5trader.arbalert import fx_hedge
    md_ = {'leg_a_raw_bid': 88.39, 'leg_a_raw_ask': 88.41}
    hedge = fx_hedge(hedged(cost_per_lot=40.0), md_,
                     {'contract_size': 1000.0, 'contract_size_raw': 1000.0,
                      'contract_size_mt5': 1000.0})
    assert hedge['notional_usd'] == pytest.approx(88400.0)
    assert hedge['lots'] == 88
    assert hedge['cost'] == pytest.approx(88 * 40.0)


def test_an_uncosted_hedge_is_unknown_not_free():
    from mt5trader.arbalert import fx_hedge
    hedge = fx_hedge(hedged(), {'leg_a_raw_bid': 88.39, 'leg_a_raw_ask': 88.41},
                     {'contract_size': 1000.0, 'contract_size_raw': 1000.0,
                      'contract_size_mt5': 1000.0})
    assert hedge['lots'] == 88 and hedge['cost'] is None


def test_a_pair_with_no_fx_needs_no_hedge():
    from mt5trader.arbalert import fx_hedge
    pair = PairConfig('X|Y', leg_a={'account': 'a', 'symbol': 'X'})
    assert fx_hedge(pair, {}, {})['needed'] is False


def test_the_hedge_cost_can_be_what_stops_an_alert(engine):
    """42.94 gap - 1.94 exit crossing = 41.00; Rs 130 x 88 lots of hedge
    on 1,000 bbl is 11.44 more, leaving 29.56 — under the 30 margin."""
    coordinator, pair, cme, mcx, now = engine
    pair.arb_margin = 30.0
    # Gap: MCX bid 8,380 - CL ask 88.41 x 94.30 = 42.94/bbl before costs.
    mcx.symbols['CRUDEOIL19NOV26FUT'].quote(8380.0, 8382.0)
    pair.fx_hedge_cost_per_lot = 130.0        # 88 lots x 130 = 11,440
    coordinator.poll_once()
    arb = coordinator.market[pair.key]['arb']
    assert arb['hedge_lots'] == 88
    assert arb['hedge_per_unit'] == pytest.approx(11.44)
    assert arb['edge'] == pytest.approx(29.56, abs=0.01)
    assert arb['active'] is False and coordinator.notifier.messages == []


def test_control_without_the_hedge_cost_the_same_gap_alerts(engine):
    coordinator, pair, cme, mcx, now = engine
    pair.arb_margin = 30.0
    mcx.symbols['CRUDEOIL19NOV26FUT'].quote(8380.0, 8382.0)
    pair.fx_hedge_cost_per_lot = 0.0
    coordinator.poll_once()
    assert coordinator.market[pair.key]['arb']['active'] is True
    text = coordinator.notifier.messages[-1]
    assert 'BUY 88 USDINR lots' in text and 'nothing was sent' in text


def test_alert_ids_keep_rising_across_an_engine_restart():
    """A page that saw alert N must not ignore the restarted engine's."""
    before = ArbWatch(clock=lambda: 1_700_000_000.0)
    _s, first = before.evaluate(crude(arb_z=0), md(25.0, 28.0))
    after_restart = ArbWatch(clock=lambda: 1_700_000_060.0)
    _s, second = after_restart.evaluate(crude(arb_z=0), md(25.0, 28.0))
    assert second['id'] > first['id']
