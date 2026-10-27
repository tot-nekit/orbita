"""Pseudonymous accounts, scrypt passwords and revocable opaque sessions."""
import base64
import hashlib
import hmac
import os
import re
import secrets
import threading
import time
import uuid
from . import store, security
from .context import owner, scope
from .database import production

SESSION_TTL = 30 * 86400
PASSWORD_WORK = threading.BoundedSemaphore(4)


class AuthError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def required():
    return os.getenv('AUTH_REQUIRED', '1' if production() else '0') == '1'


def username(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_]{3,32}', value):
        raise AuthError('Логин: от 3 до 32 латинских букв, цифр или знаков _.')
    return value.lower()


def password(value):
    if not isinstance(value, str) or not 12 <= len(value) <= 128:
        raise AuthError('Пароль должен содержать от 12 до 128 символов.')
    return value


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def _derive(value, salt):
    if not PASSWORD_WORK.acquire(blocking=False):
        raise AuthError('Сервер занят. Повтори через несколько секунд.', 429)
    try:
        # OWASP scrypt alternative: N=2^15, r=8, p=3 (32 MiB per operation).
        return hashlib.scrypt(value.encode(), salt=salt, n=32768, r=8, p=3, dklen=32, maxmem=64*1024*1024)
    finally:
        PASSWORD_WORK.release()


def hash_password(value):
    salt = secrets.token_bytes(16)
    return 'scrypt$32768$8$3$' + salt.hex() + '$' + _derive(password(value), salt).hex()


def check_password(value, encoded):
    # A constant work factor for unknown names prevents a cheap timing oracle.
    if not isinstance(value, str) or len(value) > 128:
        return False
    salt = bytes.fromhex(encoded.split('$')[4]) if encoded else bytes(16)
    actual = _derive(value, salt)
    return bool(encoded) and hmac.compare_digest(actual.hex(), encoded.split('$')[5])


def limit(action, remote, name=''):
    """Persistent bounded throttles; store keyed hashes, never raw network addresses."""
    now = time.time()
    keys = [(action + ':ip:' + remote, 20), (action + ':global', 300)]
    if name:
        keys.append((action + ':name:' + name.lower(), 10))
    with store.atomic(), store.connect() as db:
        db.execute('DELETE FROM auth_limits WHERE expires<?', (now,))
        hashes = [(hmac.new(security.secret(), key.encode(), hashlib.sha256).hexdigest(), count) for key, count in keys]
        for key, count in hashes:
            row = db.execute('SELECT attempts FROM auth_limits WHERE key=?', (key,)).fetchone()
            if row and row[0] >= count:
                raise AuthError('Слишком много попыток. Попробуй через 15 минут.', 429)
        for key, _ in hashes:
            db.execute('INSERT INTO auth_limits VALUES(?,1,?) ON CONFLICT(key) DO UPDATE SET attempts=attempts+1', (key, now + 900))


def _new_session(db, owner_id):
    now = time.time()
    token = 's2_' + secrets.token_urlsafe(32)
    db.execute('DELETE FROM auth_sessions WHERE expires<?', (now,))
    # Limit a single account's live sessions, keeping the newest nine.
    db.execute('DELETE FROM auth_sessions WHERE owner=? AND hash NOT IN (SELECT hash FROM auth_sessions WHERE owner=? ORDER BY created DESC LIMIT 9)', (owner_id, owner_id))
    db.execute('INSERT INTO auth_sessions VALUES(?,?,?,?)', (digest(token), owner_id, now + SESSION_TTL, now))
    return token


def session_owner(token):
    if not isinstance(token, str) or not re.fullmatch(r's2_[A-Za-z0-9_-]{43}', token):
        raise PermissionError('Войди в аккаунт, чтобы открыть записи.')
    with store.connect() as db:
        row = db.execute('SELECT s.owner FROM auth_sessions s JOIN users u ON u.owner=s.owner WHERE s.hash=? AND s.expires>?', (digest(token), time.time())).fetchone()
    if not row:
        raise PermissionError('Сессия истекла. Войди снова.')
    return row[0]


def revoke(token):
    if isinstance(token, str) and token.startswith('s2_'):
        with store.connect() as db:
            db.execute('DELETE FROM auth_sessions WHERE hash=?', (digest(token),))


def user(owner_id=None):
    with store.connect() as db:
        row = db.execute('SELECT owner,username,created_at FROM users WHERE owner=?', (owner_id or owner(),)).fetchone()
    return dict(row) if row else None


