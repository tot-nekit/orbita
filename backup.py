"""Online, keyed database snapshots. Never copy the database file or its WAL."""
import argparse
import os
import re
import tempfile
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from app import config, database

# Only automatic snapshots are eligible for retention. Manual files, partials,
# directories and symlinks are deliberately excluded.
AUTO_BACKUP = re.compile(r"orbita-auto-\d{8}T\d{12}Z-[a-f0-9]{16}\.sqlite3\Z")


def backup_directory():
    return Path(config.DB_PATH).resolve().parent / "backups"


def create_backup(output=None) -> Path:
    """Create, verify and exclusively publish a snapshot using the configured key."""
    source = Path(config.DB_PATH).resolve()
    if not source.is_file():
        raise FileNotFoundError("Database does not exist")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    name = "orbita-auto-" + stamp + "-" + os.urandom(8).hex() + ".sqlite3"
    # Resolving a final symlink could violate the no-overwrite contract.
    target = Path(output).absolute() if output is not None else backup_directory() / name
    if target.resolve() == source or os.path.lexists(target):
        raise FileExistsError("Backup destination already exists")
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=".orbita-backup-", suffix=".partial", dir=target.parent)
    partial = Path(temporary)
    os.close(fd)  # mkstemp uses mode 0600 before any data is written.
    deadline = time.monotonic() + 300

    def progress(status, remaining, total):
        if time.monotonic() > deadline:
            raise TimeoutError("Backup deadline exceeded")

    try:
        with closing(database.open_connection(source, readonly=True)) as db:
            with closing(database.open_connection(partial)) as dest:
                db.backup(dest, pages=256, progress=progress, sleep=0.1)
                # Make the published snapshot independent of any WAL sidecar.
                dest.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
                dest.execute("PRAGMA journal_mode=DELETE").fetchone()
                if [row[0] for row in dest.execute("PRAGMA integrity_check")] != ["ok"]:
                    raise RuntimeError("Backup integrity check failed")
                if database.encryption_enabled():
                    result = [row[0] for row in dest.execute("PRAGMA cipher_integrity_check")]
                    if result and result != ["ok"]:
                        raise RuntimeError("Encrypted backup integrity check failed")
        # Recovery must also work after closing the writer.
        with closing(database.open_connection(partial, readonly=True)) as verify:
            if [row[0] for row in verify.execute("PRAGMA integrity_check")] != ["ok"]:
                raise RuntimeError("Backup reopen verification failed")
        with partial.open("rb") as handle:
            os.fsync(handle.fileno())
        os.link(partial, target)  # atomic publication; never replaces a path
        return target
    finally:
        for suffix in ("", "-wal", "-shm", "-journal"):
            partial.with_name(partial.name + suffix).unlink(missing_ok=True)


def rotate_backups(keep=28):
    """Prune only regular automatic snapshots, after a successful new copy."""
    if type(keep) is not int or not 1 <= keep <= 10000:
        raise ValueError("Backup retention must be between 1 and 10000")
    directory = backup_directory()
    if not directory.is_dir():
        return
    candidates = sorted(
        (p for p in directory.iterdir()
         if AUTO_BACKUP.fullmatch(p.name) and not p.is_symlink() and p.is_file()),
        key=lambda p: p.name, reverse=True,
    )
    for path in candidates[keep:]:
        if not path.is_symlink() and path.is_file():
            path.unlink()


def main():
    parser = argparse.ArgumentParser(description="Проверенная резервная копия без остановки сервера")
    parser.add_argument("--output", type=Path, help="Новый файл; существующий путь не заменяется")
    args = parser.parse_args()
    try:
        create_backup(args.output)
    except Exception as exc:
        # Exception text can contain private SQL, paths or provider information.
        print("Backup failed (" + type(exc).__name__ + ").", flush=True)
        return 1
    print("Backup created and verified.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
