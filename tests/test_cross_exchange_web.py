"""The Exchanges page's cross-exchange endpoints: the FX rate, an Arrow
account, and a pair whose leg A is converted."""

import json

import pytest

from mt5trader import config as cfg
from mt5trader.webapp import create_app


@pytest.fixture
def paths(tmp_path):
    return {'status': str(tmp_path / 'status.json'),
            'commands': str(tmp_path / 'commands.jsonl'),
            'results': str(tmp_path / 'results.json'),
            'config': str(tmp_path / 'config.json')}


@pytest.fixture
def client(paths):
    app = create_app(paths['status'], paths['commands'], paths['results'],
                     config_path=paths['config'])
    return app.test_client()


def post(client, url, body):
    return client.post(url, data=json.dumps(body),
                       content_type='application/json')


def test_a_typed_rate_is_stamped_and_reported_fresh(client, paths):
    reply = post(client, '/api/fx/USDINR', {'rate': 84.25})
    assert reply.status_code == 200
    assert reply.get_json()['restart_required'] is False
    saved = cfg.load_raw(paths['config'])['fx']['USDINR']
    assert saved['rate'] == 84.25 and saved['set_at']
    rates = client.get('/api/fx').get_json()['rates']
    assert rates['USDINR']['rate'] == 84.25
    assert rates['USDINR']['stale'] is None


def test_a_rate_that_is_not_a_positive_number_is_refused(client, paths):
    assert post(client, '/api/fx/USDINR', {'rate': 0}).status_code == 400
    assert post(client, '/api/fx/USD-INR', {'rate': 84}).status_code == 400


def test_a_pair_needing_a_rate_nobody_set_lists_it_as_missing(client, paths):
    post(client, '/api/pairs/CLZ5|CRUDEOIL', {
        'leg_a': {'account': 'cme', 'symbol': 'CLZ5', 'fx': 'usdinr'},
        'leg_b': {'account': 'mcx', 'symbol': 'CRUDEOIL'}})
    rates = client.get('/api/fx').get_json()['rates']
    assert rates['USDINR']['rate'] is None
    assert rates['USDINR']['stale']


def test_an_arrow_account_saves_its_secrets_to_env_only(client, paths,
                                                       tmp_path):
    reply = post(client, '/api/accounts/mcx', {
        'kind': 'arrow', 'app_id': 'APP1', 'user_id': 'U1',
        'endpoint': '127.0.0.1:9102', 'password': 'pw-Zq81',
        'api_secret': 'apisec-Kx77', 'totp_secret': 'SEEDQ2W3'})
    assert reply.status_code == 200, reply.get_json()
    text = open(paths['config']).read()
    for secret in ('pw-Zq81', 'apisec-Kx77', 'SEEDQ2W3'):
        assert secret not in text
    env = (tmp_path / '.env').read_text()
    assert 'ARROW_TOTP_SECRET_MCX' in env and 'SEEDQ2W3' in env
    row = [a for a in client.get('/api/accounts').get_json()['accounts']
           if a['name'] == 'mcx'][0]
    assert row['kind'] == 'arrow' and row['totp_secret_set']


def test_saving_a_pair_from_a_form_that_does_not_show_fx_keeps_the_fx(
        client, paths):
    post(client, '/api/pairs/GCZ5|GOLD', {
        'leg_a': {'account': 'cme', 'symbol': 'GCZ5', 'fx': 'USDINR',
                  'price_factor': 0.3215074657},
        'leg_b': {'account': 'mcx', 'symbol': 'GOLD05DEC25FUT'}})
    post(client, '/api/pairs/GCZ5|GOLD', {
        'leg_a': {'account': 'cme', 'symbol': 'GCZ5'}})
    leg = cfg.load_raw(paths['config'])['pairs']['GCZ5|GOLD']['leg_a']
    assert leg['fx'] == 'USDINR'
    assert leg['price_factor'] == pytest.approx(0.3215074657)


def test_control_a_form_that_blanks_the_fx_clears_it(client, paths):
    post(client, '/api/pairs/GCZ5|GOLD', {
        'leg_a': {'account': 'cme', 'symbol': 'GCZ5', 'fx': 'USDINR',
                  'price_factor': 0.32}})
    post(client, '/api/pairs/GCZ5|GOLD', {
        'leg_a': {'account': 'cme', 'symbol': 'GCZ5', 'fx': '',
                  'price_factor': ''}})
    leg = cfg.load_raw(paths['config'])['pairs']['GCZ5|GOLD']['leg_a']
    assert 'fx' not in leg and 'price_factor' not in leg


def test_a_negative_unit_factor_is_refused(client):
    reply = post(client, '/api/pairs/GCZ5|GOLD', {
        'leg_a': {'account': 'cme', 'symbol': 'GCZ5', 'price_factor': -1}})
    assert reply.status_code == 400


def test_one_symbol_converted_two_ways_is_refused_at_save(client):
    post(client, '/api/pairs/GCZ5|GOLD', {
        'leg_a': {'account': 'cme', 'symbol': 'GCZ5', 'fx': 'USDINR',
                  'price_factor': 0.3215}})
    reply = post(client, '/api/pairs/GCZ5|SILVER', {
        'leg_a': {'account': 'cme', 'symbol': 'GCZ5', 'fx': 'USDINR',
                  'price_factor': 32.15}})
    assert reply.status_code == 400
    assert 'GCZ5' in reply.get_json()['error']
