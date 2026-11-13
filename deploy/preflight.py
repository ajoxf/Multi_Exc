"""What NEXUS Terminal (START-TRADING.bat) checks before it starts.

It exists because the batch file cannot read `config.json`, and the one
question that decides whether a start can work is IN that file: does
each account name its own terminal?

    terminal_path SET   MT5 launches that terminal and logs it in
                        (`mt5.initialize(path=..., login=...)`), so the
                        trader does not open MetaTrader 5 at all.
    terminal_path BLANK the runner attaches to whatever terminal is
                        already open, so one must be - and a refusal
                        here is right.

The old check refused whenever no `terminal64.exe` was running. On a
configured machine that is a refusal to start something that would have
worked, which on a trading desk at 9am is the expensive kind of wrong.

Exit codes, for the batch file:
    0  go
    1  stop, and the reason is on stdout in words

    py -3.11 deploy\\preflight.py --config config.json
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mt5trader import config as cfg                             # noqa: E402


def _accounts(raw):
    return {name: (acct or {})
            for name, acct in (raw.get('accounts') or {}).items()}


def check(raw, terminals_running=0):
    """(ok, lines). `lines` is what to show, refusal or warning alike.

    `terminals_running` is how many `terminal64.exe` the batch file
    counted. It is ADVISORY when the accounts name their terminals and
    DECIDING when they do not.
    """
    lines = []
    accounts = _accounts(raw)
    if not accounts:
        return True, ['No accounts are configured yet. The engine will '
                      'start and open the page where you enter them.']

    enabled = {key: pair for key, pair in (raw.get('pairs') or {}).items()
               if (pair or {}).get('enabled', True)}
    if not enabled:
        lines.append('No pair is enabled, so no ladder will trade. The '
                     'engine still starts, and the page can enable one.')

    # The three ways one account becomes two rows on the screen. Each is
    # a config the engine would start on and then hedge against itself.
    for field, what in (('login', 'login'),
                        ('terminal_path', 'MetaTrader 5 installation'),
                        ('endpoint', 'port')):
        seen = {}
        for name, acct in accounts.items():
            value = str(acct.get(field) or '').strip()
            if field == 'terminal_path':
                value = value.lower()
            if not value:
                continue
            if value in seen:
                return False, [
                    f"Accounts '{seen[value]}' and '{name}' share one "
                    f"{what} ({acct.get(field)}). That is ONE account "
                    f"entered twice - both legs would trade it and the "
                    f"pair would hedge against itself. Fix it on the "
                    f"Exchanges page, or re-run deploy\\configure.py."]
            seen[value] = name

    missing = [name for name, acct in accounts.items()
               if not str(acct.get('terminal_path') or '').strip()]
    if missing:
        # These accounts attach to whatever is open, so something has
        # to BE open. This is the only case that still refuses.
        if terminals_running < len(missing):
            return False, [
                f"{', '.join(missing)} has no MetaTrader 5 folder set, so it "
                f"attaches to a terminal that is already open - and "
                f"{terminals_running} are. Open the terminal, log it into "
                f"its account, press Algo Trading so it turns green, then "
                f"run this again. (Setting the folder on the Exchanges page "
                f"lets the engine open it for you next time.)"]
        lines.append(f"{', '.join(missing)} will attach to a terminal that "
                     f"is already open. Check it is signed into the right "
                     f"account.")

    launched = [name for name, acct in accounts.items()
                if str(acct.get('terminal_path') or '').strip()]
    for name in launched:
        path = accounts[name]['terminal_path']
        if not os.path.exists(path):
            return False, [
                f"Account '{name}' points at {path}, and there is no file "
                f"there. Either MetaTrader 5 was never unzipped to that "
                f"folder, or it was moved. Re-run deploy\\SETUP.bat, or "
                f"correct the path on the Exchanges page."]
    if launched:
        lines.append(f"{', '.join(launched)}: the engine opens the terminal "
                     f"and signs it in.")
    return True, lines


def _count(value):
    """However many terminals the batch file managed to count.

    Deliberately not `type=int`. The count is produced by piping
    `tasklist` through `find /c`, and if that ever hands over something
    that is not a number, argparse would exit 2 - which the batch file
    reads as a REFUSAL and the trader reads as a machine that will not
    start. An uncountable count is 0, which is only ever more cautious.
    """
    try:
        return max(0, int(str(value).strip()))
    except (TypeError, ValueError):
        return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default='config.json')
    parser.add_argument('--terminals-running', type=_count, default=0)
    args = parser.parse_args(argv)

    try:
        raw = cfg.load_raw(args.config)
    except RuntimeError as e:
        # load_raw already refused rather than returning {}, because a
        # tolerant read in front of a save is how accounts get deleted.
        print(str(e))
        return 1
    ok, lines = check(raw, args.terminals_running)
    for line in lines:
        print(line)
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
