import json, os, tempfile, threading, unittest, urllib.request, urllib.error
from pathlib import Path
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from app import store, service, ai, config
from app.server import Handler

class DailyWorkflow(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.old=store.DB_PATH
        store.DB_PATH=str(Path(self.tmp.name)/'test.db');store.init()
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        threading.Thread(target=self.server.serve_forever,daemon=True).start()
        self.base='http://127.0.0.1:'+str(self.server.server_port)
    def tearDown(self):
        self.server.shutdown();self.server.server_close();store.DB_PATH=self.old;self.tmp.cleanup()
    def get(self,path):
        with urllib.request.urlopen(self.base+path) as r:return r.status,json.load(r)
    def test_open_access_ignores_old_password_setting(self):
        with patch.dict(os.environ,{'APP_PASSWORD':'legacy-setting'}):
            self.assertEqual(self.get('/api/events')[0],200)
    def test_inbox_overdue_and_active_counts(self):
        store.save(service.validate({'title':'Без даты'}))
        store.save(service.validate({'title':'Просрочено','date':'2000-01-01'}))
        store.save(service.validate({'title':'Выполнено','date':'2000-01-01','status':'done'}))
        inbox=self.get('/api/events?bucket=inbox&status=active')[1]
        overdue=self.get('/api/events?bucket=overdue&status=active')[1]
        self.assertEqual([e['title'] for e in inbox['events']],['Без даты'])
        self.assertEqual([e['title'] for e in overdue['events']],['Просрочено'])
        stats=self.get('/api/stats')[1];self.assertEqual(stats['inbox'],1);self.assertEqual(stats['overdue'],1)
    def test_config_never_contains_provider_credentials(self):
        _,data=self.get('/api/config')
        self.assertNotIn('api_key',json.dumps(data).lower())
        self.assertIn('fallback_model',data)
    def test_login_is_only_a_redirect(self):
        with urllib.request.urlopen(self.base+'/login') as response:
            self.assertEqual(response.geturl(),self.base+'/')
            self.assertNotIn(b'login-form',response.read())
    def test_expired_link_never_silently_switches_space_and_can_be_reset(self):
        store.save(service.validate({'title':'Web record'}))
        req=urllib.request.Request(self.base+'/api/events',headers={'Cookie':'orbita_session=expired.invalid'})
        try:
            urllib.request.urlopen(req)
            self.fail('Expired link silently inherited the web account')
        except urllib.error.HTTPError as error:
            self.assertEqual(error.code,401);self.assertEqual(json.load(error)['code'],'session_expired');error.close()
        req=urllib.request.Request(self.base+'/api/logout',data=b'{}',headers={'Cookie':'orbita_session=expired.invalid','Content-Type':'application/json'})
        with urllib.request.urlopen(req) as response:
            self.assertEqual(response.status,200);self.assertIn('Max-Age=0',response.headers['Set-Cookie'])
    def test_old_confirmation_button_cannot_confirm_new_action(self):
        sid='confirmation-session-12345'
        with patch.object(config,'API_KEY',''):
            service.chat('Добавь задачу купить хлеб',sid)
            first=service.chat('Удали ее',sid)
            revision=store.state_snapshot(sid)[1]
            self.assertTrue(first['actions'])
            service.chat('отмена',sid)
            service.chat('Добавь задачу купить молоко',sid)
            service.chat('Удали ее',sid)
        before=store.list_events()[1]
        req=urllib.request.Request(self.base+'/api/chat',data=json.dumps({'text':'да','session':sid,'expected_revision':revision}).encode(),headers={'Content-Type':'application/json'})
        with urllib.request.urlopen(req) as response:
            result=json.load(response);self.assertEqual(result['code'],'stale_dialog')
        self.assertEqual(store.list_events()[1],before)
    def test_chat_planning_does_not_hold_write_lock_and_replay_skips_model(self):
        def planner(*args):
            self.assertIsNone(store.TX.get())
            done=threading.Event()
            def write():
                store.save(service.validate({'title':'Concurrent form save'}));done.set()
            thread=threading.Thread(target=write);thread.start()
            self.assertTrue(done.wait(2),'LLM held the global SQLite write lock');thread.join()
            return {'action':'create','title':'Planned task','kind':'task'}
        data=json.dumps({'text':'Добавь задачу','session':'isolated-http-session'}).encode()
        def send():
            req=urllib.request.Request(self.base+'/api/chat',data=data,headers={'Content-Type':'application/json','Idempotency-Key':'test-http-request-123456'})
            with urllib.request.urlopen(req) as response:return json.load(response)
        with patch.object(config,'API_KEY','offline-test'),patch.object(ai,'plan',side_effect=planner) as model:
            first=send();second=send()
        self.assertEqual(first,second);self.assertEqual(model.call_count,1)
        self.assertEqual(store.list_events()[1],2)

if __name__=='__main__':unittest.main()
