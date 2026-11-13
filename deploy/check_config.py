"""Log every account in `config.json` in, and say which ones worked.

The last step of an install, and the first thing to run when a desk
says "it will not connect". It answers the only question that matters
before a machine is handed to a trader: can THIS PC reach BOTH accounts
with the credentials it has been given?

Run at the end of SETUP, in front of whoever is installing, so a wrong
password or a mistyped login is found there rather than at 9am with
nobody around. The refusal carries the broker's own words - 10027,
"Invalid account", "Authorization failed" - because "check the log"
on a trader's PC means nothing.

It goes through `mt5trader.broker`, the only module allowed to import
MetaTrader5, so a success here is the same code path the engine uses.

    py -3.11 deploy\\check_config.py --config config.json

Exit code 0 only if EVERY configured account connected.
"""

import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mt5trader import config as cfg                             # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default='config.json')
    parser.add_argument('--env', default=None,
                        help='The .env holding the passwords. Defaults to '
                             'the one beside the config.')
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format='  %(message)s')

    config_path = os.path.abspath(args.config)
    raw = cfg.load_raw(config_path)
    accounts = raw.get('accounts') or {}
    if not accounts:
        print('  No accounts configured yet - nothing to check.')
        return 0

    env_path = args.env or os.path.join(os.path.dirname(config_path), '.env')
    try:
        from dotenv import load_dotenv
        load_dotenv(env_path)
    except ImportError:
        pass

    try:
        from mt5trader.broker import BrokerSession
    except ImportError as e:
        print(f'  The MetaTrader5 package is not installed here ({e}). '
              f'Run: pip install -r requirements.txt')
        return 2

    failed = []
    for name in sorted(accounts):
        account = cfg.AccountConfig.from_dict(name, accounts[name])
        print('')
        print(f'  --- {name} (login {account.login}) ---')
        if not account.password:
            # Named exactly, because the fix is to put that key in that
            # file and nothing else will do.
            print(f'  NO PASSWORD: {account.password_env} is not in '
                  f'{env_path}.')
            failed.append(name)
            continue
        # Each account gets its OWN session. The package attaches to one
        # terminal at a time, so checking them in one is how the second
        # ends up reporting the first one's connection.
        session = BrokerSession(account)
        if session.initialize():
            print(f'  OK - {name} connected.')
            try:
                session.shutdown()
            except Exception:                            # noqa: BLE001
                pass
        else:
            print(f'  FAILED - {name} did not connect (reason above).')
            failed.append(name)

    print('')
    if failed:
        print(f'  {len(failed)} of {len(accounts)} account(s) did not '
              f'connect: {", ".join(failed)}')
        print('')
        print('  Do not hand this machine to a trader yet. The commonest')
        print('  causes, in order: the password is wrong or was pasted with')
        print('  a stray space; the login belongs to a different server;')
        print('  the terminal folder in config.json is not the one that was')
        print('  installed.')
        return 1

    print(f'  All {len(accounts)} account(s) connected.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