def register(data):
    name = username(data.get('username'))
    encoded = hash_password(data.get('password'))
    recovery = secrets.token_urlsafe(32)
    owner_id = 'u:' + uuid.uuid4().hex
    with store.atomic(), store.connect() as db:
        try:
            db.execute('INSERT INTO users VALUES(?,?,?,?,?)', (owner_id, name, encoded, digest(recovery), store.utcnow()))
        except store.sqlite3.IntegrityError:
            raise AuthError('Этот логин уже занят. Выбери другой.', 409) from None
        # Legacy shared records never belong to the first person who registers.
        db.execute('INSERT INTO accounts(owner,use_ai) VALUES(?,0)', (owner_id,))
        token = _new_session(db, owner_id)
    return {'ok': True, 'username': name, 'recovery_code': recovery}, token


def login(data):
    name = username(data.get('username'))
    with store.connect() as db:
        row = db.execute('SELECT owner,password_hash FROM users WHERE username=?', (name,)).fetchone()
    if not check_password(data.get('password'), row['password_hash'] if row else None):
        raise AuthError('Неверный логин или пароль.', 401)
    with store.atomic(), store.connect() as db:
        # Recheck inside the transaction in case a concurrent password change won.
        if not db.execute('SELECT 1 FROM users WHERE owner=? AND password_hash=?', (row['owner'], row['password_hash'])).fetchone():
            raise AuthError('Пароль изменился. Войди снова.', 401)
        token = _new_session(db, row['owner'])
    return {'ok': True, 'username': name}, token


def recover(data):
    name = username(data.get('username'))
    code = data.get('recovery_code')
    if not isinstance(code, str) or not re.fullmatch(r'[A-Za-z0-9_-]{43}', code):
        raise AuthError('Неверный логин или код восстановления.', 401)
    encoded = hash_password(data.get('password'))
    replacement = secrets.token_urlsafe(32)
    with store.atomic(), store.connect() as db:
        row = db.execute('SELECT owner,recovery_hash FROM users WHERE username=?', (name,)).fetchone()
        if not hmac.compare_digest(digest(code), row['recovery_hash'] if row else '0'*64):
            raise AuthError('Неверный логин или код восстановления.', 401)
        db.execute('UPDATE users SET password_hash=?,recovery_hash=? WHERE owner=?', (encoded, digest(replacement), row['owner']))
        db.execute('DELETE FROM auth_sessions WHERE owner=?', (row['owner'],))
        token = _new_session(db, row['owner'])
    return {'ok': True, 'username': name, 'recovery_code': replacement}, token


def change_password(data):
    with store.connect() as db:
        row = db.execute('SELECT password_hash FROM users WHERE owner=?', (owner(),)).fetchone()
    if not row or not check_password(data.get('current_password'), row[0]):
        raise AuthError('Текущий пароль неверен.', 401)
    encoded = hash_password(data.get('password'))
    recovery = secrets.token_urlsafe(32)
    with store.atomic(), store.connect() as db:
        changed = db.execute('UPDATE users SET password_hash=?,recovery_hash=? WHERE owner=? AND password_hash=?', (encoded, digest(recovery), owner(), row[0])).rowcount
        if not changed:
            raise AuthError('Пароль уже изменился. Войди снова.', 401)
        db.execute('DELETE FROM auth_sessions WHERE owner=?', (owner(),))
        token = _new_session(db, owner())
    return {'ok': True, 'recovery_code': recovery}, token


def delete_account(data):
    with store.connect() as db:
        row = db.execute('SELECT password_hash FROM users WHERE owner=?', (owner(),)).fetchone()
    if not row or data.get('confirmed') is not True or not check_password(data.get('password'), row[0]):
        raise AuthError('Подтверди удаление и введи свой пароль.', 401)
    telegram_schema()
    with store.atomic(), store.connect() as db:
        if not db.execute('SELECT 1 FROM users WHERE owner=? AND password_hash=?', (owner(), row[0])).fetchone():
            raise AuthError('Пароль изменился. Войди снова.', 401)
        linked = db.execute('SELECT telegram_owner FROM telegram_links WHERE user_owner=?', (owner(),)).fetchone()
        if linked:
            _revoke_telegram(db, linked[0], owner())
        # Conversation IDs are scoped with an exact prefix, not a user-built LIKE.
        prefix = owner() + ':'
        for table, field in [('messages', 'session'), ('sessions', 'id')]:
            db.execute(f'DELETE FROM {table} WHERE substr({field},1,?)=?', (len(prefix), prefix))
        for table in ('events', 'notifications', 'receipts', 'link_codes', 'accounts', 'auth_sessions', 'ai_usage', 'telegram_inbox', 'daily_focus', 'notes', 'note_events', 'note_boards', 'users'):
            db.execute(f'DELETE FROM {table} WHERE owner=?', (owner(),))
        db.execute('DELETE FROM telegram_links WHERE user_owner=?', (owner(),))
    return {'ok': True}


