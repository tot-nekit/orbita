import unittest,threading,tempfile,json,urllib.request,urllib.error,os
from pathlib import Path
from http.server import ThreadingHTTPServer
from app import store
from app.server import Handler
class HTTP(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory();cls.old=store.DB_PATH;store.DB_PATH=str(Path(cls.tmp.name)/'http.db');store.init();cls.server=ThreadingHTTPServer(('127.0.0.1',0),Handler);cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start();cls.base='http://127.0.0.1:'+str(cls.server.server_port)
    @classmethod
    def tearDownClass(cls):cls.server.shutdown();cls.server.server_close();store.DB_PATH=cls.old;cls.tmp.cleanup()
    def request(self,path,data=None,headers=None):
        req=urllib.request.Request(self.base+path,data=json.dumps(data).encode() if data is not None else None,headers=headers or ({'Content-Type':'application/json'} if data is not None else {}))
        try:
            with urllib.request.urlopen(req) as r:return r.status,r.read(),r.headers
        except urllib.error.HTTPError as e:return e.code,e.read(),e.headers
    def test_health(self):self.assertEqual(self.request('/api/health')[0],200)
    def test_html(self):
        code,body,headers=self.request('/');self.assertEqual(code,200);self.assertIn('Орбита'.encode(),body);self.assertIn('Content-Security-Policy',headers)
    def test_bad_session(self):self.assertEqual(self.request('/api/chat',{'text':'привет','session':'x'})[0],400)
    def test_bad_content_type(self):self.assertEqual(self.request('/api/import',{}, {'Content-Type':'text/plain'})[0],415)
    def test_cross_origin(self):self.assertEqual(self.request('/api/import',{}, {'Content-Type':'application/json','Origin':'https://evil.example'})[0],403)
    def test_auth(self):self.assertEqual(self.request('/api/health')[0],200)
    def test_not_found(self):self.assertEqual(self.request('/../../etc/passwd')[0],404)
    def test_delete_confirmation(self):self.assertEqual(self.request('/api/events/delete',{'id':1})[0],400)
    def test_crud(self):
        code,body,_=self.request('/api/events/save',{'title':'HTTP test','kind':'task'});self.assertEqual(code,200);e=json.loads(body)['event'];self.assertEqual(self.request('/api/events/delete',{'id':e['id'],'confirmed':True})[0],200)
    def test_export(self):
        code,body,h=self.request('/api/export?format=csv');self.assertEqual(code,200);self.assertIn('attachment',h['Content-Disposition']);self.assertTrue(body.startswith(b'\xef\xbb\xbf'))
