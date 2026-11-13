"""A dollar leg against a rupee leg: units, the exchange rate, and the
guard the rate adds — with the controls that prove each one is the
thing doing the work.
"""

import json
import time

import pytest

from conftest import FakeBroker, FakeLeg, FakeSymbol
from mt5trader import contracts
from mt5trader.config import PairConfig, TraderConfig
from mt5trader.convert import (ConvertedLeg, Conversion,
                               conversions_from_config)
from mt5trader.coordinator import Coordinator
from mt5trader.executor import PairExecutor
from mt5trader.fx import FxBook, manual_entry
from mt5trader.models import SpreadSide


# --- units and contracts ---------------------------------------------------

def test_a_price_per_ounce_becomes_a_price_per_ten_grams_and_per_kilo():
    assert contracts.unit_factor('oz', '10g') == pytest.approx(0.3215074657)
    assert contracts.unit_factor('oz', 'kg') == pytest.approx(32.150746568)
    assert contracts.unit_factor('bbl', 'bbl') == 1.0
    # Different dimensions do not convert, and say so with None.
    assert contracts.unit_factor('oz', 'bbl') is None


def test_mcx_roots_are_matched_longest_first():
    """GOLD must not claim GOLDM's symbol, nor SILVER SILVERMIC's."""
    assert contracts.spec_for('GOLDM05DEC25FUT', 'MCX').root == 'GOLDM'
    assert contracts.spec_for('GOLD05DEC25FUT', 'MCX').root == 'GOLD'
    assert contracts.spec_for('SILVERMIC28NOV25FUT', 'MCX').root == 'SILVERMIC'
    assert contracts.spec_for('CRUDEOIL19NOV25FUT', 'MCX').root == 'CRUDEOIL'
    assert contracts.spec_for('CRUDEOILM19NOV25FUT', 'MCX').root == 'CRUDEOILM'


def test_cme_names_are_recognised_but_words_that_start_alike_are_not():
    assert contracts.spec_for('CLZ5').root == 'CL'
    assert contracts.spec_for('CL-DEC25').root == 'CL'
    assert contracts.spec_for('GC1226').root == 'GC'
    assert contracts.spec_for('MGCZ25').root == 'MGC'
    assert contracts.spec_for('CLOUD') is None


def test_contract_size_is_money_per_point_of_the_QUOTED_price():
    """MCX gold is a 1 kg lot priced per 10 g: a rupee move is 100
    rupees a lot. Silver is 30 kg priced per kg: 30."""
    by_root = {s.root: s for s in contracts.SPECS}
    assert by_root['GOLD'].contract_size == pytest.approx(100.0)
    assert by_root['GOLDM'].contract_size == pytest.approx(10.0)
    assert by_root['SILVER'].contract_size == pytest.approx(30.0)
    assert by_root['CRUDEOIL'].contract_size == pytest.approx(100.0)
    assert by_root['GC'].contract_size == pytest.approx(100.0)
    assert by_root['CL'].contract_size == pytest.approx(1000.0)


def test_lots_are_compared_in_the_commodity_itself():
    by_root = {s.root: s for s in contracts.SPECS}
    oil = contracts.lot_match(by_root['CL'], by_root['CRUDEOIL'], 1, 10)
    assert oil['mismatch_pct'] == pytest.approx(0.0)
    assert (oil['best_clip_a'], oil['best_clip_b']) == (1, 10)
    gold = contracts.lot_match(by_root['GC'], by_root['GOLD'], 1, 3)
    # 100 oz is 3.11 kg: three MCX lots leave 3.5% of an outright.
    assert gold['mismatch_pct'] == pytest.approx(-3.5, abs=0.1)


