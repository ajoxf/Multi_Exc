"""Messages to the trader's phone — Telegram, for the arbitrage alert.

Credentials live only in `.env`, like every other secret here:

    TELEGRAM_BOT_TOKEN="123456:ABC..."   # from @BotFather
    TELEGRAM_CHAT_ID="123456789"         # your chat with the bot

Never on the poll: a message goes from a background thread, so a slow
or unreachable Telegram can never delay a price, a guard or a close. A
failure is logged once per kind and never raised. The token is never
written to a log line, not even in an error.
"""

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request

API = 'https://api.telegram.org/bot{token}/sendMessage'
#: The least gap between two messages, so a burst cannot flood a phone.
MIN_GAP_SEC = 3.0


class TelegramNotifier:
    def __init__(self, token=None, chat_id=None, opener=None,
                 clock=time.monotonic, background=True):
        self.token = token if token is not None else \
            os.environ.get('TELEGRAM_BOT_TOKEN')
        self.chat_id = chat_id if chat_id is not None else \
            os.environ.get('TELEGRAM_CHAT_ID')
        self.opener = opener or urllib.request.urlopen
        self.clock = clock
        self.background = background
        self._last = None
        self._lock = threading.Lock()
        self._warned = set()
        self.sent = 0
        self.last_error = None

    @property
    def configured(self):
        return bool(self.token and self.chat_id)

    def send(self, text):
        """Queue one message. Returns False when it cannot be sent at all
        (not configured, or rate-limited) — never raises."""
        if not self.configured:
            if 'unconfigured' not in self._warned:
                self._warned.add('unconfigured')
                logging.info("telegram: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID "
                             "not set in .env — alerts stay on the screen")
            return False
        with self._lock:
            now = self.clock()
            if self._last is not None and now - self._last < MIN_GAP_SEC:
                return False
            self._last = now
        if self.background:
            threading.Thread(target=self._deliver, args=(text,),
                             daemon=True).start()
            return True
        return self._deliver(text)

    def _deliver(self, text):
        body = json.dumps({'chat_id': self.chat_id, 'text': text,
                           'disable_web_page_preview': True}).encode()
        request = urllib.request.Request(
            API.format(token=self.token), data=body,
            headers={'Content-Type': 'application/json'})
        try:
            with self.opener(request, timeout=10) as reply:
                answer = json.loads(reply.read().decode() or '{}')
            if not answer.get('ok'):
                raise ValueError(answer.get('description') or 'not ok')
            self.sent += 1
            self.last_error = None
            return True
        except (urllib.error.URLError, OSError, ValueError) as e:
            # Telegram's own words, but never the URL: it holds the token.
            reason = getattr(e, 'reason', None) or str(e)
            reason = str(reason).replace(self.token or '\0', '<token>')
            self.last_error = reason
            if reason not in self._warned:
                self._warned.add(reason)
                logging.warning("telegram: message not delivered — %s", reason)
            return False
