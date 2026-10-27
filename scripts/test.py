"""Run offline tests without reading local credentials or touching the real DB."""
import os
import tempfile
import unittest
from pathlib import Path


def main():
    os.environ.update(HOSTING_PROFILE='',TRUSTED_PROXY_CIDRS='')
    os.environ.update(ORBITA_SKIP_DOTENV='1',APP_ENV='test',DATABASE_ENCRYPTION='0',AUTH_REQUIRED='0',COOKIE_SECURE='0',OPENAI_API_KEY='',STT_API_KEY='',TELEGRAM_BOT_TOKEN='',TELEGRAM_ALLOWED_USER_IDS='',TELEGRAM_ALLOW_PUBLIC='0',TELEGRAM_MODE='polling',TELEGRAM_WEBHOOK_SECRET='',WEBHOOK_INLINE_WORK='0',APP_SESSION_SECRET='isolated-test-only-'+'x'*48)
    with tempfile.TemporaryDirectory(prefix='orbita-tests-') as temporary:
        os.environ['DATABASE_PATH']=str(Path(temporary)/'test.sqlite3')
        tests=unittest.defaultTestLoader.discover(str(Path(__file__).resolve().parents[1]/'tests'))
        return 0 if unittest.TextTestRunner(verbosity=2).run(tests).wasSuccessful() else 1


if __name__=='__main__':raise SystemExit(main())