def test_the_suggested_conversion_for_each_cross_exchange_pair():
    gold = contracts.suggest_conversion('GC1226', 'GOLD05DEC25FUT')
    assert gold['fx'] == 'USDINR'
    assert gold['price_factor'] == pytest.approx(0.3215074657)
    silver = contracts.suggest_conversion('SIZ5', 'SILVER05DEC25FUT')
    assert silver['price_factor'] == pytest.approx(32.150746568)
    oil = contracts.suggest_conversion('CLZ5', 'CRUDEOIL19NOV25FUT')
    assert (oil['fx'], oil['price_factor']) == ('USDINR', 1.0)
    # Same currency, same unit: nothing to convert.
    same = contracts.suggest_conversion('CLZ5', 'CLF6', exchange_b=None)
    assert same['fx'] is None


def test_expiry_watch_names_a_leg_that_goes_first():
    now = contracts.parse_expiry('2025-11-10')
    watch = contracts.expiry_watch('2025-11-14', '19-Nov-2025', now=now,
                                   warn_days=5, gap_days=3)
    assert watch['days_a'] == pytest.approx(4.0)
    assert any('expires in' in w and 'Leg A' in w for w in watch['warnings'])
    assert any('5 days apart' in w for w in watch['warnings'])
    assert watch['level'] == 'warn'


def test_expiry_watch_control_far_apart_dates_inside_the_limits_are_quiet():
    now = contracts.parse_expiry('2025-11-01')
    watch = contracts.expiry_watch('2025-12-19', '2025-12-19', now=now)
    assert watch['warnings'] == [] and watch['level'] == 'ok'


def test_an_expiry_nobody_could_read_is_unknown_not_far_away():
    watch = contracts.expiry_watch(0, None)
    assert watch['days_a'] is None and watch['days_b'] is None
    assert watch['unknown'] == ['Leg A', 'Leg B']


def test_an_expired_leg_is_an_alert():
    now = contracts.parse_expiry('2025-11-20')
    watch = contracts.expiry_watch('2025-11-19', '2025-12-19', now=now)
    assert watch['level'] == 'alert'


# --- the exchange rate --------------------------------------------------------

class Clock:
    def __init__(self, now=1_700_000_000.0):
        self.now = now

    def __call__(self):
        return self.now


def test_a_fresh_manual_rate_prices_and_is_not_stale():
    clock = Clock()
    book = FxBook(raw={'USDINR': manual_entry(84.25, clock=clock)},
                  clock=clock)
    status = book.status('USDINR')
    assert status['rate'] == 84.25 and status['stale'] is None


def test_an_old_manual_rate_is_still_shown_but_is_stale():
    clock = Clock()
    book = FxBook(raw={'USDINR': manual_entry(84.25, clock=clock)},
                  clock=clock)
    clock.now += 25 * 3600
    status = book.status('USDINR')
    assert status['rate'] == 84.25
    assert 'old' in status['stale']


def test_control_a_rate_limit_of_zero_turns_the_age_guard_off():
    clock = Clock()
    book = FxBook(raw={'USDINR': manual_entry(84.25, clock=clock,
                                              max_age_hours=0)},
                  clock=clock)
    clock.now += 1000 * 3600
    assert book.status('USDINR')['stale'] is None


def test_a_rate_that_was_never_set_is_unknown_not_one():
    book = FxBook(raw={})
    assert book.rate('USDINR') is None
    assert 'no USDINR rate' in book.status('USDINR')['stale']


def test_a_live_rate_is_the_MID_of_the_book_never_the_last_trade():
    class Leg:
        def tick(self, symbol):
            return {'bid': 84.00, 'ask': 84.10, 'last': 90.0}
    book = FxBook(raw={'USDINR': {'source': 'symbol', 'account': 'b',
                                  'symbol': 'USDINRFUT'}},
                  leg_for=lambda name: Leg() if name == 'b' else None)
    assert book.rate('USDINR') == pytest.approx(84.05)


