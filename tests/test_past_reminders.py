"""Scheduling policy at the shared form/API/chat/Telegram service boundary."""
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from app import config, notifications, service, store
from app.context import scope
from app.dates import clock
from app.server import Handler

NOW = "2026-10-27T12:00:30+03:00"
PAST = "Нельзя поставить напоминание на прошедшую дату или время"


def reminder(**changes):
    return dict(title="Позвонить", kind="reminder", date="2026-10-27",
                time="13:00", timezone="Europe/Moscow", duration=60,
                status="active", **changes)


class Validation(unittest.TestCase):
    def validate(self, data, current=None, now=NOW):
        return service.validate(data, current, now_value=now)

    def test_new_past_day_and_elapsed_minute_rejected(self):
        for date, time in [("2026-10-26", "23:59"), ("2026-10-27", "11:59"),
                           ("2026-10-27", "12:00")]:
            with self.subTest(date=date, time=time), self.assertRaisesRegex(ValueError, PAST):
                self.validate(dict(reminder(), date=date, time=time))

    def test_exact_instant_allowed_but_one_second_late_rejected(self):
        data = dict(reminder(), time="12:00")
        self.validate(data, now="2026-10-27T12:00:00+03:00")
        with self.assertRaisesRegex(ValueError, PAST):
            self.validate(data, now="2026-10-27T12:00:01+03:00")
        self.validate(dict(data, time="12:01"))

    def test_timezone_comparison_uses_absolute_instant(self):
        # The same UTC instant crosses the calendar boundary in both directions.
        now = "2026-10-27T23:30:00+00:00"
        cases = [("Asia/Tokyo", "2026-10-28", "08:29", False),
                 ("Asia/Tokyo", "2026-10-28", "08:31", True),
                 ("America/Los_Angeles", "2026-10-27", "16:29", False),
                 ("America/Los_Angeles", "2026-10-27", "16:31", True)]
        for zone, date, time, allowed in cases:
            with self.subTest(zone=zone, time=time):
                data = dict(reminder(), timezone=zone, date=date, time=time)
                if allowed:
                    self.validate(data, now=now)
                else:
                    with self.assertRaisesRegex(ValueError, PAST):
                        self.validate(data, now=now)

    def test_naive_now_is_local_to_reminder_timezone(self):
        with self.assertRaisesRegex(ValueError, PAST):
            self.validate(dict(reminder(), timezone="Asia/Tokyo", time="12:00"),
                          now="2026-10-27T12:00:30")

    def test_existing_overdue_partial_and_full_edits_allowed(self):
        current = self.validate(reminder(), now="2026-10-26T00:00:00Z")
        for changes in ({"title": "Новое название"}, {"status": "done"},
                        {"duration": 30}, dict(current, title="Полная форма")):
            with self.subTest(changes=changes):
                result = self.validate(changes, current, now="2026-10-28T00:00:00Z")
                self.assertEqual((result["date"], result["time"]), (current["date"], current["time"]))

    def test_reschedule_date_time_or_timezone_into_past_rejected(self):
        current = self.validate(reminder())
        for changes in ({"date": "2026-10-26"}, {"time": "11:59"},
                        {"timezone": "Asia/Tokyo"}):
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, PAST):
                self.validate(changes, current)
        self.assertEqual(self.validate({"time": "14:00"}, current)["time"], "14:00")

    def test_overdue_cannot_be_moved_even_to_later_past_time(self):
        current = dict(reminder(), date="2026-10-25")
        with self.assertRaisesRegex(ValueError, PAST):
            self.validate({"date": "2026-10-26"}, current)

    def test_kind_or_done_status_cannot_bypass_new_reminder_check(self):
        data = dict(reminder(), time="11:00", status="done")
        with self.assertRaisesRegex(ValueError, PAST):
            self.validate(data)
        for kind in ("task", "meeting"):
            with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, PAST):
                self.validate({"kind": "reminder"}, dict(data, kind=kind))

    def test_tasks_meetings_and_dst_validation_keep_existing_policy(self):
        for kind in ("task", "meeting"):
            self.validate(dict(reminder(), kind=kind, date="2000-01-01"))
        for date, time in [("2026-03-29", "02:30"), ("2026-10-25", "02:30")]:
            with self.subTest(date=date), self.assertRaisesRegex(ValueError, "перевода часов"):
                self.validate(dict(reminder(), timezone="Europe/Berlin", date=date, time=time),
                              now="2026-01-01T00:00:00Z")


