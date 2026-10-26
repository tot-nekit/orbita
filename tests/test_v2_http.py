import unittest,threading,tempfile,json,urllib.request,urllib.error,os,time,uuid
from pathlib import Path
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from app import store,security,service
from app.context import scope
from app.server import Handler
from test_v2 import init_data
class HTTPV2(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.old=store.DB_PATH;store.DB_PATH=str(Path(self.tmp.name)/'http.db');store.init();self.env=patch.dict(os.environ,{'APP_PASSWORD':'test-password','APP_SESSION_SECRET':'s'*48,'TELEGRAM_BOT_TOKEN':'test:token','TELEGRAM_ALLOW_PUBLIC':'1','TELEGRAM_ALLOWED_USER_IDS':''});self.env.start();self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=self.server.serve_forever,daemon=True).start();self.base='http://127.0.0.1:'+str(self.server.server_port)
    def tearDown(self):self.server.shutdown();self.server.server_close();self.env.stop();store.DB_PATH=self.old;self.tmp.cleanup()
    def req(self,path,data=None,token=None,extra=None):
        headers={'Content-Type':'application/json'} if data is not None else {}
        if token:headers['Authorization']='Bearer '+token
        headers.update(extra or {});req=urllib.request.Request(self.base+path,data=json.dumps(data).encode() if data is not None else None,headers=headers)
        try:
            with urllib.request.urlopen(req) as r:return r.status,r.read(),r.headers
        except urllib.error.HTTPError as r:return r.code,r.read(),r.headers
    def test_mini_shell_public(self):
        code,body,h=self.req('/mini');self.assertEqual(code,200);self.assertIn(b'telegram-web-app.js',body);self.assertNotIn('X-Frame-Options',h);self.assertIn('https://web.telegram.org',h['Content-Security-Policy'])
    def test_anon_can_api(self):self.assertEqual(self.req('/api/events')[0],200)
    def test_login_sets_httponly(self):
        code,body,h=self.req('/api/login',{});self.assertEqual(code,200);self.assertIn('HttpOnly',h['Set-Cookie'])
    def test_mini_exchange(self):
        code,body,h=self.req('/api/telegram/auth',{'initData':init_data()});self.assertEqual(code,200);token=json.loads(body)['token'];self.assertEqual(self.req('/api/config',token=token)[0],200)
    def test_bad_mini_signature(self):self.assertEqual(self.req('/api/telegram/auth',{'initData':init_data()+'x'})[0],400)
    def test_mini_never_inherits_web_cookie(self):
        code,body,h=self.req('/api/login',{'password':'test-password'});cookie=h['Set-Cookie'].split(';')[0]
        self.assertEqual(self.req('/api/events',extra={'Cookie':cookie,'X-Orbita-Client':'mini'})[0],401)
    def test_owner_in_body_ignored(self):
        a=security.issue('tg:101');b=security.issue('tg:202');code,body,_=self.req('/api/events/save',{'owner':'tg:202','title':'Secret','kind':'task'},a);self.assertEqual(code,200);i=json.loads(body)['event']['id']
        self.assertEqual(json.loads(self.req('/api/events',token=b)[1])['total'],0);self.assertEqual(self.req('/api/events/delete',{'id':i,'confirmed':True},b)[0],404)
    def test_export_isolated(self):
        with scope('tg:101'):store.save(service.validate({'title':'Private'}))
        self.assertNotIn(b'Private',self.req('/api/export',token=security.issue('tg:202'))[1])
    def test_idempotency_http(self):
        token=security.issue('tg:101');headers={'Idempotency-Key':str(uuid.uuid4())};a=self.req('/api/events/save',{'title':'One'},token,headers);b=self.req('/api/events/save',{'title':'One'},token,headers);self.assertEqual(a[1],b[1]);self.assertEqual(json.loads(self.req('/api/events',token=token)[1])['total'],1)
    def test_revision_stale(self):
        token=security.issue('tg:101');e=json.loads(self.req('/api/events/save',{'title':'One'},token)[1])['event'];self.req('/api/events/save',dict(e,title='Two'),token);self.assertEqual(self.req('/api/events/save',dict(e,title='Three'),token)[0],409)
    def test_link_cookie(self):
        with scope('tg:101'):code=security.make_link()
        status,body,h=self.req('/api/link',{'code':code},security.issue('web'));self.assertEqual(status,200);cookie=h['Set-Cookie'].split(';')[0];config=json.loads(self.req('/api/config',extra={'Cookie':cookie})[1]);self.assertTrue(config['account']['linked'])
    def test_restore(self):
        t=security.issue('tg:101');e=json.loads(self.req('/api/events/save',{'title':'One'},t)[1])['event'];self.req('/api/events/delete',{'id':e['id'],'confirmed':True},t);self.assertEqual(len(json.loads(self.req('/api/trash',token=t)[1])['events']),1);self.assertEqual(self.req('/api/events/restore',{'id':e['id']},t)[0],200)
    def test_timezone_persisted(self):
        token=security.issue('tg:101');self.req('/api/account',{'timezone':'UTC'},token);self.assertEqual(json.loads(self.req('/api/config',token=token)[1])['account']['timezone'],'UTC')
    def test_secure_cookie(self):
        with patch.dict(os.environ,{'COOKIE_SECURE':'1'}):self.assertIn('Secure',self.req('/api/login',{})[2]['Set-Cookie'])
