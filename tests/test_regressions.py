"""Offline executor regressions. Every case owns its temporary SQLite database."""
import os
import tempfile
import threading
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

from app import ai, config, dates, notifications, service, store
from app.context import scope

NOW = '2026-10-22T12:00:00+03:00'


class Regressions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='orbita-regressions-')
        self.db = patch.object(store, 'DB_PATH', str(Path(self.tmp.name) / 'test.db'))
        self.db.start()
        self.key = patch.object(config, 'API_KEY', '')
        self.key.start()
        self.env = patch.dict(os.environ, {'OPENAI_API_KEY': ''})
        self.env.start()
        self.net = patch('urllib.request.urlopen', side_effect=AssertionError('Network forbidden'))
        self.net.start()
        store.init()

    def tearDown(self):
        self.net.stop(); self.env.stop(); self.key.stop(); self.db.stop(); self.tmp.cleanup()

    def chat(self, text, sid='conversation'):
        prepared = service.prepare_chat(text, sid, now_value=NOW)
        with store.atomic():
            return service.chat(text, sid, prepared=prepared)

    def create(self, title='Original', **fields):
        return store.save(service.validate({'title': title, **fields}))

    def llm(self, text, plan, sid='conversation'):
        with patch.object(config, 'API_KEY', 'offline-stub'), patch.object(ai, 'plan', return_value=plan):
            return self.chat(text, sid)

    def test_word_hours(self):
        now = dates.clock('Europe/Moscow', NOW)
        self.assertEqual(dates.resolve('в одиннадцать утра', now)['time'], '11:00')
        self.assertEqual(dates.resolve('в двенадцать ночи', now)['time'], '00:00')

    def test_weekday_in_named_week(self):
        now = dates.clock('Europe/Moscow', NOW)
        self.assertEqual(dates.resolve('в пятницу на следующей неделе', now)['date'], '2026-10-30')
        self.assertEqual(dates.resolve('на прошлой неделе в пятницу', now)['date'], '2026-10-16')
        self.assertEqual(dates.resolve('в прошлом месяце', now)['period'], ('2026-09-01', '2026-09-30'))

    def test_named_month_and_short_meeting(self):
        e = self.chat('Завтра в15 созвон с командой')['events'][0]
        self.assertEqual((e['kind'], e['date'], e['time']), ('meeting', '2026-10-23', '15:00'))
        e = self.chat('Добавь задачу 25 октября отправить отчет')['events'][0]
        self.assertEqual(e['date'], '2026-10-25')
        self.assertNotIn('октября', e['title'])

    def test_quoted_calendar_title(self):
        e = self.chat('Добавь задачу «Завтра в 11»')['events'][0]
        self.assertIsNone(e['date']); self.assertIsNone(e['time'])
        self.assertIn('Завтра в 11', e['title'])
        e = self.chat('Добавь задачу «Каждый день»')['events'][0]
        self.assertIn('Каждый день', e['title'])
        e = self.llm('Добавь задачу «Отчет 25 октября» завтра',
                     {'action': 'create', 'title': 'Отчет 25 октября', 'date': '2026-10-25'})['events'][0]
        self.assertEqual(e['date'], '2026-10-23')

    def test_calendar_overrides_llm(self):
        e = self.llm('Напомни завтра в 11 позвонить Ивану',
                     {'action': 'create', 'title': 'Позвонить Ивану', 'kind': 'reminder',
                      'date': '2026-12-01', 'time': '19:00'})['events'][0]
        self.assertEqual((e['date'], e['time']), ('2026-10-23', '11:00'))

    def test_period_overrides_llm(self):
        self.create('This week', date='2026-10-23')
        self.create('Next week', date='2026-10-30')
        r = self.llm('Покажи следующую неделю', {'action': 'list', 'start': '2026-10-19', 'end': '2026-10-25'})
        self.assertEqual([e['title'] for e in r['events']], ['Next week'])

    def test_recurring_blocked_for_both_planners(self):
        for key in ('', 'offline-stub'):
            with self.subTest(key=key), patch.object(config, 'API_KEY', key), patch.object(ai, 'plan', return_value={
                'action': 'create', 'title': 'Walk', 'date': '2026-10-23'
            }) as planner:
                self.assertIn('не поддерживаются', self.chat('Добавь прогулку каждый день')['reply'])
                planner.assert_not_called()
        self.assertEqual(store.list_events()[1], 0)

    def test_broad_date_keeps_pending(self):
        for key in ('', 'offline-stub'):
            sid = 'wide-' + str(bool(key))
            with self.subTest(key=key), patch.object(config, 'API_KEY', key), patch.object(ai, 'plan', return_value={
                'action': 'create', 'title': 'Подготовить отчет', 'date': '2026-10-26'
            }):
                r = self.chat('Добавь задачу подготовить отчет на следующей неделе', sid)
                self.assertIn('конкретный день', r['reply'])
                self.assertEqual(store.state(sid)['pending']['action'], 'create')
                r = self.chat('30 октября', sid)
                self.assertEqual(r['events'][0]['date'], '2026-10-30')
                self.assertIn('отчет', r['events'][0]['title'].lower())

    def test_pending_rename_and_date_title(self):
        self.chat('Добавь задачу купить хлеб')
        self.chat('Переименуй ее')
        e = self.chat('Отчет за завтра')['events'][0]
        self.assertEqual(e['title'], 'Отчет за завтра'); self.assertIsNone(e['date'])
        e = self.chat('Переименуй ее в Отчет за сегодня')['events'][0]
        self.assertIsNone(e['date'])

    def test_stale_delete_needs_confirmation_again(self):
        e = self.chat('Добавь задачу купить хлеб')['events'][0]
        self.chat('Удали ее')
        store.save(dict(e, title='Revised object'), e['id'])
        r = self.chat('да')
        self.assertIn('Revised object', r['reply']); self.assertIsNotNone(store.get_event(e['id']))
        self.chat('да'); self.assertIsNone(store.get_event(e['id']))

    def test_delete_revision_compare(self):
        e = self.create()
        store.save(dict(e, title='New'), e['id'])
        self.assertFalse(store.delete(e['id'], revision=e['revision']))
        self.assertIsNotNone(store.get_event(e['id']))

    def test_bad_model_plans_fall_back(self):
        for raw in (None, [], {'action': []}, {'action': 'create', 'kind': []}, {'action': 'delete', 'id': True}):
            with self.subTest(raw=raw):
                r = self.llm('Добавь задачу купить хлеб', raw)
                self.assertEqual(r['mode'], 'local'); self.assertTrue(r['events'])

    def test_invalid_date_clarifies(self):
        r = self.chat('Добавь встречу 31.02.2027 в 15:00')
        self.assertIn('корректную дату', r['reply']); self.assertEqual(store.list_events()[1], 0)

    def test_multiple_actions_not_merged(self):
        r = self.chat('Добавь задачу купить хлеб и напомни завтра в 10 позвонить')
        self.assertIn('несколько действий', r['reply']); self.assertEqual(store.list_events()[1], 0)

    def test_preparation_without_write_lock_and_stale_snapshot(self):
        def planner(*args):
            self.assertIsNone(store.TX.get())
            def write():
                with scope('another-owner'): self.create('Concurrent write')
            t = threading.Thread(target=write); t.start(); t.join(2)
            self.assertFalse(t.is_alive(), 'Planner held SQLite write lock')
            return {'action': 'create', 'title': 'Prepared task'}
        with patch.object(config, 'API_KEY', 'offline-stub'), patch.object(ai, 'plan', side_effect=planner):
            prepared = service.prepare_chat('Добавь задачу', 'prepared', now_value=NOW)
        store.message('prepared', 'user', 'intervening request')
        before = store.history('prepared')
        with store.atomic(): r = service.chat('Добавь задачу', 'prepared', prepared=prepared)
        self.assertEqual(r['code'], 'stale_dialog'); self.assertEqual(store.history('prepared'), before)
        self.assertEqual(store.list_events()[1], 0)

    def test_prepared_apply_and_owner_check(self):
        prepared = service.prepare_chat('Добавь задачу купить хлеб', 'ready', now_value=NOW)
        with scope('other'), store.atomic():
            self.assertEqual(service.chat('Добавь задачу купить хлеб', 'ready', prepared=prepared)['code'], 'stale_dialog')
        with store.atomic():
            self.assertTrue(service.chat('Добавь задачу купить хлеб', 'ready', prepared=prepared)['changed'])

    def test_cached_result_owner_digest_expiration(self):
        self.assertIsNone(store.cached_result('key', {'a': 1}))
        store.run_once('key', {'a': 1}, lambda: {'ok': True})
        self.assertEqual(store.cached_result('key', {'a': 1}), {'ok': True})
        with self.assertRaises(ValueError): store.cached_result('key', {'a': 2})
        with scope('other'): self.assertIsNone(store.cached_result('key', {'a': 1}))
        with store.connect() as db: db.execute('UPDATE receipts SET created=0')
        self.assertIsNone(store.cached_result('key', {'a': 1}))

    def test_buckets_and_inbox_stat(self):
        self.create('Inbox'); self.create('Done inbox', status='done')
        self.create('Overdue', date='2026-10-21'); self.create('Today', date='2026-10-22')
        self.assertEqual([e['title'] for e in store.list_events(status='active', bucket='inbox')[0]], ['Inbox'])
        self.assertEqual([e['title'] for e in store.list_events(status='active', bucket='overdue', day='2026-10-22')[0]], ['Overdue'])
        self.assertEqual(store.stats('2026-10-22')['inbox'], 1)

    def test_dst_relative_interval(self):
        now = dates.clock('Europe/London', '2026-10-25T00:30:00+01:00')
        self.assertEqual(dates.resolve('через два часа', now)['time'], '01:30')

    def reminder(self):
        with scope('tg:101'):
            store.account({'chat_id': 101})
            return self.create('Call', kind='reminder', date='2026-10-22', time='12:00')

    def test_old_queued_reminder_missed(self):
        self.reminder(); now = datetime(2026, 10, 22, 9, tzinfo=timezone.utc)
        notifications.enqueue(now)
        class Offline:
            def call(self, *args, **kwargs): raise AssertionError('Expired reminder delivered')
        self.assertEqual(notifications.tick(Offline(), now + timedelta(hours=2)), 0)
        with store.connect() as db:
            self.assertEqual(db.execute('SELECT status FROM notifications').fetchone()[0], 'missed')

    def test_expired_worker_cannot_overwrite_success(self):
        self.reminder(); now = datetime(2026, 10, 22, 9, tzinfo=timezone.utc)
        entered = threading.Event(); release = threading.Event(); errors = []; calls = []
        class Slow:
            def call(self, *args, **kwargs):
                entered.set()
                if not release.wait(3): raise AssertionError('Coordination timeout')
                raise RuntimeError('Old worker failed')
        class Fast:
            def call(self, *args, **kwargs): calls.append(args)
        def first():
            try: notifications.tick(Slow(), now)
            except Exception as exc: errors.append(exc)
        thread = threading.Thread(target=first); thread.start()
        try:
            self.assertTrue(entered.wait(3))
            self.assertEqual(notifications.tick(Fast(), now + timedelta(seconds=91)), 1)
        finally:
            release.set(); thread.join(3)
        self.assertFalse(thread.is_alive()); self.assertEqual(errors, [])
        self.assertEqual(notifications.tick(Fast(), now + timedelta(seconds=92)), 0)
        self.assertEqual(len(calls), 1)
        with store.connect() as db:
            self.assertEqual(db.execute('SELECT status FROM notifications').fetchone()[0], 'sent')


if __name__ == '__main__':
    unittest.main()
