"""Telegram outages and proxy routing must not affect the website process."""
import os
import unittest
import urllib.request
from unittest.mock import patch
from app import telegram_bot


class Flaky:
    def __init__(self, failures):
        self.failures = failures
        self.calls = []

    def call(self, method, payload=None, timeout=45):
        self.calls.append(method)
        if self.failures:
            self.failures -= 1
            raise telegram_bot.BotError('offline')
        return {'url': ''} if method == 'getWebhookInfo' else {'ok': True}


class BotRuntime(unittest.TestCase):
    def test_connect_waits_with_backoff_instead_of_exiting(self):
        client, delays = Flaky(3), []
        self.assertEqual(telegram_bot.connect(client, sleep=delays.append), {'url': ''})
        self.assertEqual(delays, [5, 10, 20])
        self.assertEqual(client.calls[-2:], ['getMe', 'getWebhookInfo'])

    def test_backoff_is_capped(self):
        delays = []
        with self.assertRaises(telegram_bot.BotError):
            telegram_bot.connect(Flaky(99), sleep=delays.append, attempts=8)
        self.assertEqual(max(delays), 300)

    def test_proxy_applies_only_to_telegram_client(self):
        with patch.dict(os.environ, {'TELEGRAM_PROXY': 'http://user:secret@192.0.2.10:9801', 'TELEGRAM_BOT_TOKEN': 'test:token'}):
            client = telegram_bot.Telegram()
        handlers = [h for h in client.open.__self__.handlers if isinstance(h, urllib.request.ProxyHandler)]
        self.assertEqual(handlers[0].proxies['https'], 'http://user:secret@192.0.2.10:9801')
        with patch.dict(os.environ, {'TELEGRAM_PROXY': '', 'TELEGRAM_BOT_TOKEN': 'test:token'}):
            self.assertIs(telegram_bot.Telegram().open, urllib.request.urlopen)

    def test_invalid_proxy_is_rejected_without_echoing_credentials(self):
        for value in ('socks5://u:p@192.0.2.1:1080', 'http://192.0.2.1', 'ftp://u:p@h:1', 'http://u:p@h:1/path'):
            with self.subTest(value=value), patch.dict(os.environ, {'TELEGRAM_PROXY': value, 'TELEGRAM_BOT_TOKEN': 'test:token'}):
                with self.assertRaises(telegram_bot.BotError) as error:
                    telegram_bot.Telegram()
                self.assertNotIn('u:p', str(error.exception))


if __name__ == '__main__':
    unittest.main()
