"""Offline Telegram linking, identity, revocation and callback security."""
import base64
import hashlib
import hmac
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from app import auth, config, security, service, store, telegram_bot, notifications
from app.context import scope
from fixture_clock import historical


class TelegramLinkSecurity(unittest.TestCase):
    def setUp(self):
        clock = historical(); clock.start(); self.addCleanup(clock.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'APP_ENV':'local', 'AUTH_REQUIRED':'1',
            'DATABASE_ENCRYPTION':'0', 'APP_SESSION_SECRET':'unit-only-'+'x'*48,
            'TELEGRAM_ALLOWED_USER_IDS':'101,202', 'TELEGRAM_ALLOW_PUBLIC':'0'})
        self.env.start()
        self.db = patch.object(store, 'DB_PATH', str(Path(self.tmp.name)/'test.db'))
        self.db.start()
        self.ai = patch.object(config, 'API_KEY', '')
        self.ai.start()
        store.init()
        _, self.cookie = auth.register({'username':'alice', 'password':'unit test password 123'})
        self.account = auth.session_owner(self.cookie)
        self.n = 0

    def tearDown(self):
        self.ai.stop(); self.db.stop(); self.env.stop(); self.tmp.cleanup()

    def update(self, text=None, data=None):
        self.n += 1
        user = {'id':101, 'username':'telegram_alice', 'first_name':'Alice', 'last_name':'Test'}
        msg = {'from':user, 'chat':{'id':101,'type':'private'}}
        if text is not None:msg['text'] = text
        if data:return {'update_id':self.n, 'callback_query':{'id':str(self.n), 'from':user, 'message':msg, 'data':data}}
        return {'update_id':self.n, 'message':msg}

    def handle(self, text=None, data=None):
        class Fake:
            def call(self, *args, **kwargs):return True
        return telegram_bot.handle(self.update(text, data), Fake())

    def link(self):
        with scope('tg:101'):
            store.account({'chat_id':101})
            code = security.make_link()
        with scope(self.account):auth.link_telegram(code)

    def unlink(self):
        with scope(self.account):return auth.unlink_telegram({'confirmed':True, 'password':'unit test password 123'})

    def test_linked_bot_never_issues_account_ownership_code(self):
        self.link()
        result = self.handle('/link')
        self.assertIn('уже связан', result['text'])
        with store.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM link_codes').fetchone()[0], 0)
        with scope(self.account), self.assertRaises(ValueError):security.make_link()
        with scope('tg:101'), self.assertRaises(ValueError):security.make_link()
        self.assertIn('alice', self.handle('/account')['text'])
        self.assertIn('101', self.handle('/status')['text'])

    def test_verified_bot_identity_and_legacy_link_visibility(self):
        self.link()
        with scope(self.account):
            self.assertEqual(auth.telegram_status()['id'], '101')
            self.assertIsNone(auth.telegram_status()['username'])
        self.handle('/start')
        with scope(self.account):
            info = auth.telegram_status()
            self.assertEqual(info['username'], 'telegram_alice')
            self.assertEqual(info['display_name'], 'Alice Test')
            self.assertTrue(info['can_unlink'])
        with scope('web'):self.assertIsNone(auth.telegram_status()['id'])

    def test_unlink_requires_password_and_literal_confirmation(self):
        self.link()
        with scope(self.account):
            for data in ({'confirmed':True,'password':'wrong'}, {'confirmed':'true','password':'unit test password 123'}, {'password':'unit test password 123'}):
                with self.assertRaises(auth.AuthError):auth.unlink_telegram(data)
            self.assertTrue(auth.telegram_status()['linked'])
        with scope('tg:101'), self.assertRaises(auth.AuthError):auth.unlink_telegram({'confirmed':True,'password':'unit test password 123'})

    def test_tokens_revoked_on_link_unlink_relink_and_records_preserved(self):
        before = security.issue('tg:101')
        with scope('tg:101'):event = store.save(service.validate({'title':'Telegram private task'}))
        self.link()
        with self.assertRaises(ValueError):security.verify(before)
        linked = security.issue('tg:101')
        self.assertEqual(security.verify(linked), self.account)
        self.unlink()
        with self.assertRaises(ValueError):security.verify(linked)
        self.assertEqual(auth.session_owner(self.cookie), self.account)
        with scope(self.account):
            self.assertIsNotNone(store.get_event(event['id']))
            self.assertIsNone(store.account()['chat_id'])
            self.assertFalse(auth.telegram_status()['linked'])
        detached = security.issue('tg:101')
        self.assertEqual(security.verify(detached), 'tg:101')
        self.link()
        for token in (before, linked, detached):
            with self.assertRaises(ValueError):security.verify(token)
        self.assertEqual(security.verify(security.issue('tg:101')), self.account)

    def test_old_legacy_token_without_epoch_cannot_resurrect(self):
        body = security.b64(json.dumps({'owner':'tg:101','exp':int(time.time())+1000}).encode())
        legacy = body+'.'+security.b64(hmac.new(security.secret(),body.encode(),hashlib.sha256).digest())
        self.assertEqual(security.verify(legacy), 'tg:101')
        self.link(); self.unlink(); self.link()
        with self.assertRaises(ValueError):security.verify(legacy)

    def test_unlink_invalidates_codes_including_legacy_account_codes(self):
        self.link()
        with store.connect() as db:
            for i, who in enumerate(('tg:101', self.account)):
                code = str(i)*10
                db.execute('INSERT INTO link_codes VALUES(?,?,?)',(hashlib.sha256(code.encode()).hexdigest(),who,time.time()+600))
        self.unlink()
        for code in ('0'*10,'1'*10):
            with self.assertRaises(ValueError):security.claim_link(code)

    def test_failed_link_does_not_consume_code(self):
        self.link()
        with scope('tg:202'):code = security.make_link()
        with scope(self.account), self.assertRaises(auth.AuthError):auth.link_telegram(code)
        self.assertEqual(security.claim_link(code), 'tg:202')

    def test_import_collision_preserves_demo_marker(self):
        with scope(self.account):store.import_csv()
        with scope('tg:101'):store.import_csv()
        self.link()
        with store.connect() as db:
            rows = db.execute('SELECT source_id FROM events WHERE owner=?',(self.account,)).fetchall()
        self.assertEqual(len(rows), 2000)
        self.assertTrue(all(row[0] is not None for row in rows))

    def test_callback_revisions_and_expired_confirmation(self):
        self.handle('/start')
        with scope('tg:101'):event = store.save(service.validate({'title':'Keep this task'}))
        for data in (f'done:{event["id"]}', f'askdel:{event["id"]}'):
            self.assertIn('изменилась', self.handle(data=data)['text'])
        result = self.handle(data=f'askdel:{event["id"]}:{event["revision"]}')
        confirm = result['reply_markup']['inline_keyboard'][0][0]['callback_data']
        with scope('tg:101'):
            st = store.state('telegram-conversation'); st['confirmation']['_expires'] = time.time()-1; store.state('telegram-conversation', st)
        self.assertIn('неактуально', self.handle(data=confirm)['text'])
        with scope('tg:101'):
            self.assertIsNotNone(store.get_event(event['id']))
            store.save(dict(event,title='Changed'),event['id'])
        self.assertIn('изменилась', self.handle(data=f'done:{event["id"]}:1')['text'])
        self.assertIn('изменилась', self.handle(data=f'askdel:{event["id"]}:1')['text'])

    def test_unlinked_telegram_cannot_notify_or_mutate_web_account(self):
        self.link()
        with scope(self.account):event = store.save(service.validate({'title':'Private web task'}))
        self.unlink()
        self.handle('/notify on')
        self.assertIn('недоступна', self.handle(data=f'done:{event["id"]}:{event["revision"]}')['text'])
        with scope(self.account):
            self.assertIsNone(store.account()['chat_id'])
            self.assertEqual(store.get_event(event['id'])['status'], 'active')

    def test_signed_init_data_before_unlink_cannot_mint_new_session(self):
        from test_v2 import init_data
        stamp = int(time.time())
        raw = init_data(timestamp=stamp)
        with patch.dict(os.environ, {'TELEGRAM_BOT_TOKEN':'test:token'}):
            self.assertEqual(security.telegram_user(raw)['id'], 101)
            self.link(); self.unlink(); self.link()
            with self.assertRaises(ValueError):security.telegram_user(raw)
            with patch('app.security.time.time', return_value=stamp+2):
                self.assertEqual(security.telegram_user(init_data(timestamp=stamp+2))['id'], 101)

    def test_prepared_bot_reply_not_delivered_after_unlink_or_relink(self):
        self.link()
        result = self.handle('/account')
        self.unlink(); self.link()
        class Fake:
            def __init__(self):self.calls = []
            def call(self, *args, **kwargs):self.calls.append(args)
        client = Fake()
        telegram_bot.deliver(client, result)
        self.assertEqual(client.calls, [])

    def test_prepared_bot_mutation_fails_closed_after_unlink(self):
        self.link()
        original = service.prepare_chat
        def prepare(*args, **kwargs):
            prepared = original(*args, **kwargs)
            self.unlink()
            return prepared
        with patch.object(service, 'prepare_chat', side_effect=prepare), self.assertRaises(PermissionError):
            self.handle('Добавь задачу не создавать')
        with scope(self.account):self.assertEqual(store.list_events()[1], 0)

    def test_account_delete_revokes_telegram_before_username_reuse(self):
        self.link()
        token = security.issue('tg:101')
        epoch = auth.telegram_epoch('tg:101')
        with scope(self.account):auth.delete_account({'password':'unit test password 123','confirmed':True})
        self.assertGreater(auth.telegram_epoch('tg:101'), epoch)
        self.assertGreater(auth.telegram_revoked_before('tg:101'), 0)
        _, cookie = auth.register({'username':'alice','password':'unit test password 123'})
        with scope('tg:101'):code = security.make_link()
        with scope(auth.session_owner(cookie)):auth.link_telegram(code)
        with self.assertRaises(ValueError):security.verify(token)

    def test_queued_notification_unlink_cancels_delivery(self):
        from datetime import datetime, timezone
        now = datetime(2026, 10, 27, 9, tzinfo=timezone.utc)
        self.link()
        with scope(self.account):
            store.save(service.validate({'title':'Private reminder','kind':'reminder','date':'2026-10-27','time':'12:00'}))
        notifications.enqueue(now)
        self.unlink()
        class Fake:
            def call(self, *args, **kwargs):raise AssertionError('Revoked reminder must not send')
        self.assertEqual(notifications.tick(Fake(), now), 0)

    def test_notification_send_serializes_with_unlink(self):
        from datetime import datetime, timezone
        now = datetime(2026, 10, 27, 9, tzinfo=timezone.utc)
        self.link()
        with scope(self.account):
            store.save(service.validate({'title':'Private reminder','kind':'reminder','date':'2026-10-27','time':'12:00'}))
        entered = threading.Event(); release = threading.Event(); unlinked = threading.Event(); errors = []
        class Fake:
            def call(self, method, payload, **kwargs):
                if kwargs.get('timeout') != 15:raise AssertionError('Bounded delivery required')
                entered.set()
                if not release.wait(5):raise AssertionError('Test release timed out')
                return True
        def send():
            try:notifications.tick(Fake(), now)
            except Exception as error:errors.append(type(error).__name__)
        def unlink():
            try:self.unlink(); unlinked.set()
            except Exception as error:errors.append(type(error).__name__)
        worker = threading.Thread(target=send); worker.start()
        self.assertTrue(entered.wait(5))
        revoker = threading.Thread(target=unlink); revoker.start()
        try:self.assertFalse(unlinked.wait(.2), 'Unlink must serialize behind in-flight send')
        finally:release.set(); worker.join(5); revoker.join(5)
        self.assertTrue(unlinked.is_set())
        self.assertEqual(errors, [])
        self.assertFalse(worker.is_alive()); self.assertFalse(revoker.is_alive())

    def test_disallowed_identity_cannot_claim_link(self):
        with scope('tg:101'):code = security.make_link()
        with patch.dict(os.environ, {'TELEGRAM_ALLOWED_USER_IDS':'202'}), scope(self.account), self.assertRaises(auth.AuthError):
            auth.link_telegram(code)


if __name__ == '__main__':unittest.main()
