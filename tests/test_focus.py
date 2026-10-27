"""Daily focus: owner scope, local day, completion and the 25-minute session."""
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from app import focus, service, store
from app.context import scope

MORNING = datetime(2026, 10, 27, 7, 0, tzinfo=timezone.utc).timestamp()  # 10:00 in Moscow


class DailyFocus(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = patch.object(store, 'DB_PATH', str(Path(tmp.name) / 'focus.db'))
        db.start()
        self.addCleanup(db.stop)
        store.init()
        self.owner = scope('u:alice')
        self.owner.__enter__()
        self.addCleanup(self.owner.__exit__, None, None, None)
        self.task = store.save(service.validate({'title': 'Отчёт'}))

    def test_select_persists_and_survives_reopen(self):
        result, status = focus.post('/api/focus', {'id': self.task['id']}, MORNING)
        self.assertEqual((status, result['event']['title'], result['day']), (200, 'Отчёт', '2026-10-27'))
        store.init()
        self.assertEqual(focus.get(MORNING + 3600)['event']['id'], self.task['id'])

    def test_invalid_foreign_and_non_task_ids_do_not_change_focus(self):
        focus.post('/api/focus', {'id': self.task['id']}, MORNING)
        meeting = store.save(service.validate({'title': 'Созвон', 'kind': 'meeting', 'date': '2026-10-27', 'time': '15:00'}))
        with scope('u:bob'):
            foreign = store.save(service.validate({'title': 'Чужая'}))
        for value in (foreign['id'], meeting['id'], 999999):
            self.assertEqual(focus.post('/api/focus', {'id': value}, MORNING)[1], 404)
        for value in (True, '1', 1.5, 0, -3, 2**63):
            with self.assertRaises(ValueError):
                focus.post('/api/focus', {'id': value}, MORNING)
        with self.assertRaises(ValueError):
            focus.post('/api/focus', {}, MORNING)
        self.assertEqual(focus.get(MORNING)['event']['id'], self.task['id'])
        with scope('u:bob'):
            self.assertIsNone(focus.get(MORNING)['event'])

    def test_local_day_rollover_uses_account_timezone(self):
        store.account({'timezone': 'Asia/Vladivostok'})
        late = datetime(2026, 10, 27, 13, 30, tzinfo=timezone.utc).timestamp()  # 23:30 local
        focus.post('/api/focus', {'id': self.task['id']}, late)
        self.assertEqual(focus.get(late)['day'], '2026-10-27')
        next_day = focus.get(late + 3600)
        self.assertEqual((next_day['day'], next_day['event']), ('2026-10-28', None))

    def test_session_runs_expires_and_stops(self):
        self.assertEqual(focus.post('/api/focus/start', {}, MORNING)[1], 409)
        focus.post('/api/focus', {'id': self.task['id']}, MORNING)
        started, _ = focus.post('/api/focus/start', {}, MORNING)
        self.assertEqual(started['ends_at'] - started['started_at'], 25 * 60)
        expired = focus.get(MORNING + 26 * 60)
        self.assertLess(expired['ends_at'], expired['now'])
        stopped, _ = focus.post('/api/focus/stop', {}, MORNING + 30)
        self.assertIsNone(stopped['started_at'])

    def test_completed_task_stays_visible_without_timer(self):
        focus.post('/api/focus', {'id': self.task['id']}, MORNING)
        focus.post('/api/focus/start', {}, MORNING)
        store.save(dict(self.task, status='done'), self.task['id'])
        state = focus.get(MORNING + 60)
        self.assertTrue(state['completed'])
        self.assertIsNone(state['ends_at'])
        self.assertEqual(focus.post('/api/focus/start', {}, MORNING + 60)[1], 409)

    def test_deleted_task_and_clear_remove_focus(self):
        focus.post('/api/focus', {'id': self.task['id']}, MORNING)
        store.delete(self.task['id'])
        self.assertIsNone(focus.get(MORNING)['event'])
        other = store.save(service.validate({'title': 'Другое'}))
        focus.post('/api/focus', {'id': other['id']}, MORNING)
        cleared, _ = focus.post('/api/focus', {'id': None}, MORNING)
        self.assertIsNone(cleared['event'])


if __name__ == '__main__':
    unittest.main()
