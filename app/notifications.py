"""Durable Telegram reminder outbox. At-least-once delivery; never promises exactly-once."""
import time,secrets
from datetime import datetime,timezone
from zoneinfo import ZoneInfo
from . import store
from .context import scope

def due_at(e):
    return datetime.fromisoformat(e['date']+'T'+e['time']).replace(tzinfo=ZoneInfo(e['timezone'])).astimezone(timezone.utc)

def enqueue(now=None):
    now=now or datetime.now(timezone.utc)
    with store.atomic(),store.connect() as db:
        rows=db.execute("SELECT e.* FROM events e JOIN accounts a ON e.owner=a.owner WHERE e.deleted_at IS NULL AND e.kind='reminder' AND e.status='active' AND e.date IS NOT NULL AND e.time IS NOT NULL AND e.source_id IS NULL AND a.chat_id IS NOT NULL AND a.notify=1").fetchall()
        for raw in rows:
            e=dict(raw);due=due_at(e);lag=(now-due).total_seconds()
            if lag<0:continue
            db.execute("INSERT INTO notifications(event_id,due,owner,status) VALUES(?,?,?,?) ON CONFLICT(event_id,due) DO UPDATE SET status='pending',attempts=0,next_try=0,lease_until=0 WHERE notifications.status='cancelled' AND excluded.status='pending'",(e['id'],due.isoformat(),e['owner'],'pending' if lag<=3600 else 'missed'))

def tick(client,now=None,max_messages=None,deadline=None):
    now=now or datetime.now(timezone.utc);stamp=now.timestamp();enqueue(now);sent=0;processed=0
    while True:
        if max_messages is not None and processed>=max_messages or deadline is not None and time.monotonic()>=deadline:break
        with store.atomic(),store.connect() as db:
            row=db.execute("SELECT * FROM notifications WHERE status IN ('pending','retry','sending') AND next_try<=? AND lease_until<=? ORDER BY due LIMIT 1",(stamp,stamp)).fetchone()
            if not row:break
            processed+=1
            r=dict(row)
            lease=secrets.token_hex(16)
            db.execute("UPDATE notifications SET status='sending',lease_until=?,lease_token=? WHERE event_id=? AND due=?",(stamp+90,lease,r['event_id'],r['due']))
        with scope(r['owner']):
            e=store.get_event(r['event_id']);acc=store.account()
            valid=e and e['status']=='active' and e['kind']=='reminder' and acc['notify'] and acc['chat_id'] and due_at(e).isoformat()==r['due']
            if not valid:status='cancelled';error=None
            elif (now-datetime.fromisoformat(r['due'])).total_seconds()>3600:status='missed';error=None
            else:
                try:
                    client.call('sendMessage',{'chat_id':acc['chat_id'],'text':f'🔔 {e["title"]}\n{e["date"]} · {e["time"]} · {e["timezone"]}','reply_markup':{'inline_keyboard':[[{'text':'✓ Выполнено','callback_data':f'done:{e["id"]}:{e["revision"]}'}],[{'text':'Через 15 минут','callback_data':f'snooze:{e["id"]}:{e["revision"]}:15'},{'text':'Через час','callback_data':f'snooze:{e["id"]}:{e["revision"]}:60'}]]}})
                    status='sent';error=None;sent+=1
                except Exception:status='failed' if r['attempts']>=4 else 'retry';error='Telegram delivery failed; no credentials logged'
        with store.connect() as db:
            db.execute('UPDATE notifications SET status=?,attempts=attempts+1,next_try=?,lease_until=0,last_error=?,lease_token=NULL WHERE event_id=? AND due=? AND lease_token=?',(status,stamp+min(900,30*2**r['attempts']),error,r['event_id'],r['due'],lease))
    return sent

def health():
    last=store.runtime('bot_heartbeat')
    import os
    return bool(last and time.time()-float(last)<(180 if os.getenv('TELEGRAM_MODE')=='webhook' else 90))

def delivery_status(limit=100,now=None):
    """Current occurrence only; an old delivery is never shown for a moved record."""
    from .context import owner
    now=now or datetime.now(timezone.utc);acc=store.account()
    with store.connect() as db:
        rows=db.execute("SELECT * FROM events WHERE owner=? AND deleted_at IS NULL AND kind='reminder' AND status='active' AND date IS NOT NULL AND time IS NOT NULL ORDER BY date DESC,time DESC LIMIT ?",(owner(),limit)).fetchall()
        results=[]
        for row in rows:
            e=dict(row);due=due_at(e)
            occurrence=db.execute('SELECT status,attempts FROM notifications WHERE owner=? AND event_id=? AND due=?',(owner(),e['id'],due.isoformat())).fetchone()
            status='demo' if e['source_id'] is not None else 'not_connected' if not owner().startswith(('tg:','u:')) or not acc['chat_id'] else 'disabled' if not acc['notify'] else occurrence['status'] if occurrence else 'scheduled' if due>now else 'missed' if (now-due).total_seconds()>3600 else 'pending'
            results.append({'event_id':e['id'],'status':status,'due':due.isoformat(),'attempts':occurrence['attempts'] if occurrence else 0})
    return results

def snooze(event_id,minutes,revision,now=None):
    """Move a single active reminder, with optimistic concurrency for old buttons."""
    from datetime import timedelta
    from .service import validate
    if type(event_id) is not int or type(revision) is not int:raise ValueError('Некорректная версия напоминания.')
    if type(minutes) is not int or minutes not in (15,60):raise ValueError('Выбери 15 или 60 минут.')
    with store.atomic():
        event=store.get_event(event_id)
        if not event or event['kind']!='reminder' or event['status']!='active':raise ValueError('Активное напоминание не найдено.')
        if event['source_id'] is not None:raise ValueError('Учебные записи не отправляют уведомления. Создай личное напоминание.')
        if event['revision']!=revision:raise ValueError('Напоминание уже изменилось. Открой актуальную запись.')
        now=now or datetime.now(timezone.utc)
        target=(now.astimezone(timezone.utc)+timedelta(minutes=minutes)).astimezone(ZoneInfo(event['timezone']))
        return store.save(validate({'date':target.date().isoformat(),'time':target.strftime('%H:%M')},event),event_id)