def test_a_live_rate_from_an_unreachable_account_is_unknown():
    book = FxBook(raw={'USDINR': {'source': 'symbol', 'account': 'b',
                                  'symbol': 'USDINRFUT'}},
                  leg_for=lambda name: None)
    assert book.rate('USDINR') is None


def test_a_rate_saved_to_the_file_reaches_a_running_book(tmp_path):
    path = tmp_path / 'config.json'
    path.write_text(json.dumps({'fx': {'USDINR': {'rate': 83.0,
                                                  'set_at': time.time()}}}))
    book = FxBook(config_path=str(path))
    assert book.rate('USDINR') == 83.0
    path.write_text(json.dumps({'fx': {'USDINR': {'rate': 84.5,
                                                  'set_at': time.time()}}}))
    import os
    os.utime(path, (time.time() + 5, time.time() + 5))
    assert book.rate('USDINR') == 84.5


# --- the converted leg --------------------------------------------------------

def oil_leg(rate=84.0, clock=None, symbol='CLZ5'):
    clock = clock or Clock()
    broker = FakeBroker('cme', [FakeSymbol(symbol, 70.00, 70.02,
                                           contract_size=1000.0,
                                           volume_min=1.0, volume_step=1.0)])
    inner = FakeLeg(broker)
    raw = {} if rate is None else {'USDINR': manual_entry(rate, clock=clock)}
    book = FxBook(raw=raw, clock=clock)
    return ConvertedLeg(inner, {symbol: Conversion(symbol, 'USDINR', 1.0)},
                        book), broker, book, clock


def test_a_dollar_tick_is_restated_in_rupees_with_its_own_price_beside_it():
    leg, _broker, _book, _clock = oil_leg(84.0)
    tick = leg.tick('CLZ5')
    assert tick['bid'] == pytest.approx(70.00 * 84.0)
    assert tick['ask'] == pytest.approx(70.02 * 84.0)
    assert tick['raw_bid'] == 70.00 and tick['fx_rate'] == 84.0


def test_an_unknown_rate_is_no_price_at_all():
    leg, _broker, _book, _clock = oil_leg(rate=None)
    assert leg.tick('CLZ5') is None


def test_a_limit_is_sent_in_the_brokers_own_dollars():
    leg, broker, _book, _clock = oil_leg(84.0)
    reply = leg.place_limit('CLZ5', 'BUY', 1.0, 69.50 * 84.0)
    assert reply['ok']
    pending = next(iter(broker.pendings.values()))
    assert pending['price'] == pytest.approx(69.50)


def test_a_limit_cannot_be_priced_without_the_rate():
    leg, broker, book, _clock = oil_leg(84.0)
    book._raw = {}
    reply = leg.place_limit('CLZ5', 'BUY', 1.0, 5838.0)
    assert not reply['ok'] and 'rate' in reply['error']
    assert not broker.pendings


def test_a_CLOSE_goes_without_the_rate_because_a_guard_never_prevents_one():
    leg, broker, book, _clock = oil_leg(84.0)
    opened = leg.order('CLZ5', 'BUY', 1.0)
    assert opened['ok']
    ticket = opened['position_tickets'][0]
    book._raw = {}                         # the rate is now unknown
    closed = leg.close_ticket('CLZ5', ticket, 1.0, 'BUY')
    assert closed['ok']
    assert closed['price'] is None and closed['price_raw']
    assert not broker.positions or all(
        p['volume'] <= 0 for p in broker.positions.values())


