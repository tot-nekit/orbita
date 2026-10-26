"""Owner-scoped SQLite repository, legacy migration, transactions and idempotency."""
import json,csv,re,hashlib,time,os
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from datetime import datetime,timezone,timedelta
from difflib import SequenceMatcher
from .config import DB_PATH,ROOT
from .context import owner
from .database import sqlite3,configure_connection,validate_runtime
TX=ContextVar('orbita_transaction',default=None)
def utcnow():return datetime.now(timezone.utc).isoformat()
@contextmanager
def connect():
    if TX.get() is not None:
        yield TX.get();return
    Path(DB_PATH).parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    if not Path(DB_PATH).exists():
        try:
            fd=os.open(DB_PATH,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600);os.close(fd)
        except FileExistsError:pass
    db=sqlite3.connect(DB_PATH,timeout=60);db.row_factory=sqlite3.Row
    try:
        configure_connection(db)
        # Changing journal mode is a database-wide operation. Concurrent first
        # connections may get SQLITE_BUSY even with the normal busy timeout.
        db.execute('PRAGMA busy_timeout=1000')
        deadline=time.monotonic()+60
        while True:
            try:
                mode=db.execute('PRAGMA journal_mode').fetchone()[0]
                if str(mode).lower()!='wal':db.execute('PRAGMA journal_mode=WAL').fetchone()
                break
            except sqlite3.OperationalError as exc:
                if not any(word in str(exc).lower() for word in ('locked','busy')) or time.monotonic()>=deadline:raise
                time.sleep(.05)
        db.execute('PRAGMA busy_timeout=60000')
        with db:yield db
    finally:db.close()
@contextmanager
def atomic():
    if TX.get() is not None:yield;return
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        if owner().startswith('u:') and not db.execute('SELECT 1 FROM users WHERE owner=?',(owner(),)).fetchone():raise PermissionError('Аккаунт удалён. Войди снова.')
        token=TX.set(db)
        try:yield
        finally:TX.reset(token)
SCHEMA='''CREATE TABLE IF NOT EXISTS events (
 id INTEGER PRIMARY KEY,owner TEXT NOT NULL DEFAULT 'web',title TEXT NOT NULL,kind TEXT NOT NULL,
 date TEXT,time TEXT,duration INTEGER NOT NULL DEFAULT 60,status TEXT NOT NULL DEFAULT 'active',
 created_at TEXT NOT NULL,source_id INTEGER,timezone TEXT NOT NULL DEFAULT 'Europe/Moscow',
 deleted_at TEXT,revision INTEGER NOT NULL DEFAULT 1,UNIQUE(owner,source_id));'''
