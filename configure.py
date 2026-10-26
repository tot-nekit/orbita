"""Interactive LOCAL setup. Never run through shared chat with real credentials."""
import getpass,secrets,os,re,json,base64
from pathlib import Path
ROOT=Path(__file__).resolve().parent

def main():
    print('Орбита: настройка сайта и Telegram. Секреты вводятся скрыто и остаются в локальном .env.')
    path=ROOT/'.env'
    if path.exists() and input('.env уже есть. Перезаписать? [y/N] ').lower()!='y':return
    key=getpass.getpass('VyceAI API key (пусто — локальный режим): ')
    bot=getpass.getpass('Telegram bot token из BotFather (пусто — только сайт): ')
    if bot and not re.fullmatch(r'\d+:[A-Za-z0-9_-]+',bot):raise SystemExit('Проверь формат токена. Файл не изменён.')
    username=input('Username бота без @ (можно пропустить): ').strip().lstrip('@') if bot else ''
    ids=input('Разрешённые Telegram ID через запятую (можно получить через /id): ').strip() if bot else ''
    url=input('Публичный HTTPS адрес сайта без /mini (пусто — Mini App пока отключён): ').strip().rstrip('/') if bot else ''
    if ids and not re.fullmatch(r'\d+(?:\s*,\s*\d+)*',ids):raise SystemExit('Некорректный список ID. Файл не изменён.')
    if username and not re.fullmatch(r'[A-Za-z0-9_]{5,32}',username):raise SystemExit('Некорректный username. Файл не изменён.')
    if url and not url.startswith('https://'):raise SystemExit('Mini App требует HTTPS. Файл не изменён.')
    # Never rotate a database key implicitly when reconfiguring an existing installation.
    from app import config
    database_key=os.getenv('DATABASE_KEY','')
    key_path=os.getenv('DATABASE_KEY_FILE','')
    if key_path:
        if not Path(key_path).is_file():raise SystemExit('Файл ключа базы не найден. Настройки не изменены.')
    elif not database_key:
        key_path=str(ROOT/'data/.database_key')
        Path(key_path).parent.mkdir(parents=True,exist_ok=True)
        try:
            fd=os.open(key_path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
            with os.fdopen(fd,'w') as f:f.write(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()+'\n')
        except FileExistsError:pass
    db=Path(os.getenv('DATABASE_PATH',str(ROOT/'data/orbita.sqlite3')))
    header=b''
    if db.exists():
        with db.open('rb') as file:header=file.read(16)
    if header==b'SQLite format 3\0':
        raise SystemExit('Найдена прежняя база без шифрования. Сначала выполни перенос по docs/PRIVACY.md. База и .env не изменены.')
    values={'HOST':'127.0.0.1','PORT':'8000','APP_ENV':'local','AUTH_REQUIRED':'1','DATABASE_ENCRYPTION':'1','DATABASE_KEY':database_key,'DATABASE_KEY_FILE':key_path,'APP_SESSION_SECRET':os.getenv('APP_SESSION_SECRET') or secrets.token_urlsafe(48),'COOKIE_SECURE':'0','OPENAI_API_KEY':key,'OPENAI_BASE_URL':'https://vyceai.com/v1','OPENAI_MODEL':'claude-sonnet-4-6','LLM_FALLBACK_MODEL':'deepseek-v4-flash','LLM_TIMEOUT':'16','LLM_MAX_TOKENS':'1200','LLM_TEMPERATURE':'0.1','STT_ENABLED':'0','STT_MODEL':'gpt-4o-mini-transcribe','TELEGRAM_BOT_TOKEN':bot,'TELEGRAM_BOT_USERNAME':username,'TELEGRAM_ALLOWED_USER_IDS':ids,'TELEGRAM_ALLOW_PUBLIC':'0','PUBLIC_BASE_URL':url}
    values['DATABASE_PATH']=str(db.resolve())
    if any('\n' in v or '\r' in v for v in values.values()):raise SystemExit('Переносы строк в настройках запрещены.')
    path.write_text('\n'.join(k+'='+json.dumps(v,ensure_ascii=False) for k,v in values.items())+'\n');os.chmod(path,0o600)
    print('Готово. Запуск: python run.py all. Открой http://localhost:8000.')
    print('Создай личный аккаунт в браузере и сохрани код восстановления. Почта не требуется.')
    if url:print('Для публикации используй docs/HOSTING.md: HTTPS, production-режим и постоянный диск.')
if __name__=='__main__':main()