def test_contract_size_is_restated_per_converted_unit():
    """100 oz of COMEX gold is 311.03 lots of 10 g: the FX cancels, the
    unit does not."""
    clock = Clock()
    broker = FakeBroker('cme', [FakeSymbol('GCZ5', 4000.0, 4000.5,
                                           contract_size=100.0)])
    leg = ConvertedLeg(FakeLeg(broker),
                       {'GCZ5': Conversion('GCZ5', 'USDINR', 0.3215074657)},
                       FxBook(raw={'USDINR': manual_entry(84.0, clock=clock)},
                              clock=clock))
    report = leg.symbol_report('GCZ5')
    assert report['contract_size'] == pytest.approx(311.034768)
    assert report['contract_size_raw'] == 100.0
    assert report['currency'] == 'INR'
    # The money identity: a 1-dollar move of 1 lot is 100 USD = 8,400
    # INR, and in converted units it is 84 x 0.3215 = 27.007 rupees per
    # 10 g, times 311.03 per lot = 8,400.
    assert report['contract_size'] * 84.0 * 0.3215074657 == \
        pytest.approx(100.0 * 84.0)


def test_unknown_positions_stay_unknown_through_the_conversion():
    leg, _broker, _book, _clock = oil_leg(84.0)
    leg.inner.positions = lambda symbol=None: None
    assert leg.positions() is None


def test_two_pairs_converting_one_symbol_two_ways_is_a_clash():
    a = PairConfig('a', leg_a={'account': 'x', 'symbol': 'GCZ5',
                               'fx': 'USDINR', 'price_factor': 0.3215},
                   leg_b={'account': 'y', 'symbol': 'GOLD'})
    b = PairConfig('b', leg_a={'account': 'x', 'symbol': 'GCZ5',
                               'fx': 'USDINR', 'price_factor': 32.15},
                   leg_b={'account': 'y', 'symbol': 'SILVER'})
    _convs, clashes = conversions_from_config(
        TraderConfig(pairs={'a': a, 'b': b}))
    assert clashes and 'GCZ5' in clashes[0]


# --- the engine, end to end ---------------------------------------------------

@pytest.fixture
def cross(timeline):
    clock = Clock()
    cme = FakeBroker('cme', [FakeSymbol('CLZ5', 70.00, 70.02,
                                        contract_size=1000.0,
                                        volume_min=1.0, volume_step=1.0)],
                     timeline)
    mcx = FakeBroker('mcx', [FakeSymbol('CRUDEOIL19NOV25FUT', 5900.0, 5902.0,
                                        contract_size=100.0, volume_min=1.0,
                                        volume_step=1.0, tick_size=1.0)],
                     timeline)
    book = FxBook(raw={'USDINR': manual_entry(84.0, clock=clock)},
                  clock=clock)
    pair = PairConfig(
        'CLZ5|CRUDEOIL19NOV25FUT', name='Crude MCX vs NYMEX',
        leg_a={'account': 'cme', 'symbol': 'CLZ5', 'fx': 'USDINR',
               'price_factor': 1.0},
        leg_b={'account': 'mcx', 'symbol': 'CRUDEOIL19NOV25FUT'},
        hedge_ratio=1.0, hedge_ratio_for='CLZ5|CRUDEOIL19NOV25FUT',
        increment=1.0, clip_lots_a=1.0, clip_lots_b=10.0)
    config = TraderConfig(pairs={pair.key: pair})
    config.settings['LEG_DEADLINE_SEC'] = 0.05
    config.settings['AUTO_REFRESH_STALE_SEC'] = 0
    convs, _ = conversions_from_config(config)
    legs = {'cme': ConvertedLeg(FakeLeg(cme), convs['cme'], book),
            'mcx': FakeLeg(mcx)}
    coordinator = Coordinator(config, legs, sleep=lambda s: None)
    coordinator.fxbook = book
    coordinator.resolve_symbols()
    return coordinator, config, pair, legs, book, clock


def test_the_spread_is_rupees_minus_rupees(cross):
    coordinator, _config, pair, _legs, _book, _clock = cross
    coordinator.poll_once()
    md = coordinator.market[pair.key]
    assert md['spread'] == pytest.approx(5901.0 - 70.01 * 84.0)
    # LONG the spread = buy B at its ask, sell A at its bid.
    assert md['long_spread'] == pytest.approx(5902.0 - 70.00 * 84.0)
    assert md['fx_reason'] is None and not md['guard_reason']


