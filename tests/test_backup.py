"""Backup recovery, confidentiality, exclusive publication and scoped retention."""
import importlib.util
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

import backup
from app import database

ROOT = Path(__file__).resolve().parent.parent


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "source.sqlite3"
        self.setting = patch.object(backup.config, "DB_PATH", str(self.source))
        self.setting.start()
        self.addCleanup(self.setting.stop)
        with sqlite3.connect(self.source) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("CREATE TABLE example(value TEXT)")
            db.execute("INSERT INTO example VALUES('committed')")

    def test_online_wal_snapshot_includes_only_committed_data(self):
        with sqlite3.connect(self.source) as writer:
            writer.execute("INSERT INTO example VALUES('uncommitted')")
            with patch.object(database, "open_connection", wraps=database.open_connection) as connect:
                result = backup.create_backup()
            self.assertEqual(connect.call_args_list[0].kwargs, {"readonly": True})
            with sqlite3.connect(result) as restored:
                self.assertEqual(restored.execute("SELECT value FROM example").fetchall(), [("committed",)])
                self.assertEqual(restored.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            writer.rollback()
        self.assertTrue(backup.AUTO_BACKUP.fullmatch(result.name))
        if os.name == "posix":
            self.assertEqual(result.stat().st_mode & 0o777, 0o600)
        self.assertFalse(list(result.parent.glob("*.partial*")))
        self.assertFalse(Path(str(result) + "-wal").exists())

    def test_existing_paths_and_symlinks_are_never_replaced(self):
        target = self.root / "existing.sqlite3"
        target.write_bytes(b"keep")
        link = self.root / "dangling.sqlite3"
        link.symlink_to(self.root / "missing")
        for path in (self.source, target, link):
            with self.subTest(path=path.name), self.assertRaises(FileExistsError):
                backup.create_backup(path)
        self.assertEqual(target.read_bytes(), b"keep")
        self.assertTrue(link.is_symlink())
        self.assertFalse((self.root / "missing").exists())

    def test_failure_removes_partial_and_does_not_publish(self):
        target = self.root / "failed.sqlite3"
        with patch.object(database, "open_connection", side_effect=RuntimeError("private diagnostic")):
            with self.assertRaises(RuntimeError):
                backup.create_backup(target)
        self.assertFalse(target.exists())
        self.assertFalse(list(self.root.glob(".orbita-backup-*")))
        self.assertTrue(self.source.exists())

    def test_concurrent_publisher_cannot_be_overwritten_or_deleted(self):
        target = self.root / "race.sqlite3"
        link = os.link

        def compete(source, destination):
            Path(destination).write_bytes(b"other publisher")
            link(source, destination)

        with patch.object(backup.os, "link", side_effect=compete):
            with self.assertRaises(FileExistsError):
                backup.create_backup(target)
        self.assertEqual(target.read_bytes(), b"other publisher")
        self.assertFalse(list(self.root.glob(".orbita-backup-*")))

    def test_retention_deletes_only_old_automatic_regular_files(self):
        directory = backup.backup_directory()
        directory.mkdir()
        names = ["orbita-auto-20261026T00000" + str(i) + "000000Z-" + "a" * 16 + ".sqlite3" for i in range(5)]
        for name in names:
            self.assertTrue(backup.AUTO_BACKUP.fullmatch(name))
            (directory / name).write_bytes(b"snapshot")
        protected = [directory / "manual.sqlite3", directory / "orbita-old.sqlite3", directory / ".orbita-backup-x.partial"]
        for path in protected:
            path.write_bytes(b"keep")
        external = self.root / "outside.sqlite3"
        external.write_bytes(b"external")
        symlink = directory / ("orbita-auto-20261025T000000000000Z-" + "b" * 16 + ".sqlite3")
        symlink.symlink_to(external)
        subdirectory = directory / ("orbita-auto-20261024T000000000000Z-" + "c" * 16 + ".sqlite3")
        subdirectory.mkdir()
        backup.rotate_backups(2)
        self.assertEqual(sorted(p.name for p in directory.iterdir() if p.name in names), names[-2:])
        self.assertTrue(all(p.exists() for p in protected))
        self.assertTrue(symlink.is_symlink())
        self.assertTrue(subdirectory.is_dir())
        self.assertEqual(external.read_bytes(), b"external")
        self.assertTrue(self.source.exists())

    def test_invalid_retention_does_not_delete_anything(self):
        result = backup.create_backup()
        for keep in (0, -1, True, 10001, 1.5, "2"):
            with self.subTest(keep=keep), self.assertRaises(ValueError):
                backup.rotate_backups(keep)
        self.assertTrue(result.exists())

    def test_missing_source_does_not_create_a_database(self):
        self.source.unlink()
        with self.assertRaises(FileNotFoundError):
            backup.create_backup()
        self.assertFalse(self.source.exists())

    @unittest.skipUnless(importlib.util.find_spec("sqlcipher3"), "SQLCipher driver unavailable")
    def test_encrypted_snapshot_recovers_with_key_and_rejects_plaintext_and_wrong_key(self):
        code = """
import sqlite3
from contextlib import closing
from pathlib import Path
from app import config, database
import backup
with closing(database.open_connection(config.DB_PATH)) as db:
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('CREATE TABLE sample(value TEXT)')
    db.execute("INSERT INTO sample VALUES('backup-private-fixture')")
    db.commit()
    result = backup.create_backup()
with closing(database.open_connection(result, readonly=True)) as restored:
    assert restored.execute('SELECT value FROM sample').fetchone()[0] == 'backup-private-fixture'
assert b'backup-private-fixture' not in result.read_bytes()
assert not result.read_bytes().startswith(b'SQLite format 3')
try:
    with sqlite3.connect(result) as plain: plain.execute('SELECT * FROM sample').fetchall()
except sqlite3.DatabaseError: pass
else: raise AssertionError('Plaintext SQLite opened encrypted backup')
with closing(database.sqlite3.connect(str(result))) as wrong:
    wrong.execute('PRAGMA key = "wrong-fixture-key"')
    try: wrong.execute('SELECT * FROM sample').fetchall()
    except database.sqlite3.DatabaseError: pass
    else: raise AssertionError('Wrong key opened encrypted backup')
print('encrypted recovery verified')
"""
        env = dict(os.environ, APP_ENV="test", DATABASE_ENCRYPTION="1",
                   DATABASE_KEY="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
                   DATABASE_PATH=str(self.root / "encrypted.sqlite3"))
        process = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env,
                                 capture_output=True, text=True, timeout=30)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertIn("encrypted recovery verified", process.stdout)


