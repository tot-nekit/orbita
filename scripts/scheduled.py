"""One bounded cron invocation: recover inbox, send reminders and back up if due."""
import json
import os
import secrets
import time
from app import store, webhooks, notifications
from app.context import scope


def backup_if_due():
    from scripts.backup import create_backup, rotate_backups
    from run import backup_settings
    interval, keep = backup_settings()
    now = time.time()
    token = secrets.token_hex(16)
    with scope('web'), store.atomic():
        last = float(store.runtime('scheduled_backup_success') or '0')
        lease = json.loads(store.runtime('scheduled_backup_lease') or '{}')
        if now-last < interval or lease.get('until', 0) > now:return False
        store.runtime('scheduled_backup_lease', json.dumps({'until':now+360, 'token':token}))
    try:
        create_backup()
        rotate_backups(keep)
        with store.atomic():
            current = json.loads(store.runtime('scheduled_backup_lease') or '{}')
            if current.get('token') == token:store.runtime('scheduled_backup_success', time.time())
        return True
    finally:
        with store.atomic():
            current = json.loads(store.runtime('scheduled_backup_lease') or '{}')
            if current.get('token') == token:store.runtime('scheduled_backup_lease', '{}')


def run_once(client=None):
    from app import database
    database.validate_runtime()
    store.init()
    with scope('web'):
        if os.getenv('TELEGRAM_BOT_TOKEN'):
            if os.getenv('TELEGRAM_MODE') != 'webhook':raise ValueError('Для плановых задач нужен TELEGRAM_MODE=webhook.')
            webhooks.secret()
            if client is None:
                from app.telegram_bot import Telegram
                client = Telegram()
            store.runtime('bot_heartbeat', time.time())
            webhooks.drain(client, limit=10, budget=30)
            notifications.tick(client, max_messages=20, deadline=time.monotonic()+20)
            store.runtime('bot_heartbeat', time.time())
        if os.getenv('BACKUP_ENABLED', '1') != '0':backup_if_due()


def main():
    try:run_once()
    except Exception:
        print('Scheduled tasks failed; private contents omitted.', flush=True)
        return 1
    print('Scheduled tasks completed; private contents omitted.', flush=True)
    return 0


if __name__ == '__main__':raise SystemExit(main())
