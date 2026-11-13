"""The setup wizard and the start-up preflight.

These two run on a machine nobody is watching, in front of somebody who
cannot read a traceback, and what they write is the file the engine
trades from. So every refusal below is paired with a CONTROL that turns
the same guard off and asserts the opposite — a test that only ever
sees a refusal cannot tell a working guard from a function that refuses
everything.
"""

import importlib.util
import json
from pathlib import Path

import pytest

DEPLOY = Path(__file__).resolve().parent.parent / 'deploy'


def _load(name):
    spec = importlib.util.spec_from_file_location(name, DEPLOY / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


configure = _load('configure')
preflight = _load('preflight')
add_pairs = _load('add_pairs')

PASSWORD_A = 'a-secret-with a space and #hash'
PASSWORD_B = 'b-secret'


def answers(**over):
    base = {
        'login_a': '10006', 'password_a': PASSWORD_A,
        'server_a': 'MentoMarkets-Server',
        'login_b': '10007', 'password_b': PASSWORD_B,
        'server_b': 'MentoMarkets-Server',
        'symbol_a': 'XAUUSD.f', 'symbol_b': 'GCZ6',
        'pair_type': 'SPOT_FUTURE',
        'terminal_a': r'C:\MT5-A\terminal64.exe',
        'terminal_b': r'C:\MT5-B\terminal64.exe',
    }
    base.update(over)
    return base


# --- what a good run writes ---------------------------------------------

def test_the_wizard_writes_a_config_the_app_can_read():
    out = configure.build_config(answers(), {})
    account_a = out['accounts'][NAME_A]
    account_b = out['accounts'][NAME_B]

    assert account_a['login'] == 10006
    assert account_b['login'] == 10007
    # One port each. Two accounts on one port is one leg runner serving
    # both legs, which is the same terminal twice.
    assert account_a['endpoint'] != account_b['endpoint']
    assert account_a['terminal_path'] != account_b['terminal_path']

    pair = out['pairs']['XAUUSD.f|GCZ6']
    assert pair['leg_a'] == {'account': NAME_A, 'symbol': 'XAUUSD.f'}
    assert pair['leg_b'] == {'account': NAME_B, 'symbol': 'GCZ6'}
    assert pair['enabled'] is True
    # Beta stamped with what it was computed FOR, so a stale one from
    # another instrument cannot silently define the spread.
    assert pair['hedge_ratio_for'] == 'XAUUSD.f|GCZ6'


def test_the_env_key_comes_from_the_app_not_from_the_wizard():
    """A wizard that spells the key itself writes one the loader does
    not read, and the symptom is a password that is silently never
    there."""
    from mt5trader.config import env_key_for
    out = configure.build_config(answers(), {})
    assert out['accounts'][NAME_A]['password_env'] == \
        env_key_for(NAME_A)
    assert out['accounts'][NAME_B]['password_env'] == \
        env_key_for(NAME_B)


def test_the_instrument_details_are_left_for_mt5_to_answer():
    """Tick size, contract size and lot steps are READ from the broker.
    A number typed at setup is a number that can be wrong, and every
    money figure on the ladder runs through it."""
    pair = configure.build_config(answers(), {})['pairs']['XAUUSD.f|GCZ6']
    assert pair['increment'] is None
    assert pair['clip_lots_a'] is None
    assert pair['clip_lots_b'] is None


# --- the refusals, each with its control --------------------------------

def test_one_login_on_both_accounts_is_refused():
    with pytest.raises(configure.SetupError, match='hedge against itself'):
        configure.build_config(answers(login_b='10006'), {})


def test_control_two_logins_are_accepted():
    out = configure.build_config(answers(login_b='10007'), {})
    assert out['accounts'][NAME_B]['login'] == 10007


def test_one_terminal_folder_for_both_accounts_is_refused():
    with pytest.raises(configure.SetupError, match='ONE login'):
        configure.build_config(
            answers(terminal_b=r'C:\MT5-A\terminal64.exe'), {})


def test_control_two_terminal_folders_are_accepted():
    out = configure.build_config(
        answers(terminal_b=r'C:\MT5-B\terminal64.exe'), {})
    assert out['accounts'][NAME_B]['terminal_path'] == \
        r'C:\MT5-B\terminal64.exe'


def test_the_same_symbol_on_both_legs_is_refused():
    """A spread of an instrument against itself is always zero."""
    with pytest.raises(configure.SetupError, match='always zero'):
        configure.build_config(answers(symbol_b='XAUUSD.f'), {})


def test_control_two_symbols_are_accepted():
    out = configure.build_config(answers(symbol_b='GCZ6'), {})
    assert 'XAUUSD.f|GCZ6' in out['pairs']


@pytest.mark.parametrize('field', ['login_a', 'password_a', 'server_a',
                                   'login_b', 'password_b', 'server_b',
                                   'symbol_a', 'symbol_b'])
def test_a_blank_answer_is_refused(field):
    with pytest.raises(configure.SetupError):
        configure.build_config(answers(**{field: ''}), {})


def test_control_every_answer_filled_in_is_accepted():
    assert configure.build_config(answers(), {})['accounts']


def test_an_email_address_where_the_login_goes_is_refused():
    """The commonest wrong answer: brokers e-mail the account details,
    so the address is what is on the screen when the trader types."""
    with pytest.raises(configure.SetupError, match='digits the broker'):
        configure.build_config(answers(login_a='trader@example.com'), {})


def test_control_the_digits_are_accepted():
    out = configure.build_config(answers(login_a='10006'), {})
    assert out['accounts'][NAME_A]['login'] == 10006


# --- not overwriting a desk that is already trading ----------------------

def test_a_configured_machine_is_not_overwritten(tmp_path):
    (tmp_path / 'config.json').write_text(json.dumps({
        'accounts': {'Real Desk': {'login': 55555, 'endpoint': '127.0.0.1:9101'}},
        'pairs': {}}), encoding='utf-8')
    with pytest.raises(configure.SetupError, match='already has accounts'):
        configure.apply_config(answers(), str(tmp_path))
    # And it is still there, untouched.
    still = json.loads((tmp_path / 'config.json').read_text(encoding='utf-8'))
    assert 'Real Desk' in still['accounts']


def test_control_force_replaces_it_and_keeps_a_backup(tmp_path):
    (tmp_path / 'config.json').write_text(json.dumps({
        'accounts': {'Real Desk': {'login': 55555, 'endpoint': '127.0.0.1:9101'}},
        'pairs': {}}), encoding='utf-8')
    configure.apply_config(answers(), str(tmp_path), force=True)
    now = json.loads((tmp_path / 'config.json').read_text(encoding='utf-8'))
    assert NAME_A in now['accounts']
    backup = json.loads((tmp_path / 'config.json.bak').read_text(
        encoding='utf-8'))
    assert 'Real Desk' in backup['accounts']


def test_the_shipped_example_is_replaced_without_being_asked(tmp_path):
    """`start.py` copies config.example.json on a first run, so the file
    EXISTING is not evidence that anyone configured anything."""
    example = json.loads(
        (Path(__file__).resolve().parent.parent
         / 'config.example.json').read_text(encoding='utf-8'))
    (tmp_path / 'config.example.json').write_text(json.dumps(example),
                                                  encoding='utf-8')
    (tmp_path / 'config.json').write_text(json.dumps(example),
                                          encoding='utf-8')
    configure.apply_config(answers(), str(tmp_path))
    now = json.loads((tmp_path / 'config.json').read_text(encoding='utf-8'))
    assert set(now['accounts']) == {NAME_A, NAME_B}
    # The example's fictional broker rows are gone, not sitting on the
    # screen as two accounts that can never connect.
    assert 'leg_a' not in now['accounts']


def test_the_settings_block_survives(tmp_path):
    example = json.loads(
        (Path(__file__).resolve().parent.parent
         / 'config.example.json').read_text(encoding='utf-8'))
    (tmp_path / 'config.example.json').write_text(json.dumps(example),
                                                  encoding='utf-8')
    configure.apply_config(answers(), str(tmp_path))
    now = json.loads((tmp_path / 'config.json').read_text(encoding='utf-8'))
    assert now['settings']['POLL_INTERVAL_SEC'] == \
        example['settings']['POLL_INTERVAL_SEC']


# --- the passwords -------------------------------------------------------

def test_passwords_reach_env_and_nothing_else(tmp_path):
    configure.apply_config(answers(), str(tmp_path))
    written = (tmp_path / 'config.json').read_text(encoding='utf-8')
    assert PASSWORD_A not in written
    assert PASSWORD_B not in written

    env = (tmp_path / '.env').read_text(encoding='utf-8')
    # Quoted, so a password with a space or a # survives the read back.
    # The key is the app's own, off the account name: AC-10006 becomes
    # MT5_PASSWORD_AC_10006.
    from mt5trader.config import env_key_for
    assert f'{env_key_for(NAME_A)}="{PASSWORD_A}"' in env
    assert f'{env_key_for(NAME_B)}="{PASSWORD_B}"' in env
    assert 'MT5_PASSWORD_AC_10006' in env


def test_a_password_is_not_in_the_refusal_text():
    """A refusal is shown on screen and pasted into chat when it is
    reported. It must not carry the password that caused it."""
    with pytest.raises(configure.SetupError) as caught:
        configure.build_config(answers(login_b='10006'), {})
    assert PASSWORD_A not in str(caught.value)
    assert PASSWORD_B not in str(caught.value)


def test_rerunning_does_not_walk_the_ports_upward(tmp_path):
    configure.apply_config(answers(), str(tmp_path))
    first = json.loads((tmp_path / 'config.json').read_text(encoding='utf-8'))
    configure.apply_config(answers(), str(tmp_path), force=True)
    again = json.loads((tmp_path / 'config.json').read_text(encoding='utf-8'))
    assert first['accounts'][NAME_A]['endpoint'] == \
        again['accounts'][NAME_A]['endpoint']
    assert first['accounts'][NAME_B]['endpoint'] == \
        again['accounts'][NAME_B]['endpoint']


# --- the presets ---------------------------------------------------------

def test_the_shipped_presets_file_parses_and_is_usable():
    """It is DATA the office edits, so a stray comma is a real risk -
    and a broken file must be caught here, not on a trader's PC."""
    presets = configure.load_presets()
    assert presets, 'deploy/presets.json produced no pairs'
    for preset in presets:
        assert preset['label']
        assert preset['leg_a'] and preset['leg_b']
        # Anything unrecognised reads as RELATED, which silently drops
        # the fair value off a basis. Caught here instead.
        assert preset['pair_type'] in ('SPOT_FUTURE', 'FUTURE_FUTURE',
                                       'RELATED')


def test_a_broken_presets_file_leaves_the_wizard_usable(tmp_path):
    """No menu is survivable - the trader types both symbols. A setup
    that refuses to open over a stray comma is not."""
    (tmp_path / 'deploy').mkdir()
    (tmp_path / 'deploy' / 'presets.json').write_text('{ not json',
                                                      encoding='utf-8')
    assert configure.load_presets(str(tmp_path)) == []


def test_a_dated_leg_is_filled_in_from_the_contract_box():
    preset = {'label': 'Gold', 'leg_a': 'XAUUSD', 'leg_b': 'GC{contract}',
              'pair_type': 'SPOT_FUTURE'}
    assert configure.preset_needs(preset) == (False, True)
    assert configure.expand_preset(preset, '', 'Z6') == ('XAUUSD', 'GCZ6')


def test_a_calendar_spread_dates_both_legs_separately():
    """Two months of one instrument. One contract box would make the
    two legs the same symbol, and that spread is always zero."""
    preset = {'label': 'WTI', 'leg_a': 'USOIL{contract}',
              'leg_b': 'USOIL{contract}', 'pair_type': 'FUTURE_FUTURE'}
    assert configure.preset_needs(preset) == (True, True)
    assert configure.expand_preset(preset, 'X6', 'Z6') == ('USOILX6',
                                                           'USOILZ6')


def test_a_missing_contract_month_is_refused():
    preset = {'label': 'Gold', 'leg_a': 'XAUUSD', 'leg_b': 'GC{contract}',
              'pair_type': 'SPOT_FUTURE'}
    with pytest.raises(configure.SetupError, match='contract month'):
        configure.expand_preset(preset, '', '')


def test_control_a_pair_that_needs_no_contract_asks_for_none():
    preset = {'label': 'Spot pair', 'leg_a': 'XAUUSD', 'leg_b': 'XAGUSD',
              'pair_type': 'RELATED'}
    assert configure.preset_needs(preset) == (False, False)
    assert configure.expand_preset(preset) == ('XAUUSD', 'XAGUSD')


def test_a_calendar_spread_with_one_month_twice_is_refused():
    """Straight through expand_preset into build_config: both legs come
    out as USOILZ6, and a spread of an instrument against itself is
    always zero."""
    preset = {'label': 'WTI', 'leg_a': 'USOIL{contract}',
              'leg_b': 'USOIL{contract}', 'pair_type': 'FUTURE_FUTURE'}
    symbol_a, symbol_b = configure.expand_preset(preset, 'Z6', 'Z6')
    with pytest.raises(configure.SetupError, match='always zero'):
        configure.build_config(
            answers(symbol_a=symbol_a, symbol_b=symbol_b), {})


def test_control_two_different_months_go_through():
    preset = {'label': 'WTI', 'leg_a': 'USOIL{contract}',
              'leg_b': 'USOIL{contract}', 'pair_type': 'FUTURE_FUTURE'}
    symbol_a, symbol_b = configure.expand_preset(preset, 'X6', 'Z6')
    out = configure.build_config(
        answers(symbol_a=symbol_a, symbol_b=symbol_b,
                pair_type=preset['pair_type']), {})
    assert out['pairs']['USOILX6|USOILZ6']['pair_type'] == 'FUTURE_FUTURE'


# --- the monthly roll ----------------------------------------------------

#: The two accounts the wizard writes for the logins in `answers()`.
NAME_A = configure.account_name(10006)
NAME_B = configure.account_name(10007)


def _roll(**over):
    spec = {'pairs': [{'name': 'Gold basis', 'leg_a': 'XAUUSD.f',
                       'leg_b': 'GCZ6', 'pair_type': 'SPOT_FUTURE'}]}
    spec.update(over)
    return spec


def _configured_raw():
    return configure.build_config(answers(), {})


def test_the_shipped_roll_list_parses_and_matches_the_wizard():
    """pairs.json names accounts by NAME, and the wizard is what creates
    them. If those two ever disagree the trader gets a refusal on a
    machine that is perfectly well set up."""
    spec = add_pairs.load_list(str(DEPLOY / 'pairs.json'))
    # It must NOT name the accounts: names carry the login, so they
    # differ on every desk and one file has to serve them all.
    assert 'leg_a_account' not in spec
    assert 'leg_b_account' not in spec
    for entry in spec['pairs']:
        assert entry['leg_a'] and entry['leg_b']
        assert entry['pair_type'] in ('SPOT_FUTURE', 'FUTURE_FUTURE',
                                      'RELATED')


def test_a_new_contract_month_is_added():
    raw = _configured_raw()
    to_add, _ = add_pairs.plan(
        _roll(pairs=[{'name': 'Gold basis', 'leg_a': 'XAUUSD.f',
                      'leg_b': 'GCH7', 'pair_type': 'SPOT_FUTURE'}]), raw)
    assert 'XAUUSD.f|GCH7' in to_add
    added = to_add['XAUUSD.f|GCH7']
    assert added['leg_a'] == {'account': NAME_A, 'symbol': 'XAUUSD.f'}
    assert added['enabled'] is True
    assert added['hedge_ratio_for'] == 'XAUUSD.f|GCH7'


def test_a_pair_already_there_is_left_exactly_as_it_is():
    """The rule the whole script is built around. A trader may be
    holding a position on it, and re-writing the pair moves the ladder
    out from under the money."""
    raw = _configured_raw()
    raw['pairs']['XAUUSD.f|GCZ6']['enabled'] = False
    raw['pairs']['XAUUSD.f|GCZ6']['increment'] = 0.05
    before = dict(raw['pairs']['XAUUSD.f|GCZ6'])

    to_add, notes = add_pairs.plan(_roll(), raw)
    assert to_add == {}
    assert any('already here' in note for note in notes)
    # Not re-enabled, not re-stamped, not touched.
    assert raw['pairs']['XAUUSD.f|GCZ6'] == before


def test_control_a_pair_not_there_yet_is_added(tmp_path):
    raw = _configured_raw()
    assert 'XAUUSD.f|GCZ6' in raw['pairs']
    to_add, _ = add_pairs.plan(
        _roll(pairs=[{'leg_a': 'XAGUSD.f', 'leg_b': 'SIU6',
                      'pair_type': 'SPOT_FUTURE'}]), raw)
    assert list(to_add) == ['XAGUSD.f|SIU6']


def test_nothing_is_ever_removed(tmp_path):
    """The roll list is short and the config is not. Adding this month
    must not take away last month."""
    configure.apply_config(answers(), str(tmp_path))
    spec = _roll(pairs=[{'leg_a': 'XAGUSD.f', 'leg_b': 'SIU6',
                         'pair_type': 'SPOT_FUTURE'}])
    add_pairs.apply(spec, str(tmp_path / 'config.json'))
    now = json.loads((tmp_path / 'config.json').read_text(encoding='utf-8'))
    assert 'XAUUSD.f|GCZ6' in now['pairs']       # what the wizard wrote
    assert 'XAGUSD.f|SIU6' in now['pairs']       # what the roll added
    # And the accounts are untouched.
    assert set(now['accounts']) == {NAME_A, NAME_B}


def test_running_it_twice_changes_nothing_the_second_time(tmp_path):
    configure.apply_config(answers(), str(tmp_path))
    config_path = str(tmp_path / 'config.json')
    spec = _roll(pairs=[{'leg_a': 'XAGUSD.f', 'leg_b': 'SIU6',
                         'pair_type': 'SPOT_FUTURE'}])
    add_pairs.apply(spec, config_path)
    first = json.loads((tmp_path / 'config.json').read_text(encoding='utf-8'))
    added, _ = add_pairs.apply(spec, config_path)
    second = json.loads((tmp_path / 'config.json').read_text(encoding='utf-8'))
    assert added == []
    assert first == second


def test_a_dry_run_writes_nothing(tmp_path):
    configure.apply_config(answers(), str(tmp_path))
    config_path = str(tmp_path / 'config.json')
    before = (tmp_path / 'config.json').read_text(encoding='utf-8')
    added, _ = add_pairs.apply(
        _roll(pairs=[{'leg_a': 'XAGUSD.f', 'leg_b': 'SIU6'}]),
        config_path, dry_run=True)
    assert added == ['XAGUSD.f|SIU6']
    assert (tmp_path / 'config.json').read_text(encoding='utf-8') == before


def test_a_roll_list_for_another_setup_is_refused():
    """Account names the machine does not have. Guessing which local
    account was meant is how a leg ends up on the wrong terminal."""
    raw = _configured_raw()
    with pytest.raises(add_pairs.PairsError, match='differently-named'):
        add_pairs.plan(_roll(leg_a_account='Leg A', leg_b_account=NAME_B),
                       raw)


def test_control_the_matching_account_names_go_through():
    raw = _configured_raw()
    to_add, _ = add_pairs.plan(
        _roll(pairs=[{'leg_a': 'XAGUSD.f', 'leg_b': 'SIU6'}]), raw)
    assert to_add


def test_both_legs_on_one_account_is_refused():
    raw = _configured_raw()
    with pytest.raises(add_pairs.PairsError, match='against itself'):
        add_pairs.plan(_roll(leg_a_account=NAME_A, leg_b_account=NAME_A), raw)


def test_a_machine_with_no_accounts_is_refused():
    with pytest.raises(add_pairs.PairsError, match='no accounts'):
        add_pairs.plan(_roll(), {})


def test_the_same_symbol_on_both_legs_is_skipped_not_added():
    raw = _configured_raw()
    to_add, notes = add_pairs.plan(
        _roll(pairs=[{'leg_a': 'USOILV6', 'leg_b': 'USOILV6'}]), raw)
    assert to_add == {}
    assert any('always zero' in note for note in notes)


def test_an_unparseable_roll_list_changes_nothing(tmp_path):
    bad = tmp_path / 'pairs.json'
    bad.write_text('{ "pairs": [ ,, ] }', encoding='utf-8')
    with pytest.raises(add_pairs.PairsError, match='not valid JSON'):
        add_pairs.load_list(str(bad))


def test_an_unknown_pair_type_reads_as_related():
    """RELATED is the reading with NO fair value - the safe way to be
    wrong about a typo in a hand-edited list."""
    raw = _configured_raw()
    to_add, _ = add_pairs.plan(
        _roll(pairs=[{'leg_a': 'XAGUSD.f', 'leg_b': 'SIU6',
                      'pair_type': 'SPOTFUTURE'}]), raw)
    assert to_add['XAGUSD.f|SIU6']['pair_type'] == 'RELATED'


def test_control_a_spelled_pair_type_is_kept():
    raw = _configured_raw()
    to_add, _ = add_pairs.plan(
        _roll(pairs=[{'leg_a': 'XAGUSD.f', 'leg_b': 'SIU6',
                      'pair_type': 'SPOT_FUTURE'}]), raw)
    assert to_add['XAGUSD.f|SIU6']['pair_type'] == 'SPOT_FUTURE'


# --- one roll list for every desk ---------------------------------------

def test_the_accounts_are_read_off_the_machine_when_the_list_omits_them():
    """The whole point of not naming them: names carry the login, so
    they differ on every desk, and one file has to serve them all."""
    raw = _configured_raw()
    to_add, _ = add_pairs.plan(
        {'pairs': [{'leg_a': 'XAGUSD.f', 'leg_b': 'SIZ6'}]}, raw)
    added = to_add['XAGUSD.f|SIZ6']
    assert added['leg_a']['account'] == NAME_A
    assert added['leg_b']['account'] == NAME_B


def test_the_same_list_works_on_a_differently_named_machine():
    """A machine set up by hand, whose accounts are called something
    else entirely. The list does not change; the reading does."""
    raw = _configured_raw()
    raw['accounts'] = {'Account : 10006': raw['accounts'][NAME_A],
                       'Account : 10007': raw['accounts'][NAME_B]}
    pair = raw['pairs']['XAUUSD.f|GCZ6']
    pair['leg_a']['account'] = 'Account : 10006'
    pair['leg_b']['account'] = 'Account : 10007'

    to_add, _ = add_pairs.plan(
        {'pairs': [{'leg_a': 'XAGUSD.f', 'leg_b': 'SIZ6'}]}, raw)
    added = to_add['XAGUSD.f|SIZ6']
    assert added['leg_a']['account'] == 'Account : 10006'
    assert added['leg_b']['account'] == 'Account : 10007'


def test_control_a_named_list_still_wins():
    """Naming them is still allowed, and still takes precedence."""
    raw = _configured_raw()
    to_add, _ = add_pairs.plan(
        _roll(leg_a_account=NAME_A, leg_b_account=NAME_B,
              pairs=[{'leg_a': 'XAGUSD.f', 'leg_b': 'SIZ6'}]), raw)
    assert to_add['XAGUSD.f|SIZ6']['leg_a']['account'] == NAME_A


def test_a_machine_with_no_pairs_yet_is_refused_not_guessed():
    raw = _configured_raw()
    raw['pairs'] = {}
    with pytest.raises(add_pairs.PairsError, match='no pair to read them'):
        add_pairs.plan({'pairs': [{'leg_a': 'XAGUSD.f', 'leg_b': 'SIZ6'}]},
                       raw)


def test_pairs_that_disagree_about_the_legs_are_refused_not_guessed():
    """Two pairs routed opposite ways have no single answer, and
    picking one is how a leg lands on the wrong terminal."""
    raw = _configured_raw()
    raw['pairs']['B|A'] = {
        'leg_a': {'account': NAME_B, 'symbol': 'B'},
        'leg_b': {'account': NAME_A, 'symbol': 'A'}}
    with pytest.raises(add_pairs.PairsError, match='disagree'):
        add_pairs.plan({'pairs': [{'leg_a': 'XAGUSD.f', 'leg_b': 'SIZ6'}]},
                       raw)


# --- the account names --------------------------------------------------

def test_the_account_is_named_for_its_login():
    """`AC-100015 -> AC-100016` in the ladder header. 'Account A' told a
    trader looking at two ladders nothing at all."""
    assert configure.account_name(100015) == 'AC-100015'
    out = configure.build_config(answers(), {})
    assert set(out['accounts']) == {'AC-10006', 'AC-10007'}


def test_a_re_run_with_a_corrected_login_leaves_no_stale_row(tmp_path):
    """Names carry the login, so a corrected one writes a NEW name. The
    row it replaces sits on the same MT5 folder, and one installation
    holds one login - left behind, it is a terminal clash the trader
    never caused."""
    configure.apply_config(answers(), str(tmp_path))
    configure.apply_config(answers(login_b='10099'), str(tmp_path),
                           force=True)
    now = json.loads((tmp_path / 'config.json').read_text(encoding='utf-8'))
    assert set(now['accounts']) == {'AC-10006', 'AC-10099'}
    # And the pair that named the row now gone went with it, rather than
    # pointing at an account that cannot resolve.
    for pair in now['pairs'].values():
        assert pair['leg_b']['account'] in now['accounts']


def test_control_an_unrelated_account_is_left_alone(tmp_path):
    """Only rows on the two terminals being written are cleared. A third
    account on its own installation is somebody else's and stays."""
    configure.apply_config(answers(), str(tmp_path))
    raw = json.loads((tmp_path / 'config.json').read_text(encoding='utf-8'))
    raw['accounts']['AC-77777'] = {
        'terminal_path': r'C:\MT5-C\terminal64.exe', 'login': 77777,
        'endpoint': '127.0.0.1:9109'}
    (tmp_path / 'config.json').write_text(json.dumps(raw), encoding='utf-8')

    configure.apply_config(answers(login_b='10099'), str(tmp_path),
                           force=True)
    now = json.loads((tmp_path / 'config.json').read_text(encoding='utf-8'))
    assert 'AC-77777' in now['accounts']


# --- the default server --------------------------------------------------

def test_the_default_server_is_filled_in_from_presets():
    assert configure.default_server() == 'MentoMarkets-Server'


def test_two_legs_can_sit_at_different_brokers():
    """Filled in, not fixed. The two servers are separate answers, so a
    desk running one leg elsewhere types over the second box."""
    out = configure.build_config(
        answers(server_a='MentoMarkets-Server',
                server_b='OtherBroker-Live'), {})
    assert out['accounts'][NAME_A]['server'] == 'MentoMarkets-Server'
    assert out['accounts'][NAME_B]['server'] == 'OtherBroker-Live'


# --- the preflight -------------------------------------------------------

def _configured(tmp_path, **over):
    """A config as the wizard writes it, with the two terminals actually
    on disk so the existence check has something to find."""
    terminals = {}
    for leg in ('A', 'B'):
        folder = tmp_path / f'MT5-{leg}'
        folder.mkdir(exist_ok=True)
        exe = folder / 'terminal64.exe'
        exe.write_text('', encoding='utf-8')
        terminals[leg] = str(exe)
    raw = configure.build_config(
        answers(terminal_a=terminals['A'], terminal_b=terminals['B']), {})
    for name, changes in over.items():
        raw['accounts'][name].update(changes)
    return raw


def test_a_configured_machine_starts_with_no_terminal_open(tmp_path):
    """The whole point. An account that names its own folder is OPENED
    and signed in by the engine, so nothing needs to be running — and
    the old check refused exactly this."""
    ok, lines = preflight.check(_configured(tmp_path), terminals_running=0)
    assert ok, lines


def test_control_an_attaching_account_still_needs_a_terminal(tmp_path):
    """Blank terminal_path means attach to whatever is open. Then
    something must BE open, and refusing is right."""
    raw = _configured(tmp_path, **{NAME_A: {'terminal_path': ''}})
    ok, lines = preflight.check(raw, terminals_running=0)
    assert not ok
    assert 'no MetaTrader 5 folder set' in lines[0]

    ok_now, _ = preflight.check(raw, terminals_running=1)
    assert ok_now


def test_a_terminal_folder_that_is_not_there_is_refused(tmp_path):
    raw = _configured(tmp_path, **{
        NAME_B: {'terminal_path': str(tmp_path / 'gone' / 'terminal64.exe')}})
    ok, lines = preflight.check(raw, terminals_running=2)
    assert not ok
    assert 'no file there' in lines[0]


@pytest.mark.parametrize('field,value', [
    ('login', 10006),
    ('endpoint', '127.0.0.1:9101'),
])
def test_one_account_entered_twice_is_refused(tmp_path, field, value):
    """A hand-edit that puts one login, port or folder on both rows is a
    pair that hedges against itself. Caught before the engine starts."""
    raw = _configured(tmp_path, **{NAME_B: {field: value}})
    ok, lines = preflight.check(raw, terminals_running=2)
    assert not ok
    assert 'hedge against itself' in lines[0]


def test_one_terminal_folder_on_both_rows_is_refused(tmp_path):
    raw = _configured(tmp_path)
    raw['accounts'][NAME_B]['terminal_path'] = \
        raw['accounts'][NAME_A]['terminal_path']
    ok, lines = preflight.check(raw, terminals_running=2)
    assert not ok
    assert 'hedge against itself' in lines[0]


def test_control_two_distinct_accounts_are_allowed_through(tmp_path):
    ok, _ = preflight.check(_configured(tmp_path), terminals_running=2)
    assert ok


def test_an_unconfigured_machine_is_allowed_to_start(tmp_path):
    """A fresh install has no accounts, and they are entered ON THE
    SCREEN. Refusing here would leave the trader nowhere to enter them."""
    ok, lines = preflight.check({}, terminals_running=0)
    assert ok
    assert 'start' in lines[0]


def test_no_enabled_pair_is_said_but_not_refused(tmp_path):
    raw = _configured(tmp_path)
    for pair in raw['pairs'].values():
        pair['enabled'] = False
    ok, lines = preflight.check(raw, terminals_running=2)
    assert ok
    assert any('No pair is enabled' in line for line in lines)


# --- The Python probe inside setup.ps1 ---------------------------------
#
#     setup.ps1 decides whether a machine has a usable Python by running
#     one line of Python and reading what comes back. Windows PowerShell
#     5.1 - what SETUP.bat starts on every office PC - rebuilds a native
#     command line by quoting any argument that contains a space, and it
#     does NOT escape the double quotes already inside it. A probe
#     containing a quote therefore reached python.exe torn into pieces,
#     died of SyntaxError, and a perfectly good Python 3.11 was reported
#     as "installed but this window still cannot find it".
#
#     The interpreter cannot be run from Linux CI, so what is checked
#     here is the property that made it break: the text of the probe.

import re
import subprocess
import sys

SETUP_PS1 = (DEPLOY / 'setup.ps1').read_text(encoding='utf-8')


def _probe_line():
    match = re.search(r"^\s*\$probe = '(.*)'\s*$", SETUP_PS1, re.MULTILINE)
    assert match, 'setup.ps1 no longer assigns $probe on one line'
    return match.group(1)


def test_the_version_probe_survives_windows_powershell_quoting():
    probe = _probe_line()
    assert '"' not in probe, (
        'a double quote in the probe is re-split by Windows PowerShell '
        '5.1 and python.exe never sees the whole line')
    assert probe.isascii(), 'a console on the default code page mangles it'
    assert probe.count(' ') <= 1, (
        'keep the probe to the one unavoidable space after "import": '
        'the fewer spaces, the less PowerShell has to quote')


def test_control_the_probe_answers_what_the_script_parses():
    """The control: quote-free is worthless if the line does not run.

    This runs the REAL probe on the interpreter running the suite and
    asserts it produces exactly the three numbers setup.ps1 splits into
    major, minor and bits."""
    out = subprocess.run([sys.executable, '-c', _probe_line()],
                         capture_output=True, text=True, check=True).stdout
    parts = out.strip().split()
    assert len(parts) == 3
    assert (int(parts[0]), int(parts[1])) == sys.version_info[:2]
    assert int(parts[2]) in (32, 64)


def test_no_python_one_liner_in_setup_carries_a_double_quote():
    """Every -c in the script, not only the one that broke."""
    for argument in re.findall(r"@\('-c', '([^']*)'\)", SETUP_PS1):
        assert '"' not in argument, argument


# --- The rollout kit's shape -------------------------------------------
#
#     A trader is handed one folder and told to double-click. Everything
#     below is about what they see when they open it, and about a PC
#     that is set up TWICE because two shifts share it.

SETUP_BAT = (DEPLOY / 'SETUP.bat').read_text(encoding='utf-8')
MAKE_KIT = (DEPLOY / 'MAKE-KIT.bat').read_text(encoding='utf-8')
ROLLOUT = json.loads((DEPLOY / 'rollout.json').read_text(encoding='utf-8'))


def test_the_kit_puts_one_clickable_thing_at_the_top_level():
    """Start-Setup.bat alone; the .ps1 a trader must not click is
    tucked into setup-files with the two files it reads."""
    assert '"%KIT%\\Start-Setup.bat"' in MAKE_KIT
    for name in ('setup.ps1', 'rollout.json', 'MT5-golden.zip'):
        assert f'copy /y "%~dp0{name}"' in MAKE_KIT
        assert f'"%KIT%\\setup-files\\" >nul' in MAKE_KIT
    assert 'mkdir "%KIT%\\setup-files"' in MAKE_KIT
    assert '"%KIT%\\setup-files\\%%F"' in MAKE_KIT
    # And an older, flat kit rebuilt in place is cleaned up, or the
    # trader still sees four files and picks the wrong one.
    for stale in ('SETUP.bat', 'setup.ps1', 'rollout.json',
                  'MT5-golden.zip'):
        assert f'del /q "%KIT%\\{stale}"' in MAKE_KIT


def test_the_shim_finds_the_script_in_the_kit_layout():
    assert 'set "PS1=%~dp0setup-files\\setup.ps1"' in SETUP_BAT


def test_control_the_shim_still_finds_it_beside_itself():
    """The repository layout, where setup.ps1 IS beside SETUP.bat. A
    kit-only lookup would break every run from the clone."""
    assert 'set "PS1=%~dp0setup.ps1"' in SETUP_BAT
    assert SETUP_BAT.index('set "PS1=%~dp0setup.ps1"') < \
        SETUP_BAT.index('set "PS1=%~dp0setup-files\\setup.ps1"')


def test_a_refusal_names_the_file_that_was_actually_clicked():
    """Start-Setup.bat on a kit, SETUP.bat in the clone. A refusal
    telling a trader to re-run a file that is not in front of them is a
    refusal they cannot act on."""
    assert 'set "MT5_SETUP_NAME=%~nx0"' in SETUP_BAT
    assert '$SetupName = $env:MT5_SETUP_NAME' in SETUP_PS1
    assert "$SetupName = 'SETUP.bat'" in SETUP_PS1   # the control: a
    # direct run of setup.ps1 still names something real.
    body = SETUP_PS1[SETUP_PS1.index('$SetupName = $env:'):]
    assert "run ' + $SetupName + ' again" in body


def test_the_desktop_icon_is_named_not_hardcoded():
    """One PC running two shifts is set up twice, with two -Root
    folders. A literal name would mean the second run repointed the
    first desk's icon at the second desk's install."""
    assert "(Join-Path $desktop ($ShortcutName + '.lnk'))" in SETUP_PS1
    assert "'START TRADING.lnk'" not in SETUP_PS1
    assert '[string] $ShortcutName' in SETUP_PS1


def test_control_the_icon_has_a_default_so_nobody_must_pass_one():
    assert ROLLOUT['shortcut_name'] == 'NEXUS Terminal'
    assert "'shortcut_name' 'NEXUS Terminal'" in SETUP_PS1


def test_two_desks_on_one_pc_are_two_configs_not_four_accounts(tmp_path):
    """The reason `OFFICE-PC.md` says two installs.

    A pair is keyed by its two SYMBOLS, so the evening desk trading the
    same instruments as the morning desk would overwrite its row rather
    than sit beside it - and the second desk's accounts would be the
    ones left holding it."""
    raw = _configured(tmp_path)
    first = dict(raw['pairs'])
    second = configure.build_config(
        answers(login_a='10003', login_b='10004',
                terminal_a='C:\\MT5-C', terminal_b='C:\\MT5-D'),
        raw, example={})
    # Four accounts do coexist...
    assert len(second['accounts']) == 4
    # ...but the pair did not: same symbols, same key, one row.
    assert set(second['pairs']) == set(first)
    legs = {leg['account'] for pair in second['pairs'].values()
            for leg in (pair['leg_a'], pair['leg_b'])}
    assert legs == {'AC-10003', 'AC-10004'}


def test_control_different_symbols_on_the_second_desk_do_sit_beside(tmp_path):
    """The control: the collision is the SYMBOLS, not the accounts."""
    raw = _configured(tmp_path)
    before = set(raw['pairs'])
    second = configure.build_config(
        answers(login_a='10003', login_b='10004',
                terminal_a='C:\\MT5-C', terminal_b='C:\\MT5-D',
                symbol_a='XAGUSD.f', symbol_b='SIZ6'),
        raw, example={})
    assert set(second['pairs']) > before


# --- What the version probe is allowed to believe -----------------------
#
#     An office PC answered the probe with a line beginning
#     'Extracting:' - a wrapper around python.exe speaking before the
#     program did - and the install died with
#
#         Cannot convert value "Extracting:" to type "System.Int32"
#
#     Two defects in one line: the answer was read as "whatever came
#     back", and the [int] cast sat OUTSIDE the try that makes a bad
#     interpreter a skip rather than a crash.


def test_the_probe_answer_must_be_three_plain_numbers():
    assert r"'^(\d+)\s+(\d+)\s+(\d+)$'" in SETUP_PS1, (
        'the answer must be matched whole and anchored, or a wrapper '
        'that prints its own line gets read as a version')
    # And the cast that killed the install is gone from the raw output.
    assert '[int] $parts[2]' not in SETUP_PS1
    assert '$parts = @(([string] $out)' not in SETUP_PS1


def test_control_a_bad_answer_is_said_and_skipped_not_fatal():
    """The control: it must keep LOOKING, not stop. A machine whose
    'python' is a wrapper still has a real interpreter to find, or one
    to install."""
    body = SETUP_PS1[SETUP_PS1.index('function Test-Python'):
                     SETUP_PS1.index('function Test-StoreStub')]
    assert 'Warn (' in body, 'a skipped interpreter is named out loud'
    assert body.count('return $null') >= 3
    assert 'Fail (' not in body, 'a wrapper must never end the install'


def test_only_the_matched_digits_are_ever_cast():
    """Every [int] in the script now runs on something already proven
    to be digits. Block comments are stripped first - the explanation
    of the bug names [int] too."""
    import re as _re
    casts = _re.findall(r'\[int\]\s*(\S+)', _ps_code())
    assert casts, 'the cast disappeared entirely - has the probe changed?'
    for cast in casts:
        assert 'Groups[' in cast, cast


# --- The bare Windows 10/11 PC ------------------------------------------
#
#     Everything below is a failure somebody has already had on a fresh
#     office machine, checked here because none of it can be exercised
#     from Linux. Each guard is paired with the control that proves it
#     is a guard and not a blanket refusal.


def _ps_code(text=None):
    """setup.ps1 with its comments removed.

    Every ordering check below has to compare against the line that
    RUNS. The block comments name Invoke-WebRequest while explaining
    why the progress bar is turned off, and an index found there is
    earlier than every guard in the file."""
    import re as _re
    code = _re.sub(r'<#.*?#>', '', text if text is not None else SETUP_PS1,
                   flags=_re.S)
    return '\n'.join(l for l in code.split('\n')
                     if not l.strip().startswith('#'))


def test_tls_12_is_forced_before_anything_is_downloaded():
    """Windows PowerShell 5.1 on Windows 10 can still default to TLS
    1.0/1.1. python.org and github.com refuse those, and the failure
    reads as a firewall problem rather than a protocol one."""
    assert 'SecurityProtocolType]::Tls12' in SETUP_PS1
    code = _ps_code()
    assert code.index('Tls12') < code.index('Invoke-WebRequest')


def test_control_tls_is_added_to_what_the_host_already_has():
    """The control: -bor, not assignment. A host that negotiates TLS
    1.3 must keep it."""
    assert '-bor' in SETUP_PS1
    assert 'SecurityProtocol =\n        [Net.ServicePointManager]' \
        '::SecurityProtocol -bor' in SETUP_PS1


def test_native_exit_codes_stay_readable_on_powershell_7_4():
    """There a non-zero exit becomes TERMINATING under
    ErrorActionPreference Stop. This script reads exit codes and
    DECIDES - a failed login is a warning, not the end of the install."""
    assert '$PSNativeCommandUseErrorActionPreference = $false' in SETUP_PS1
    assert 'Test-Path variable:PSNativeCommandUseErrorActionPreference' \
        in SETUP_PS1


def test_git_is_found_on_disk_when_path_has_not_caught_up():
    """The lesson Python taught this script three times: PATH in a
    console that was already open does not learn about an install."""
    assert 'function Find-Git' in SETUP_PS1
    assert "'Git\\cmd\\git.exe'" in SETUP_PS1
    # And every later git call goes through what Find-Git returned, or
    # the fallback buys nothing.
    for line in _ps_code().split('\n'):
        stripped = line.strip()
        if stripped.startswith('git ') or ' | git ' in stripped:
            raise AssertionError('bare git call: ' + stripped)


def test_control_the_git_installers_own_exit_code_is_read():
    """The control: a refused install must not be reported as a PATH
    problem - that is what sent the operator hunting last time."""
    assert SETUP_PS1.count('-Wait -PassThru') >= 2      # Git and Python
    assert 'The Git installer failed with exit code' in SETUP_PS1
    assert 'so Git could not be' in SETUP_PS1           # 1618


def test_a_32_bit_pc_is_refused_before_any_download():
    assert '[Environment]::Is64BitOperatingSystem' in SETUP_PS1
    code = _ps_code()
    assert code.index('Is64BitOperatingSystem') < code.index('Invoke-WebRequest')


def test_a_full_disk_is_refused_before_any_download():
    assert '$needGb = 3' in SETUP_PS1
    code = _ps_code()
    assert code.index('$needGb') < code.index('Invoke-WebRequest')


def test_control_an_unmeasurable_drive_is_not_treated_as_full():
    """Unmeasured is not zero - the same rule the engine follows for a
    leg it could not read."""
    assert 'if ($drive -and $null -ne $drive.Free) {' in SETUP_PS1


def test_the_desktop_icon_can_never_fail_the_install():
    """By then the code is cloned, both terminals are unpacked, the
    suite has passed and both accounts have logged in. Controlled
    folder access must not undo that."""
    tail = SETUP_PS1[SETUP_PS1.index("Step 'Desktop shortcut'"):]
    assert 'try {' in tail and '} catch {' in tail
    assert 'Controlled folder access' in tail
    assert 'Fail (' not in tail


def test_control_a_bad_icon_name_is_cleaned_rather_than_refused():
    assert r"""$safeName = ($ShortcutName -replace '[\\/:*?"<>|]', '-')""" \
        in SETUP_PS1
    assert "if (-not $safeName) { $safeName = 'NEXUS Terminal' }" in SETUP_PS1


# --- The four .bat files a desk runs after the install ------------------
#
#     setup.ps1 runs once. These run every day, on the trader's own PC,
#     with nobody watching - so the same Windows facts that bit the
#     installer are checked here too.

DAILY_BATS = {
    name: (DEPLOY.parent / name if name == 'START-TRADING.bat'
           else DEPLOY / name).read_text(encoding='utf-8')
    for name in ('START-TRADING.bat', 'UPDATE.bat', 'VERIFY.bat',
                 'ADD-PAIRS.bat')
}


def test_every_daily_script_is_plain_ascii():
    """A console on the default code page turns anything else into
    mojibake in the one message that matters."""
    for name, text in DAILY_BATS.items():
        assert text.isascii(), name


def test_a_3_14_desk_is_not_told_it_has_no_python():
    """rollout.json allows 3.14 and the suite has passed on it, so a PC
    set up that way is normal. Asking the launcher only for 3.11 sent
    such a machine down the 'no Python at all' path."""
    for name, text in DAILY_BATS.items():
        assert 'py -3.14 -c' in text, name
        assert 'py -3 -c' in text, name
        assert text.index('py -3.11 -c') < text.index('py -3.14 -c') \
            < text.index('py -3 -c') < text.index('python -c'), name


def test_control_the_launcher_is_still_asked_first():
    """The control: PATH must not win. py -3.11 on a machine that also
    has 3.9 on PATH is the good outcome."""
    for name, text in DAILY_BATS.items():
        assert text.index('set "PY="') < text.index('py -3.11 -c'), name


def test_a_32_bit_python_stops_every_daily_script():
    """MT5's handshake fails against 32-bit with an error that says
    nothing. Starting looks like it worked, which is worse."""
    for name, text in DAILY_BATS.items():
        assert 'struct.calcsize(chr(80))*8==64' in text, name
        assert "This machine's Python is 32-bit" in text, name


def test_control_an_untested_version_only_warns(caplog=None):
    """The control, and it is the point of the pair: the bitness guard
    must not become a blanket refusal. A version the suite has not run
    on may well be fine, and stopping a trader at 9am over a version
    number is the wrong trade-off."""
    for name, text in DAILY_BATS.items():
        after = text[text.index('sys.version_info[:2] in ((3,11),(3,14))'):]
        # The parenthesised block that reacts to it, and nothing beyond.
        block = after[after.index('if errorlevel 1 ('):]
        block = block[:block.index('\n)')]
        assert '[!]' in block, name
        assert 'exit /b' not in block, name
        assert 'pause' not in block, name


def test_no_quote_character_inside_a_cmd_python_one_liner():
    """cmd ends a quoted argument on the first inner quote, exactly as
    PowerShell mangled the installer's probe. chr(80) is why these read
    the way they do."""
    import re as _re
    for name, text in DAILY_BATS.items():
        for arg in _re.findall(r'-c "([^"]*)"', text):
            assert '"' not in arg, (name, arg)


def test_pip_does_not_print_a_wall_of_yellow_it_cannot_act_on():
    """On a per-user Python, Scripts\\ is not on PATH and pip warns once
    per console script. Nothing in this repo runs pytest, flask or
    playwright BY NAME, so the warning is noise that reads like a
    failure to whoever is watching."""
    for name, text in DAILY_BATS.items():
        for call in [l for l in text.split('\n') if '-m pip install' in l]:
            assert '--no-warn-script-location' in call, (name, call)
    assert "'--no-warn-script-location'" in SETUP_PS1


def test_control_nothing_is_ever_run_by_bare_script_name():
    """The control that makes the suppression safe. If anything called
    pytest, pip, flask or playwright DIRECTLY, Scripts\\ would have to
    be on PATH and silencing the warning would hide a real fault.

    Checked per call site rather than per line: setup.ps1 wraps its
    argument lists, so the '-m' can sit on the line above the tool."""
    import re as _re
    for name, text in DAILY_BATS.items():
        for line in text.split('\n'):
            if line.strip().startswith('REM'):
                continue
            for tool in ('pytest', 'flask', 'playwright', 'pip'):
                if _re.search(r'%PY%\s+' + tool + r'\b', line):
                    raise AssertionError((name, line.strip()))
    # setup.ps1: every quoted tool argument must have '-m' just before
    # it, whatever the line breaks look like.
    flat = ' '.join(SETUP_PS1.split())
    for tool in ('pytest', 'pip'):
        for hit in _re.finditer(r"'" + tool + r"'", flat):
            before = flat[max(0, hit.start() - 120):hit.start()]
            assert "'-m'" in before, (tool, before[-60:])


def test_declining_the_update_prompt_does_not_slam_the_window_shut():
    """'It never leaves the trader with a black window' is a rule this
    file states and then broke on its own prompt."""
    text = DAILY_BATS['UPDATE.bat']
    block = text[text.index('if /i not "%GOON%"=="YES"'):][:200]
    assert 'pause' in block


# --- What the office actually trades ------------------------------------

PAIRS_JSON = json.loads((DEPLOY / 'pairs.json').read_text(encoding='utf-8'))
PRESETS_JSON = json.loads((DEPLOY / 'presets.json').read_text(
    encoding='utf-8'))


def test_the_roll_list_is_the_two_pairs_this_desk_trades():
    names = [(p['leg_a'], p['leg_b'], p['pair_type'])
             for p in PAIRS_JSON['pairs']]
    assert names == [('XAUUSD.c', 'GCZ6.s', 'SPOT_FUTURE'),
                     ('USOILX6.c', 'UKOILX6.s', 'RELATED')]


def test_the_two_legs_carry_the_suffix_of_their_own_account():
    """Not a typo and not decoration. The legs sit on DIFFERENT
    accounts with different symbol suffixes, so a spelling copied from
    the wrong terminal is a symbol that cannot trade - the pair sits
    there reading unknown."""
    for pair in PAIRS_JSON['pairs']:
        assert pair['leg_a'].endswith('.c'), pair
        assert pair['leg_b'].endswith('.s'), pair
    for preset in PRESETS_JSON['presets']:
        assert preset['leg_a'].endswith('.c'), preset
        assert preset['leg_b'].endswith('.s'), preset


def test_control_the_wizard_offers_the_same_instruments(tmp_path):
    """The control that keeps the two files honest with each other. A
    fresh PC is seeded by presets.json and topped up by pairs.json; if
    they name different instruments, the first desk installed gets a
    pair nobody else has."""
    presets = configure.load_presets(str(tmp_path))
    assert presets, 'deploy/presets.json produced no pairs'
    # Compared on the INSTRUMENT, because the contract code is exactly
    # what differs between a seed and a roll.
    def instruments(rows):
        found = set()
        for row in rows:
            for leg in (row['leg_a'], row['leg_b']):
                for known in ('XAUUSD', 'GC', 'USOIL', 'UKOIL'):
                    if leg.startswith(known):
                        found.add(known)
        return found

    assert instruments(presets) == instruments(PAIRS_JSON['pairs'])
    assert instruments(presets) == {'XAUUSD', 'GC', 'USOIL', 'UKOIL'}
    for preset in presets:
        assert preset['pair_type'] in ('SPOT_FUTURE', 'FUTURE_FUTURE',
                                       'RELATED')


def test_the_spread_row_says_which_way_each_side_wants_it_to_go():
    """H -> L under the Bid, L -> H under the Ask. The two columns are
    not two prices of the same thing: the Bid is where the spread is
    SOLD and a short profits as it falls; the Ask is where it is
    BOUGHT and a long profits as it rises."""
    app_js = (DEPLOY.parent / 'mt5trader' / 'static' /
              'app.js').read_text(encoding='utf-8')
    hint = app_js[app_js.index('spread-hint'):][:900]
    assert 'H &rarr; L' in hint and 'L &rarr; H' in hint
    assert hint.index('H &rarr; L') < hint.index('L &rarr; H')
    assert 'c-bid hint-down' in hint and 'c-ask hint-up' in hint


def test_control_the_hint_is_only_on_the_spread_row():
    """The control. The header above is shared with the two LEG rows,
    where bid and ask are just that leg's own book and say nothing
    about direction - a hint there would be wrong, not merely noisy."""
    app_js = (DEPLOY.parent / 'mt5trader' / 'static' /
              'app.js').read_text(encoding='utf-8')
    assert app_js.count('hint-down') == 1
    assert app_js.count('hint-up') == 1
    # Directly ABOVE the spread row, so the label and the number it is
    # about are read together - and never in the shared header.
    assert app_js.index('spread-hint') < app_js.index('<tr class=\"spread\">')
    between = app_js[app_js.index('spread-hint'):
                     app_js.index('<tr class=\"spread\">')]
    assert '<tr' not in between.replace('spread-hint', ''), \
        'something was inserted between the hint and the row it labels'


# --- A stray Python must not block the install --------------------------
#
#     A fresh EC2 box had Python 3.7 on PATH. Find-Python returned it,
#     the version check refused the whole setup, and the operator was
#     told to go and install 3.11 by hand - by a script whose very next
#     line installs 3.11 by itself.


def test_a_python_this_project_cannot_use_is_not_a_find():
    """It is stepped over, not returned. Whether a machine happens to
    have some other Python on PATH is a fact about the machine, not a
    reason to stop installing."""
    code = _ps_code()
    assert 'function Test-Usable' in code
    # Every route out of Find-Python goes through the filter.
    body = code[code.index('function Find-Python'):
                code.index('function Assert-Python')]
    for line in body.split('\n'):
        if 'return $found' in line:
            assert 'Test-Usable' in line, line.strip()
    assert body.count('Test-Usable $found') >= 3


def test_control_a_usable_python_is_still_used_rather_than_reinstalled():
    """The control. The filter must not become 'install every time' -
    a machine that already has a proven 64-bit interpreter uses it."""
    code = _ps_code()
    assert '$Found.Bits -eq 64' in code
    assert '($PyVersions -contains $Found.Version)' in code
    assert 'if (Test-Usable $found) { return $found }' in code


def test_the_stray_python_is_said_out_loud_and_left_alone():
    """Said, because a desk should know why a second Python appeared.
    Left alone, because whatever else on that PC uses it goes on
    using it - nothing is uninstalled and nothing is repointed."""
    code = _ps_code()
    assert 'is being ' in code and 'installed alongside it' in code
    assert 'left ' in code and 'exactly as it is' in code
    for word in ('uninstall', 'Remove-Item $found', 'Uninstall'):
        assert word not in code[code.index('function Find-Python'):
                                code.index('function Assert-Python')]


def test_the_installer_does_the_tick_boxes_itself():
    """'Add python.exe to PATH' and 'py launcher' are switches on the
    silent install, not something anybody clicks."""
    code = _ps_code()
    assert 'PrependPath=1' in code
    assert 'Include_launcher=1' in code
    assert 'InstallLauncherAllUsers=1' in code
    assert 'InstallAllUsers=1' in code
    assert '/quiet' in code


def test_control_the_requirements_are_why_3_7_cannot_be_allowed():
    """Not a preference. Flask 3, pytest 8 and python-dotenv 1 all
    require 3.8 or newer, so a 3.7 box cannot install the dependencies
    whatever this script decides."""
    requirements = (DEPLOY.parent / 'requirements.txt').read_text(
        encoding='utf-8')
    assert 'Flask>=3' in requirements
    assert 'pytest>=8' in requirements
    assert 'python-dotenv>=1' in requirements
    assert '3.7' not in ROLLOUT['python_versions']


# --- The pair's name is a field like any other --------------------------


def test_the_contract_code_reaches_the_pair_name_too():
    """A desk read `USOIL{contract} | UKOIL{contract}` above a ladder
    that was trading USOILX6.c against UKOILX6.s. Every other field
    had the token substituted; the NAME was the one that never did,
    and the name is what the ladder header and the Pairs table show."""
    preset = {'label': 'x', 'name': 'USOIL{contract} | UKOIL{contract}',
              'leg_a': 'USOIL{contract}.c', 'leg_b': 'UKOIL{contract}.s',
              'pair_type': 'RELATED'}
    assert configure.expand_name(preset, 'X6', 'X6') == 'USOILX6 | UKOILX6'
    assert '{contract}' not in configure.expand_name(preset, 'X6', 'X6')


def test_two_different_months_are_named_after_the_right_leg_each():
    """A calendar spread dates both legs. Leg A's code fills the first
    token and leg B's the rest, or the pair is named after one month
    twice."""
    preset = {'label': 'x', 'name': 'GC{contract} vs GC{contract}',
              'leg_a': 'GC{contract}', 'leg_b': 'GC{contract}',
              'pair_type': 'FUTURE_FUTURE'}
    assert configure.expand_name(preset, 'Z6', 'H7') == 'GCZ6 vs GCH7'


def test_control_a_name_with_no_token_is_left_exactly_as_it_is():
    """The control. Most presets do not date their name at all, and
    substitution must not touch them."""
    preset = {'label': 'x', 'name': 'XAUUSD | GCZ6',
              'leg_a': 'XAUUSD.c', 'leg_b': 'GCZ6.s'}
    assert configure.expand_name(preset, 'Z6', 'Z6') == 'XAUUSD | GCZ6'
    assert configure.expand_name({'label': 'x'}, 'Z6', 'Z6') == ''


# --- The clone the trader is not allowed to read ------------------------
#
#     A desk ran deploy\UPDATE.BAT and was told 'This folder is not a
#     git clone'. It was a clone, and a healthy one: SETUP asks for
#     Administrator and clones as that account, UPDATE is double-clicked
#     as the trader, and git refuses a repository owned by somebody
#     else. Its complaint went into a 2>nul and nobody ever saw it.


def test_the_two_faults_are_told_apart():
    """No .git at all is a different problem from a .git git will not
    touch, and one message for both sent an operator off to reinstall a
    machine whose clone was fine."""
    update = DAILY_BATS['UPDATE.bat']
    assert 'if not exist ".git" (' in update
    assert 'There is no .git folder here' in update
    assert 'There IS a clone here, but git will not read it' in update


def test_git_says_it_in_its_own_words():
    """The refusal is re-run WITHOUT 2>nul so the reason reaches the
    screen. 'dubious ownership' is a sentence an operator can act on;
    'not a git clone' was not even true."""
    update = DAILY_BATS['UPDATE.bat']
    after = update[update.index('There IS a clone here'):]
    after = after[:after.index(':know_where_we_are')]
    assert '\ngit rev-parse HEAD\n' in after, 'the error is never shown'
    assert 'dubious ownership' in after
    assert 'safe.directory' in after


def test_control_a_readable_clone_is_not_sent_down_either_path():
    """The control. The check must still pass straight through on the
    normal machine - it is a diagnosis, not a new gate."""
    update = DAILY_BATS['UPDATE.bat']
    assert 'if defined WAS goto :know_where_we_are' in update
    assert update.index('if defined WAS goto :know_where_we_are') < \
        update.index('if not exist ".git" (')
    assert ':know_where_we_are' in update


def test_setup_registers_the_clone_for_every_account_on_the_pc():
    """Fixing the cause, not only the message. SETUP is the one process
    that is already Administrator, so it is the one that can do this."""
    code = _ps_code()
    assert 'safe.directory' in code
    assert '--system' in code, '--global would write it into the ' \
        'Administrator profile, the one account that did not need it'
    assert "$safe = $Root -replace '\\\\', '/'" in code


def test_control_a_failure_to_register_does_not_fail_the_install():
    """The control. The install is complete either way, and UPDATE now
    names the fix in its own refusal - so this must warn, never stop."""
    code = _ps_code()
    block = code[code.index("$safe = $Root"):]
    block = block[:block.index("Step 'Dependencies'")]
    assert 'try {' in block and '} catch {' in block
    assert 'Warn (' in block
    assert 'Fail (' not in block


def test_the_ownership_fix_needs_no_administrator():
    """--system writes into C:\\Program Files\\Git\\etc\\gitconfig, so a
    Command Prompt that is not truly elevated answers 'access denied -
    cannot lock config file'. That is a second dead end on top of the
    first, in front of somebody who has already been told to reinstall.

    --global writes to the profile of whoever is sitting there - the
    account that runs UPDATE, and the account git is refusing."""
    update = DAILY_BATS['UPDATE.bat']
    offer = update[update.index('There IS a clone here'):]
    offer = offer[:offer.index(':know_where_we_are')]
    assert 'git config --global --add safe.directory' in offer
    # And it is OFFERED, not printed for somebody to type.
    assert 'set /p "FIXIT=' in offer
    assert offer.index('--global') < offer.index('--system'), \
        'the no-elevation fix has to come first'


def test_control_the_all_accounts_fix_is_still_offered_as_the_second():
    """The control: --global is per-user, so a PC with two shifts still
    needs the system-wide one, and SETUP - already Administrator - goes
    on doing exactly that."""
    update = DAILY_BATS['UPDATE.bat']
    assert 'EVERY account on this PC' in update
    assert 'git config --system --add safe.directory' in update
    assert '--system' in _ps_code()


def test_control_declining_the_offer_changes_nothing():
    """The control that matters on a trading box. Answering anything
    but YES must leave the machine exactly as it was."""
    update = DAILY_BATS['UPDATE.bat']
    assert 'if /i not "%FIXIT%"=="YES" goto :not_fixed' in update
    after = update[update.index(':not_fixed'):]
    assert 'Nothing has been changed' in after
    assert 'exit /b 1' in after


# --- The kit must point at the trunk ------------------------------------


def test_the_rollout_kit_installs_from_the_trunk():
    """rollout.json is the ONE place a new PC learns which branch to
    clone. It sat on a claude/... working branch through the rollout,
    and when main became the trunk that branch stopped moving - so
    every PC built from the kit would have been installed onto frozen
    code, silently.

    A feature branch is a fine thing to point at deliberately for one
    machine (Start-Setup.bat takes -Branch). It is not a thing to ship
    to the office on a USB stick."""
    branch = ROLLOUT['branch']
    assert branch == 'main', branch
    assert not branch.startswith('claude/'), (
        'the kit is shipping a working branch to every new PC')


def test_control_the_branch_is_still_configurable():
    """The control. Pinning it to main in the file must not mean the
    branch has stopped being a setting - a desk testing a fix needs to
    point one machine somewhere else."""
    code = _ps_code()
    assert "$Branch    = Setting $Branch    'branch'" in code
    assert '[string] $Branch' in code          # and on the command line


def test_no_document_still_names_the_old_working_branch():
    """Two documents told an operator to check out a branch that is no
    longer the trunk. A runbook that is wrong about the branch is worse
    than one that says nothing."""
    for name in ('OFFICE-PC.md', 'RUNBOOK.md'):
        text = (DEPLOY / name).read_text(encoding='utf-8')
        assert 'monitoring-positions-market-grid' not in text, name