def test_k_is_rupees_per_rupee_of_spread(cross):
    coordinator, _config, pair, _legs, _book, _clock = cross
    snap = coordinator.snapshot()['pairs'][pair.key]
    # 10 MCX lots x 100 bbl = 1,000 bbl against one 1,000 bbl CL lot.
    assert snap['spread_units'] == pytest.approx(1000.0)
    assert snap['contract_a'] == pytest.approx(1000.0)
    assert snap['cross']['lot_match']['mismatch_pct'] == pytest.approx(0.0)
    assert snap['cross']['conversion']['fx_rate'] == 84.0


def test_a_stale_rate_withholds_the_order(cross):
    coordinator, config, pair, legs, book, clock = cross
    clock.now += 48 * 3600
    coordinator.poll_once()
    md = coordinator.market[pair.key]
    assert 'USDINR' in md['guard_reason']
    assert md['feed_badge'] == 'FX stale'
    executor = PairExecutor(config, legs, sleep=lambda s: None)
    refusal = executor.precheck(pair, SpreadSide.BUY, md, 1.0)
    assert refusal and 'USDINR' in refusal


def test_control_a_fresh_rate_lets_the_same_order_through(cross):
    coordinator, config, pair, legs, book, clock = cross
    clock.now += 48 * 3600
    book._raw['USDINR'] = manual_entry(84.0, clock=clock)
    coordinator.poll_once()
    md = coordinator.market[pair.key]
    executor = PairExecutor(config, legs, sleep=lambda s: None)
    assert executor.precheck(pair, SpreadSide.BUY, md, 1.0) is None


def test_a_spread_entered_and_closed_across_the_two_currencies(cross):
    coordinator, config, pair, legs, book, clock = cross
    coordinator.poll_once()
    md = coordinator.market[pair.key]
    executor = PairExecutor(config, legs, sleep=lambda s: None)
    result = executor.market_entry(pair, SpreadSide.BUY, md, 1.0)
    assert result.ok, result.reason
    position = result.position
    # The dollar leg's fill came back in rupees.
    assert position.leg_a.price == pytest.approx(70.00 * 84.0)
    # The rate goes stale. Orders are withheld; the CLOSE is not.
    clock.now += 48 * 3600
    coordinator.poll_once()
    closed = executor.close_position(pair, position,
                                     coordinator.market[pair.key])
    assert getattr(closed, 'ok', closed), getattr(closed, 'reason', closed)
    assert not position.is_open


def test_a_rupee_pair_says_its_money_is_rupees(cross):
    coordinator, _config, pair, _legs, _book, _clock = cross
    coordinator.poll_once()
    snap = coordinator.snapshot()
    assert snap['pairs'][pair.key]['currency'] == 'INR'
    assert snap['money_currency'] == 'INR'


def test_the_chart_series_is_recorded_as_the_engine_polls(cross):
    coordinator, _config, pair, _legs, _book, _clock = cross
    coordinator.poll_once()
    rows = coordinator.history.series(pair.key)
    assert rows and rows[-1]['spread'] == pytest.approx(5901.0 - 70.01 * 84.0)
    assert rows[-1]['fx'] == 84.0


def test_silver_100_is_its_own_contract_priced_per_ten_grams():
    """Not the 30 kg SILVER whose name it starts with."""
    spec = contracts.spec_for('SILVER10026DECFUT', 'MCX')
    assert spec.root == 'SILVER100'
    assert spec.contract_size == pytest.approx(10.0)      # 100 g / 10 g
    conv = contracts.suggest_conversion('SIH7', 'SILVER10026DECFUT')
    assert conv['unit_b'] == '10g'
    assert conv['price_factor'] == pytest.approx(0.3215074657)
    # The others are unchanged: 30 kg, 5 kg, 1 kg, all priced per kg.
    for symbol, size in (('SILVER05MAR27FUT', 30.0), ('SILVERM27FEB27FUT', 5.0),
                         ('SILVERMIC27FEB27FUT', 1.0)):
        assert contracts.spec_for(symbol, 'MCX').contract_size == size