def init():
    validate_runtime()
    with connect() as db:
        # Serialize the entire migration across web/bot processes. executescript
        # implicitly commits, so run these simple DDL statements individually.
        db.execute('BEGIN IMMEDIATE')
        old=db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='events'").fetchone()
        if old and 'owner' not in [r['name'] for r in db.execute('PRAGMA table_info(events)')]:
            db.execute('ALTER TABLE events RENAME TO legacy_events')
            db.execute(SCHEMA)
            cols='id,title,kind,date,time,duration,status,created_at,source_id,timezone'
            db.execute(f'INSERT INTO events({cols}) SELECT {cols} FROM legacy_events');db.execute('DROP TABLE legacy_events')
        schema=SCHEMA+'''
        CREATE INDEX IF NOT EXISTS events_owner_date ON events(owner,deleted_at,date,time);
        CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY,state TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,session TEXT NOT NULL,role TEXT NOT NULL,text TEXT NOT NULL,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS accounts(owner TEXT PRIMARY KEY,chat_id INTEGER,timezone TEXT NOT NULL DEFAULT 'Europe/Moscow',notify INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS receipts(owner TEXT,key TEXT,digest TEXT,result TEXT,created REAL,PRIMARY KEY(owner,key));
        CREATE TABLE IF NOT EXISTS link_codes(hash TEXT PRIMARY KEY,owner TEXT,expires REAL);
        CREATE TABLE IF NOT EXISTS runtime(key TEXT PRIMARY KEY,value TEXT);
        CREATE TABLE IF NOT EXISTS users(owner TEXT PRIMARY KEY,username TEXT UNIQUE NOT NULL,password_hash TEXT NOT NULL,recovery_hash TEXT NOT NULL,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS auth_sessions(hash TEXT PRIMARY KEY,owner TEXT NOT NULL,expires REAL NOT NULL,created REAL NOT NULL);
        CREATE INDEX IF NOT EXISTS auth_sessions_owner ON auth_sessions(owner);
        CREATE TABLE IF NOT EXISTS auth_limits(key TEXT PRIMARY KEY,attempts INTEGER NOT NULL,expires REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS telegram_links(telegram_owner TEXT PRIMARY KEY,user_owner TEXT UNIQUE NOT NULL);
        CREATE TABLE IF NOT EXISTS ai_usage(owner TEXT,day TEXT,requests INTEGER NOT NULL,PRIMARY KEY(owner,day));
        CREATE TABLE IF NOT EXISTS telegram_inbox(id INTEGER PRIMARY KEY,owner TEXT NOT NULL,digest TEXT NOT NULL,payload TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pending',attempts INTEGER NOT NULL DEFAULT 0,next_try REAL NOT NULL DEFAULT 0,lease_until REAL NOT NULL DEFAULT 0,lease_token TEXT,created REAL NOT NULL,last_error TEXT);
        CREATE INDEX IF NOT EXISTS telegram_inbox_pending ON telegram_inbox(status,next_try,lease_until,id);
        CREATE TABLE IF NOT EXISTS notifications(event_id INTEGER,due TEXT,owner TEXT,status TEXT,attempts INTEGER DEFAULT 0,next_try REAL DEFAULT 0,lease_until REAL DEFAULT 0,last_error TEXT,lease_token TEXT,PRIMARY KEY(event_id,due));
        '''
        for statement in schema.split(';'):
            if statement.strip():db.execute(statement)
        if 'lease_token' not in [r['name'] for r in db.execute('PRAGMA table_info(notifications)')]:
            db.execute('ALTER TABLE notifications ADD COLUMN lease_token TEXT')
        if 'use_ai' not in [r['name'] for r in db.execute('PRAGMA table_info(accounts)')]:
            db.execute('ALTER TABLE accounts ADD COLUMN use_ai INTEGER NOT NULL DEFAULT 1')
        # Migrate v1 conversation keys once; owner-prefix prevents guessing another account's session.
        if not db.execute("SELECT 1 FROM runtime WHERE key='sessions_v2'").fetchone():
            db.execute("UPDATE sessions SET id='web:'||id WHERE id NOT LIKE 'web:%' AND id NOT LIKE 'tg:%'")
            db.execute("UPDATE messages SET session='web:'||session WHERE session NOT LIKE 'web:%' AND session NOT LIKE 'tg:%'")
            db.execute("INSERT INTO runtime VALUES('sessions_v2','1')")

def import_csv():
    path=ROOT/'resources/source_events.csv'
    if not path.exists():raise ValueError('CSV задания не найден в resources/.')
    added=0
    with path.open(encoding='utf-8-sig') as f,connect() as db:
        for r in csv.DictReader(f):
            kind='meeting' if re.search(r'встреча|созвон|демо|планёрк',r['text'],re.I) else 'reminder' if r['text'].lower().startswith('напомнить') else 'task'
            added+=db.execute('INSERT OR IGNORE INTO events(owner,title,kind,date,time,created_at,source_id) VALUES(?,?,?,?,?,?,?)',(owner(),r['text'],kind,r['date'],r['time'],r['date']+'T00:00:00Z',int(r['id']))).rowcount
    return added

def get_event(i,include_deleted=False):
    with connect() as db:
        r=db.execute('SELECT * FROM events WHERE owner=? AND id=?'+('' if include_deleted else ' AND deleted_at IS NULL'),(owner(),i)).fetchone()
    return dict(r) if r else None

def save(e,event_id=None):
    cols=['title','kind','date','time','duration','status','timezone']
    vals=[e.get(k,{'duration':60,'status':'active','timezone':'Europe/Moscow'}.get(k)) for k in cols]
    with connect() as db:
        if event_id:
            cur=db.execute('UPDATE events SET '+','.join(k+'=?' for k in cols)+',revision=revision+1 WHERE owner=? AND id=? AND deleted_at IS NULL',vals+[owner(),event_id])
            if not cur.rowcount:raise ValueError('Запись не найдена.')
            i=event_id
        else:i=db.execute('INSERT INTO events('+','.join(cols)+',owner,created_at) VALUES('+','.join('?' for _ in cols)+',?,?)',vals+[owner(),utcnow()]).lastrowid
    return get_event(i)
def delete(i,revision=None):
    with connect() as db:
        sql='UPDATE events SET deleted_at=?,revision=revision+1 WHERE id=? AND owner=? AND deleted_at IS NULL'
        args=[utcnow(),i,owner()]
        if revision is not None:sql+=' AND revision=?';args.append(revision)
        return db.execute(sql,args).rowcount>0

