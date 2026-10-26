"""Offline operator tools. Keys and passwords are never accepted in command arguments."""
import argparse
import base64
import os
import secrets
from pathlib import Path
from app import database, store
from app.context import scope


def encrypt_copy(source, output):
    """Export a consistent plaintext SQLite snapshot to a NEW SQLCipher database."""
    from sqlcipher3 import dbapi2 as cipher
    source, output = Path(source).resolve(), Path(output).resolve()
    if not source.is_file() or source == output or output.exists():
        raise ValueError('Нужны существующая исходная база и новый путь для зашифрованной копии.')
    key = database.key_bytes().hex()
    output.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    db = cipher.connect(source.as_uri() + '?mode=rw', uri=True, timeout=60)
    try:
        if not db.execute('PRAGMA cipher_version').fetchone():
            raise RuntimeError('SQLCipher недоступен.')
        db.execute('PRAGMA temp_store=MEMORY')
        db.execute('ATTACH DATABASE ? AS encrypted KEY "x\'' + key + '\'"', (str(output),))
        db.execute('BEGIN IMMEDIATE')
        db.execute("SELECT sqlcipher_export('encrypted')").fetchone()
        version = int(db.execute('PRAGMA user_version').fetchone()[0])
        db.execute('PRAGMA encrypted.user_version=' + str(version))
        source_counts = {name: db.execute('SELECT count(*) FROM "' + name.replace('"', '""') + '"').fetchone()[0]
                         for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        db.commit()
        db.execute('DETACH DATABASE encrypted')
        with cipher.connect(str(output)) as verified:
            verified.execute('PRAGMA key="x\'' + key + '\'"')
            if verified.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise RuntimeError('Зашифрованная копия не прошла проверку.')
            if verified.execute('PRAGMA cipher_integrity_check').fetchall():
                raise RuntimeError('Проверка шифрования завершилась ошибкой.')
            for name, count in source_counts.items():
                if verified.execute('SELECT count(*) FROM "' + name.replace('"', '""') + '"').fetchone()[0] != count:
                    raise RuntimeError('Количество записей в копии отличается.')
        return output
    except Exception:
        db.rollback()
        output.unlink(missing_ok=True)
        raise
    finally:
        db.close()


def adopt_legacy(name, legacy='web'):
    """Explicit local operator action. Registration can never invoke this migration."""
    from app import auth
    if legacy != 'web':
        raise ValueError('Эта команда переносит только старое локальное пространство web.')
    with store.atomic(), store.connect() as db:
        row = db.execute('SELECT owner FROM users WHERE username=?', (auth.username(name),)).fetchone()
        if not row:
            raise ValueError('Сначала зарегистрируй аккаунт в приложении.')
        target = row[0]
        db.execute('UPDATE events SET source_id=NULL WHERE owner=? AND source_id IN (SELECT source_id FROM events WHERE owner=?)', (legacy, target))
        db.execute('UPDATE events SET owner=? WHERE owner=?', (target, legacy))
        db.execute('UPDATE notifications SET owner=? WHERE owner=?', (target, legacy))
        for (old_sid,) in db.execute('SELECT id FROM sessions WHERE substr(id,1,4)=?', ('web:',)).fetchall():
            new_sid = target + old_sid[3:]
            if db.execute('SELECT 1 FROM sessions WHERE id=?', (new_sid,)).fetchone():
                raise ValueError('Есть диалоги с одинаковым ID. Автоматический перенос отменён без изменений.')
            db.execute('UPDATE sessions SET id=? WHERE id=?', (new_sid, old_sid))
            db.execute('UPDATE messages SET session=? WHERE session=?', (new_sid, old_sid))
        db.execute('DELETE FROM receipts WHERE owner=?', (legacy,))
        db.execute('DELETE FROM accounts WHERE owner=?', (legacy,))


def main():
    parser = argparse.ArgumentParser(description='Локальное обслуживание Орбиты')
    sub = parser.add_subparsers(dest='command', required=True)
    key = sub.add_parser('keygen', help='Сохранить новый ключ в приватный файл; не перезаписывает старый')
    key.add_argument('--output', type=Path, required=True)
    enc = sub.add_parser('encrypt', help='Создать проверенную зашифрованную копию; исходная база остаётся на месте')
    enc.add_argument('--source', type=Path, required=True)
    enc.add_argument('--output', type=Path, required=True)
    adopt = sub.add_parser('adopt-legacy', help='Перенести прежние web-записи в явно указанный зарегистрированный аккаунт')
    adopt.add_argument('--username', required=True)
    adopt.add_argument('--confirm', action='store_true', required=True)
    args = parser.parse_args()
    if args.command == 'keygen':
        args.output.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as f:
            f.write(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode() + '\n')
        print('Ключ сохранён в приватный файл. Храни отдельную защищённую копию; в Git ключ не добавляется.')
    elif args.command == 'encrypt':
        encrypt_copy(args.source, args.output)
        print('Зашифрованная копия проверена. Исходная база сохранена; переключение выполняется отдельно после остановки сервиса.')
    else:
        store.init()
        adopt_legacy(args.username)
        print('Старые записи перенесены в указанный аккаунт.')


if __name__ == '__main__':
    main()
