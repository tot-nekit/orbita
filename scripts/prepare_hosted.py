"""Prepare a NEW managed-hosting installation without printing credentials."""
import argparse
import base64
import json
import os
from pathlib import Path
import secrets
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]


def prepare(url, private_dir):
    parsed = urlsplit(url)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
            or parsed.path not in ('', '/') or parsed.query or parsed.fragment
            or not parsed.hostname.endswith('.alwaysdata.net')):
        raise ValueError('Нужен HTTPS адрес твоего аккаунта вида https://account.alwaysdata.net.')
    path = ROOT/'.env'
    if path.exists() or path.is_symlink():raise ValueError('.env уже существует. Настройки и ключи не перезаписаны.')
    private_dir = Path(private_dir).resolve()
    private_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    key_path = private_dir/'database.key'
    if key_path.exists() or key_path.is_symlink():raise ValueError('Файл ключа уже существует. Используй существующую конфигурацию для обновления.')
    database_path = private_dir/'orbita.sqlite3'
    if database_path.exists():raise ValueError('Найдена база. Для переноса сохрани её существующий ключ.')
    fd = os.open(key_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'w') as file:
        file.write(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()+'\n')
    values = {'APP_ENV':'production', 'HOSTING_PROFILE':'alwaysdata',
              'PUBLIC_BASE_URL':url.rstrip('/'), 'AUTH_REQUIRED':'1',
              'DATABASE_ENCRYPTION':'1', 'DATABASE_PATH':str(database_path),
              'DATABASE_KEY_FILE':str(key_path), 'APP_SESSION_SECRET':secrets.token_urlsafe(48),
              'COOKIE_SECURE':'1', 'TELEGRAM_MODE':'webhook',
              'TELEGRAM_WEBHOOK_SECRET':secrets.token_urlsafe(48),
              'TELEGRAM_BOT_TOKEN':'', 'TELEGRAM_ALLOW_PUBLIC':'0',
              'OPENAI_API_KEY':'', 'STT_API_KEY':'', 'STT_ENABLED':'0',
              'BACKUP_ENABLED':'1', 'BACKUP_INTERVAL_HOURS':'6', 'BACKUP_KEEP':'28',
              'TRUSTED_PROXY_CIDRS':''}
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'w') as file:
        file.write('\n'.join(name+'='+json.dumps(value) for name, value in values.items())+'\n')


def main():
    parser = argparse.ArgumentParser(description='Первая установка на alwaysdata Free; существующие ключи не меняются')
    parser.add_argument('--url', required=True)
    parser.add_argument('--private-dir', type=Path, default=Path.home()/'orbita-private')
    args = parser.parse_args()
    try:prepare(args.url, args.private_dir)
    except (ValueError, OSError) as exc:raise SystemExit(str(exc)) from None
    print('Созданы приватные .env и ключ базы. Секреты не выводятся. Сохрани ключ отдельно от хостинга.')
    print('Дальше: сайт — python -m scripts.hosted; задача раз в минуту — python -m scripts.scheduled. См. docs/ALWAYSDATA.md.')


if __name__ == '__main__':main()