def restore(i):
    with connect() as db:
        e=db.execute('SELECT deleted_at FROM events WHERE owner=? AND id=?',(owner(),i)).fetchone()
        if not e or not e[0]:raise ValueError('Удалённая запись не найдена.')
        if datetime.fromisoformat(e[0])<datetime.now(timezone.utc)-timedelta(minutes=10):raise ValueError('Срок отмены удаления истёк (10 минут).')
        db.execute('UPDATE events SET deleted_at=NULL,revision=revision+1 WHERE owner=? AND id=?',(owner(),i))
    return get_event(i)

def list_events(start=None,end=None,kind=None,status=None,limit=200,offset=0,bucket=None,day=None):
    sql='SELECT * FROM events WHERE owner=? AND deleted_at IS NULL';args=[owner()]
    for expr,v in [('date>=?',start),('date<=?',end),('kind=?',kind),('status=?',status)]:
        if v:sql+=' AND '+expr;args.append(v)
    if bucket=='inbox':sql+=' AND date IS NULL'
    elif bucket=='overdue':
        if not day:
            from .dates import clock
            day=clock(account()['timezone']).date().isoformat()
        sql+=' AND date<?';args.append(day)
    elif bucket is not None:raise ValueError('Неизвестный раздел записей.')
    with connect() as db:
        total=db.execute('SELECT COUNT(*) FROM ('+sql+')',args).fetchone()[0]
        rows=db.execute(sql+' ORDER BY date IS NULL,date,time IS NULL,time,id LIMIT ? OFFSET ?',args+[limit,offset]).fetchall()
    return [dict(r) for r in rows],total

STOP={'найди','найти','задача','задачу','задачи','встреча','встречу','встречи','событие','когда','была','было','меня','про','мне','покажи','старые','раньше','ставил','напоминание','с','по','и','я','у','на','который','пожалуйста'}
SYNONYMS={'отчет':'отчет','репорт':'отчет','доклад':'отчет','презентац':'презентац','слайд':'презентац','созвон':'встреч','встреч':'встреч','звонок':'звон','позвон':'звон','звон':'звон','контракт':'договор','договор':'договор'}
def tokens(s):
    out=[]
    for w in re.findall(r'[a-zа-я0-9]+',s.lower().replace('ё','е')):
        if w in STOP or len(w)<2:continue
        w=re.sub(r'(иями|ями|ами|ого|ему|ому|ией|ий|ой|ей|ом|ем|ам|ям|ах|ях|ов|ев|ую|юю|а|я|у|ю|ы|и|е|о|ь)$','',w)
        w=next((v for k,v in SYNONYMS.items() if w.startswith(k)),w)
        if w:out.append(w)
    return out

def search(q,kind=None,limit=30,offset=0):
    ts=tokens(q)
    if not ts:return [],0
    with connect() as db:rows=[dict(r) for r in db.execute('SELECT * FROM events WHERE owner=? AND deleted_at IS NULL'+(' AND kind=?' if kind else ''),[owner()]+([kind] if kind else []))]
    ranked=[]
    for row in rows:
        ws=tokens(row['title']);scores=[]
        for t in ts:
            scores.append(max((1 if t==w else .88 if min(len(t),len(w))>=4 and (t.startswith(w) or w.startswith(t)) else .7 if SequenceMatcher(None,t,w).ratio()>=.78 else 0 for w in ws),default=0))
        score=sum(scores)/len(scores)
        if score>=.55:ranked.append((score,row))
    ranked.sort(key=lambda x:(x[0],x[1]['date'] or ''),reverse=True)
    return [r for _,r in ranked[offset:offset+limit]],len(ranked)

def conflicts(e,exclude=None):
    if e['kind']!='meeting' or e.get('status')=='done' or not e.get('date') or not e.get('time'):return []
    from zoneinfo import ZoneInfo
    begin=datetime.fromisoformat(e['date']+'T'+e['time']).replace(tzinfo=ZoneInfo(e['timezone']));finish=begin+timedelta(minutes=e['duration'])
    with connect() as db:rows=[dict(r) for r in db.execute("SELECT * FROM events WHERE owner=? AND deleted_at IS NULL AND kind='meeting' AND status='active' AND time IS NOT NULL",(owner(),))]
    result=[]
    for r in rows:
        if r['id']==exclude:continue
        rb=datetime.fromisoformat(r['date']+'T'+r['time']).replace(tzinfo=ZoneInfo(r['timezone']))
        if begin<rb+timedelta(minutes=r['duration']) and rb<finish:result.append(r)
    return result

def sidkey(sid):return owner()+':'+sid
def state_snapshot(sid):
    """Read state and a monotonic conversation revision in one SQLite statement."""
    with connect() as db:
        r=db.execute('SELECT (SELECT state FROM sessions WHERE id=?) AS state, (SELECT COALESCE(MAX(id),0) FROM messages WHERE session=?) AS revision',(sidkey(sid),sidkey(sid))).fetchone()
    return (json.loads(r['state']) if r['state'] else {},r['revision'])

