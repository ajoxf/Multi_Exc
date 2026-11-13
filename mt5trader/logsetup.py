"""Logs, on disk, for the morning after.

A trader reported that two legs of a live spread had been closed by
this system sixty seconds after they put them on. The reconciler said
so at CRITICAL, with the account, the ticket and the reason - into a
console window that had been closed. There was nothing on disk. The
incident could not be investigated, only reasoned about, and reasoning
about money is not good enough.

Every process writes here now: the launcher, the web server, the
coordinator and both leg runners, each to its own file so a leg's
chatter never buries a reconciler decision.

ROTATING, and small enough that a year of them is megabytes rather
than a full disk - a trading box that stops because a log filled the
volume is a worse fault than the one being logged.

APPEND, never truncate. The file survives a restart, which is exactly
the event most worth reading across.

The console keeps everything it had. This adds a second destination;
it takes nothing away, and a desk watching the black window sees what
it always did.
"""

import logging
import logging.handlers
import os

#: Where they go, beside the code, next to config.json - the folder an
#: operator is already being asked to look in.
LOG_DIR_NAME = 'logs'

#: Per file, and how many kept. 2 MB x 5 is ten megabytes per process:
#: enough to cover days of normal running, and days is the window an
#: incident is reported in.
MAX_BYTES = 2_000_000
BACKUPS = 5

#: The line. A timestamp with milliseconds, because two legs filling in
#: the same second is the ordinary case and the ORDER of them is the
#: whole question. The module and line number, so a message can be
#: found in the source that wrote it.
FORMAT = ('%(asctime)s.%(msecs)03d %(levelname)-8s [%(process)d] '
          '%(module)s:%(lineno)d %(message)s')
DATEFMT = '%Y-%m-%d %H:%M:%S'


def log_dir(root=None):
    root = root or os.getcwd()
    return os.path.join(root, LOG_DIR_NAME)


def setup(name, root=None, level=logging.INFO, max_bytes=MAX_BYTES,
          backups=BACKUPS):
    """Add a rotating file handler for this process. Returns its path.

    Idempotent: called twice in one process it does not add a second
    handler, so nothing is ever written to the same file twice.

    Never raises. A read-only folder, a full disk or a locked file must
    not stop a trading process from starting - it means we lose the
    logs, which is bad, and stopping the engine over it would be worse.
    """
    root = root or os.getcwd()
    directory = log_dir(root)
    path = os.path.join(directory, f'{name}.log')
    logger = logging.getLogger()
    for existing in logger.handlers:
        if getattr(existing, '_mt5trader_log', None) == path:
            return path
    try:
        os.makedirs(directory, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            path, maxBytes=max_bytes, backupCount=backups,
            encoding='utf-8', delay=False)
    except Exception as e:                       # read-only, full, locked
        logging.warning('could not open the log file %s (%s) - this '
                        'process logs to its console only', path, e)
        return None
    handler.setFormatter(logging.Formatter(FORMAT, datefmt=DATEFMT))
    handler.setLevel(level)
    handler._mt5trader_log = path
    logger.addHandler(handler)
    # The ROOT logger has to pass the record down to the handler at all.
    # Left at WARNING, an INFO line is dropped before any handler sees
    # it - and the INFO lines are the ones that say what led up to the
    # CRITICAL one.
    if logger.level == logging.NOTSET or logger.level > level:
        logger.setLevel(level)
    logging.info('logging to %s', path)
    return path
