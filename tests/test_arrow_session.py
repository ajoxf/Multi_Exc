"""The Arrow leg, against a fake SDK that keeps a NETTING book.

The real exchange holds one net position per symbol. The fake does the
same, so every test here is about the gap between that and the per-order
tickets the engine needs — and an opposite order here really does net a
position away, exactly as it would at MCX.
"""

import enum
import json
from types import SimpleNamespace

import pytest

from mt5trader.arrow_broker import IST_OFFSET_SEC, ArrowSession
from mt5trader.config import AccountConfig, TraderConfig, arrow_env_key
from mt5trader.legs import LocalLeg
from mt5trader.models import OrderSide


def make_sdk(with_mcx=True, ltp_only=False):
    members = {'NSE': 'NSE', 'NFO': 'NFO'}
    if with_mcx:
        members['MCX'] = 'MCX'
    sdk = SimpleNamespace(
        Exchange=enum.Enum('Exchange', members),
        OrderType=enum.Enum('OrderType', {'MKT': 'MKT', 'LMT': 'LMT'}),
        ProductType=enum.Enum('ProductType', {'NRML': 'M', 'MIS': 'I'}),
        TransactionType=enum.Enum('TransactionType',
                                  {'BUY': 'BUY', 'SELL': 'SELL'}),
        Retention=enum.Enum('Retention', {'DAY': 'DAY', 'IOC': 'IOC'}),
        Variety=enum.Enum('Variety', {'REGULAR': 'REGULAR'}),
        QuoteMode=enum.Enum('QuoteMode', {'LTP': 'LTP'} if ltp_only
                            else {'LTP': 'LTP', 'FULL': 'FULL'}),
    )
    sdk.ArrowClient = lambda app_id=None: FakeExchange.current
    return sdk


class FakeExchange:
    """One Arrow login on a netting exchange."""
    current = None

    def __init__(self):
        FakeExchange.current = self
        self.instruments = [
            {'ExchSeg': 'MCXFO', 'TradingSymbol': 'CRUDEOIL19NOV25FUT',
             'Symbol': 'CRUDEOIL', 'Expiry': '19-Nov-2025', 'LotSize': '100',
             'TickSize': '1', 'Token': '1'},
            {'ExchSeg': 'MCXFO', 'TradingSymbol': 'GOLD05DEC25FUT',
             'Symbol': 'GOLD', 'Expiry': '05-Dec-2025', 'LotSize': '1',
             'TickSize': '1', 'Token': '2'},
            {'ExchSeg': 'NSEFO', 'TradingSymbol': 'NIFTY25NOVFUT',
             'Symbol': 'NIFTY', 'LotSize': '75', 'Token': '3'},
        ]
        self.book = {'CRUDEOIL19NOV25FUT': (5900.0, 5902.0),
                     'GOLD05DEC25FUT': (125000.0, 125010.0)}
        self.orders = {}
        self.net = {}
        self.sent = []
        self.next_id = 250000001
        self.fail_positions = False
        self.fill_market = True
        self.before_send = None

    def auto_login(self, **kwargs):
        self.login = kwargs

    def get_instruments(self):
        return json.dumps(self.instruments).encode()

    def get_quotes(self, mode, pairs):
        out = []
        for symbol, _exchange in pairs:
            if symbol in self.book:
                bid, ask = self.book[symbol]
                out.append({'TradingSymbol': symbol, 'BestBidPrice': bid,
                            'BestAskPrice': ask, 'Ltp': (bid + ask) / 2})
        return out

    def place_order(self, exchange, symbol, quantity, disclosed_quantity,
                    product, order_type, variety, transaction_type, price,
                    validity, mpp):
        if self.before_send:
            self.before_send()
        oid = str(self.next_id)
        self.next_id += 1
        self.sent.append(dict(symbol=symbol, quantity=quantity,
                              order_type=order_type.value,
                              side=transaction_type.value, price=price,
                              mpp=mpp, exchange=exchange.value))
        order = {'orderNo': oid, 'tradingSymbol': symbol, 'status': 'OPEN',
                 'filledQty': 0, 'avgPrice': 0, 'qty': quantity,
                 'side': transaction_type.value}
        self.orders[oid] = order
        if order_type.value == 'MKT' and self.fill_market:
            bid, ask = self.book[symbol]
            self.fill(oid, ask if transaction_type.value == 'BUY' else bid)
        return oid

    def fill(self, oid, price, qty=None):
        order = self.orders[oid]
        qty = order['qty'] if qty is None else qty
        order.update(status='COMPLETE' if qty == order['qty'] else 'OPEN',
                     filledQty=qty, avgPrice=price)
        sign = 1 if order['side'] == 'BUY' else -1
        self.net[order['tradingSymbol']] = \
            self.net.get(order['tradingSymbol'], 0) + sign * qty

    def get_order_book(self):
        return list(self.orders.values())

    def cancel_order(self, oid):
        self.orders[oid]['status'] = 'CANCELLED'
        return {'status': 'ok'}

    def modify_order(self, oid, **kwargs):
        self.orders[oid]['price'] = kwargs['price']

    def get_positions(self):
        if self.fail_positions:
            raise ConnectionError('positions endpoint timed out')
        return [{'TradingSymbol': s, 'NetQty': q} for s, q in self.net.items()
                if q]

    def get_user_limits(self):
        return {'availableMargin': 900000.0, 'marginUsed': 100000.0}

    def invalidate_session(self):
        pass


