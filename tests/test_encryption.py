"""Real on-disk SQLCipher checks in isolated processes (not a mocked PRAGMA)."""
import base64
import importlib.util
import os
import secrets
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent


@unittest.skipUnless(importlib.util.find_spec('sqlcipher3'), 'Install SQLCipher dependency for encrypted storage tests')
class Encryption(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=Path(self.tmp.name)/'private.db'
        self.env=dict(os.environ,DATABASE_PATH=str(self.path),DATABASE_ENCRYPTION='1',DATABASE_KEY=base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),APP_ENV='local',AUTH_REQUIRED='1',OPENAI_API_KEY='')

    def tearDown(self):self.tmp.cleanup()

    def run_code(self,code,env=None,ok=True):
        result=subprocess.run([sys.executable,'-B','-c',code],cwd=ROOT,env=env or self.env,capture_output=True,text=True,timeout=90)
        if ok:self.assertEqual(result.returncode,0,result.stderr)
        else:self.assertNotEqual(result.returncode,0)
        return result

    def seed(self):
        self.run_code("from app import store; store.init(); store.save({'title':'Private canary abc-998177','kind':'task'}); assert store.list_events()[1]==1")

    def test_database_is_encrypted_and_reopens(self):
        self.seed()
        self.assertNotEqual(self.path.read_bytes()[:16],b'SQLite format 3\0')
        self.assertNotIn(b'Private canary',self.path.read_bytes())
        self.run_code("from app import store; store.init(); assert store.list_events()[0][0]['title']=='Private canary abc-998177'")

    def test_wrong_key_and_missing_key_fail_closed(self):
        self.seed()
        bad=dict(self.env,DATABASE_KEY=base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())
        self.run_code('from app import store; store.init()',bad,ok=False)
        self.run_code('from app import store; store.init()',dict(self.env,DATABASE_KEY='',DATABASE_KEY_FILE=''),ok=False)

    def test_plain_sqlite_cannot_open_encrypted_database(self):
        self.seed()
        self.run_code('import sqlite3,os; sqlite3.connect(os.environ["DATABASE_PATH"]).execute("SELECT * FROM events").fetchall()',ok=False)

    def test_plaintext_source_is_never_silently_accepted(self):
        import sqlite3
        with sqlite3.connect(self.path) as db:db.execute('CREATE TABLE old(value TEXT)')
        self.run_code('from app import store; store.init()',ok=False)

    def test_migration_preserves_all_rows_and_original(self):
        source=Path(self.tmp.name)/'legacy.db'
        import sqlite3
        with sqlite3.connect(source) as db:
            db.execute('CREATE TABLE sample(id INTEGER PRIMARY KEY,value TEXT)')
            db.execute('INSERT INTO sample VALUES(7,?)',('Legacy private canary',))
        code="""
import os
from pathlib import Path
from scripts.manage import encrypt_copy
from app import database
target=Path(os.environ['DATABASE_PATH'])
source=target.with_name('legacy.db')
encrypt_copy(source,target)
db=database.open_connection(target)
assert db.execute('SELECT id,value FROM sample').fetchone()[:]==(7,'Legacy private canary')
db.close()
try:encrypt_copy(source,target)
except ValueError:pass
else:raise AssertionError('Existing output must not be overwritten')
"""
        self.run_code(code)
        self.assertTrue(source.is_file())
        self.assertNotIn(b'Legacy private canary',self.path.read_bytes())

    def test_wal_contains_no_plaintext(self):
        code="""
import os
from pathlib import Path
from app import store
store.init()
with store.connect() as db:
 db.execute('PRAGMA wal_autocheckpoint=0')
 db.execute('INSERT INTO events(owner,title,kind,created_at) VALUES(?,?,?,?)',('web','WAL secret canary 987171','task',store.utcnow()))
 db.commit()
 p=Path(os.environ['DATABASE_PATH']+'-wal')
 assert p.is_file()
 assert b'WAL secret canary' not in p.read_bytes()
"""
        self.run_code(code)

    def test_production_rejects_insecure_modes(self):
        code='from app import database; database.validate_runtime()'
        secure=dict(self.env,APP_ENV='production',PUBLIC_BASE_URL='https://orbita.example',COOKIE_SECURE='1',APP_SESSION_SECRET='test-secret-'+'s'*48)
        self.run_code(code,secure)
        for field,value in [('AUTH_REQUIRED','0'),('DATABASE_ENCRYPTION','0'),('COOKIE_SECURE','0'),('PUBLIC_BASE_URL','http://orbita.example'),('APP_SESSION_SECRET','')]:
            self.run_code(code,dict(secure,**{field:value}),ok=False)


if __name__=='__main__':unittest.main()
