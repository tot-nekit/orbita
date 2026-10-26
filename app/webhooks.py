"""Authenticated Telegram inbox, finite request-triggered work and cron recovery."""
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from . import auth, security, store
from .context import scope

_wake_lock = threading.Lock()


class QueueFull(ValueError):
    pass


def secret():
    value = os.getenv('TELEGRAM_WEBHOOK_SECRET', '')
    if not re.fullmatch(r'[A-Za-z0-9_-]{32,256}', value):
        raise RuntimeError('Настрой отдельный TELEGRAM_WEBHOOK_SECRET: 32–256 латинских букв, цифр, _ или -.')
    return value


def actor(update):
    callback = update.get('callback_query')
    if callback is not None and not isinstance(callback, dict):return None
    message = callback.get('message', {}) if isinstance(callback, dict) else update.get('message', {})
    if not isinstance(message, dict):return None
    user = callback.get('from', {}) if callback else message.get('from', {})
    chat = message.get('chat', {})
    if not isinstance(user, dict) or not isinstance(chat, dict):return None
    uid = user.get('id')
    if type(uid) is not int or not 0 < uid <= 10**20 or chat.get('id') != uid or chat.get('type') != 'private':return None
    return uid


def receive(update, supplied_secret):
    if not isinstance(supplied_secret, str) or len(supplied_secret) > 512 or not hmac.compare_digest(supplied_secret.encode(), secret().encode()):
        raise PermissionError('Недействительная подпись webhook.')
    if not isinstance(update, dict) or type(update.get('update_id')) is not int or not 0 <= update['update_id'] < 2**63:
        raise ValueError('Некорректный update_id.')
    uid = actor(update)
    if uid is None:return False
    # Keep /id available for setup, but do not store strangers' private messages.
    message = update.get('message')
    text = message.get('text') if isinstance(message, dict) else None
    if not security.allowed(uid) and (not isinstance(text, str) or text.strip() != '/id'):return False
    payload = json.dumps(update, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    if len(payload.encode()) > 256*1024:raise ValueError('Слишком большое обновление Telegram.')
    digest = hashlib.sha256(payload.encode()).hexdigest()
    owner = auth.resolve_telegram('tg:' + str(uid))
    now = time.time()
    with scope('web'), store.atomic(), store.connect() as db:
        previous = db.execute('SELECT digest FROM telegram_inbox WHERE id=?', (update['update_id'],)).fetchone()
        if previous:
            if not hmac.compare_digest(previous[0], digest):raise ValueError('update_id уже использован для другого обновления.')
            return False
        db.execute("DELETE FROM telegram_inbox WHERE status IN ('done','failed') AND created<?", (now-7*86400,))
        pending = db.execute("SELECT count(*) FROM telegram_inbox WHERE status IN ('pending','retry','working')").fetchone()[0]
        own = db.execute("SELECT count(*) FROM telegram_inbox WHERE owner=? AND status IN ('pending','retry','working')", (owner,)).fetchone()[0]
        if pending >= 1000 or own >= 20:raise QueueFull('Очередь занята. Обновление будет повторено Telegram.')
        db.execute('INSERT INTO telegram_inbox(id,owner,digest,payload,created) VALUES(?,?,?,?,?)', (update['update_id'], owner, digest, payload, now))
    return True


def _claim():
    now = time.time()
    with store.atomic(), store.connect() as db:
        row = db.execute("""SELECT i.* FROM telegram_inbox i
            WHERE i.status IN ('pending','retry','working') AND i.next_try<=? AND i.lease_until<=?
            AND NOT EXISTS (SELECT 1 FROM telegram_inbox p WHERE p.owner=i.owner AND p.id<i.id
                AND p.status IN ('pending','retry','working')) ORDER BY i.id LIMIT 1""", (now, now)).fetchone()
        if not row:return None
        token = secrets.token_hex(16)
        db.execute("UPDATE telegram_inbox SET status='working',lease_until=?,lease_token=? WHERE id=?", (now+300, token, row['id']))
        return dict(row, lease_token=token)


def drain(client=None, limit=10, budget=40):
    from .telegram_bot import Telegram, BotError, handle, deliver
    deadline = time.monotonic()+budget
    processed = 0
    with scope('web'):
        while processed < limit and time.monotonic() < deadline:
            job = _claim()
            if not job:break
            processed += 1
            status = 'done'
            error = None
            try:
                # A deleted account's in-flight job may not revive its data.
                if job['owner'].startswith('u:') and not auth.user(job['owner']):
                    result = None
                else:
                    if client is None:client = Telegram()
                    update = json.loads(job['payload'])
                    try:result = handle(update, client)
                    except (ValueError, BotError):
                        result = {'chat_id': actor(update), 'text': 'Не удалось выполнить запрос. Проверь дату или время и попробуй ещё раз.'}
                    with store.connect() as db:
                        owned = db.execute("SELECT 1 FROM telegram_inbox WHERE id=? AND lease_token=? AND lease_until>?", (job['id'], job['lease_token'], time.time())).fetchone()
                    if not owned:continue
                    deliver(client, result)
            except PermissionError:
                pass  # A concurrently deleted account has no reply or further writes.
            except Exception:
                status = 'failed' if job['attempts'] >= 4 else 'retry'
                error = 'Telegram job failed; private contents omitted'
            with store.connect() as db:
                db.execute("""UPDATE telegram_inbox SET status=?,attempts=attempts+1,next_try=?,lease_until=0,
                    lease_token=NULL,last_error=?,payload=CASE WHEN ? IN ('done','failed') THEN '{}' ELSE payload END
                    WHERE id=? AND lease_token=?""", (status, time.time()+min(900,30*2**job['attempts']), error, status, job['id'], job['lease_token']))
    return processed


def wake():
    """Finite work started by a real incoming message, never a keep-alive daemon."""
    if os.getenv('WEBHOOK_INLINE_WORK', '1') != '1' or not _wake_lock.acquire(blocking=False):return
    def work():
        try:drain()
        finally:_wake_lock.release()
    try:threading.Thread(target=work, name='telegram-webhook', daemon=True).start()
    except RuntimeError:_wake_lock.release()  # Persistent inbox is recovered by cron.