@pytest.fixture
def arrow_env(monkeypatch):
    monkeypatch.setenv('ARROW_PASSWORD_MCX', 'pw-Zq81')
    monkeypatch.setenv('ARROW_API_SECRET_MCX', 'apisec-Kx77')
    monkeypatch.setenv('ARROW_TOTP_SECRET_MCX', 'BASE32SEED')


def make_session(tmp_path, sdk=None, exchange=None):
    exchange = exchange or FakeExchange()
    account = AccountConfig('mcx', kind='arrow', app_id='APP', user_id='U1',
                            endpoint='127.0.0.1:9102')
    session = ArrowSession(account, sdk=sdk or make_sdk(),
                           ledger_path=str(tmp_path / 'ledger.json'),
                           sleep=lambda s: None)
    return session, exchange


@pytest.fixture
def arrow(tmp_path, arrow_env):
    session, exchange = make_session(tmp_path)
    assert session.initialize(), session.last_error
    return session, exchange


# --- configuration ------------------------------------------------------

def test_arrow_secrets_live_in_env_under_their_own_keys(arrow_env):
    account = AccountConfig('mcx', kind='arrow', app_id='APP', user_id='U1')
    assert account.password_env == 'ARROW_PASSWORD_MCX'
    assert account.api_secret_env == arrow_env_key('mcx', 'API_SECRET')
    assert account.totp_secret == 'BASE32SEED'
    saved = json.dumps(account.to_dict())
    for secret in ('pw-Zq81', 'apisec-Kx77', 'BASE32SEED'):
        assert secret not in saved


def test_an_arrow_account_round_trips_through_the_config():
    raw = {'accounts': {'mcx': {'kind': 'arrow', 'app_id': 'APP',
                                'user_id': 'U1', 'endpoint': '127.0.0.1:9102'}}}
    config = TraderConfig.from_raw(raw)
    again = TraderConfig.from_raw(config.to_raw())
    assert again.accounts['mcx'].kind == 'arrow'
    assert again.accounts['mcx'].exchange == 'MCX'
    assert again.accounts['mcx'].user_id == 'U1'


def test_an_mt5_account_is_still_an_mt5_account():
    account = AccountConfig.from_dict('cme', {'login': 5000001})
    assert account.kind == 'mt5' and account.login == 5000001
    assert account.password_env == 'MT5_PASSWORD_CME'


# --- connecting --------------------------------------------------------

def test_an_sdk_without_mcx_refuses_to_start_and_says_why(tmp_path, arrow_env):
    session, _ = make_session(tmp_path, sdk=make_sdk(with_mcx=False))
    assert not session.initialize()
    assert 'no MCX exchange' in session.last_error


def test_control_an_sdk_with_mcx_starts(tmp_path, arrow_env):
    session, _ = make_session(tmp_path, sdk=make_sdk(with_mcx=True))
    assert session.initialize()


def test_missing_secrets_are_named_not_guessed(tmp_path, monkeypatch):
    monkeypatch.delenv('ARROW_TOTP_SECRET_MCX', raising=False)
    monkeypatch.setenv('ARROW_PASSWORD_MCX', 'pw')
    monkeypatch.setenv('ARROW_API_SECRET_MCX', 'secret')
    session, _ = make_session(tmp_path)
    assert not session.initialize()
    assert 'TOTP secret' in session.last_error


def test_the_master_is_filtered_to_the_exchange(arrow):
    session, _ = arrow
    assert session.ensure_symbol('CRUDEOIL19NOV25FUT') is not None
    assert session.ensure_symbol('NIFTY25NOVFUT') is None


# --- prices -------------------------------------------------------------

def test_the_tick_is_the_book_not_the_last_trade(arrow):
    session, _ = arrow
    tick = LocalLeg(session).tick('CRUDEOIL19NOV25FUT')
    assert (tick['bid'], tick['ask']) == (5900.0, 5902.0)


def test_a_one_sided_book_is_no_price(arrow):
    session, exchange = arrow
    exchange.book['CRUDEOIL19NOV25FUT'] = (5900.0, None)
    session._quoted_at = 0
    assert session.symbol_tick('CRUDEOIL19NOV25FUT') is None


