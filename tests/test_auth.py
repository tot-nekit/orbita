"""Account isolation, recovery races, revocation and no-email registration."""
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from app import auth, security, service, store
from app.context import scope
from app.server import Handler


class Accounts(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = store.DB_PATH
        store.DB_PATH = str(Path(self.tmp.name) / 'accounts.db')
        self.env = patch.dict(os.environ, {'AUTH_REQUIRED':'1', 'APP_ENV':'local', 'APP_SESSION_SECRET':'unit-test-only-'+'x'*48, 'COOKIE_SECURE':'0', 'TELEGRAM_ALLOW_PUBLIC':'1', 'TELEGRAM_ALLOWED_USER_IDS':''})
        self.env.start()
        store.init()
        self.server = ThreadingHTTPServer(('127.0.0.1',0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = 'http://127.0.0.1:' + str(self.server.server_port)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.env.stop()
        store.DB_PATH = self.old
        self.tmp.cleanup()

    def req(self, path, data=None, cookie=None, headers=None):
        h = {'Content-Type':'application/json'}
        if cookie:h['Cookie']=cookie
        h.update(headers or {})
        req = urllib.request.Request(self.base+path, headers=h, data=json.dumps(data).encode() if data is not None else None)
        try:r = urllib.request.urlopen(req)
        except urllib.error.HTTPError as e:r=e
        with r:
            body=r.read()
            return r.status, json.loads(body), r.headers.get('Set-Cookie','').split(';')[0]

    def register(self, name='alice'):
        code, body, cookie = self.req('/api/auth/register', {'username':name, 'password':'test password 12345'})
        self.assertEqual(code,200,body)
        return body,cookie

    def test_anonymous_denied_and_legacy_token_denied(self):
        for path in ('/api/events','/api/export','/api/history?session=abcdefghijklmnop','/api/config'):
            self.assertEqual(self.req(path)[0],401)
        self.assertEqual(self.req('/api/events', headers={'Authorization':'Bearer '+security.issue('web')})[0],401)
        self.assertEqual(self.req('/api/login',{})[0],400)
        self.assertEqual(self.req('/api/health')[0],200)
        self.assertFalse(self.req('/api/auth/status')[1]['authenticated'])

    def test_no_email_private_defaults_and_hashed_credentials(self):
        result,cookie=self.register()
        self.assertEqual(len(result['recovery_code']),43)
        self.assertTrue(cookie.startswith('orbita_session=s2_'))
        cfg=self.req('/api/config',cookie=cookie)[1]
        self.assertTrue(cfg['account']['private'])
        self.assertFalse(cfg['account']['use_ai'])
        self.assertEqual(cfg['account']['username'],'alice')
        with store.connect() as db:
            row=db.execute('SELECT * FROM users').fetchone()
            self.assertNotIn('test password',row['password_hash'])
            self.assertNotEqual(row['recovery_hash'],result['recovery_code'])
            session=db.execute('SELECT hash FROM auth_sessions').fetchone()[0]
            self.assertNotIn(session,cookie)

    def test_owner_isolation_all_surfaces(self):
        _,a=self.register('alice');_,b=self.register('bob')
        event=self.req('/api/events/save',{'title':'Alice private event','owner':'web'},a)[1]['event']
        self.assertEqual(self.req('/api/events',cookie=b)[1]['total'],0)
        self.assertEqual(self.req('/api/events?q=private',cookie=b)[1]['total'],0)
        self.assertEqual(self.req('/api/events/delete',{'id':event['id'],'confirmed':True},b)[0],404)
        self.assertEqual(self.req('/api/events/save',dict(event,title='Stolen'),b)[0],404)
        self.assertEqual(self.req('/api/events/restore',{'id':event['id']},b)[0],404)
        self.assertEqual(self.req('/api/export',cookie=b)[1],[])
        self.req('/api/chat',{'text':'создай задачу секретный отчёт','session':'same-dialog-123456'},a)
        self.assertEqual(self.req('/api/history?session=same-dialog-123456',cookie=b)[1]['messages'],[])

    def test_legacy_not_adopted_on_registration(self):
        with scope('web'):store.save(service.validate({'title':'Old private records'}))
        _,cookie=self.register()
        self.assertEqual(self.req('/api/events',cookie=cookie)[1]['total'],0)
        with scope('web'):self.assertEqual(store.list_events()[1],1)

    def test_logout_revokes_stolen_cookie(self):
        _,cookie=self.register()
        self.assertEqual(self.req('/api/logout',{},cookie)[0],200)
        self.assertEqual(self.req('/api/events',cookie=cookie)[0],401)

    def test_recovery_once_revokes_all_sessions(self):
        result,cookie=self.register()
        code,_,other=self.req('/api/auth/login',{'username':'ALICE','password':'test password 12345'})
        self.assertEqual(code,200)
        payload={'username':'alice','recovery_code':result['recovery_code'],'password':'new password 987654'}
        code,reset,new_cookie=self.req('/api/auth/recover',payload)
        self.assertEqual(code,200)
        self.assertNotEqual(reset['recovery_code'],result['recovery_code'])
        for old in (cookie,other):self.assertEqual(self.req('/api/events',cookie=old)[0],401)
        self.assertEqual(self.req('/api/events',cookie=new_cookie)[0],200)
        self.assertEqual(self.req('/api/auth/recover',payload)[0],401)
        self.assertEqual(self.req('/api/auth/login',{'username':'alice','password':'test password 12345'})[0],401)

    def test_password_change_rotates_session_and_recovery(self):
        result,cookie=self.register()
        code,updated,new_cookie=self.req('/api/auth/password',{'current_password':'test password 12345','password':'changed password 1234'},cookie)
        self.assertEqual(code,200)
        self.assertNotEqual(result['recovery_code'],updated['recovery_code'])
        self.assertEqual(self.req('/api/config',cookie=cookie)[0],401)
        self.assertEqual(self.req('/api/config',cookie=new_cookie)[0],200)

    def test_persistent_rate_limit_and_cross_site(self):
        for _ in range(10):auth.limit('test','127.0.0.1','alice')
        with self.assertRaises(auth.AuthError):auth.limit('test','127.0.0.1','alice')
        self.assertEqual(self.req('/api/auth/register',{'username':'alice','password':'test password 12345'},headers={'Origin':'https://evil.test'})[0],403)
        self.assertEqual(self.req('/api/auth/login',{},headers={'Sec-Fetch-Site':'cross-site'})[0],403)

    def test_account_delete_removes_private_data_and_tokens(self):
        _,a=self.register('alice');_,b=self.register('bob')
        self.req('/api/events/save',{'title':'Alice event'},a)
        self.req('/api/events/save',{'title':'Bob event'},b)
        self.req('/api/chat',{'text':'привет','session':'same-dialog-123456'},a)
        code,_,_=self.req('/api/auth/delete',{'password':'test password 12345','confirmed':True},a)
        self.assertEqual(code,200)
        self.assertEqual(self.req('/api/events',cookie=a)[0],401)
        self.assertEqual(self.req('/api/events',cookie=b)[1]['total'],1)
        with store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM messages').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT count(*) FROM users').fetchone()[0],1)

    def test_telegram_link_merges_without_switching_cookie(self):
        _,cookie=self.register()
        with scope('tg:101'):
            store.account({'chat_id':101})
            store.save(service.validate({'title':'Telegram event'}))
            code=security.make_link()
        self.assertEqual(self.req('/api/link',{'code':code},cookie)[0],200)
        self.assertEqual(self.req('/api/events',cookie=cookie)[1]['total'],1)
        self.assertEqual(self.req('/api/config',cookie=cookie)[1]['account']['username'],'alice')
        self.assertEqual(auth.resolve_telegram('tg:101'),auth.session_owner(cookie.split('=',1)[1]))
        self.assertEqual(security.verify(security.issue('tg:101')),auth.resolve_telegram('tg:101'))
        _,other=self.register('bob')
        self.assertEqual(self.req('/api/link',{'code':code},other)[0],400)

    def test_simultaneous_recovery_has_one_winner(self):
        result,_=self.register()
        payload={'username':'alice','recovery_code':result['recovery_code'],'password':'new password 987654'}
        with ThreadPoolExecutor(2) as pool:results=list(pool.map(lambda _:self.req('/api/auth/recover',payload)[0],range(2)))
        self.assertEqual(sorted(results),[200,401])

    def test_ai_off_prevents_provider_requests(self):
        _,cookie=self.register()
        with patch('app.ai.plan',side_effect=AssertionError('Provider must not be called')),patch('app.config.API_KEY','fake-test-only'):
            result=self.req('/api/chat',{'text':'создай задачу отчёт завтра','session':'test-private-dialog'},cookie)
            self.assertEqual(result[0],200)
            self.assertEqual(result[1]['mode'],'local')

    def test_ai_quota_is_global_and_owner_scoped(self):
        with patch.dict(os.environ,AI_DAILY_USER_LIMIT='2',AI_DAILY_LIMIT='3'):
            with scope('tg:101'):
                store.reserve_ai_request();store.reserve_ai_request()
                with self.assertRaises(ValueError):store.reserve_ai_request()
            with scope('tg:202'):
                store.reserve_ai_request()
                with self.assertRaises(ValueError):store.reserve_ai_request()
        with store.connect() as db:self.assertEqual(db.execute('SELECT sum(requests) FROM ai_usage').fetchone()[0],3)

    def test_inflight_deleted_owner_cannot_create_records(self):
        _,cookie=self.register()
        ident=auth.session_owner(cookie.split('=',1)[1])
        self.req('/api/auth/delete',{'password':'test password 12345','confirmed':True},cookie)
        with scope(ident),self.assertRaises(PermissionError):
            store.run_once('inflight-test',{},lambda:store.save(service.validate({'title':'Should never be written'})))
        with store.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM events').fetchone()[0],0)


if __name__=='__main__':unittest.main()
