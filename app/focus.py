"""Daily focus: one chosen task per local day plus an optional 25-minute session."""
import time
from datetime import datetime
from zoneinfo import ZoneInfo
from . import store
from .context import owner

SESSION = 25 * 60


def _day(stamp):
    return datetime.fromtimestamp(stamp, ZoneInfo(store.account()['timezone'])).date().isoformat()


def _row(db, day):
    return db.execute('SELECT event_id,started_at,ends_at FROM daily_focus WHERE owner=? AND day=?', (owner(), day)).fetchone()


def _payload(day, row, stamp):
    event = store.get_event(row['event_id']) if row and row['event_id'] else None
    if event and event['kind'] != 'task':
        event = None
    completed = bool(event and event['status'] == 'done')
    timer = event is not None and not completed and row['started_at'] is not None
    return {'day': day, 'event': event, 'completed': completed,
            'started_at': row['started_at'] if timer else None,
            'ends_at': row['ends_at'] if timer else None, 'now': int(stamp)}


def get(now=None):
    stamp = time.time() if now is None else now
    day = _day(stamp)
    with store.connect() as db:
        row = _row(db, day)
    return _payload(day, row, stamp)


def post(path, data, now=None):
    stamp = time.time() if now is None else now
    day = _day(stamp)
    with store.connect() as db:
        # Yesterday's focus never leaks into today and old rows are not kept.
        db.execute('DELETE FROM daily_focus WHERE owner=? AND day<?', (owner(), day))
        if path == '/api/focus':
            if 'id' not in data:
                raise ValueError('Укажи задачу для фокуса.')
            value = data['id']
            if value is None:
                db.execute('DELETE FROM daily_focus WHERE owner=? AND day=?', (owner(), day))
            else:
                if type(value) is not int or not 0 < value < 2**63:
                    raise ValueError('Некорректный ID.')
                event = store.get_event(value)
                if not event or event['kind'] != 'task' or event['status'] != 'active':
                    return {'error': 'Активная задача не найдена.'}, 404
                db.execute('INSERT INTO daily_focus(owner,day,event_id) VALUES(?,?,?) ON CONFLICT(owner,day) '
                           'DO UPDATE SET event_id=excluded.event_id,started_at=NULL,ends_at=NULL', (owner(), day, value))
        else:
            current = _payload(day, _row(db, day), stamp)
            if not current['event'] or current['completed']:
                return {'error': 'Сначала выбери активную задачу для фокуса.'}, 409
            if path == '/api/focus/start':
                db.execute('UPDATE daily_focus SET started_at=?,ends_at=? WHERE owner=? AND day=?',
                           (int(stamp), int(stamp) + SESSION, owner(), day))
            else:
                db.execute('UPDATE daily_focus SET started_at=NULL,ends_at=NULL WHERE owner=? AND day=?', (owner(), day))
        row = _row(db, day)
    return _payload(day, row, stamp), 200
