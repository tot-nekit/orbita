"""Telegram HMAC verification, expiring app sessions and one-time account linking."""
import base64,hashlib,hmac,json,os,secrets,time,re
from urllib.parse import parse_qsl
from http.cookies import SimpleCookie
from pathlib import Path
from . import store
from .context import owner

def secret():
    value=os.getenv('APP_SESSION_SECRET')
    if value:
        if len(value)<32:raise ValueError('APP_SESSION_SECRET должен быть не короче 32 символов.')
        return value.encode()
    path=Path(store.DB_PATH).parent/'.session_secret';path.parent.mkdir(parents=True,exist_ok=True)
    try:
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'w') as f:f.write(secrets.token_urlsafe(48))
    except FileExistsError:pass
    return path.read_bytes()
def b64(data):return base64.urlsafe_b64encode(data).decode().rstrip('=')
def issue(owner_id,ttl=43200):
    body=b64(json.dumps({'owner':owner_id,'exp':int(time.time())+ttl},separators=(',',':')).encode())
    return body+'.'+b64(hmac.new(secret(),body.encode(),hashlib.sha256).digest())
def verify(token):
    try:
        body,signature=token.split('.')
        if not hmac.compare_digest(signature,b64(hmac.new(secret(),body.encode(),hashlib.sha256).digest())):raise ValueError()
        data=json.loads(base64.urlsafe_b64decode(body+'='*(-len(body)%4)))
        if data['exp']<=time.time() or not re.fullmatch(r'(?:web|tg:[1-9][0-9]{0,19})',data['owner']):raise ValueError()
        from . import auth
        if data['owner']=='web' and auth.required():raise ValueError()
        if data['owner'].startswith('tg:'):
            if not allowed(data['owner'][3:]):raise ValueError()
            return auth.resolve_telegram(data['owner'])
        return data['owner']
    except Exception:raise ValueError('Сессия истекла или недействительна. Открой приложение заново.') from None

def allowed(uid):
    ids={v.strip() for v in os.getenv('TELEGRAM_ALLOWED_USER_IDS','').split(',') if v.strip()}
    return str(uid) in ids or (not ids and os.getenv('TELEGRAM_ALLOW_PUBLIC')=='1')

def telegram_user(raw,bot_token=None,now=None):
    token=bot_token or os.getenv('TELEGRAM_BOT_TOKEN','')
    if not token:raise ValueError('Telegram ещё не настроен на сервере.')
    if not isinstance(raw,str) or len(raw)>16000:raise ValueError('Некорректные данные Telegram.')
    pairs=parse_qsl(raw,keep_blank_values=True,strict_parsing=True)
    data=dict(pairs)
    if len(pairs)!=len(data):raise ValueError('Повторяющиеся поля Telegram.')
    received=data.pop('hash','')
    check='\n'.join(k+'='+v for k,v in sorted(data.items()))
    key=hmac.new(b'WebAppData',token.encode(),hashlib.sha256).digest()
    expected=hmac.new(key,check.encode(),hashlib.sha256).hexdigest()
    if not re.fullmatch(r'[0-9a-f]{64}',received) or not hmac.compare_digest(expected,received):raise ValueError('Подпись Telegram не прошла проверку.')
    try:
        stamp=int(data['auth_date']);current=time.time() if now is None else now
        if stamp>current+30 or current-stamp>3600:raise ValueError()
        user=json.loads(data['user']);uid=user['id']
        if type(uid) is not int or uid<=0 or uid>10**20:raise ValueError()
    except Exception:raise ValueError('Данные Telegram устарели или не содержат пользователя.') from None
    if not allowed(uid):raise ValueError('Доступ ограничен. Владелец должен разрешить твой Telegram ID.')
    return user

def make_link():
    code=secrets.token_hex(5).upper();hashed=hashlib.sha256(code.encode()).hexdigest()
    with store.connect() as db:
        db.execute('DELETE FROM link_codes WHERE owner=? OR expires<?',(owner(),time.time()))
        db.execute('INSERT INTO link_codes VALUES(?,?,?)',(hashed,owner(),time.time()+600))
    return code

def claim_link(code):
    if not isinstance(code,str) or not re.fullmatch(r'[0-9A-F]{10}',code.strip().upper()):raise ValueError('Код состоит из 10 символов. Получи его командой /link у бота.')
    hashed=hashlib.sha256(code.strip().upper().encode()).hexdigest()
    with store.atomic(),store.connect() as db:
        r=db.execute('SELECT owner FROM link_codes WHERE hash=? AND expires>?',(hashed,time.time())).fetchone()
        if not r:raise ValueError('Код уже использован или истёк. Получи новый через /link.')
        db.execute('DELETE FROM link_codes WHERE hash=?',(hashed,))
        return r[0]

def cookie_token(header):
    try:
        cookies=SimpleCookie();cookies.load(header or '')
        return cookies['orbita_session'].value if 'orbita_session' in cookies else None
    except Exception:return None