class BackupScheduleTests(unittest.TestCase):
    def test_failed_backup_retries_without_retention_and_does_not_log_exception_text(self):
        import io
        import run
        from contextlib import redirect_stdout
        stop = threading.Event()
        with patch.object(run.threading, "Event", return_value=stop), \
                patch.object(stop, "wait", side_effect=lambda interval: stop.set()) as wait, \
                patch.object(backup, "create_backup", side_effect=RuntimeError("private diagnostic")), \
                patch.object(backup, "rotate_backups") as rotate, redirect_stdout(io.StringIO()) as output:
            self.assertEqual(run.backup_worker(), 0)
        wait.assert_called_once()
        rotate.assert_not_called()
        self.assertIn("retry scheduled", output.getvalue())
        self.assertNotIn("private diagnostic", output.getvalue())

    def test_interval_and_retention_reject_nonfinite_and_dangerous_values(self):
        import run
        for value in ("nan", "inf", "-inf", "0", "-1", "bad", "99999999999999999999999"):
            with self.subTest(value=value), patch.dict(os.environ, BACKUP_INTERVAL_HOURS=value, BACKUP_KEEP=value):
                self.assertEqual(run.backup_settings(), (21600, 28))
        with patch.dict(os.environ, BACKUP_INTERVAL_HOURS="0.5", BACKUP_KEEP="3"):
            self.assertEqual(run.backup_settings(), (1800, 3))

    def test_restart_waits_for_schedule_instead_of_pushing_out_old_copies(self):
        import run
        stop = threading.Event()
        waits = []
        def wait(seconds):
            waits.append(seconds)
            stop.set()
        with patch.object(run.threading, "Event", return_value=stop), patch.object(stop, "wait", side_effect=wait), \
                patch.object(backup, "newest_backup_age", return_value=600), \
                patch.object(backup, "create_backup") as create:
            self.assertEqual(run.backup_worker(), 0)
        create.assert_not_called()
        self.assertEqual(waits, [21600 - 600])


class OffsiteBackupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for item in (patch.object(backup.config, "DB_PATH", str(self.root / "db.sqlite3")),
                     patch.object(backup.database, "encryption_enabled", return_value=True),
                     patch.dict(os.environ, {"TELEGRAM_BACKUP_CHAT_ID": "101", "TELEGRAM_BOT_TOKEN": "test:token"})):
            item.start()
            self.addCleanup(item.stop)
        backup.backup_directory().mkdir()
        self.file = backup.backup_directory() / "orbita-auto-20261027T000000000000Z-0123456789abcdef.sqlite3"
        self.file.write_bytes(b"encrypted bytes")

    class Client:
        def __init__(self, fail=False):
            self.sent, self.fail = [], fail
        def document(self, chat, data, **kw):
            if self.fail:
                raise RuntimeError("private diagnostic with token")
            self.sent.append((chat, data, kw["filename"]))

    def test_sends_once_per_day_to_owner_chat(self):
        client = self.Client()
        self.assertTrue(backup.send_offsite(self.file, client, now=1_000_000))
        self.assertFalse(backup.send_offsite(self.file, client, now=1_000_000 + 3600))
        self.assertTrue(backup.send_offsite(self.file, client, now=1_000_000 + 24 * 3600))
        self.assertEqual(client.sent[0], (101, b"encrypted bytes", self.file.name))
        self.assertEqual(len(client.sent), 2)

    def test_failure_is_quiet_and_retried_later(self):
        import io
        from contextlib import redirect_stdout
        with redirect_stdout(io.StringIO()) as output:
            self.assertFalse(backup.send_offsite(self.file, self.Client(fail=True), now=1_000_000))
        self.assertNotIn("private diagnostic", output.getvalue())
        self.assertTrue(backup.send_offsite(self.file, self.Client(), now=1_000_100))

    def test_disabled_without_chat_encryption_or_valid_id(self):
        client = self.Client()
        with patch.dict(os.environ, {"TELEGRAM_BACKUP_CHAT_ID": ""}):
            self.assertFalse(backup.send_offsite(self.file, client))
        with patch.dict(os.environ, {"TELEGRAM_BACKUP_CHAT_ID": "-1; rm"}):
            self.assertFalse(backup.send_offsite(self.file, client))
        with patch.object(backup.database, "encryption_enabled", return_value=False):
            self.assertFalse(backup.send_offsite(self.file, client))
        self.assertEqual(client.sent, [])
