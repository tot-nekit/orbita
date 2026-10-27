"""Delivery UI and defer flow use disposable data and a fake Telegram transport."""
import json, os, tempfile, threading, unittest, urllib.request, urllib.error
from pathlib import Path
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from app import store, service, notifications, security, telegram_bot, config
from app.context import scope
from app.server import Handler
from fixture_clock import historical

class DeliveryWorkflow(unittest.TestCase):
    def setUp(self):
        clock=historical();clock.start();self.addCleanup(clock.stop)
        self.tmp=tempfile.TemporaryDirectory();self.db=patch.object(store,'DB_PATH',str(Path(self.tmp.name)/'data.db'));self.db.start()
        self.key=patch.object(config,'API_KEY','');self.key.start()
        self.env=patch.dict(os.environ,{'APP_SESSION_SECRET':'test-only-'+'x'*48,'TELEGRAM_ALLOW_PUBLIC':'1','TELEGRAM_ALLOWED_USER_IDS':''});self.env.start()
        store.init();self.now=datetime(2026,10,22,9,tzinfo=timezone.utc)
    def tearDown(self):self.env.stop();self.key.stop();self.db.stop();self.tmp.cleanup()
    def reminder(self):
        store.account({'chat_id':101,'notify':True})
        return store.save(service.validate({'title':'Позвонить','kind':'reminder','date':'2026-10-22','time':'12:00'}))
    def test_snooze_and_stale_revision(self):
        with scope('tg:101'):
            e=self.reminder();moved=notifications.snooze(e['id'],15,e['revision'],self.now)
            self.assertEqual(moved['time'],'12:15');self.assertEqual(moved['revision'],2)
            with self.assertRaises(ValueError):notifications.snooze(e['id'],15,e['revision'],self.now)
    def test_snooze_cross_owner_and_wrong_kind(self):
        with scope('tg:101'):e=self.reminder()
        with scope('tg:202'),self.assertRaises(ValueError):notifications.snooze(e['id'],15,e['revision'],self.now)
        e=store.save(service.validate({'title':'Task'}))
        with self.assertRaises(ValueError):notifications.snooze(e['id'],60,e['revision'],self.now)
    def test_snooze_across_midnight(self):
        with scope('tg:101'):
            e=self.reminder();moved=notifications.snooze(e['id'],60,e['revision'],datetime(2026,10,22,20,50,tzinfo=timezone.utc))
            self.assertEqual((moved['date'],moved['time']),('2026-10-23','00:50'))
    def test_status_hides_old_delivery_for_moved_record(self):
        with scope('tg:101'):
            e=self.reminder();notifications.enqueue(self.now)
            with store.connect() as db:db.execute("UPDATE notifications SET status='sent'")
            self.assertEqual(notifications.delivery_status(now=self.now)[0]['status'],'sent')
            notifications.snooze(e['id'],15,e['revision'],self.now)
            self.assertEqual(notifications.delivery_status(now=self.now)[0]['status'],'scheduled')
            store.account({'notify':False});self.assertEqual(notifications.delivery_status(now=self.now)[0]['status'],'disabled')
        with scope('tg:202'):self.assertEqual(notifications.delivery_status(now=self.now),[])
    def test_web_has_no_false_delivery_promise(self):
        self.reminder();self.assertEqual(notifications.delivery_status(now=self.now)[0]['status'],'not_connected')
    def test_notification_includes_versioned_snooze_and_replay(self):
        class Fake:
            def __init__(self):self.calls=[]
            def call(self,method,payload=None,**kw):self.calls.append((method,payload));return True
        client=Fake()
        with scope('tg:101'):e=self.reminder()
        self.assertEqual(notifications.tick(client,self.now),1)
        payload=[p for method,p in client.calls if method=='sendMessage'][0]
        buttons=payload['reply_markup']['inline_keyboard'];data=buttons[1][0]['callback_data']
        self.assertEqual(data,f'snooze:{e["id"]}:1:15')
        update={'update_id':1001,'callback_query':{'id':'cb','from':{'id':101},'message':{'chat':{'id':101,'type':'private'}},'data':data}}
        first=telegram_bot.handle(update,client);second=telegram_bot.handle(update,client);self.assertEqual(first,second)
        with scope('tg:101'):self.assertEqual(store.get_event(e['id'])['revision'],2)
    def test_http_status_is_owner_scoped_and_delete_detects_stale(self):
        with scope('tg:101'):e=self.reminder();store.save(dict(e,title='Изменено'),e['id'])
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
        base='http://127.0.0.1:'+str(server.server_port)
        try:
            for uid,count in [('tg:101',1),('tg:202',0)]:
                req=urllib.request.Request(base+'/api/notifications',headers={'Authorization':'Bearer '+security.issue(uid)})
                with urllib.request.urlopen(req) as response:self.assertEqual(len(json.load(response)['notifications']),count)
            req=urllib.request.Request(base+'/api/events/delete',data=json.dumps({'id':e['id'],'confirmed':True,'revision':1}).encode(),headers={'Authorization':'Bearer '+security.issue('tg:101'),'Content-Type':'application/json'})
            with self.assertRaises(urllib.error.HTTPError) as error:urllib.request.urlopen(req)
            self.assertEqual(error.exception.code,409);error.exception.close()
            with scope('tg:101'):self.assertIsNotNone(store.get_event(e['id']))
        finally:server.shutdown();server.server_close()

if __name__=='__main__':unittest.main()