def test_an_ltp_only_sdk_is_no_price_rather_than_a_mid_made_of_last(
        tmp_path, arrow_env):
    session, _ = make_session(tmp_path, sdk=make_sdk(ltp_only=True))
    assert session.initialize()
    assert session.symbol_tick('CRUDEOIL19NOV25FUT') is None


def test_contract_size_is_the_exchange_spec_and_a_disagreement_is_named(arrow):
    session, _ = arrow
    # MCX gold: a 1 kg lot priced per 10 g. The master says LotSize 1.
    size, note = session.contract_size('GOLD05DEC25FUT')
    assert size == pytest.approx(100.0)
    assert note and 'spec' in note
    # Crude: the master's 100 bbl IS the contract size. Nothing to say.
    size, note = session.contract_size('CRUDEOIL19NOV25FUT')
    assert size == pytest.approx(100.0) and note is None


# --- orders and the ledger -----------------------------------------------

def test_lots_go_to_the_exchange_as_units_and_market_is_mpp(arrow):
    session, exchange = arrow
    result = LocalLeg(session).order('CRUDEOIL19NOV25FUT', 'BUY', 2.0)
    assert result['ok'] and result['filled_volume'] == 2.0
    sent = exchange.sent[-1]
    assert sent['quantity'] == 200            # 2 lots x 100 bbl
    assert sent['mpp'] is True and sent['price'] == 0.0
    assert sent['exchange'] == 'MCX'
    assert result['price'] == 5902.0
    assert result['position_tickets'] == [result['ticket']]


def test_the_intent_is_on_disk_BEFORE_the_order_is_sent(arrow):
    session, exchange = arrow
    seen = {}

    def check():
        with open(session.ledger_path) as f:
            ledger = json.load(f)
        seen['states'] = [o['state'] for o in ledger['orders'].values()]
    exchange.before_send = check
    session.send_market_order('CRUDEOIL19NOV25FUT', OrderSide.BUY, 1.0)
    assert 'sending' in seen['states']


def test_an_order_lost_mid_send_is_flagged_unknown_after_a_restart(
        tmp_path, arrow_env):
    session, exchange = make_session(tmp_path)
    session.initialize()

    def die():
        raise SystemExit('process killed')
    exchange.before_send = die
    with pytest.raises(SystemExit):
        session.send_market_order('CRUDEOIL19NOV25FUT', OrderSide.BUY, 1.0)
    again, _ = make_session(tmp_path, exchange=exchange)
    assert again.initialize()
    states = [o['state'] for o in again.ledger['orders'].values()]
    assert states == ['unknown']
    # And it is NOT reported as a position: nobody knows if it is one.
    assert again.positions_by_magic() == []


def test_a_close_by_ticket_is_an_opposite_order_for_that_ticket(arrow):
    session, exchange = arrow
    leg = LocalLeg(session)
    first = leg.order('CRUDEOIL19NOV25FUT', 'BUY', 1.0)
    second = leg.order('CRUDEOIL19NOV25FUT', 'BUY', 1.0)
    closed = leg.close_ticket('CRUDEOIL19NOV25FUT', first['ticket'], 1.0,
                              'BUY')
    assert closed['ok']
    assert exchange.sent[-1]['side'] == 'SELL'
    assert exchange.sent[-1]['quantity'] == 100
    assert exchange.net['CRUDEOIL19NOV25FUT'] == 100
    tickets = {p['ticket']: p['volume'] for p in leg.positions()}
    assert tickets == {second['ticket']: 1.0}


def test_a_close_of_a_ticket_that_is_not_ours_is_refused_and_sends_nothing(
        arrow):
    session, exchange = arrow
    before = len(exchange.sent)
    result = session.close_position_ticket('CRUDEOIL19NOV25FUT', '999', 1.0,
                                           OrderSide.BUY)
    assert not result.success and 'not an open position' in result.error
    assert len(exchange.sent) == before


def test_a_long_and_a_short_spread_can_stand_side_by_side(arrow):
    """Hedging semantics above a netting exchange: two tickets, net zero
    at Arrow, both reported."""
    session, exchange = arrow
    leg = LocalLeg(session)
    leg.order('CRUDEOIL19NOV25FUT', 'BUY', 1.0)
    leg.order('CRUDEOIL19NOV25FUT', 'SELL', 1.0)
    assert exchange.net['CRUDEOIL19NOV25FUT'] == 0
    sides = sorted(p['side'] for p in leg.positions())
    assert sides == ['BUY', 'SELL']