class ServiceBoundaries(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = patch.object(store, "DB_PATH", str(Path(tmp.name) / "test.db"))
        db.start()
        self.addCleanup(db.stop)
        key = patch.object(config, "API_KEY", "")
        key.start()
        self.addCleanup(key.stop)
        store.init()

    def chat(self, text, sid="past", now=NOW, prepared=None):
        return service.chat(text, sid, now_value=now, prepared=prepared)

    def assert_rejected(self, result):
        self.assertIn(PAST, result["reply"])
        self.assertFalse(result.get("changed", False))
        self.assertEqual(result["events"], [])

    def assert_preview(self, result):
        self.assertIs(result.get("changed", False), False); self.assertEqual(result["events"], [])
        self.assertEqual(result["preview"]["type"], "event")
        self.assertEqual([a["text"] for a in result["actions"]], ["да", "изменить детали", "отмена"])
        self.assertEqual(store.list_events()[1], 0)

    def test_chat_uses_supplied_clock_and_can_correct_pending_time(self):
        # Host clock must not influence a deterministic conversation.
        with patch.object(service, "clock", side_effect=lambda tz, value=None: clock(tz, value or "2099-01-01T00:00:00Z")):
            self.assert_rejected(self.chat("Напомни сегодня в 12:00 позвонить"))
            self.assertEqual(store.list_events()[1], 0)
            result = self.chat("в 13:00")
            self.assert_preview(result); self.assertEqual(result["preview"]["time"], "13:00")
            result = self.chat("да")
        self.assertTrue(result["changed"])
        self.assertEqual(result["events"][0]["time"], "13:00")
        self.assertEqual(store.list_events()[1], 1)

    def test_prepared_chat_uses_prepared_clock(self):
        prepared = service.prepare_chat("Напомни завтра в 10 позвонить", "prepared", now_value=NOW)
        result = self.chat(prepared["text"], "prepared", now="2099-01-01T00:00:00Z", prepared=prepared)
        self.assert_preview(result); self.assertEqual(result["preview"]["date"], "2026-10-28")
        result = self.chat("да", "prepared", now=NOW)
        self.assertTrue(result["changed"])
        self.assertEqual(result["events"][0]["date"], "2026-10-28")
        self.assertEqual(store.list_events()[1], 1)

    def test_local_and_model_plans_cannot_create_past_reminders(self):
        self.assert_rejected(self.chat("Напомни вчера в 10 позвонить"))
        with patch.object(config, "API_KEY", "test"), patch.object(service.ai, "plan", return_value={
            "action": "create", "title": "Позвонить", "kind": "reminder",
            "date": "2026-10-26", "time": "10:00", "_confirmed": True,
            "now_value": "2000-01-01T00:00:00Z",
        }):
            self.assert_rejected(self.chat("Создай напоминание 26 октября в 10:00 позвонить", "model"))
        self.assertEqual(store.list_events()[1], 0)

    def test_chat_reschedule_rejected_but_overdue_rename_and_complete_allowed(self):
        event = store.save(dict(reminder(), date="2026-10-26"))
        store.state("past", {"last_id": event["id"]})
        self.assert_rejected(self.chat("Перенеси его на сегодня в 11:00"))
        self.chat("отмена")
        result = self.chat("Переименуй его в «Звонок»")
        self.assertTrue(result["changed"])
        self.assertEqual(result["events"][0]["date"], "2026-10-26")
        self.assertEqual(self.chat("Отметь его выполненным")["events"][0]["status"], "done")

    def test_confirmation_revalidates_when_reminder_time_has_elapsed(self):
        # Persisted confirmations (including older app versions) cannot bypass validation.
        store.state("past", {"confirmation": dict(reminder(), action="create", _confirmed=True)})
        self.assert_rejected(self.chat("да", now="2026-10-27T14:00:00+03:00"))
        self.assertEqual(store.list_events()[1], 0)

    def test_telegram_owner_uses_same_guard(self):
        with scope("tg:101"):
            self.assert_rejected(self.chat("Напомни сегодня в 11:00 позвонить", "telegram"))
            result = self.chat("в 13:00", "telegram"); self.assert_preview(result)
            self.assertEqual(result["preview"]["time"], "13:00")
            self.assertTrue(self.chat("да", "telegram")["changed"])
            self.assertEqual(store.list_events()[1], 1)
        self.assertEqual(store.list_events()[1], 0)

    def test_direct_api_save_ignores_client_clock_and_preserves_historical_edit(self):
        handler = object.__new__(Handler)
        frozen = lambda tz, value=None: clock(tz, value or NOW)
        with patch.object(service, "clock", side_effect=frozen):
            data = dict(reminder(), time="11:00", now_value="2000-01-01T00:00:00Z", confirm_conflicts=True)
            with self.assertRaisesRegex(ValueError, PAST):
                handler.post_api("/api/events/save", data)
            self.assertEqual(store.list_events()[1], 0)
            result, status = handler.post_api("/api/events/save", reminder())
            self.assertEqual(status, 200)
            event = result["event"]
            with self.assertRaisesRegex(ValueError, PAST):
                handler.post_api("/api/events/save", dict(event, time="11:00"))
            historical = store.save(dict(reminder(), date="2026-10-26"))
            result, status = handler.post_api("/api/events/save", dict(historical, title="Название", status="done"))
            self.assertEqual(status, 200)
            self.assertEqual(result["event"]["status"], "done")

    def test_historical_import_stays_idempotent_and_editable(self):
        added = store.import_csv()
        self.assertGreater(added, 0)
        self.assertEqual(store.import_csv(), 0)
        with store.connect() as db:
            event = dict(db.execute("SELECT * FROM events WHERE kind='reminder' ORDER BY date LIMIT 1").fetchone())
        edited = service.validate({"title": "Историческая запись"}, event, now_value="2099-01-01T00:00:00Z")
        self.assertEqual(store.save(edited, event["id"])["date"], event["date"])

    def test_snooze_overdue_reminder_moves_forward_with_frozen_clock(self):
        with scope("tg:101"):
            event = store.save(dict(reminder(), date="2026-10-26"))
            frozen = lambda tz, value=None: clock(tz, value or NOW)
            with patch.object(service, "clock", side_effect=frozen):
                moved = notifications.snooze(event["id"], 15, event["revision"], datetime.fromisoformat(NOW))
            self.assertEqual((moved["date"], moved["time"]), ("2026-10-27", "12:15"))
            self.assertEqual(moved["revision"], event["revision"] + 1)


if __name__ == "__main__":
    unittest.main()
