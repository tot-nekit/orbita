import unittest,tempfile,os,time,json,hmac,hashlib,urllib.parse,sqlite3,threading,uuid
from pathlib import Path
from datetime import datetime,timezone,timedelta
from unittest.mock import patch
from app import store,service,security,notifications,telegram_bot,config
from app.context import scope
from fixture_clock import historical

def init_data(uid=101,token='test:token',timestamp=None,**extra):
    data={'auth_date':str(int(time.time()) if timestamp is None else timestamp),'query_id':'query-test','user':json.dumps({'id':uid,'first_name':'Тест'},ensure_ascii=False,separators=(',',':')),**extra}
    secret=hmac.new(b'WebAppData',token.encode(),hashlib.sha256).digest();data['hash']=hmac.new(secret,'\n'.join(k+'='+v for k,v in sorted(data.items())).encode(),hashlib.sha256).hexdigest()
    return urllib.parse.urlencode(data)

class Case(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.old=store.DB_PATH;store.DB_PATH=str(Path(self.tmp.name)/'v2.db');store.init();self.env=patch.dict(os.environ,{'TELEGRAM_ALLOW_PUBLIC':'1','TELEGRAM_ALLOWED_USER_IDS':'','APP_SESSION_SECRET':'x'*48,'TELEGRAM_BOT_TOKEN':'test:token'});self.env.start();self.key=config.API_KEY;config.API_KEY=''
    def tearDown(self):config.API_KEY=self.key;self.env.stop();store.DB_PATH=self.old;self.tmp.cleanup()
    def create(self,title='Купить билеты',**kw):return store.save(service.validate({'title':title,**kw}))

class Security(Case):
    def test_valid_hmac(self):self.assertEqual(security.telegram_user(init_data())['id'],101)
    def test_tampered_user(self):
        with self.assertRaises(ValueError):security.telegram_user(init_data().replace('101','102'))
    def test_wrong_token(self):
        with self.assertRaises(ValueError):security.telegram_user(init_data(token='other'))
    def test_old_auth(self):
        with self.assertRaises(ValueError):security.telegram_user(init_data(timestamp=int(time.time())-3601))
    def test_future_auth(self):
        with self.assertRaises(ValueError):security.telegram_user(init_data(timestamp=int(time.time())+100))
    def test_duplicate_fields(self):
        with self.assertRaises(ValueError):security.telegram_user(init_data()+'&auth_date=1')
    def test_negative_uid(self):
        with self.assertRaises(ValueError):security.telegram_user(init_data(uid=-1))
    def test_bool_uid(self):
        with self.assertRaises(ValueError):security.telegram_user(init_data(uid=True))
    def test_allowlist(self):
        with patch.dict(os.environ,{'TELEGRAM_ALLOWED_USER_IDS':'777'}),self.assertRaises(ValueError):security.telegram_user(init_data())
    def test_private_by_default(self):
        with patch.dict(os.environ,{'TELEGRAM_ALLOW_PUBLIC':'0'}),self.assertRaises(ValueError):security.telegram_user(init_data())
    def test_tokens(self):self.assertEqual(security.verify(security.issue('tg:101')),'tg:101')
    def test_web_token(self):self.assertEqual(security.verify(security.issue('web')),'web')
    def test_expired_token(self):
        with self.assertRaises(ValueError):security.verify(security.issue('tg:101',-1))
    def test_tampered_token(self):
        with self.assertRaises(ValueError):security.verify(security.issue('tg:101')+'a')
    def test_revoke_allowlist(self):
        token=security.issue('tg:101')
        with patch.dict(os.environ,{'TELEGRAM_ALLOWED_USER_IDS':'777'}),self.assertRaises(ValueError):security.verify(token)
    def test_link_one_use(self):
        with scope('tg:101'):code=security.make_link()
        self.assertEqual(security.claim_link(code),'tg:101')
        with self.assertRaises(ValueError):security.claim_link(code)
    def test_link_expiry(self):
        with scope('tg:101'):code=security.make_link()
        with store.connect() as db:db.execute('UPDATE link_codes SET expires=0')
        with self.assertRaises(ValueError):security.claim_link(code)
    def test_new_link_revokes_old(self):
        with scope('tg:101'):old=security.make_link();new=security.make_link()
        with self.assertRaises(ValueError):security.claim_link(old)
        self.assertEqual(security.claim_link(new),'tg:101')
    def test_cookie_parsing(self):self.assertEqual(security.cookie_token('orbita_session=abc; other=1'),'abc')

class Isolation(Case):
    def setUp(self):
        super().setUp()
        with scope('tg:101'):self.a=self.create('Договор с Иваном');store.state('session',{'last_id':self.a['id']});store.message('session','user','private')
    def test_cannot_read(self):self.assertIsNone(store.get_event(self.a['id']))
    def test_cannot_list(self):self.assertEqual(store.list_events()[1],0)
    def test_cannot_search(self):self.assertEqual(store.search('договор')[1],0)
    def test_cannot_delete(self):self.assertFalse(store.delete(self.a['id']))
    def test_cannot_write(self):
        with self.assertRaises(ValueError):store.save(self.a,self.a['id'])
    def test_no_context_leak(self):self.assertEqual(store.state('session'),{});self.assertEqual(store.history('session'),[])
    def test_account_settings_isolated(self):
        with scope('tg:101'):store.account({'timezone':'UTC'})
        self.assertEqual(store.account()['timezone'],'Europe/Moscow')
    def test_import_per_owner(self):
        self.assertEqual(store.import_csv(),1000)
        with scope('tg:101'):self.assertEqual(store.import_csv(),1000);self.assertEqual(store.import_csv(),0)
    def test_no_conflicts_between_accounts(self):
        with scope('tg:101'):self.create('Встреча',kind='meeting',date='2026-10-20',time='15:00')
        self.assertEqual(store.conflicts(service.validate({'title':'Встреча','kind':'meeting','date':'2026-10-20','time':'15:00'})),[])
    def test_export_query_owner_cannot_override(self):self.assertEqual(store.stats('2026-10-20')['total'],0)

class Reliability(Case):
    def test_replay_no_duplicate(self):
        result=store.run_once('key',{'a':1},lambda:self.create());again=store.run_once('key',{'a':1},lambda:self.create());self.assertEqual(result,again);self.assertEqual(store.list_events()[1],1)
    def test_same_key_different_payload(self):
        store.run_once('key',{'a':1},lambda:self.create())
        with self.assertRaises(ValueError):store.run_once('key',{'a':2},lambda:self.create())
    def test_atomic_rollback(self):
        def fail():self.create();raise ValueError('test')
        with self.assertRaises(ValueError):store.run_once('key',{},fail)
        self.assertEqual(store.list_events()[1],0)
    def test_parallel_replay(self):
        results=[];errors=[];start=threading.Event()
        def worker():
            try:start.wait();results.append(store.run_once('key',{},lambda:self.create()))
            except Exception as e:errors.append(e)
        threads=[threading.Thread(target=worker) for _ in range(2)]
        for t in threads:t.start()
        start.set()
        for t in threads:t.join()
        self.assertEqual(errors,[]);self.assertEqual(results[0],results[1]);self.assertEqual(store.list_events()[1],1)
    def test_owner_receipts(self):
        store.run_once('key',{},lambda:self.create())
        with scope('tg:101'):store.run_once('key',{},lambda:self.create());self.assertEqual(store.list_events()[1],1)
    def test_undo_delete(self):
        e=self.create();store.delete(e['id']);self.assertIsNone(store.get_event(e['id']));self.assertEqual(store.restore(e['id'])['title'],e['title'])
    def test_undo_expired(self):
        e=self.create();store.delete(e['id'])
        with store.connect() as db:db.execute('UPDATE events SET deleted_at=?',((datetime.now(timezone.utc)-timedelta(minutes=11)).isoformat(),))
        with self.assertRaises(ValueError):store.restore(e['id'])
    def test_pagination(self):
        for i in range(35):self.create(str(i))
        a,total=store.list_events(limit=30);b,_=store.list_events(limit=30,offset=30);self.assertEqual(total,35);self.assertEqual(len(b),5);self.assertFalse({e['id'] for e in a}&{e['id'] for e in b})
    def test_dst_gap(self):
        with self.assertRaises(ValueError):service.validate({'title':'x','kind':'meeting','date':'2026-03-29','time':'01:30','timezone':'Europe/London'})
    def test_dst_fold(self):
        with self.assertRaises(ValueError):service.validate({'title':'x','kind':'meeting','date':'2026-10-25','time':'01:30','timezone':'Europe/London'})
    def test_legacy_migration(self):
        with store.connect() as db:
            db.execute('DROP TABLE events');db.execute("CREATE TABLE events(id INTEGER PRIMARY KEY,title TEXT,kind TEXT,date TEXT,time TEXT,duration INTEGER,status TEXT,created_at TEXT,source_id INTEGER UNIQUE,timezone TEXT)")
            db.execute("INSERT INTO events VALUES(42,'Legacy','task',NULL,NULL,60,'active','2026-01-01',NULL,'Europe/Moscow')")
        store.init();self.assertEqual(store.get_event(42)['owner'],'web');self.assertEqual(store.get_event(42)['title'],'Legacy')

class FakeTelegram:
    def __init__(self):self.calls=[];self.fail=False;self.docs=[]
    def call(self,method,payload=None,**kw):
        self.calls.append((method,payload))
        if self.fail:raise telegram_bot.BotError('test')
        return True
    def audio(self,file_id):return b'fake-ogg'
    def document(self,chat_id,data):self.docs.append((chat_id,data))
class Bot(Case):
    def setUp(self):super().setUp();self.client=FakeTelegram();self.n=0
    def update(self,text=None,uid=101,data=None,voice=None,update_id=None):
        self.n+=1;msg={'chat':{'id':uid,'type':'private'},'from':{'id':uid},'message_id':self.n}
        if text is not None:msg['text']=text
        if voice:msg['voice']=voice
        u={'update_id':update_id or self.n}
        if data:u['callback_query']={'id':str(self.n),'from':{'id':uid},'message':msg,'data':data}
        else:u['message']=msg
        return u
    def run_update(self,**kw):return telegram_bot.handle(self.update(**kw),self.client)
    def test_start(self):self.assertIn('Привет',self.run_update(text='/start')['text'])
    def test_id_without_access(self):
        with patch.dict(os.environ,{'TELEGRAM_ALLOW_PUBLIC':'0'}):self.assertIn('101',self.run_update(text='/id')['text'])
    def test_denied_no_data(self):
        with patch.dict(os.environ,{'TELEGRAM_ALLOW_PUBLIC':'0'}):self.assertIn('ограничен',self.run_update(text='/start')['text'])
    def test_create(self):
        r=self.run_update(text='Добавь задачу купить хлеб')
        with scope('tg:101'):
            self.assertEqual(store.list_events()[1],0)
            self.assertEqual(service.dialog_preview('telegram-conversation')['type'],'event')
        cb=r['reply_markup']['inline_keyboard'][0][0]['callback_data'];self.assertTrue(cb.startswith('yes:'))
        self.run_update(data=cb)
        with scope('tg:101'):self.assertEqual(store.list_events()[1],1)
    def test_duplicate_update(self):
        u=self.update(text='Добавь задачу купить хлеб');a=telegram_bot.handle(u,self.client);b=telegram_bot.handle(u,self.client);self.assertEqual(a,b)
        with scope('tg:101'):self.assertEqual(store.list_events()[1],0)
        confirmation=self.update(data=a['reply_markup']['inline_keyboard'][0][0]['callback_data'])
        a=telegram_bot.handle(confirmation,self.client);b=telegram_bot.handle(confirmation,self.client);self.assertEqual(a,b)
        with scope('tg:101'):self.assertEqual(store.list_events()[1],1)
    def test_shared_data_with_mini(self):
        self.run_update(text='Добавь задачу купить хлеб')
        with scope('tg:101'):self.assertEqual(store.search('хлеб')[1],0)
        self.run_update(text='да')
        with scope('tg:101'):self.assertEqual(store.search('хлеб')[1],1)
    def test_foreign_callback_denied(self):
        with scope('tg:999'):e=self.create()
        self.assertIn('удалена',self.run_update(data='done:'+str(e['id']))['text'])
        with scope('tg:999'):self.assertEqual(store.get_event(e['id'])['status'],'active')
    def test_delete_confirmation_nonce(self):
        with scope('tg:101'):e=self.create()
        r=self.run_update(data=f'askdel:{e["id"]}:{e["revision"]}');callback=r['reply_markup']['inline_keyboard'][0][0]['callback_data'];self.assertTrue(callback.startswith('yes:'))
        self.assertIn('неактуально',self.run_update(data='yes:wrong')['text']);self.run_update(data=callback)
        with scope('tg:101'):self.assertIsNone(store.get_event(e['id']))
    def test_voice_confirmation(self):
        config.API_KEY='test'
        with patch.object(config,'STT_ENABLED',True),patch('app.ai.transcribe',return_value='Добавь задачу купить хлеб'):r=self.run_update(voice={'file_id':'x','duration':5})
        with scope('tg:101'):self.assertEqual(store.list_events()[1],0)
        cb=r['reply_markup']['inline_keyboard'][0][0]['callback_data']
        with patch('app.ai.plan',side_effect=TimeoutError):r=self.run_update(data=cb)
        with scope('tg:101'):
            self.assertEqual(store.list_events()[1],0)
            self.assertEqual(service.dialog_preview('telegram-conversation')['title'],'Купить хлеб')
        cb=r['reply_markup']['inline_keyboard'][0][0]['callback_data'];self.assertTrue(cb.startswith('yes:'))
        self.run_update(data=cb)
        with scope('tg:101'):self.assertEqual(store.list_events()[1],1)
    def test_voice_without_key(self):self.assertIn('AI-ключ',self.run_update(voice={'file_id':'x','duration':5})['text'])
    def test_voice_too_long(self):
        config.API_KEY='test';self.assertIn('60 секунд',self.run_update(voice={'file_id':'x','duration':61})['text'])
    def test_export(self):
        self.run_update(text='Добавь задачу купить хлеб')
        with scope('tg:101'):self.assertEqual(store.list_events()[1],0)
        self.run_update(text='да');r=self.run_update(text='/export');self.assertIn('хлеб',r['document']);telegram_bot.deliver(self.client,r);self.assertEqual(len(self.client.docs),1)
    def test_timezone_sync(self):
        self.run_update(text='/timezone UTC')
        with scope('tg:101'):self.assertEqual(store.account()['timezone'],'UTC')
    def test_notify_off(self):
        self.run_update(text='/notify off')
        with scope('tg:101'):self.assertEqual(store.account()['notify'],0)
    def test_groups_ignored(self):
        u=self.update(text='/start');u['message']['chat']['type']='group';self.assertIsNone(telegram_bot.handle(u,self.client))
    def test_output_chunking(self):telegram_bot.deliver(self.client,{'chat_id':101,'text':'x'*8000});self.assertEqual(len(self.client.calls),3)

class Reminders(Case):
    def setUp(self):
        clock=historical();clock.start();self.addCleanup(clock.stop)
        super().setUp();self.client=FakeTelegram();self.now=datetime(2026,10,15,12,0,tzinfo=timezone.utc)
        with scope('tg:101'):
            store.account({'chat_id':101});self.e=self.create('Позвонить Ивану',kind='reminder',date='2026-10-15',time='15:00')
    def status(self):
        with store.connect() as db:return db.execute('SELECT status FROM notifications').fetchone()[0]
    def test_due_once(self):self.assertEqual(notifications.tick(self.client,self.now),1);self.assertEqual(notifications.tick(self.client,self.now),0);self.assertEqual(self.status(),'sent')
    def test_future_not_sent(self):self.assertEqual(notifications.tick(self.client,self.now-timedelta(minutes=1)),0)
    def test_old_marked_missed(self):self.assertEqual(notifications.tick(self.client,self.now+timedelta(hours=2)),0);self.assertEqual(self.status(),'missed')
    def test_import_not_delivered(self):
        with store.connect() as db:db.execute('UPDATE events SET source_id=42')
        self.assertEqual(notifications.tick(self.client,self.now),0)
    def test_disabled(self):
        with scope('tg:101'):store.account({'notify':0})
        self.assertEqual(notifications.tick(self.client,self.now),0)
    def test_done_not_sent(self):
        with scope('tg:101'):e=dict(self.e,status='done');store.save(e,e['id'])
        self.assertEqual(notifications.tick(self.client,self.now),0)
    def test_deleted_not_sent(self):
        with scope('tg:101'):store.delete(self.e['id'])
        self.assertEqual(notifications.tick(self.client,self.now),0)
    def test_retry_then_success(self):
        self.client.fail=True;notifications.tick(self.client,self.now);self.assertEqual(self.status(),'retry');self.client.fail=False;self.assertEqual(notifications.tick(self.client,self.now+timedelta(seconds=31)),1)
    def test_deleted_pending_cancelled(self):
        notifications.enqueue(self.now)
        with scope('tg:101'):store.delete(self.e['id'])
        self.assertEqual(notifications.tick(self.client,self.now),0);self.assertEqual(self.status(),'cancelled')
    def test_rescheduled_pending_cancelled(self):
        notifications.enqueue(self.now)
        with scope('tg:101'):store.save(dict(self.e,time='16:00'),self.e['id'])
        self.assertEqual(notifications.tick(self.client,self.now),0);self.assertEqual(self.status(),'cancelled')
    def test_outbox_survives_restart(self):notifications.enqueue(self.now);store.init();self.assertEqual(notifications.tick(self.client,self.now),1)
    def test_cancelled_reminder_reenabled(self):
        notifications.enqueue(self.now)
        with scope('tg:101'):store.account({'notify':0})
        notifications.tick(self.client,self.now);self.assertEqual(self.status(),'cancelled')
        with scope('tg:101'):store.account({'notify':1})
        self.assertEqual(notifications.tick(self.client,self.now+timedelta(seconds=20)),1)
    def test_reschedule_back_after_cancellation(self):
        notifications.enqueue(self.now)
        with scope('tg:101'):store.save(dict(self.e,time='16:00'),self.e['id'])
        notifications.tick(self.client,self.now)
        with scope('tg:101'):store.save(self.e,self.e['id'])
        self.assertEqual(notifications.tick(self.client,self.now+timedelta(seconds=20)),1)
    def test_health(self):self.assertFalse(notifications.health());store.runtime('bot_heartbeat',time.time());self.assertTrue(notifications.health())