def test_volume_the_exchange_does_not_hold_is_withheld_from_the_report(arrow):
    """Squared off at the broker (by hand, or by the broker's risk desk):
    the ledger still says long 2, the exchange holds 1. The newest
    ticket is reported as gone so the reconciler counts its strikes."""
    session, exchange = arrow
    leg = LocalLeg(session)
    old = leg.order('CRUDEOIL19NOV25FUT', 'BUY', 1.0)
    new = leg.order('CRUDEOIL19NOV25FUT', 'BUY', 1.0)
    exchange.net['CRUDEOIL19NOV25FUT'] = 100
    rows = {p['ticket']: p['volume'] for p in leg.positions()}
    assert rows == {old['ticket']: 1.0}
    assert session.netting['CRUDEOIL19NOV25FUT']['mismatch']
    assert new['ticket'] in session.netting['CRUDEOIL19NOV25FUT']['withheld']


def test_control_when_the_exchange_holds_it_all_every_ticket_is_reported(
        arrow):
    session, exchange = arrow
    leg = LocalLeg(session)
    leg.order('CRUDEOIL19NOV25FUT', 'BUY', 1.0)
    leg.order('CRUDEOIL19NOV25FUT', 'BUY', 1.0)
    assert sum(p['volume'] for p in leg.positions()) == 2.0
    assert not session.netting['CRUDEOIL19NOV25FUT']['mismatch']


def test_a_manual_position_on_the_same_account_is_not_claimed(arrow):
    session, exchange = arrow
    exchange.net['GOLD05DEC25FUT'] = 3
    assert LocalLeg(session).positions() == []
    assert session.netting['GOLD05DEC25FUT']['mismatch']


def test_positions_that_cannot_be_read_are_unknown_not_flat(arrow):
    session, exchange = arrow
    LocalLeg(session).order('CRUDEOIL19NOV25FUT', 'BUY', 1.0)
    exchange.fail_positions = True
    assert LocalLeg(session).positions() is None


def test_a_limit_rests_and_its_fill_becomes_a_ticket(arrow):
    session, exchange = arrow
    leg = LocalLeg(session)
    reply = leg.place_limit('CRUDEOIL19NOV25FUT', 'BUY', 1.0, 5895.0)
    assert reply['ok']
    assert exchange.sent[-1]['order_type'] == 'LMT'
    assert exchange.sent[-1]['price'] == 5895.0
    pending = leg.pending_orders()
    assert [p['ticket'] for p in pending] == [reply['ticket']]
    exchange.fill(reply['ticket'], 5895.0)
    state = leg.order_state(reply['ticket'])
    assert state['filled_volume'] == 1.0
    assert state['position_tickets'] == [reply['ticket']]
    assert state['server_offset_sec'] == IST_OFFSET_SEC
    # Read twice, booked once.
    leg.order_state(reply['ticket'])
    assert [p['volume'] for p in leg.positions()] == [1.0]
    assert leg.pending_orders() == []


def test_a_limit_that_would_cross_is_kept_resting(arrow):
    session, exchange = arrow
    reply = session.place_pending_limit('CRUDEOIL19NOV25FUT', OrderSide.BUY,
                                        1.0, 5910.0)
    assert reply['price'] == 5901.0 and reply['price_note']


def test_pendings_that_cannot_be_read_are_unknown_not_none_left(arrow):
    session, exchange = arrow
    session.place_pending_limit('CRUDEOIL19NOV25FUT', OrderSide.BUY, 1.0,
                                5890.0)
    exchange.get_order_book = lambda: (_ for _ in ()).throw(
        ConnectionError('down'))
    assert session.pending_orders_by_magic() is None


def test_a_rejection_carries_the_exchanges_own_words(arrow):
    session, exchange = arrow
    exchange.fill_market = False
    original = exchange.place_order

    def reject(**kwargs):
        oid = original(**kwargs)
        exchange.orders[oid].update(status='REJECTED',
                                    rejectionReason='RMS: Margin Exceeds')
        return oid
    exchange.place_order = reject
    result = session.send_market_order('CRUDEOIL19NOV25FUT', OrderSide.BUY,
                                       1.0)
    assert not result.success
    assert 'RMS: Margin Exceeds' in result.error


def test_the_ledger_survives_a_restart(tmp_path, arrow_env):
    session, exchange = make_session(tmp_path)
    session.initialize()
    ticket = LocalLeg(session).order('CRUDEOIL19NOV25FUT', 'BUY', 1.0)['ticket']
    again, _ = make_session(tmp_path, exchange=exchange)
    again.initialize()
    assert [p['ticket'] for p in again.positions_by_magic()] == [ticket]


def test_the_terminal_report_says_netting_and_that_it_is_handled(arrow):
    session, _ = arrow
    report = session.terminal_report()
    assert report['hedging'] is False and report['netting_ledger'] is True
    assert report['logged_in'] and report['login'] == 'U1'
