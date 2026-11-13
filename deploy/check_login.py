"""Can this machine actually log a terminal in? Answer it in one run.

This exists to settle ONE question, and the whole office rollout turns
on the answer:

    Does a freshly installed MetaTrader 5, never signed in by hand,
    accept `initialize(path=..., login=..., password=..., server=...)`
    for a server it has not been told about?

If YES, a new PC needs nothing but the program folder, and SETUP can
stay three clicks.

If NO, the server name cannot be resolved until the terminal has the
broker in its own list - and that list lives in
`%APPDATA%\\MetaQuotes\\Terminal\\<hash>\\config\\servers.dat`, NOT in
the program folder, so copying `C:\\MT5-A` to a new machine cannot
carry it. The rollout then has to move that folder too.

Reaching for `/portable` to force the settings into the program folder
is the obvious fix and the wrong one: a terminal started portable
refuses IPC from a normally-started Python, so the legs would never
connect. `mt5trader/mt5_errors.py` already records that.

It goes through `mt5trader.broker`, which is the only module allowed to
import MetaTrader5 - and, usefully, the same code path the engine uses,
so a success here is a success for real rather than for a probe.

    py -3.11 deploy\\check_login.py --terminal "C:\\MT5-A\\terminal64.exe" ^
        --login 100015 --server MentoMarkets-Server

The password is read from `.env` (or from the environment) the way the
engine reads it, so it is never typed on a command line where it would
land in the console history.
"""

import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mt5trader import config as cfg                             # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--terminal', required=True,
                        help=r'The terminal64.exe to log in, e.g. '
                             r'C:\MT5-A\terminal64.exe')
    parser.add_argument('--login', required=True, type=int)
    parser.add_argument('--server', required=True)
    parser.add_argument('--name', default=None,
                        help='The account name whose .env key holds the '
                             'password. Defaults to AC-<login>.')
    parser.add_argument('--env', default=None,
                        help='Path to the .env holding the password.')
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format='  %(message)s')

    name = args.name or f'AC-{args.login}'
    env_path = args.env or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')
    key = cfg.env_key_for(name)
    if not os.environ.get(key):
        # The engine's own reader, so a password with a space or a #
        # arrives here exactly as it arrives there.
        try:
            from dotenv import load_dotenv
            load_dotenv(env_path)
        except ImportError:
            pass
    if not os.environ.get(key):
        print(f'No password. This reads {key} from {env_path} - the same '
              f'key and the same file the engine uses. Put it there, or '
              f'set that variable in this window, and run again.')
        return 2

    account = cfg.AccountConfig(name, terminal_path=args.terminal,
                                login=args.login, password_env=key,
                                server=args.server)

    print(f'  Terminal: {args.terminal}')
    print(f'  Login:    {args.login} on {args.server}')
    print('')

    # Imported HERE, not at the top: this module must be importable on
    # a machine with no MetaTrader5 package for the rest of the suite to
    # collect, and the refusal below is friendlier than an ImportError.
    try:
        from mt5trader.broker import BrokerSession
    except ImportError as e:
        print(f'The MetaTrader5 package is not installed here ({e}). '
              f'Run: pip install -r requirements.txt')
        return 2

    session = BrokerSession(account)
    ok = session.initialize()
    print('')
    if ok:
        print('  CONNECTED.')
        print('')
        print('  This terminal logged in WITHOUT being signed in by hand,')
        print('  so a new PC needs only the program folder. The rollout')
        print('  stays as designed.')
        return 0

    print('  NOT CONNECTED - the reason is in the lines above.')
    print('')
    print('  If it names the SERVER or an unknown/invalid account, this')
    print('  terminal cannot resolve the server by name on its own. The')
    print('  broker list lives in %APPDATA%\\MetaQuotes\\Terminal\\<hash>\\')
    print('  config\\servers.dat, not in the program folder, so the')
    print('  rollout has to carry that folder as well as C:\\MT5-A.')
    print('')
    print('  Do NOT use /portable to force the settings into the program')
    print('  folder: a portable terminal refuses IPC from a normally')
    print('  started Python and the legs would never connect.')
    return 1


if __name__ == '__main__':
    sys.exit(main())
