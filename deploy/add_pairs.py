"""Put this month's contracts onto a machine that is already set up.

The roll list lives in `pairs.json`, which whoever maintains this edits
when the contract months change. The trader double-clicks ADD-PAIRS.BAT
and the new ladders appear on their Exchanges page.

It only ever ADDS. That is the whole design:

  * a pair already in the config is REPORTED and LEFT ALONE - not
    re-written, not re-enabled, not re-stamped. A trader may be holding
    a position on it, and editing the pair under a live position moves
    the ladder out from under the money;
  * nothing is ever deleted or disabled;
  * accounts are never touched at all.

So the worst a wrong roll list can do is add a pair nobody wanted,
which is visible on the screen and removable in one click. It cannot
take away a pair somebody is trading.

    py -3.11 deploy\\add_pairs.py
    py -3.11 deploy\\add_pairs.py --pairs other-list.json --dry-run
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mt5trader import config as cfg                             # noqa: E402
from mt5trader.hedgeratio import pair_signature                 # noqa: E402


class PairsError(Exception):
    """A refusal in words the trader can act on."""


def repo_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_list(path):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except FileNotFoundError:
        raise PairsError(
            f'{path} is not there. That file is the list of contracts to '
            f'add, and it comes from whoever maintains this - ask them '
            f'for the current one.') from None
    except ValueError as e:
        raise PairsError(
            f'{path} is not valid JSON ({e}). It has been edited by hand '
            f'and something is out of place - a missing comma, usually. '
            f'Nothing has been changed on this machine.') from None
    if not isinstance(data, dict) or not data.get('pairs'):
        raise PairsError(f'{path} lists no pairs to add.')
    return data


def pair_key(symbol_a, symbol_b):
    """The key everything else writes: no stray whitespace, one spelling
    of the separator."""
    return f'{str(symbol_a or "").strip()}|{str(symbol_b or "").strip()}'


def infer_accounts(raw):
    """Which account is leg A on THIS machine, and which is leg B.

    Read off the pairs the machine already trades. That is not a guess:
    it is what this config does every day, and the answer is only used
    to put a new pair on the same two accounts as the existing ones.

    It exists so ONE roll list serves the whole office. Account names
    carry the MT5 login now - AC-100015 - so they differ on every
    desk, and a list that named them would need editing per machine,
    which is exactly the job ADD-PAIRS is here to remove.

    Ambiguity is refused rather than resolved. A machine whose pairs
    disagree about which account is leg A has no single answer, and
    picking one would be the guess that puts a leg on the wrong
    terminal.
    """
    on_a, on_b = set(), set()
    for pair in (raw.get('pairs') or {}).values():
        name_a = ((pair or {}).get('leg_a') or {}).get('account')
        name_b = ((pair or {}).get('leg_b') or {}).get('account')
        if name_a:
            on_a.add(name_a)
        if name_b:
            on_b.add(name_b)

    if not on_a or not on_b:
        raise PairsError(
            'The roll list does not name the two accounts, and this '
            'machine has no pair to read them from yet. Run the setup '
            'wizard first (deploy\\configure.py), or add '
            '"leg_a_account" and "leg_b_account" to the list.')
    if len(on_a) > 1 or len(on_b) > 1:
        raise PairsError(
            f"This machine's pairs disagree about which account is which: "
            f"leg A is on {', '.join(sorted(on_a))} and leg B on "
            f"{', '.join(sorted(on_b))}. Refusing to pick one - a leg on "
            f"the wrong terminal is not something to guess at. Name the "
            f"two accounts in the roll list instead.")
    return on_a.pop(), on_b.pop()


def plan(spec, raw):
    """(to_add, notes) - what would change, without changing anything.

    Pure, so `--dry-run` and the real run cannot disagree about what is
    about to happen.
    """
    accounts = raw.get('accounts') or {}
    if not accounts:
        raise PairsError(
            'This machine has no accounts yet, so there is nothing to hang '
            'a pair on. Run the setup wizard first: deploy\\configure.py.')

    account_a = str(spec.get('leg_a_account') or '').strip()
    account_b = str(spec.get('leg_b_account') or '').strip()
    if not account_a and not account_b:
        account_a, account_b = infer_accounts(raw)

    for name, which in ((account_a, 'leg_a_account'),
                        (account_b, 'leg_b_account')):
        if not name:
            raise PairsError(f'The roll list does not say which account '
                             f'{which} is.')
        if name not in accounts:
            raise PairsError(
                f"The roll list points at an account called '{name}', and "
                f"this machine has {', '.join(sorted(accounts))}. Either "
                f"the list is for a differently-named setup, or this "
                f"machine was configured by hand. Nothing has been "
                f"changed.")
    if account_a == account_b:
        raise PairsError(
            f"The roll list puts both legs on '{account_a}'. Both legs on "
            f"one account is one account trading against itself.")

    existing = raw.get('pairs') or {}
    to_add, notes = {}, []
    for entry in spec['pairs']:
        symbol_a = str((entry or {}).get('leg_a') or '').strip()
        symbol_b = str((entry or {}).get('leg_b') or '').strip()
        if not symbol_a or not symbol_b:
            notes.append(f'skipped {entry!r}: a leg has no symbol.')
            continue
        if symbol_a.lower() == symbol_b.lower():
            notes.append(f'skipped {symbol_a}: both legs are the same '
                         f'symbol, and that spread is always zero.')
            continue
        key = pair_key(symbol_a, symbol_b)
        if key in existing:
            # Left EXACTLY as it is. Somebody may be holding a position
            # on it, and re-writing the pair moves the ladder under it.
            notes.append(f'{key} is already here - left alone.')
            continue
        if key in to_add:
            notes.append(f'{key} is in the list twice - added once.')
            continue
        to_add[key] = {
            'name': entry.get('name') or key,
            'leg_a': {'account': account_a, 'symbol': symbol_a},
            'leg_b': {'account': account_b, 'symbol': symbol_b},
            'pair_type': cfg.pair_type_name(entry.get('pair_type')),
            'hedge_ratio': 1.0,
            'hedge_ratio_for': pair_signature(symbol_a, symbol_b),
            # Read from MT5 on the first connect. A number typed into a
            # roll list is a number that can be wrong, and every money
            # figure on the ladder runs through it.
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
    return to_add, notes


def apply(spec, config_path, dry_run=False):
    """Add what is missing. Returns (added keys, notes)."""
    raw = cfg.load_raw(config_path)
    to_add, notes = plan(spec, raw)
    if not to_add or dry_run:
        return sorted(to_add), notes
    raw.setdefault('pairs', {}).update(to_add)
    # allow_shrink stays FALSE: this endpoint only ever grows the file,
    # so a save that would drop a section is a bug, not an edit.
    cfg.save_raw(config_path, raw)
    return sorted(to_add), notes


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',
                        default=os.path.join(repo_root(), 'config.json'))
    parser.add_argument('--pairs',
                        default=os.path.join(os.path.dirname(
                            os.path.abspath(__file__)), 'pairs.json'))
    parser.add_argument('--dry-run', action='store_true',
                        help='Say what would be added, and change nothing.')
    args = parser.parse_args(argv)

    try:
        spec = load_list(args.pairs)
        added, notes = apply(spec, args.config, args.dry_run)
    except PairsError as e:
        print(str(e))
        return 1
    except RuntimeError as e:
        # load_raw refused rather than returning {} - a tolerant read in
        # front of a save is how a config gets emptied.
        print(str(e))
        return 1

    for note in notes:
        print('  ' + note)
    if not added:
        print('')
        print('  Nothing to add - this machine already has every pair on '
              'the list.')
        return 0
    for key in added:
        print(f'  ADDED {key}')
    print('')
    if args.dry_run:
        print('  Dry run: nothing was written.')
    else:
        print(f'  {len(added)} pair(s) added. If the engine is running it '
              f'restarts itself within a few seconds and the new ladders '
              f'appear; if it is not, they are there next time you press '
              f'NEXUS Terminal.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
