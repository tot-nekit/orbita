"""Managed-hosting HTTP, durable webhook retries and registered-owner delivery."""
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from wsgiref.simple_server import WSGIRequestHandler, make_server
from wsgiref.validate import validator
from app import auth, config, notifications, security, service, store, webhooks
from app.context import scope
from app.wsgi import application
from app.wsgi import WSGIHandler
from io import BytesIO

SECRET='webhook-unit-fixture-'+'x'*32


class Quiet(WSGIRequestHandler):
    def log_message(self,*args):pass


class FakeTelegram:
    def __init__(self, fail=False):self.sent=[];self.fail=fail
    def call(self,method,payload=None,timeout=45):
        if method=='sendMessage':
            if self.fail:raise RuntimeError('Synthetic network failure')
            self.sent.append(payload)
        return True


class ManagedHosting(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.old=store.DB_PATH;self.old_config=config.DB_PATH
        store.DB_PATH=config.DB_PATH=str(Path(self.tmp.name)/'managed.sqlite3')
        self.env=patch.dict(os.environ,APP_ENV='test',DATABASE_ENCRYPTION='0',AUTH_REQUIRED='1',COOKIE_SECURE='0',APP_SESSION_SECRET='managed-test-only-'+'x'*48,TELEGRAM_BOT_TOKEN='test:token',TELEGRAM_MODE='webhook',TELEGRAM_WEBHOOK_SECRET=SECRET,TELEGRAM_ALLOWED_USER_IDS='101,202',TELEGRAM_ALLOW_PUBLIC='0',WEBHOOK_INLINE_WORK='0')
        self.env.start();store.init()
        self.http=make_server('127.0.0.1',0,validator(application),handler_class=Quiet)
        threading.Thread(target=self.http.serve_forever,daemon=True).start()
        self.base='http://127.0.0.1:'+str(self.http.server_port)

    def tearDown(self):
        self.http.shutdown();self.http.server_close();self.env.stop()
        store.DB_PATH=self.old;config.DB_PATH=self.old_config;self.tmp.cleanup()

    def req(self,path,data=None,cookie=None,secret=None):
        headers={'Content-Type':'application/json'}
        if cookie:headers['Cookie']=cookie
        if secret is not None:headers['X-Telegram-Bot-Api-Secret-Token']=secret
        request=urllib.request.Request(self.base+path,headers=headers,data=json.dumps(data).encode() if data is not None else None)
        try:response=urllib.request.urlopen(request)
        except urllib.error.HTTPError as exc:response=exc
        with response:
            body=response.read()
            content=json.loads(body) if 'application/json' in response.headers['Content-Type'] else body
            return response.status,content,response.headers

    def update(self,i=1,uid=101,text='Добавь задачу проверить отчёт завтра'):
        return {'update_id':i,'message':{'from':{'id':uid},'chat':{'id':uid,'type':'private'},'text':text}}

    def register(self,name='managed_user'):
        status,_,headers=self.req('/api/auth/register',{'username':name,'password':'managed fixture phrase 123'})
        self.assertEqual(status,200)
        return headers['Set-Cookie'].split(';')[0]

    def test_wsgi_real_http_registration_isolation_and_export(self):
        status,_,headers=self.req('/')
        self.assertEqual(status,200);self.assertIn('Content-Security-Policy',headers)
        self.assertEqual(self.req('/api/events')[0],401)
        alice=self.register('alice');bob=self.register('bob')
        self.assertEqual(self.req('/api/events/save',{'title':'Личный отчёт'},alice)[0],200)
        query='/api/events?'+urllib.parse.urlencode({'q':'отчёт'})
        self.assertEqual(self.req(query,cookie=alice)[1]['total'],1)
        self.assertEqual(self.req(query,cookie=bob)[1]['total'],0)
        self.assertEqual(self.req('/api/export',cookie=bob)[1],[])
        self.req('/api/logout',{},alice)
        self.assertEqual(self.req('/api/events',cookie=alice)[0],401)

    def test_webhook_secret_precedes_queue_and_account_mutation(self):
        for supplied in (None,'incorrect'):
            self.assertEqual(self.req('/api/telegram/webhook',self.update(),secret=supplied)[0],401)
        with store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM telegram_inbox').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT count(*) FROM events').fetchone()[0],0)

    def test_ack_before_processing_and_duplicate_delivery_once(self):
        update=self.update()
        self.assertEqual(self.req('/api/telegram/webhook',update,secret=SECRET)[0],200)
        with store.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM events').fetchone()[0],0)
        client=FakeTelegram();self.assertEqual(webhooks.drain(client),1)
        self.assertEqual(len(client.sent),1)
        self.assertEqual(self.req('/api/telegram/webhook',update,secret=SECRET)[0],200)
        self.assertEqual(webhooks.drain(client),0)
        with store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM events').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT status,payload FROM telegram_inbox').fetchone()[:],('done','{}'))

    def test_retry_uses_receipt_without_creating_a_second_event(self):
        webhooks.receive(self.update(),SECRET)
        client=FakeTelegram(fail=True);webhooks.drain(client)
        with store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM events').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT status FROM telegram_inbox').fetchone()[0],'retry')
            db.execute('UPDATE telegram_inbox SET next_try=0')
        client.fail=False;webhooks.drain(client)
        self.assertEqual(len(client.sent),1)
        with store.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM events').fetchone()[0],1)

    def test_per_owner_order_and_another_owner_can_proceed(self):
        for i,uid in [(10,101),(11,101),(12,202)]:webhooks.receive(self.update(i,uid),SECRET)
        first=webhooks._claim();self.assertEqual(first['id'],10)
        second=webhooks._claim();self.assertEqual(second['id'],12)
        self.assertIsNone(webhooks._claim())

    def test_stale_worker_cannot_send_after_another_worker_finished(self):
        import app.telegram_bot as bot
        webhooks.receive(self.update(),SECRET)
        started=threading.Event();release=threading.Event();original=bot.handle;count=0
        def delayed(update,client):
            nonlocal count
            count+=1
            if count==1:started.set();self.assertTrue(release.wait(10))
            return original(update,client)
        client=FakeTelegram()
        with patch.object(bot,'handle',side_effect=delayed),ThreadPoolExecutor(1) as pool:
            old=pool.submit(webhooks.drain,client)
            self.assertTrue(started.wait(5))
            with store.connect() as db:db.execute('UPDATE telegram_inbox SET lease_until=0')
            webhooks.drain(client)
            release.set();old.result(timeout=10)
        self.assertEqual(len(client.sent),1)
        with store.connect() as db:
            self.assertEqual(db.execute('SELECT status FROM telegram_inbox').fetchone()[0],'done')
            self.assertEqual(db.execute('SELECT count(*) FROM events').fetchone()[0],1)

    def test_terminal_failure_wipes_payload_and_allows_next_message(self):
        webhooks.receive(self.update(),SECRET)
        with store.connect() as db:db.execute('UPDATE telegram_inbox SET attempts=4')
        webhooks.drain(FakeTelegram(fail=True))
        with store.connect() as db:self.assertEqual(db.execute('SELECT status,payload FROM telegram_inbox').fetchone()[:],('failed','{}'))
        webhooks.receive(self.update(2),SECRET)
        self.assertEqual(webhooks.drain(FakeTelegram()),1)

    def test_linked_registered_owner_delivery_and_delete_pending_inbox(self):
        cookie=self.register()
        with scope('tg:101'):
            store.account({'chat_id':101})
            code=security.make_link()
        webhooks.receive(self.update(),SECRET)
        self.assertEqual(self.req('/api/link',{'code':code},cookie)[0],200)
        self.req('/api/events/save',{'title':'Будущее напоминание','kind':'reminder','date':'2099-01-01','time':'12:00'},cookie)
        result=self.req('/api/notifications',cookie=cookie)[1]
        self.assertEqual(result['notifications'][0]['status'],'scheduled')
        self.req('/api/auth/delete',{'password':'managed fixture phrase 123','confirmed':True},cookie)
        with store.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM telegram_inbox').fetchone()[0],0)
        self.assertEqual(webhooks.drain(FakeTelegram()),0)

    def test_unknown_private_user_is_not_persisted_but_id_is_available(self):
        self.assertFalse(webhooks.receive(self.update(uid=303),SECRET))
        self.assertTrue(webhooks.receive(self.update(2,303,'/id'),SECRET))
        client=FakeTelegram();webhooks.drain(client)
        self.assertEqual(client.sent[0]['chat_id'],303)

    def test_reused_id_different_payload_and_queue_pressure(self):
        webhooks.receive(self.update(),SECRET)
        self.assertEqual(self.req('/api/telegram/webhook',self.update(text='Изменённое сообщение'),secret=SECRET)[0],400)
        for i in range(2,21):webhooks.receive(self.update(i),SECRET)
        self.assertEqual(self.req('/api/telegram/webhook',self.update(21),secret=SECRET)[0],503)

    def test_scheduled_backup_is_verified_once_per_interval(self):
        from scheduled import backup_if_due
        self.assertTrue(backup_if_due())
        self.assertFalse(backup_if_due())
        self.assertEqual(len(list((Path(self.tmp.name)/'backups').glob('*.sqlite3'))),1)

    def test_proxy_ip_requires_explicit_network_and_ignores_vps_header(self):
        request=WSGIHandler({'PATH_INFO':'/', 'wsgi.input':BytesIO(), 'REMOTE_ADDR':'127.0.0.1',
                             'HTTP_X_REAL_IP':'198.51.100.9', 'HTTP_X_ORBITA_CLIENT_IP':'203.0.113.9'})
        with patch.dict(os.environ, HOSTING_PROFILE='alwaysdata', TRUSTED_PROXY_CIDRS=''):
            self.assertEqual(request.remote(),'127.0.0.1')
            with patch.dict(os.environ, TRUSTED_PROXY_CIDRS='127.0.0.1/32'):
                self.assertEqual(request.remote(),'198.51.100.9')
                request.headers.replace_header('X-REAL-IP','invalid')
                self.assertEqual(request.remote(),'127.0.0.1')

    def test_malformed_telegram_shapes_are_ignored_without_persistence(self):
        for update in ({'update_id':1,'callback_query':['bad']},
                       {'update_id':2,'message':{'from':{'id':303},'chat':{'id':303,'type':'private'},'text':12}},
                       {'update_id':3,'message':[]}):
            self.assertEqual(self.req('/api/telegram/webhook',update,secret=SECRET)[0],200)
        with store.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM telegram_inbox').fetchone()[0],0)


if __name__=='__main__':unittest.main()