def telegram_schema():
    """Idempotent compatibility migration; call from store.init when integrated."""
    with store.connect() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS telegram_identities(
            telegram_owner TEXT PRIMARY KEY, username TEXT, display_name TEXT,
            epoch INTEGER NOT NULL DEFAULT 0, verified_at TEXT,
            revoked_before INTEGER NOT NULL DEFAULT 0)""")
        columns = {row['name'] for row in db.execute('PRAGMA table_info(telegram_identities)')}
        if 'revoked_before' not in columns:
            db.execute('ALTER TABLE telegram_identities ADD COLUMN revoked_before INTEGER NOT NULL DEFAULT 0')


def telegram_epoch(telegram_owner):
    telegram_schema()
    with store.connect() as db:
        row = db.execute('SELECT epoch FROM telegram_identities WHERE telegram_owner=?', (telegram_owner,)).fetchone()
    return row[0] if row else 0


def telegram_revoked_before(telegram_owner):
    telegram_schema()
    with store.connect() as db:
        row = db.execute('SELECT revoked_before FROM telegram_identities WHERE telegram_owner=?', (telegram_owner,)).fetchone()
    return row[0] if row else 0


def record_telegram_identity(verified_user):
    """Only call after Telegram HMAC verification or an authenticated bot update."""
    uid = verified_user.get('id')
    if type(uid) is not int or not 0 < uid < 10**20:
        raise ValueError('Некорректный Telegram ID.')
    name = verified_user.get('username')
    name = name if isinstance(name, str) and re.fullmatch(r'[A-Za-z0-9_]{1,32}', name) else None
    display = ' '.join(v.strip() for k in ('first_name', 'last_name')
                       if isinstance((v := verified_user.get(k)), str))[:200] or None
    telegram_schema()
    with store.connect() as db:
        db.execute("""INSERT INTO telegram_identities(telegram_owner,username,display_name,verified_at)
            VALUES(?,?,?,?) ON CONFLICT(telegram_owner) DO UPDATE SET
            username=excluded.username,display_name=excluded.display_name,verified_at=excluded.verified_at""",
            ('tg:' + str(uid), name, display, store.utcnow()))


def telegram_status():
    """Public account-scoped metadata, never a token or link code."""
    telegram_schema()
    with store.connect() as db:
        link = db.execute('SELECT telegram_owner FROM telegram_links WHERE user_owner=?', (owner(),)).fetchone()
        target = link[0] if link else owner() if owner().startswith('tg:') else None
        row = db.execute('SELECT username,display_name,verified_at FROM telegram_identities WHERE telegram_owner=?', (target,)).fetchone() if target else None
    return {'linked': bool(link), 'id': target[3:] if target else None,
            'username': row['username'] if row else None,
            'display_name': row['display_name'] if row else None,
            'verified_at': row['verified_at'] if row else None,
            'can_unlink': bool(link and user())}


def _revoke_telegram(db, target, account_owner):
    db.execute("""INSERT INTO telegram_identities(telegram_owner,epoch,revoked_before) VALUES(?,1,?)
        ON CONFLICT(telegram_owner) DO UPDATE SET epoch=epoch+1,revoked_before=MAX(revoked_before,excluded.revoked_before)""", (target, int(time.time())))
    db.execute('DELETE FROM link_codes WHERE owner IN (?,?)', (target, account_owner))


def unlink_telegram(data):
    """Keep all records with the web account; revoke Telegram access, not logout."""
    telegram_schema()
    with store.connect() as db:
        row = db.execute('SELECT password_hash FROM users WHERE owner=?', (owner(),)).fetchone()
    if data.get('confirmed') is not True or not row or not check_password(data.get('password'), row[0]):
        raise AuthError('Подтверди отключение Telegram и введи текущий пароль.', 401)
    with store.atomic(), store.connect() as db:
        if not db.execute('SELECT 1 FROM users WHERE owner=? AND password_hash=?', (owner(), row[0])).fetchone():
            raise AuthError('Пароль изменился. Войди снова.', 401)
        link = db.execute('SELECT telegram_owner FROM telegram_links WHERE user_owner=?', (owner(),)).fetchone()
        if not link:
            raise AuthError('Telegram уже отключён.', 409)
        _revoke_telegram(db, link[0], owner())
        db.execute('DELETE FROM telegram_links WHERE user_owner=?', (owner(),))
        db.execute('UPDATE accounts SET chat_id=NULL WHERE owner=?', (owner(),))
        # Retried old bot updates must not run against a subsequently relinked account.
        db.execute('DELETE FROM telegram_inbox WHERE owner=?', (owner(),))
        db.execute("UPDATE notifications SET status='cancelled',lease_token=NULL,lease_until=0 WHERE owner=? AND status IN ('pending','retry','sending')", (owner(),))
    return {'ok': True, 'reload': True}


def resolve_telegram(telegram_owner):
    with store.connect() as db:
        row = db.execute('SELECT user_owner FROM telegram_links WHERE telegram_owner=?', (telegram_owner,)).fetchone()
    return row[0] if row else telegram_owner


def link_telegram(code):
    if not user():
        raise AuthError('Привязка доступна зарегистрированному аккаунту.', 403)
    telegram_schema()
    with store.atomic(), store.connect() as db:
        target = security.claim_link(code)
        if not re.fullmatch(r'tg:[1-9][0-9]{0,19}', target):
            raise AuthError('Этот Telegram уже связан с другим аккаунтом.', 409)
        if db.execute('SELECT 1 FROM telegram_links WHERE telegram_owner=? OR user_owner=?', (target, owner())).fetchone():
            raise AuthError('Telegram уже связан с аккаунтом.', 409)
        if not security.allowed(target[3:]):
            raise AuthError('Доступ этого Telegram к боту отключён.', 403)
        _revoke_telegram(db, target, owner())
        old = db.execute('SELECT chat_id FROM accounts WHERE owner=?', (target,)).fetchone()
        db.execute('INSERT INTO telegram_links VALUES(?,?)', (target, owner()))
        # Preserve all existing Telegram events, including imported sample IDs.
        collisions = db.execute('SELECT id FROM events WHERE owner=? AND source_id IN (SELECT source_id FROM events WHERE owner=?)', (target, owner())).fetchall()
        smallest = db.execute('SELECT MIN(source_id) FROM events WHERE owner IN (?,?)', (target, owner())).fetchone()[0]
        next_source = min(0, smallest or 0) - 1
        for collision in collisions:
            db.execute('UPDATE events SET source_id=? WHERE id=?', (next_source, collision[0]))
            next_source -= 1
        db.execute('UPDATE events SET owner=? WHERE owner=?', (owner(), target))
        db.execute('UPDATE notifications SET owner=? WHERE owner=?', (owner(), target))
        db.execute('UPDATE telegram_inbox SET owner=? WHERE owner=?', (owner(), target))
        # Notes travel with the Telegram space; boards with the same name merge.
        db.execute('UPDATE notes SET owner=? WHERE owner=?', (owner(), target))
        db.execute('UPDATE note_events SET owner=? WHERE owner=?', (owner(), target))
        db.execute('INSERT OR IGNORE INTO note_boards(owner,name,position,created_at) SELECT ?,name,position+100,created_at FROM note_boards WHERE owner=?', (owner(), target))
        db.execute('DELETE FROM note_boards WHERE owner=?', (target,))
        for row in db.execute('SELECT id FROM sessions WHERE substr(id,1,?)=?', (len(target)+1, target+':')).fetchall():
            old_sid = row[0]
            new_sid = owner() + old_sid[len(target):]
            if db.execute('SELECT 1 FROM sessions WHERE id=?', (new_sid,)).fetchone():
                new_sid = owner() + ':imported-' + uuid.uuid4().hex
            db.execute('UPDATE sessions SET id=? WHERE id=?', (new_sid, old_sid))
            db.execute('UPDATE messages SET session=? WHERE session=?', (new_sid, old_sid))
        if old and old['chat_id']:
            store.account({'chat_id': old['chat_id']})
        db.execute('DELETE FROM accounts WHERE owner=?', (target,))
        db.execute('DELETE FROM receipts WHERE owner=?', (target,))
        # A focus chosen in the old Telegram space would point at moved records.
        db.execute('DELETE FROM daily_focus WHERE owner=?', (target,))
