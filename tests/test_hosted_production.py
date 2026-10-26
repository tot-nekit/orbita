"""Real managed WSGI server: encrypted persistence without Caddy or polling."""
import base64
import importlib.util
import http.client
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parent.parent


@unittest.skipUnless(importlib.util.find_spec('waitress') and importlib.util.find_spec('sqlcipher3'), 'Requires production dependencies')
class HostedProduction(unittest.TestCase):
    def test_encrypted_restart_origin_cookie_limits_and_scheduled_backup(self):
        with tempfile.TemporaryDirectory(prefix='orbita-hosted-') as tmp:
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0));port = sock.getsockname()[1]
            env = dict(os.environ, ORBITA_SKIP_DOTENV='1', APP_ENV='production',
                       HOSTING_PROFILE='alwaysdata', PUBLIC_BASE_URL='https://orbita.test',
                       AUTH_REQUIRED='1', COOKIE_SECURE='1', DATABASE_ENCRYPTION='1',
                       DATABASE_KEY=base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
                       DATABASE_KEY_FILE='', APP_SESSION_SECRET=secrets.token_urlsafe(48),
                       DATABASE_PATH=str(Path(tmp)/'private.sqlite3'), IP='127.0.0.1',
                       PORT=str(port), TELEGRAM_BOT_TOKEN='', TELEGRAM_MODE='webhook',
                       TRUSTED_PROXY_CIDRS='', OPENAI_API_KEY='', STT_API_KEY='', BACKUP_ENABLED='1')
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

            def req(path, data=None, cookie=None, **extra):
                headers = {'Host':'orbita.test', 'Content-Type':'application/json', 'Origin':'https://orbita.test'}
                headers.update(extra)
                if cookie:headers['Cookie'] = cookie
                body = data if isinstance(data, bytes) else json.dumps(data).encode() if data is not None else None
                request = urllib.request.Request('http://127.0.0.1:'+str(port)+path, headers=headers, data=body)
                try:response = opener.open(request, timeout=5)
                except urllib.error.HTTPError as exc:response = exc
                with response:
                    raw = response.read()
                    result = json.loads(raw) if 'application/json' in response.headers.get('Content-Type', '') else raw
                    return response.status, result, response.headers

            def start():
                child = subprocess.Popen([sys.executable, 'hosted.py'], cwd=ROOT, env=env,
                                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                deadline = time.monotonic()+15
                while time.monotonic() < deadline and child.poll() is None:
                    try:
                        if req('/api/health')[0] == 200:return child
                    except OSError:pass
                    time.sleep(.05)
                child.terminate();child.wait(timeout=5)
                self.fail('Managed production website failed to start')

            def stop(child):
                child.terminate();child.wait(timeout=5)
                with socket.socket() as sock:
                    sock.settimeout(1);self.assertNotEqual(sock.connect_ex(('127.0.0.1', port)), 0)

            child = start()
            try:
                self.assertEqual(req('/api/events')[0], 401)
                self.assertEqual(req('/api/auth/register', {}, Origin='https://foreign.test')[0], 403)
                self.assertEqual(req('/', Host='foreign.test')[0], 400)
                # The transport rejects an oversized Content-Length before it
                # accepts the body; a sender that keeps uploading gets EPIPE.
                connection = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
                try:
                    connection.putrequest('POST', '/api/auth/register', skip_host=True)
                    connection.putheader('Host', 'orbita.test')
                    connection.putheader('Content-Length', str(2*1024*1024+1))
                    connection.endheaders()
                    response = connection.getresponse()
                    self.assertEqual(response.status, 413);response.read()
                finally:connection.close()
                status, _, headers = req('/api/auth/register', {'username':'hosted_user', 'password':'hosted fixture phrase 123'})
                self.assertEqual(status, 200)
                self.assertIn('Secure', headers['Set-Cookie']);self.assertIn('HttpOnly', headers['Set-Cookie'])
                cookie = headers['Set-Cookie'].split(';')[0]
                settings = req('/api/config', cookie=cookie)[1]
                self.assertTrue(settings['privacy']['encrypted'])
                self.assertEqual(settings['limits']['upload_bytes'], 2*1024*1024)
                self.assertEqual(req('/api/events/save', {'title':'Managed encrypted restart canary'}, cookie)[0], 200)
                for path in Path(tmp).glob('private.sqlite3*'):
                    self.assertNotIn(b'Managed encrypted restart canary', path.read_bytes())
                result = subprocess.run([sys.executable, 'scheduled.py'], cwd=ROOT, env=env, capture_output=True, timeout=25)
                self.assertEqual(result.returncode, 0)
                copies = list((Path(tmp)/'backups').glob('*.sqlite3'))
                self.assertEqual(len(copies), 1)
                self.assertNotEqual(copies[0].read_bytes()[:16], b'SQLite format 3\0')
            finally:stop(child)
            child = start()
            try:
                self.assertEqual(req('/api/events', cookie=cookie)[1]['events'][0]['title'], 'Managed encrypted restart canary')
                self.assertEqual(req('/api/logout', {}, cookie)[0], 200)
                self.assertEqual(req('/api/events', cookie=cookie)[0], 401)
            finally:stop(child)


if __name__ == '__main__':unittest.main()