def gold_engine(contract_size_a=None):
    clock = Clock()
    cme = FakeBroker('cme', [FakeSymbol('GCG7', 4000.0, 4000.5,
                                        contract_size=100.0, volume_min=1.0,
                                        volume_step=1.0)])
    mcx = FakeBroker('mcx', [FakeSymbol('GOLD05FEB27FUT', 121000.0, 121020.0,
                                        contract_size=100.0, volume_min=1.0,
                                        volume_step=1.0)])
    pair = PairConfig('GCG7|GOLD05FEB27FUT',
                      leg_a={'account': 'cme', 'symbol': 'GCG7', 'fx': 'USDINR',
                             'price_factor': 0.3215074657},
                      leg_b={'account': 'mcx', 'symbol': 'GOLD05FEB27FUT'},
                      contract_size_a=contract_size_a)
    config = TraderConfig(pairs={pair.key: pair})
    convs, _ = conversions_from_config(config)
    book = FxBook(raw={'USDINR': manual_entry(88.0, clock=clock)}, clock=clock)
    legs = {'cme': ConvertedLeg(FakeLeg(cme), convs['cme'], book),
            'mcx': FakeLeg(mcx)}
    coordinator = Coordinator(config, legs, sleep=lambda s: None)
    coordinator.resolve_symbols()
    return pair


def test_a_contract_override_is_in_the_legs_OWN_units_and_is_converted():
    """'100 oz' typed on a converted gold leg is 311.03 lots of 10 g to
    the engine — applied as typed it made the leg's money 3.1x small."""
    pair = gold_engine(contract_size_a=100.0)
    assert pair.meta_a['contract_size'] == pytest.approx(311.034768)
    # A broker that is genuinely wrong, corrected to 10 oz, scales too.
    pair = gold_engine(contract_size_a=10.0)
    assert pair.meta_a['contract_size'] == pytest.approx(31.1034768)


def test_control_with_no_override_the_brokers_figure_is_used():
    pair = gold_engine(contract_size_a=None)
    assert pair.meta_a['contract_size'] == pytest.approx(311.034768)
    assert pair.meta_a['contract_size_raw'] == 100.0


def test_contract_from_lot_shows_its_working():
    worked = contracts.contract_from_lot(5, 'kg', 'kg')
    assert worked['contract_size'] == 5.0
    assert worked['steps'][-1] == 'contract size = 5000 / 1000 = 5 per lot'
    assert contracts.contract_from_lot(1, 'kg', '10g')['contract_size'] == 100
    assert contracts.contract_from_lot(30, '', None)['contract_size'] == 30
    assert contracts.contract_from_lot(1, 'kg', 'bbl')['contract_size'] is None
    assert contracts.contract_from_lot(0, 'kg', 'kg') is None


def test_same_quantity_is_judged_from_the_contracts_actually_traded():
    """1 CL is 1,000 bbl: ten 100-bbl MCX lots match it exactly."""
    m = contracts.quantity_match(1000, 'bbl', 100, 'bbl', 1, 10)
    assert m['qty_a'] == m['qty_b'] == 1000
    assert m['mismatch_pct'] == pytest.approx(0.0)
    assert (m['best_clip_a'], m['best_clip_b']) == (1, 10)
    # COMEX gold 100 oz against MCX GOLD (100 per 10g = 1 kg): 1 : 3 is
    # 3.5% short, and the closest small clip is offered.
    g = contracts.quantity_match(100, 'oz', 100, '10g', 1, 3)
    assert g['qty_a'] == pytest.approx(3110.34768)
    assert g['qty_b'] == pytest.approx(3000.0)
    assert g['mismatch_pct'] == pytest.approx(-3.548, abs=0.01)
    # Silver mini, 5 kg, against SI 5,000 oz = 155.5 kg: 1 : 31.
    s = contracts.quantity_match(5000, 'oz', 5, 'kg', 1, 1)
    assert s['best_clip_b'] == 31


