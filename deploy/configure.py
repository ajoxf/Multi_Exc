"""The setup wizard: six questions, then `config.json` and `.env`.

This replaces the Exchanges page for a first install. A trader who has
never seen the app answers two logins, two passwords, two servers and
one instrument pair, and the machine is configured.

Three rules shape every line below.

**It never invents a format.** The `.env` key, the free port, and the
three clash refusals all come from `mt5trader.config` - the same
functions the Exchanges page calls. A wizard that hand-rolls
`MT5_PASSWORD_` + name is a wizard that writes a key the loader does
not read, and the symptom is a password that is silently never there.

**It never overwrites a working config.** A machine that is already
trading gets a refusal, not a fresh file. `save_raw` keeps a `.bak`
either way, but the refusal is what stops the mistake.

**Passwords go to `.env` and nowhere else.** Not into `config.json`,
not into a print, not into the exception text. `write_env_value` quotes
them so a password with a space or a `#` survives.

Run it from the repo root:

    py -3.11 deploy\\configure.py

or, for an unattended install, with the answers in a file:

    py -3.11 deploy\\configure.py --answers answers.json
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mt5trader import config as cfg                             # noqa: E402
from mt5trader.hedgeratio import pair_signature                 # noqa: E402


class SetupError(Exception):
    """A refusal the operator can act on. The message is shown as-is, so
    it says what is wrong and what to type instead - never 'check the
    log', which on a trader's PC means nothing."""


#: The token a preset leaves for the trader to fill in: the contract
#: month and year, which changes every roll and so cannot be shipped.
CONTRACT_TOKEN = '{contract}'


def _presets_path(root=None):
    """Where presets.json is, preferring the machine being configured.

    Normally these are the same file: `--root` is the clone, and this
    script lives in its `deploy` folder. They differ when the wizard is
    pointed at another folder, and then the one beside THIS script is
    the sensible fallback - losing the whole menu (and the default
    server with it) because a path was given is worse than reading the
    copy we were shipped with.
    """
    beside = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          'presets.json')
    if root:
        in_root = os.path.join(root, 'deploy', 'presets.json')
        if os.path.exists(in_root):
            return in_root
    return beside


def load_presets(root=None):
    """The pairs the wizard offers, from `deploy/presets.json`.

    A FILE, not a table in this module. The office adds an instrument
    by editing data on one machine; baking the list in here would make
    every new pair a code change and a release to every PC.

    A presets file that will not parse gives an EMPTY list, not a
    crash: the trader can still type both symbols, and a setup that
    refuses to open over a stray comma is worse than one with no menu.
    """
    try:
        with open(_presets_path(root), 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    presets = data.get('presets') if isinstance(data, dict) else data
    return [p for p in (presets or []) if isinstance(p, dict) and p.get('label')]


def default_server(root=None):
    """The server name to fill both boxes with, from `presets.json`.

    Filled in, NOT fixed. Both boxes stay editable and they are asked
    for separately, so a desk whose two legs sit at different brokers
    types the second one over and nothing else changes.
    """
    try:
        with open(_presets_path(root), 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError):
        return ''
    return str((data or {}).get('default_server') or '') \
        if isinstance(data, dict) else ''


def preset_needs(preset):
    """(leg A needs a contract code, leg B needs one).

    Driven by where `{contract}` actually appears, so a preset that
    dates both legs - a calendar spread - asks twice, and one that
    dates neither asks nothing.
    """
    return (CONTRACT_TOKEN in str(preset.get('leg_a') or ''),
            CONTRACT_TOKEN in str(preset.get('leg_b') or ''))


def expand_preset(preset, contract_a='', contract_b=''):
    """The two symbol names, with the contract codes filled in.

    Refuses a blank code where one is needed rather than writing
    `GC` and leaving the trader to find out at the first connect that
    the symbol does not exist.
    """
    needs_a, needs_b = preset_needs(preset)
    contract_a = str(contract_a or '').strip()
    contract_b = str(contract_b or '').strip()
    for needed, value, leg in ((needs_a, contract_a, 'Leg A'),
                               (needs_b, contract_b, 'Leg B')):
        if needed and not value:
            raise SetupError(
                f'{leg} needs a contract month. That is the code your '
                f'broker puts after the instrument for the month and year '
                f'- Z6, 1226, Z2026 - and it changes at every roll, which '
                f'is why it is asked for rather than shipped.')
    symbol_a = str(preset.get('leg_a') or '').replace(CONTRACT_TOKEN,
                                                      contract_a)
    symbol_b = str(preset.get('leg_b') or '').replace(CONTRACT_TOKEN,
                                                      contract_b)
    return symbol_a, symbol_b


def expand_name(preset, contract_a='', contract_b=''):
    """The pair's DISPLAY name, with the contract codes filled in too.

    A preset's name may carry the token as its legs do -
    `USOIL{contract} | UKOIL{contract}` - and it was the only field
    that never had it substituted. The result reached the ladder
    header and the Pairs table literally, so a desk read
    `USOIL{contract} | UKOIL{contract}` above a ladder trading
    USOILX6.c against UKOILX6.s.

    Leg A's code fills the first token and leg B's the rest: a pair
    whose two legs are different months would otherwise be named after
    one of them twice.
    """
    name = str(preset.get('name') or '')
    if not name:
        return name
    contract_a = str(contract_a or '').strip()
    contract_b = str(contract_b or '').strip()
    if CONTRACT_TOKEN in name and contract_a:
        name = name.replace(CONTRACT_TOKEN, contract_a, 1)
    if contract_b:
        name = name.replace(CONTRACT_TOKEN, contract_b)
    return name.replace(CONTRACT_TOKEN, contract_a).strip()


#: How the two accounts are named: `AC-` and the MT5 login.
#:
#: The name is what the ladder header prints - `AC-100015 -> AC-100016`
#: - and what the Positions and Fills tables put in their account
#: column. 'Account A' told a trader looking at two ladders nothing at
#: all; the login is the number they see in MetaTrader 5, on the
#: broker's statement, and in the account panel right beside it.
#:
#: The form is the desk's own: the machines set up by hand already read
#: AC-100015, so nothing new has to be learned.
#:
#: It is also what `env_key_for` turns into the `.env` key
#: (AC-10006 -> MT5_PASSWORD_AC_10006), which is why the key is asked
#: for rather than spelled anywhere.
ACCOUNT_PREFIX = 'AC-'


def account_name(login):
    """What this account is called, everywhere it is shown."""
    return f'{ACCOUNT_PREFIX}{login}'


#: Where SETUP.bat unzips the two terminals. Two FOLDERS, never one
#: folder and a shortcut: a terminal holds one login, so two runners on
#: one installation are one account trading against itself.
DEFAULT_TERMINAL_A = r'C:\MT5-A\terminal64.exe'
DEFAULT_TERMINAL_B = r'C:\MT5-B\terminal64.exe'


def repo_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _example_raw(root):
    try:
        with open(os.path.join(root, 'config.example.json'),
                  'r', encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def is_pristine(raw, example):
    """Is this config still the shipped example, or somebody's work?

    `start.py` copies `config.example.json` to `config.json` on a first
    run, so "the file exists" is NOT evidence that anyone configured
    anything. Replacing the example is right; replacing a desk's real
    accounts is the mistake this exists to prevent.
    """
    if not raw:
        return True
    if not (raw.get('accounts') or raw.get('pairs')):
        return True
    return (raw.get('accounts') == (example.get('accounts') or {})
            and raw.get('pairs') == (example.get('pairs') or {}))


def _need(value, what):
    text = str(value or '').strip()
    if not text:
        raise SetupError(f'{what} is blank. Every field on this page is '
                         f'needed before the engine can connect.')
    return text


def _login(value, which):
    text = _need(value, f'{which} login')
    try:
        return int(text)
    except ValueError:
        raise SetupError(
            f"{which} login {text!r} is not a number. An MT5 login is the "
            f"digits the broker gave you (for example 10006), not the "
            f"e-mail address you sign in to their website with.") from None


def build_config(answers, raw, example=None):
    """The new config as a dict, or a `SetupError` saying why not.

    Pure: it reads nothing and writes nothing. That is what lets the
    guards below be tested - each one with a control that turns it off
    and asserts the save goes through.
    """
    example = example or {}
    raw = dict(raw or {})

    login_a = _login(answers.get('login_a'), 'Account A')
    login_b = _login(answers.get('login_b'), 'Account B')
    server_a = _need(answers.get('server_a'), 'Account A server')
    server_b = _need(answers.get('server_b'), 'Account B server')
    _need(answers.get('password_a'), 'Account A password')
    _need(answers.get('password_b'), 'Account B password')
    symbol_a = _need(answers.get('symbol_a'), 'Leg A symbol')
    symbol_b = _need(answers.get('symbol_b'), 'Leg B symbol')
    terminal_a = _need(answers.get('terminal_a') or DEFAULT_TERMINAL_A,
                       'Account A terminal')
    terminal_b = _need(answers.get('terminal_b') or DEFAULT_TERMINAL_B,
                       'Account B terminal')

    if login_a == login_b:
        raise SetupError(
            f'Both accounts have login {login_a}. That is one MT5 account '
            f'entered twice - both legs would trade it and the pair would '
            f'hedge against itself. Account B needs the SECOND account the '
            f'broker gave you.')
    if terminal_a.strip().lower() == terminal_b.strip().lower():
        raise SetupError(
            f'Both accounts point at {terminal_a}. One MetaTrader 5 '
            f'installation holds ONE login, so both legs would end up on '
            f'the same account. Give Account B its own folder - SETUP '
            f'makes {DEFAULT_TERMINAL_B} for exactly this.')
    if symbol_a.strip().lower() == symbol_b.strip().lower():
        raise SetupError(
            f'Both legs are {symbol_a}. A spread of an instrument against '
            f'itself is always zero - Leg B must be the other contract.')

    # Settings only. The example's ACCOUNTS and PAIRS are placeholders
    # with a fictional broker in them; carrying those across would leave
    # a row on the screen that can never connect.
    settings = raw.get('settings') or example.get('settings') or {}

    name_a, name_b = account_name(login_a), account_name(login_b)

    accounts = dict(raw.get('accounts') or {})
    pairs = dict(raw.get('pairs') or {})
    if is_pristine(raw, example):
        accounts, pairs = {}, {}

    # Clear the ground the two new rows are about to stand on.
    #
    # Their own names go first, so a re-run overwrites rather than
    # reading this machine's OWN endpoints as taken and walking the
    # pair up to 9103, 9105, 9107 a run at a time.
    #
    # Then any other row on one of these two TERMINALS. Names carry the
    # login now, so re-running with a corrected login writes new names
    # and would otherwise leave the old row pointing at the same
    # folder - and one MT5 installation holds one login, so the pair
    # would refuse to start with a terminal clash it did not cause.
    # A pair whose leg named a row removed here goes with it: it names
    # an account that is no longer there and could never resolve.
    doomed = {name_a, name_b}
    ours = {terminal_a.strip().lower(), terminal_b.strip().lower()}
    for other, acct in list(accounts.items()):
        if str((acct or {}).get('terminal_path') or '').strip().lower() in ours:
            doomed.add(other)
    for name in doomed:
        accounts.pop(name, None)
    for key, pair in list(pairs.items()):
        legs = {(pair or {}).get('leg_a', {}).get('account'),
                (pair or {}).get('leg_b', {}).get('account')}
        if legs & doomed:
            pairs.pop(key, None)

    endpoint_a = (answers.get('endpoint_a') or '').strip()
    endpoint_b = (answers.get('endpoint_b') or '').strip()
    accounts[name_a] = {
        'terminal_path': terminal_a,
        'login': login_a,
        'password_env': cfg.env_key_for(name_a),
        'server': server_a,
        # One port serves ONE leg runner. Asking the config for a free
        # one keeps this right even on a box that already had accounts.
        'endpoint': endpoint_a or cfg.next_free_port({'accounts': accounts}),
    }
    accounts[name_b] = {
        'terminal_path': terminal_b,
        'login': login_b,
        'password_env': cfg.env_key_for(name_b),
        'server': server_b,
        'endpoint': endpoint_b or cfg.next_free_port({'accounts': accounts}),
    }

    staged = {'accounts': accounts}
    for name in (name_a, name_b):
        acct = accounts[name]
        for clash in (cfg.endpoint_clash(staged, name, acct['endpoint']),
                      cfg.login_clash(staged, name, acct['login']),
                      cfg.terminal_clash(staged, name, acct['terminal_path'])):
            if clash:
                raise SetupError(clash)

    key = f'{symbol_a}|{symbol_b}'
    pairs[key] = {
        'name': answers.get('name') or key,
        'leg_a': {'account': name_a, 'symbol': symbol_a},
        'leg_b': {'account': name_b, 'symbol': symbol_b},
        'pair_type': cfg.pair_type_name(answers.get('pair_type')),
        # Beta is stamped with what it was computed FOR, so a stale one
        # from another instrument cannot silently define the spread.
        'hedge_ratio': 1.0,
        'hedge_ratio_for': pair_signature(symbol_a, symbol_b),
        # Deliberately unset: the app reads tick size, contract size and
        # lot steps from MT5 on the first connect. A number typed here
        # is a number that can be wrong, and every money figure on the
        # screen runs through it.
        'increment': None,
        'clip_lots_a': None,
        'clip_lots_b': None,
        'default_quantity': 1.0,
        'order_type': 'LIMIT',
        'time_in_force': 'DAY',
        'overnight': 'ALLOW',
        'quoting_leg': None,
        'enabled': True,
        'rows': 30,
    }

    out = dict(raw)
    out['accounts'] = accounts
    out['pairs'] = pairs
    out['settings'] = settings
    return out


def apply_config(answers, root=None, force=False):
    """Write `config.json` and `.env`. Returns the paths written.

    Refuses a machine that is already configured unless `force` - and
    `save_raw` keeps a `.bak` beside the file even then.
    """
    root = root or repo_root()
    config_path = os.path.join(root, 'config.json')
    env_path = os.path.join(root, '.env')
    example = _example_raw(root)
    raw = cfg.load_raw(config_path)

    if not force and not is_pristine(raw, example):
        names = ', '.join(sorted((raw.get('accounts') or {}))) or 'none'
        raise SetupError(
            f'{config_path} already has accounts on it ({names}). This '
            f'machine is set up. Re-run with --force to replace it - the '
            f'old file is kept as config.json.bak.')

    out = build_config(answers, raw, example)
    cfg.save_raw(config_path, out)

    # LAST, and only here. A password must never reach config.json, a
    # print, or an exception message.
    for leg, field in (('a', 'password_a'), ('b', 'password_b')):
        # The same name build_config wrote, from the same login, so the
        # key here and the key in the config cannot drift apart.
        name = account_name(_login(answers.get(f'login_{leg}'), 'Account'))
        cfg.write_env_value(env_path, cfg.env_key_for(name),
                            answers.get(field))
    return config_path, env_path


# --- the window ---------------------------------------------------------

def _run_gui(root, terminal_a, terminal_b):          # pragma: no cover
    """Six questions in one window. No menus, no tabs, no scrolling.

    The two terminal folders are NOT among the six: SETUP made them and
    passes them in. A trader asked to type a path is a trader who can
    type the same path twice, and two accounts on one installation is
    the failure this whole setup is arranged to prevent.
    """
    import tkinter as tk
    from tkinter import messagebox, ttk

    win = tk.Tk()
    win.title('MT5-Trader setup')
    win.resizable(False, False)
    pad = {'padx': 8, 'pady': 4}
    fields = {}

    # Filled in for both legs, and editable on both. The two servers are
    # separate answers on purpose: a desk running one leg at a second
    # broker types over the second box and nothing else changes.
    server = default_server(root)

    def account_frame(text, grid_row, keys):
        """One account: login, password, server. The grid row is the
        position WITHIN this frame, so the two frames cannot drift."""
        frame = ttk.LabelFrame(win, text=text)
        frame.grid(row=grid_row, column=0, sticky='ew', padx=10, pady=6)
        for line, (label, key, show) in enumerate(keys):
            ttk.Label(frame, text=label).grid(row=line, column=0,
                                              sticky='e', **pad)
            entry = ttk.Entry(frame, width=32, show=show)
            if key.startswith('server') and server:
                entry.insert(0, server)
            entry.grid(row=line, column=1, sticky='w', **pad)
            fields[key] = entry

    account_frame('Account A  (Leg A)', 0,
                  [('Login', 'login_a', None),
                   ('Password', 'password_a', '*'),
                   ('Server', 'server_a', None)])
    account_frame('Account B  (Leg B)', 1,
                  [('Login', 'login_b', None),
                   ('Password', 'password_b', '*'),
                   ('Server', 'server_b', None)])

    presets = load_presets(root)
    by_label = {p['label']: p for p in presets}
    TYPE_MY_OWN = 'Type my own...'

    frame_p = ttk.LabelFrame(win, text='What this desk trades')
    frame_p.grid(row=2, column=0, sticky='ew', padx=10, pady=6)
    ttk.Label(frame_p, text='Pair').grid(row=0, column=0, sticky='e', **pad)
    choice = ttk.Combobox(frame_p, width=38, state='readonly',
                          values=list(by_label) + [TYPE_MY_OWN])
    choice.current(0)
    choice.grid(row=0, column=1, sticky='w', **pad)

    # The contract month asked for PER LEG, because a calendar spread
    # dates both and a basis dates only the future.
    ttk.Label(frame_p, text='Leg A contract').grid(row=1, column=0,
                                                   sticky='e', **pad)
    con_a = ttk.Entry(frame_p, width=12)
    con_a.grid(row=1, column=1, sticky='w', **pad)
    ttk.Label(frame_p, text='Leg B contract').grid(row=2, column=0,
                                                   sticky='e', **pad)
    con_b = ttk.Entry(frame_p, width=12)
    con_b.grid(row=2, column=1, sticky='w', **pad)

    ttk.Label(frame_p, text='Leg A symbol').grid(row=3, column=0, sticky='e',
                                                 **pad)
    sym_a = ttk.Entry(frame_p, width=30)
    sym_a.grid(row=3, column=1, sticky='w', **pad)
    ttk.Label(frame_p, text='Leg B symbol').grid(row=4, column=0, sticky='e',
                                                 **pad)
    sym_b = ttk.Entry(frame_p, width=30)
    sym_b.grid(row=4, column=1, sticky='w', **pad)

    hint = ttk.Label(frame_p, text='', foreground='#555')
    hint.grid(row=5, column=0, columnspan=2, sticky='w', **pad)

    def selected():
        return by_label.get(choice.get())

    def refresh(*_):
        """Show only the boxes this pair actually needs.

        A greyed box the trader cannot type in says 'not your problem'
        far more clearly than an enabled one they have to guess about.
        """
        preset = selected()
        if not preset:
            con_a.configure(state='disabled')
            con_b.configure(state='disabled')
            sym_a.configure(state='normal')
            sym_b.configure(state='normal')
            hint.configure(text='Type both symbols exactly as your broker '
                                'spells them.')
            return
        needs_a, needs_b = preset_needs(preset)
        con_a.configure(state='normal' if needs_a else 'disabled')
        con_b.configure(state='normal' if needs_b else 'disabled')
        # The symbols are shown, and read-only, so the trader can SEE
        # what is about to be written without being able to break it.
        sym_a.configure(state='normal')
        sym_b.configure(state='normal')
        sym_a.delete(0, 'end')
        sym_b.delete(0, 'end')
        sym_a.insert(0, str(preset.get('leg_a') or ''))
        sym_b.insert(0, str(preset.get('leg_b') or ''))
        sym_a.configure(state='readonly')
        sym_b.configure(state='readonly')
        if needs_a or needs_b:
            hint.configure(text='Contract = the month and year code, e.g. '
                                'Z6 or 1226.')
        else:
            hint.configure(text='')

    choice.bind('<<ComboboxSelected>>', refresh)
    refresh()

    def save():
        preset = selected()
        answers = {key: entry.get() for key, entry in fields.items()}
        try:
            if preset:
                symbol_a, symbol_b = expand_preset(preset, con_a.get(),
                                                   con_b.get())
                pair_type = preset.get('pair_type', 'SPOT_FUTURE')
                name = expand_name(preset, con_a.get(), con_b.get())
            else:
                symbol_a, symbol_b = sym_a.get(), sym_b.get()
                # RELATED: the reading with NO fair value, which is the
                # safe way to be wrong about a pair nobody described.
                # Calling two unknown symbols a basis GIVES them a fair
                # value they may not have; the Exchanges page can
                # correct it in one click once somebody looks.
                pair_type, name = 'RELATED', None
        except SetupError as e:
            messagebox.showerror('Not saved', str(e))
            return
        answers.update({'symbol_a': symbol_a, 'symbol_b': symbol_b,
                        'pair_type': pair_type, 'name': name,
                        'terminal_a': terminal_a, 'terminal_b': terminal_b})
        try:
            config_path, _ = apply_config(answers, root)
        except SetupError as e:
            messagebox.showerror('Not saved', str(e))
            return
        except Exception as e:                       # noqa: BLE001
            messagebox.showerror('Not saved', f'{type(e).__name__}: {e}')
            return
        messagebox.showinfo(
            'Saved',
            f'This machine is configured.\n\n{config_path}\n\n'
            f'Double-click NEXUS Terminal on the Desktop to begin.')
        win.destroy()

    ttk.Button(win, text='Save and finish', command=save).grid(
        row=3, column=0, pady=10)
    win.mainloop()
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--answers',
                        help='JSON file of answers, for an unattended '
                             'install. Without it a window is shown.')
    parser.add_argument('--root', default=repo_root(),
                        help='The MT5-Trader folder to configure.')
    parser.add_argument('--force', action='store_true',
                        help='Replace a config that already has accounts. '
                             'The old one is kept as config.json.bak.')
    parser.add_argument('--terminal-a', default=DEFAULT_TERMINAL_A,
                        help='Account A terminal64.exe, as SETUP unpacked '
                             'it.')
    parser.add_argument('--terminal-b', default=DEFAULT_TERMINAL_B,
                        help='Account B terminal64.exe. Must be a DIFFERENT '
                             'folder from Account A.')
    args = parser.parse_args(argv)

    if not args.answers:
        try:
            return _run_gui(args.root, args.terminal_a, args.terminal_b)
        except ImportError:
            print('This Python has no tkinter, so the window cannot open.\n'
                  'Write the answers to a file and pass --answers instead.',
                  file=sys.stderr)
            return 2

    with open(args.answers, 'r', encoding='utf-8') as f:
        answers = json.load(f)
    # The answers file wins; the flags are what SETUP knows about this
    # machine, which is better than the built-in default.
    answers.setdefault('terminal_a', args.terminal_a)
    answers.setdefault('terminal_b', args.terminal_b)
    # Same default the window fills in, so an unattended install and a
    # watched one produce the same config from the same answers.
    server = default_server(args.root)
    if server:
        answers.setdefault('server_a', server)
        answers.setdefault('server_b', server)
    try:
        config_path, env_path = apply_config(answers, args.root, args.force)
    except SetupError as e:
        print(str(e), file=sys.stderr)
        return 1
    # The paths, never the values: one of those files is passwords.
    print(f'Wrote {config_path}')
    print(f'Wrote {env_path}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
