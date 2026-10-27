"""HTTP boundaries, durable abuse limits and bounded production transport."""
import io
import hashlib
import hmac
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from urllib.parse import urlencode
from http.server import ThreadingHTTPServer
from unittest.mock import patch

from app import auth, config, security, server, service, store
from app.context import scope
from app.wsgi import WSGIHandler, application


class SecurityHTTP(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = store.DB_PATH
        store.DB_PATH = str(Path(self.tmp.name)/'security.db')
        self.env = patch.dict(os.environ, ORBITA_SKIP_DOTENV='1', APP_ENV='test',
                              DATABASE_ENCRYPTION='0', AUTH_REQUIRED='0',
                              APP_SESSION_SECRET='security-http-fixture-'+'x'*48,
                              TELEGRAM_ALLOW_PUBLIC='1', TELEGRAM_ALLOWED_USER_IDS='',
                              HOSTING_PROFILE='', COOKIE_SECURE='0')
        self.env.start()
        store.init()
        self.http = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        self.base = 'http://127.0.0.1:'+str(self.http.server_port)

    def tearDown(self):
        self.http.shutdown();self.http.server_close();self.thread.join(5)
        self.env.stop();store.DB_PATH = self.old_db;self.tmp.cleanup()

    def req(self, path, data=None, cookie=None, raw=None, headers=None):
        h = {'Content-Type': 'application/json'}
        if cookie:h['Cookie'] = cookie
        h.update(headers or {})
        body = raw if raw is not None else json.dumps(data).encode() if data is not None else None
        request = urllib.request.Request(self.base+path, data=body, headers=h)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:response = opener.open(request, timeout=5)
        except urllib.error.HTTPError as exc:response = exc
        with response:return response.status, json.load(response), response.headers

    def register(self):
        status, _, headers = self.req('/api/auth/register',
            {'username': 'security_user', 'password': 'fixture password 12345'})
        self.assertEqual(status, 200)
        return headers['Set-Cookie'].split(';')[0]

    def test_json_shape_and_parser_failures_do_not_mutate(self):
        for raw in (b'null', b'[]', b'false', b'"text"', b'{', b'\xff', b'['*2000):
            with self.subTest(raw=raw[:20]):
                self.assertEqual(self.req('/api/events/save', raw=raw)[0], 400)
        self.assertEqual(self.req('/api/events')[1]['total'], 0)
        self.assertEqual(self.req('/api/import', {}, headers={'Content-Type':'text/application/json-fake'})[0], 415)

    def test_ids_and_revisions_are_exact_positive_sqlite_integers(self):
        event = self.req('/api/events/save', {'title':'Keep this'})[1]['event']
        for path in ('save', 'delete', 'restore', 'snooze'):
            for value in (True, False, '1', 1.0, [], {}, 0, -1, 2**63):
                with self.subTest(path=path, value=value):
                    data = {'id':value, 'title':'Changed', 'confirmed':True, 'minutes':15, 'revision':1}
                    self.assertEqual(self.req('/api/events/'+path, data)[0], 400)
        for value in (True, '1', 1.0, [], 0, -1, 2**63):
            self.assertEqual(self.req('/api/events/save', dict(event, revision=value))[0], 400)
        unchanged = self.req('/api/events')[1]['events'][0]
        self.assertEqual((unchanged['title'], unchanged['revision']), ('Keep this', 1))
        self.assertEqual(self.req('/api/events/delete', {'id':event['id'], 'confirmed':True})[0], 200)
        self.assertEqual(self.req('/api/events/restore', {'id':event['id']})[0], 200)

    def test_internal_type_and_key_errors_never_echo_details(self):
        for error in (KeyError('synthetic private credential'), TypeError('synthetic private credential'),
                      OverflowError('synthetic private credential')):
            with patch.object(server.Handler, 'get_api', side_effect=error):
                status, body, _ = self.req('/api/events')
                self.assertEqual(status, 400)
                self.assertNotIn('synthetic', json.dumps(body))

    def test_link_attempts_persist_after_failures_and_in_new_process(self):
        for _ in range(10):self.assertEqual(self.req('/api/link', {'code':'invalid'})[0], 400)
        server.AUTH_ATTEMPTS.clear()
        self.assertEqual(self.req('/api/link', {'code':'invalid'})[0], 429)
        env = dict(os.environ, DATABASE_PATH=store.DB_PATH)
        code = "from app import auth; auth.limit('link','127.0.0.1','web')"
        result = subprocess.run([sys.executable, '-c', code], cwd=config.ROOT, env=env,
                                capture_output=True, text=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('AuthError', result.stderr)
        with store.connect() as db:
            rows = db.execute('SELECT key,attempts FROM auth_limits').fetchall()
        self.assertTrue(rows)
        self.assertTrue(all('127.0.0.1' not in row['key'] for row in rows))

    def test_config_profile_and_password_confirmed_unlink(self):
        cookie = self.register()
        with scope('tg:101'):
            auth.record_telegram_identity({'id':101, 'username':'fixture_tg', 'first_name':'Fixture'})
            store.account({'chat_id':101})
            link = security.make_link()
        self.assertEqual(self.req('/api/link', {'code':link}, cookie)[0], 200)
        cfg = self.req('/api/config', cookie=cookie)[1]['account']['telegram']
        self.assertEqual(cfg['username'], 'fixture_tg');self.assertTrue(cfg['can_unlink'])
        self.assertEqual(self.req('/api/telegram/unlink', {'confirmed':True, 'password':'wrong'}, cookie)[0], 401)
        self.assertEqual(self.req('/api/telegram/unlink', {'confirmed':True, 'password':'fixture password 12345'}, cookie)[0], 200)
        self.assertFalse(self.req('/api/config', cookie=cookie)[1]['account']['telegram']['linked'])

    def test_unlink_throttled_before_password_work(self):
        cookie = self.register()
        with patch.object(auth, 'unlink_telegram', side_effect=auth.AuthError('wrong', 401)) as unlink:
            for _ in range(10):self.assertEqual(self.req('/api/telegram/unlink', {}, cookie)[0], 401)
            self.assertEqual(self.req('/api/telegram/unlink', {}, cookie)[0], 429)
            self.assertEqual(unlink.call_count, 10)

    def test_mini_exchange_records_identity_and_rejects_replayed_proofs(self):
        bot_token = 'synthetic:telegram-token'
        def proof(stamp):
            fields = {'auth_date':str(stamp), 'user':json.dumps({'id':101, 'username':'signed_fixture', 'first_name':'Signed'})}
            key = hmac.new(b'WebAppData', bot_token.encode(), hashlib.sha256).digest()
            fields['hash'] = hmac.new(key, '\n'.join(k+'='+v for k,v in sorted(fields.items())).encode(), hashlib.sha256).hexdigest()
            return urlencode(fields)
        server.AUTH_ATTEMPTS.clear()
        with patch.dict(os.environ, TELEGRAM_BOT_TOKEN=bot_token), patch('time.time', return_value=1700000000):
            cookie = self.register()
            initial = proof(1700000000)
            status, body, _ = self.req('/api/telegram/auth', {'initData':initial})
            self.assertEqual(status, 200, body)
            old_token = body['token']
            with scope('tg:101'):link = security.make_link()
            with patch('time.time', return_value=1700000010):
                self.assertEqual(self.req('/api/link', {'code':link}, cookie)[0], 200)
                self.assertEqual(self.req('/api/telegram/auth', {'initData':initial})[0], 400)
                self.assertEqual(self.req('/api/events', headers={'Authorization':'Bearer '+old_token})[0], 401)
                current = proof(1700000010+1)
                status, body, _ = self.req('/api/telegram/auth', {'initData':current})
                self.assertEqual(status, 200, body)
                token = body['token']
                self.assertEqual(self.req('/api/config', cookie=cookie)[1]['account']['telegram']['username'], 'signed_fixture')
            with patch('time.time', return_value=1700000030):
                self.assertEqual(self.req('/api/telegram/unlink', {'confirmed':True, 'password':'fixture password 12345'}, cookie)[0], 200)
                self.assertEqual(self.req('/api/telegram/auth', {'initData':current})[0], 400)
                self.assertEqual(self.req('/api/events', headers={'Authorization':'Bearer '+token})[0], 401)

    def test_proxy_identity_and_host_enforcement_survive_wsgi(self):
        req = WSGIHandler({'PATH_INFO':'/', 'wsgi.input':io.BytesIO(),
                          'REMOTE_ADDR':'127.0.0.1', 'HTTP_X_ORBITA_CLIENT_IP':'198.51.100.9'})
        with patch.object(server.database, 'production', return_value=True):
            self.assertEqual(req.remote(), '198.51.100.9')
            req.client_address = ('203.0.113.7', 0)
            self.assertEqual(req.remote(), '203.0.113.7')
            with patch.dict(os.environ, PUBLIC_BASE_URL='https://orbita.test'):
                self.assertEqual(self.req('/api/config', headers={'Host':'foreign.test'})[0], 400)
                with patch.object(auth, 'limit', side_effect=auth.AuthError('limited', 429)) as limit:
                    status, body, _ = self.req('/api/link', {}, headers={'Host':'orbita.test', 'X-Orbita-Client-IP':'198.51.100.9'})
                    self.assertEqual(status, 429, body)
                    limit.assert_called_once_with('link', '198.51.100.9', 'web')


class ProductionTransport(unittest.TestCase):
    def test_bounded_runtime_preserves_socket_peer(self):
        from app.http_runtime import settings, serve
        options = settings()
        self.assertEqual(options['threads'], 4);self.assertEqual(options['connection_limit'], 16)
        self.assertEqual(options['max_request_body_size'], config.request_body_limit())
        self.assertGreater(options['inbuf_overflow'], options['max_request_body_size'])
        self.assertNotIn('trusted_proxy', options)
        with patch('waitress.serve') as adapter:
            serve('127.0.0.1', 8001)
        self.assertIs(adapter.call_args.args[0], application)

    def test_main_selects_waitress_only_for_production_and_refuses_public_bind(self):
        with patch.object(server.store, 'init'), patch.object(server.security, 'secret'), \
             patch.object(server.database, 'production', return_value=True), \
             patch('app.http_runtime.serve') as serve, patch.dict(os.environ, HOST='127.0.0.1', PORT='8001'):
            server.main();serve.assert_called_once_with('127.0.0.1', 8001)
            with patch.dict(os.environ, HOST='0.0.0.0'):
                with self.assertRaises(ValueError):server.main()

    def test_real_waitress_rejects_oversized_body_before_upload(self):
        import http.client
        from waitress import create_server
        from app.http_runtime import settings
        from waitress import wasyncore
        transport = create_server(lambda env, start: [], host='127.0.0.1', port=0, map={}, **settings())
        stop = threading.Event()
        def run():
            while not stop.is_set():wasyncore.loop(timeout=.05, count=1, map=transport._map)
        worker = threading.Thread(target=run, daemon=True);worker.start()
        connection = http.client.HTTPConnection('127.0.0.1', transport.effective_port, timeout=5)
        try:
            connection.putrequest('POST', '/api/events/save')
            connection.putheader('Content-Length', str(config.request_body_limit()+1))
            connection.endheaders()
            response = connection.getresponse()
            self.assertEqual(response.status, 413);response.read()
        finally:
            connection.close();stop.set();worker.join(5)
            transport.task_dispatcher.shutdown();wasyncore.close_all(map=transport._map)


if __name__ == '__main__':unittest.main()