def test_an_override_changes_the_quantity_match():
    """The broker's figure, or the override, decides — not the table."""
    pair_leg_b = {'price_unit': 'kg'}
    on_spec = contracts.pair_quantities({'price_unit': 'oz'}, pair_leg_b,
                                        'SIH7', 'SILVER05MAR27FUT',
                                        5000, 30, 1, 5)
    overridden = contracts.pair_quantities({'price_unit': 'oz'}, pair_leg_b,
                                           'SIH7', 'SILVER05MAR27FUT',
                                           5000, 20, 1, 5)
    assert on_spec['qty_b'] == pytest.approx(150000.0)
    assert overridden['qty_b'] == pytest.approx(100000.0)
    assert overridden['source'] == 'contracts'


def test_with_no_units_or_sizes_the_exchange_table_is_used_and_said():
    m = contracts.pair_quantities({}, {}, 'CLZ5', 'CRUDEOIL19NOV25FUT',
                                  None, None, 1, 10)
    assert m['source'] == 'exchange table'
    assert m['mismatch_pct'] == pytest.approx(0.0)


def test_unknown_units_are_no_match_not_a_match_of_zero():
    assert contracts.quantity_match(100, 'oz', 100, 'bbl') is None
    assert contracts.quantity_match(None, 'oz', 100, '10g') is None


def test_the_ladder_is_told_how_much_each_leg_holds(cross):
    coordinator, _config, pair, _legs, _book, _clock = cross
    coordinator.poll_once()
    match = coordinator.snapshot()['pairs'][pair.key]['cross']['lot_match']
    assert match['qty_a'] == pytest.approx(1000.0)     # 1 x 1,000 bbl
    assert match['qty_b'] == pytest.approx(1000.0)     # 10 x 100 bbl
    assert match['source'] == 'contracts'


def test_a_converted_gold_leg_is_counted_in_ounces_not_converted_units():
    pair = gold_engine()
    coordinator_match = contracts.pair_quantities(
        pair.leg_a, pair.leg_b, pair.symbol_a, pair.symbol_b,
        pair.meta_a['contract_size_raw'], pair.meta_b['contract_size'], 1, 3)
    assert coordinator_match['qty_a'] == pytest.approx(3110.34768)


def test_history_bars_are_restated_like_ticks():
    """The Algo builds its band from these: dollar bars minus rupee bars
    would be a spread that never existed."""
    leg, broker, book, _clock = oil_leg(84.0)
    leg.inner.rates = lambda symbol, tf, count: [
        {'time': 1, 'close': 70.0, 'spread': 2, 'point': 0.01}]
    bars = leg.rates('CLZ5', 15, 10)
    assert bars[0]['close'] == pytest.approx(70.0 * 84.0)
    assert bars[0]['point'] == pytest.approx(0.01 * 84.0)
    assert bars[0]['close_raw'] == 70.0


def test_with_no_rate_there_is_no_history_rather_than_dollar_history():
    leg, broker, book, _clock = oil_leg(84.0)
    leg.inner.rates = lambda symbol, tf, count: [
        {'time': 1, 'close': 70.0, 'spread': 2, 'point': 0.01}]
    book._raw = {}
    assert leg.rates('CLZ5', 15, 10) is None


def test_control_an_unconverted_symbol_passes_its_bars_through():
    leg, broker, book, _clock = oil_leg(84.0)
    raw = [{'time': 1, 'close': 1.5, 'spread': 2, 'point': 0.0001}]
    leg.inner.rates = lambda symbol, tf, count: raw
    assert leg.rates('EURUSD', 15, 10) == raw
