"""Real production launcher/edge/encryption/restart; HTTPS ingress is separate."""
import base64
import importlib.util
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent


@unittest.skipUnless(shutil.which('caddy') and importlib.util.find_spec('sqlcipher3'), 'Requires Caddy and SQLCipher (also runs in the production container)')
class Production(unittest.TestCase):
    def test_real_edge_encrypted_backup_restart_and_cookie_revocation(self):
        with tempfile.TemporaryDirectory(prefix='orbita-production-') as tmp:
            with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
            with socket.socket() as sock:sock.bind(('127.0.0.1',0));inner=sock.getsockname()[1]
            env=dict(os.environ,ORBITA_SKIP_DOTENV='1',APP_ENV='production',PUBLIC_BASE_URL='https://orbita.test',AUTH_REQUIRED='1',DATABASE_ENCRYPTION='1',DATABASE_KEY=base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),APP_SESSION_SECRET=secrets.token_urlsafe(48),COOKIE_SECURE='1',DATABASE_PATH=str(Path(tmp)/'private.sqlite3'),HOST='127.0.0.1',PORT=str(port),APP_PORT=str(inner),CADDY_ENABLED='1',TELEGRAM_BOT_TOKEN='',OPENAI_API_KEY='',STT_API_KEY='',BACKUP_ENABLED='1')
            opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
            def req(path,data=None,cookie=None):
                headers={'Host':'orbita.test','Content-Type':'application/json','Origin':'https://orbita.test'}
                if cookie:headers['Cookie']=cookie
                request=urllib.request.Request('http://127.0.0.1:'+str(port)+path,headers=headers,data=json.dumps(data).encode() if data is not None else None)
                try:response=opener.open(request,timeout=5)
                except urllib.error.HTTPError as response:
                    body=response.read();code=response.code;response.close();return code,json.loads(body),{}
                with response:return response.status,json.load(response),response.headers
            def start():
                child=subprocess.Popen([sys.executable,'run.py','all'],cwd=ROOT,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                deadline=time.monotonic()+20
                while time.monotonic()<deadline and child.poll() is None:
                    try:
                        if req('/api/health')[0]==200:return child
                    except (OSError,ValueError):pass
                    time.sleep(.1)
                child.terminate();child.wait(timeout=15)
                self.fail('Production service did not become healthy')
            def stop(child):
                child.terminate();child.wait(timeout=15)
                # Supervisor must close both listeners, not leave an orphaned web/edge.
                for candidate in (port,inner):
                    with socket.socket() as sock:
                        sock.settimeout(1);self.assertNotEqual(sock.connect_ex(('127.0.0.1',candidate)),0)
            child=start()
            try:
                self.assertEqual(req('/api/events')[0],401)
                status,account,headers=req('/api/auth/register',{'username':'prod_fixture','password':'fixture password 12345'})
                self.assertEqual(status,200)
                self.assertIn('Secure',headers['Set-Cookie']);self.assertIn('HttpOnly',headers['Set-Cookie'])
                cookie=headers['Set-Cookie'].split(';')[0]
                self.assertTrue(req('/api/config',cookie=cookie)[1]['privacy']['encrypted'])
                self.assertEqual(req('/api/events/save',{'title':'Encrypted restart canary'},cookie)[0],200)
                self.assertNotIn(b'Encrypted restart canary',Path(env['DATABASE_PATH']).read_bytes())
                backup=subprocess.run([sys.executable,'-m','scripts.backup'],cwd=ROOT,env=env,capture_output=True,timeout=20)
                self.assertEqual(backup.returncode,0)
                copies=list((Path(tmp)/'backups').glob('*.sqlite3'))
                self.assertTrue(copies)
                for path in copies:self.assertNotEqual(path.read_bytes()[:16],b'SQLite format 3\0')
            finally:stop(child)
            child=start()
            try:
                self.assertEqual(req('/api/events',cookie=cookie)[1]['events'][0]['title'],'Encrypted restart canary')
                self.assertEqual(req('/api/logout',{},cookie)[0],200)
                self.assertEqual(req('/api/events',cookie=cookie)[0],401)
            finally:stop(child)


if __name__=='__main__':unittest.main()
