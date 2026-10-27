"""Regression: concurrent fresh/legacy SQLite startup in separate processes."""
import os,sys,sqlite3,subprocess,tempfile,unittest,signal,time,threading
from pathlib import Path
from unittest.mock import patch,MagicMock
from app import store

ROOT=Path(__file__).resolve().parent.parent
class StartupRegression(unittest.TestCase):
    def start_many(self,path):
        env=dict(os.environ,DATABASE_PATH=str(path),APP_ENV='test',DATABASE_ENCRYPTION='0',AUTH_REQUIRED='0')
        code="from app import store; store.init(); store.init(); store.account(); print('ready')"
        ps=[subprocess.Popen([sys.executable,'-c',code],cwd=ROOT,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True) for _ in range(8)]
        results=[p.communicate(timeout=90) for p in ps]
        for p,(out,err) in zip(ps,results):self.assertEqual(p.returncode,0,err);self.assertIn('ready',out)
        with sqlite3.connect(path) as db:
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0],'ok')
            self.assertEqual(db.execute('PRAGMA journal_mode').fetchone()[0],'wal')
            self.assertEqual(db.execute("SELECT count(*) FROM runtime WHERE key='sessions_v2'").fetchone()[0],1)
    def test_concurrent_fresh_start(self):
        with tempfile.TemporaryDirectory() as tmp:self.start_many(Path(tmp)/'fresh.db')
    def test_concurrent_legacy_migration(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'legacy.db'
            with sqlite3.connect(path) as db:
                db.execute("CREATE TABLE events(id INTEGER PRIMARY KEY,title TEXT,kind TEXT,date TEXT,time TEXT,duration INTEGER,status TEXT,created_at TEXT,source_id INTEGER UNIQUE,timezone TEXT)")
                db.execute("INSERT INTO events VALUES(7,'Keep me','task',NULL,NULL,60,'active','2026-01-01',NULL,'Europe/Moscow')")
            self.start_many(path)
            with sqlite3.connect(path) as db:self.assertEqual(db.execute('SELECT title,owner FROM events WHERE id=7').fetchone(),('Keep me','web'))
    def test_locked_pragma_retries_and_closes(self):
        db=MagicMock();db.execute.side_effect=[None,sqlite3.OperationalError('database is locked'),MagicMock(fetchone=lambda:('wal',)),None]
        with patch.object(store.sqlite3,'connect',return_value=db),patch.object(store.time,'sleep') as sleep:
            with store.connect():pass
        sleep.assert_called_once();db.close.assert_called_once()
    def test_other_pragma_error_closes_without_retry(self):
        db=MagicMock();db.execute.side_effect=sqlite3.OperationalError('disk I/O error')
        with patch.object(store.sqlite3,'connect',return_value=db),patch.object(store.time,'sleep') as sleep:
            with self.assertRaises(sqlite3.OperationalError):
                with store.connect():pass
        sleep.assert_not_called();db.close.assert_called_once()


class SupervisionRegression(unittest.TestCase):
    def test_unexpected_clean_worker_exit_is_failure_and_stops_sibling(self):
        import run
        sleeping=MagicMock(pid=990001);sleeping.poll.return_value=None
        exited=MagicMock(pid=990002);exited.poll.return_value=0
        commands=[('web',['fixture-web'],{}),('bot',['fixture-bot'],{})]
        with patch.object(run.subprocess,'Popen',side_effect=[sleeping,exited]),patch.object(run,'stop_processes') as cleanup:
            self.assertEqual(run.supervise(commands,threading.Event()),1)
        cleanup.assert_called_once_with([sleeping,exited])

    def test_partial_spawn_failure_cleans_up_started_worker(self):
        import run
        process=MagicMock()
        with patch.object(run.subprocess,'Popen',side_effect=[process,OSError('fixture')]),patch.object(run,'stop_processes') as cleanup:
            with self.assertRaises(OSError):run.supervise([('web',[],{}),('bot',[],{})],threading.Event())
        cleanup.assert_called_once_with([process])

    def test_auxiliary_worker_restarts_alone_and_website_keeps_running(self):
        import run
        web=MagicMock(pid=990003);web.poll.return_value=None
        bot=MagicMock(pid=990004);bot.poll.return_value=1
        replacement=MagicMock(pid=990005);replacement.poll.return_value=None
        stop=threading.Event();clock=[0.0];ticks=[]
        def wait(_):
            # Real supervisor polls every 0.25s: the restart must survive many polls.
            clock[0]+=0.25;ticks.append(clock[0])
            if clock[0]>8:stop.set()
        with patch.object(run.subprocess,'Popen',side_effect=[web,bot,replacement]) as popen, \
                patch.object(run.time,'monotonic',side_effect=lambda:clock[0]), \
                patch.object(stop,'wait',side_effect=wait),patch.object(run,'stop_processes') as cleanup, \
                patch('builtins.print') as printed:
            self.assertEqual(run.supervise([('web',['w'],{}),('bot',['b'],{})],stop,restartable={'bot'}),0)
        self.assertEqual(popen.call_count,3)
        cleanup.assert_called_once_with([web,replacement])
        messages=[c.args[0] for c in printed.call_args_list]
        self.assertEqual(sum('restarting it' in m for m in messages),1)
        self.assertIn('Restarted bot.',messages)

    def test_repeated_failed_http_health_stops_the_service(self):
        import run
        process=MagicMock();process.poll.return_value=None
        # First check has a 60s startup grace; later checks are 30s apart.
        clock=iter([0,61,61,92,92,123,123])
        stop=MagicMock();stop.is_set.return_value=False
        with patch.object(run.subprocess,'Popen',return_value=process),patch.object(run,'stop_processes') as cleanup,patch.object(run.time,'monotonic',side_effect=lambda:next(clock)):
            self.assertEqual(run.supervise([('web',[],{})],stop,8000,lambda port:False),1)
        cleanup.assert_called_once_with([process])

    @unittest.skipUnless(os.name=='posix','POSIX signal/process-group regression')
    def test_sigterm_reaps_real_child_and_releases_listener(self):
        import socket
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);ready=root/'ready'
            child_code="import os,socket,time,pathlib; s=socket.socket(); s.bind(('127.0.0.1',0)); s.listen(); pathlib.Path("+repr(str(ready))+").write_text(str(os.getpid())+' '+str(s.getsockname()[1])); time.sleep(120)"
            code="""
import os,sys,threading
import run
stop=threading.Event()
previous,received=run.install_stop_handlers(stop)
try: result=run.supervise([('fixture',[sys.executable,'-c',sys.argv[1]],dict(os.environ))],stop)
finally: run.restore_handlers(previous)
raise SystemExit(result)
"""
            parent=subprocess.Popen([sys.executable,'-c',code,child_code],cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            self.addCleanup(lambda:parent.kill() if parent.poll() is None else None)
            deadline=time.monotonic()+15
            while not ready.exists() and parent.poll() is None and time.monotonic()<deadline:time.sleep(.03)
            self.assertTrue(ready.exists(),'child failed to start')
            pid,port=map(int,ready.read_text().split())
            try:
                parent.send_signal(signal.SIGTERM)
                out,err=parent.communicate(timeout=15)
                self.assertEqual(parent.returncode,0,err)
                with self.assertRaises(ProcessLookupError):os.kill(pid,0)
                with socket.socket() as probe:probe.bind(('127.0.0.1',port))
            finally:
                if parent.poll() is None:parent.kill();parent.wait()
                try:os.killpg(pid,signal.SIGKILL)
                except ProcessLookupError:pass

    @unittest.skipUnless(os.name=='posix','POSIX signal/process-group regression')
    def test_shutdown_kills_child_that_ignores_sigterm(self):
        import run
        process=subprocess.Popen([sys.executable,'-c','import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); print("ready",flush=True); time.sleep(120)'],start_new_session=True,stdout=subprocess.PIPE,text=True)
        try:
            self.assertEqual(process.stdout.readline().strip(),'ready')
            run.stop_processes([process],timeout=.1)
            self.assertEqual(process.returncode,-signal.SIGKILL)
        finally:
            if process.poll() is None:process.kill();process.wait()
            process.stdout.close()