def state(sid,value=None):
    with connect() as db:
        if value is not None:db.execute('INSERT OR REPLACE INTO sessions VALUES(?,?)',(sidkey(sid),json.dumps(value,ensure_ascii=False)))
        else:
            r=db.execute('SELECT state FROM sessions WHERE id=?',(sidkey(sid),)).fetchone();return json.loads(r[0]) if r else {}
def message(sid,role,text):
    with connect() as db:db.execute('INSERT INTO messages(session,role,text,created_at) VALUES(?,?,?,?)',(sidkey(sid),role,text,utcnow()))
def history(sid):
    with connect() as db:rows=db.execute('SELECT role,text,created_at FROM messages WHERE session=? ORDER BY id DESC LIMIT 80',(sidkey(sid),)).fetchall()
    return [dict(r) for r in reversed(rows)]

def account(values=None):
    with connect() as db:
        public=os.getenv('AUTH_REQUIRED','1' if os.getenv('APP_ENV')=='production' else '0')=='1'
        db.execute('INSERT OR IGNORE INTO accounts(owner,use_ai) VALUES(?,?)',(owner(),0 if public else 1))
        if values:
            for k in ('chat_id','timezone','notify','use_ai'):
                if k in values:db.execute(f'UPDATE accounts SET {k}=? WHERE owner=?',(values[k],owner()))
        return dict(db.execute('SELECT * FROM accounts WHERE owner=?',(owner(),)).fetchone())

def _digest(payload):return hashlib.sha256(json.dumps(payload,sort_keys=True,ensure_ascii=False).encode()).hexdigest()

def cached_result(key,payload):
    """Read-only replay shortcut. run_once still arbitrates concurrent requests."""
    with connect() as db:
        r=db.execute('SELECT digest,result FROM receipts WHERE owner=? AND key=? AND created>=?',(owner(),key,time.time()-86400)).fetchone()
    if not r:return None
    if r['digest']!=_digest(payload):raise ValueError('Этот ключ запроса уже использован для другого действия.')
    return json.loads(r['result'])

def run_once(key,payload,fn):
    digest=_digest(payload)
    with atomic():
        with connect() as db:
            db.execute('DELETE FROM receipts WHERE created<?',(time.time()-86400,))
            r=db.execute('SELECT digest,result FROM receipts WHERE owner=? AND key=?',(owner(),key)).fetchone()
            if r:
                if r['digest']!=digest:raise ValueError('Этот ключ запроса уже использован для другого действия.')
                return json.loads(r['result'])
            result=fn()
            db.execute('INSERT INTO receipts VALUES(?,?,?,?,?)',(owner(),key,digest,json.dumps(result,ensure_ascii=False),time.time()))
            return result

def stats(day):
    with connect() as db:
        base=' FROM events WHERE owner=? AND deleted_at IS NULL'
        return {k:db.execute('SELECT COUNT(*)'+base+suffix,[owner()]+args).fetchone()[0] for k,suffix,args in [('total','',[]),('today'," AND date=? AND status='active'",[day]),('done'," AND status='done'",[]),('meetings'," AND date=? AND kind='meeting' AND status='active'",[day]),('overdue'," AND date<? AND status='active'",[day]),('inbox'," AND date IS NULL AND status='active'",[])]}

def runtime(key,value=None):
    with connect() as db:
        if value is not None:db.execute('INSERT OR REPLACE INTO runtime VALUES(?,?)',(key,str(value)))
        else:
            r=db.execute('SELECT value FROM runtime WHERE key=?',(key,)).fetchone();return r[0] if r else None

def reserve_ai_request():
    """Bound public provider spending across all channels and processes."""
    day=datetime.now(timezone.utc).date().isoformat()
    per_user=max(1,int(os.getenv('AI_DAILY_USER_LIMIT','50')))
    global_limit=max(1,int(os.getenv('AI_DAILY_LIMIT','1000')))
    with atomic(),connect() as db:
        db.execute('DELETE FROM ai_usage WHERE day<?',((datetime.now(timezone.utc)-timedelta(days=7)).date().isoformat(),))
        own=db.execute('SELECT requests FROM ai_usage WHERE owner=? AND day=?',(owner(),day)).fetchone()
        total=db.execute('SELECT COALESCE(sum(requests),0) FROM ai_usage WHERE day=?',(day,)).fetchone()[0]
        if own and own[0]>=per_user or total>=global_limit:raise ValueError('Дневной лимит ИИ исчерпан. Доступны формы и простые текстовые команды.')
        db.execute('INSERT INTO ai_usage VALUES(?,?,1) ON CONFLICT(owner,day) DO UPDATE SET requests=requests+1',(owner(),day))
