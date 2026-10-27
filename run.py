#!/usr/bin/env python3
"""Small cross-platform process launcher. No installed supervisor required."""
import argparse,os,sys,subprocess,time,signal,tempfile
import json,math,shutil,threading,urllib.request
from pathlib import Path
from app import config

def doctor():
    from zoneinfo import ZoneInfo
    checks=[]
    checks.append(('Python 3.11+',sys.version_info>=(3,11)))
    checks.append(('CSV задания',(config.ROOT/'resources/source_events.csv').exists()))
    try:ZoneInfo('Europe/Moscow');zone=True
    except Exception:zone=False
    checks.append(('IANA timezones',zone))
    try:
        from app import database
        database.validate_runtime()
        valid=True
    except Exception:valid=False
    checks.append(('Ключи и политика доступа настроены корректно',valid))
    target=Path(config.DB_PATH).parent;target.mkdir(parents=True,exist_ok=True)
    try:
        with tempfile.TemporaryFile(dir=target) as f:f.write(b'ok')
        writable=True
    except Exception:writable=False
    checks.append(('Директория данных доступна на запись',writable))
    for name,ok in checks:print(('OK   ' if ok else 'FAIL ')+name)
    print('Шифрование базы: '+('включено' if os.getenv('DATABASE_ENCRYPTION','1' if os.getenv('APP_ENV')=='production' else '0')=='1' else 'выключено (только локальный режим)'))
    print('LLM: '+(config.MODEL+'; резерв: '+(config.FALLBACK_MODEL or 'нет')+' (сеть не проверялась)' if config.API_KEY else 'локальный режим'))
    print('Распознавание речи: '+('настроено (сеть не проверялась)' if config.STT_ENABLED and config.STT_API_KEY else 'отключено'))
    print('Telegram: '+('токен задан (сеть не проверялась)' if os.getenv('TELEGRAM_BOT_TOKEN') else 'не настроен'))
    print('Mini App HTTPS: '+('задан' if os.getenv('PUBLIC_BASE_URL','').startswith('https://') else 'не задан'))
    if os.getenv('TELEGRAM_BOT_TOKEN') and not os.getenv('TELEGRAM_ALLOWED_USER_IDS') and os.getenv('TELEGRAM_ALLOW_PUBLIC')!='1':print('Внимание: бот закрыт. Отправь /id и добавь ID в TELEGRAM_ALLOWED_USER_IDS.')
    print('Секреты не выводятся. Проверка не обращается к внешним API.')
    return 0 if all(ok for _,ok in checks) else 1

def safe_number(name, default, minimum, maximum, integer=False):
    """Reject non-finite, invalid and unsafe operator settings without logging values."""
    try:
        value = int(os.getenv(name,str(default))) if integer else float(os.getenv(name,str(default)))
        if not math.isfinite(value) or not minimum <= value <= maximum:raise ValueError
        return value
    except (ValueError,OverflowError):
        print(name+': invalid setting; using default.',flush=True)
        return default

def backup_settings():
    return (safe_number('BACKUP_INTERVAL_HOURS',6,1/60,24*365)*3600,
            safe_number('BACKUP_KEEP',28,1,10000,integer=True))

def install_stop_handlers(stop):
    previous={};received=[]
    def on_signal(signum,frame):
        received.append(signum);stop.set()
    for signum in (signal.SIGINT,signal.SIGTERM):previous[signum]=signal.signal(signum,on_signal)
    return previous,received

def restore_handlers(previous):
    for signum,handler in previous.items():signal.signal(signum,handler)

def backup_worker():
    from scripts.backup import create_backup,rotate_backups,newest_backup_age,send_offsite
    interval,keep=backup_settings();stop=threading.Event()
    previous,received=install_stop_handlers(stop)
    try:
        # A restart must not create a fresh copy and push older restore points out.
        age=newest_backup_age()
        if age is not None and age<interval:stop.wait(interval-age)
        while not stop.is_set():
            try:
                path=create_backup();rotate_backups(keep)
                print('Backup created and verified; retention applied.',flush=True)
                send_offsite(path)
            except Exception as exc:
                print('Backup failed ('+type(exc).__name__+'); retry scheduled.',flush=True)
            stop.wait(interval)
        return 0
    finally:restore_handlers(previous)

def stop_processes(processes,timeout=10):
    """Signal all children first; use a single shutdown deadline for the service."""
    for process in processes:
        try:
            if os.name=='posix':os.killpg(process.pid,signal.SIGTERM)
            elif process.poll() is None:process.terminate()
        except ProcessLookupError:pass
    deadline=time.monotonic()+timeout
    for process in processes:
        try:process.wait(timeout=max(0,deadline-time.monotonic()))
        except subprocess.TimeoutExpired:pass
    # A direct child may have exited while its own descendants still run.
    for process in processes:
        try:
            if os.name=='posix':os.killpg(process.pid,signal.SIGKILL)
            elif process.poll() is None:process.kill()
        except ProcessLookupError:pass
    for process in processes:process.wait()

def health_ok(port):
    try:
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open('http://127.0.0.1:'+str(port)+'/api/health',timeout=2) as response:
            return response.status==200 and json.loads(response.read(4096)).get('ok') is True
    except Exception:return False

def supervise(commands,stop,health_port=None,health_check=health_ok,restartable=()):
    """Web/edge failures restart the service; auxiliary workers restart alone."""
    processes=[];launched=time.monotonic();next_check=launched+60;failed_checks=0
    started={};pending={};delays={}
    try:
        for label,command,env in commands:
            if stop.is_set():return 0
            process=subprocess.Popen(command,cwd=config.ROOT,env=env,start_new_session=os.name=='posix')
            processes.append(process);started[label]=launched;print('Started '+label+'.',flush=True)
        while not stop.is_set():
            for index,((label,command,env),process) in enumerate(zip(commands,processes)):
                status=process.poll()
                if status is None:continue
                if label in restartable:
                    now=time.monotonic()
                    if label not in pending:
                        # A worker that ran for 10 minutes starts again from a short delay.
                        delay=5 if now-started[label]>600 else delays.get(label,5)
                        delays[label]=min(delay*2,300);pending[label]=now+delay
                        print(label+' exited; restarting it in '+str(int(delay))+'s. Website keeps running.',flush=True)
                    elif now>=pending[label]:
                        del pending[label]
                        processes[index]=subprocess.Popen(command,cwd=config.ROOT,env=env,start_new_session=os.name=='posix')
                        started[label]=now;print('Restarted '+label+'.',flush=True)
                    continue
                print(label+' exited; stopping service for platform restart.',flush=True)
                return max(1,status) if status>=0 else 128-status
            if health_port is not None and time.monotonic()>=next_check:
                failed_checks=0 if health_check(health_port) else failed_checks+1
                next_check=time.monotonic()+30
                if failed_checks>=3:
                    print('HTTP health check failed repeatedly; stopping service for platform restart.',flush=True)
                    return 1
            stop.wait(.25)
        return 0
    finally:stop_processes(processes)

def port_setting(name,default):
    try:
        port=int(os.getenv(name,str(default)))
        if not 1<=port<=65535:raise ValueError
        return port
    except ValueError:raise ValueError('Invalid '+name) from None

def main():
    parser=argparse.ArgumentParser(description='Орбита: сайт + Telegram + резервное копирование')
    parser.add_argument('mode',nargs='?',choices=['web','bot','all','backup','doctor'],default='all');args=parser.parse_args()
    if args.mode=='doctor':return doctor()
    if os.getenv('HOSTING_PROFILE')=='alwaysdata':
        print('alwaysdata Free: start the website with python -m scripts.hosted and scheduled tasks with python -m scripts.scheduled.',flush=True)
        return 1
    if args.mode=='bot' and os.getenv('TELEGRAM_MODE')=='webhook':
        print('Webhook mode uses the website and scheduled.py; polling must remain stopped.',flush=True)
        return 1
    if sys.version_info<(3,11):raise SystemExit('Нужен Python 3.11+')
    if args.mode=='backup':return backup_worker()
    stop=threading.Event();previous,received=install_stop_handlers(stop)
    try:
        production=os.getenv('APP_ENV','development').lower()=='production'
        has_web=args.mode in ('web','all')
        edge=has_web and (production or os.getenv('CADDY_ENABLED')=='1')
        public_port=port_setting('PORT',8000)
        app_port=port_setting('APP_PORT',8001) if edge else public_port
        if edge and public_port==app_port:raise ValueError('PORT and APP_PORT must differ')
        if edge and not shutil.which('caddy'):raise ValueError('Install Caddy before starting the HTTP edge')
        if production and os.getenv('HOST','127.0.0.1')!='127.0.0.1':raise ValueError('Production HOST must be loopback')
        if production and has_web:
            # Fail before spawning any sibling if the production transport is missing.
            import waitress
        # Complete schema, key and WAL setup before any worker starts.
        from app import store
        store.init()
        commands=[]
        if has_web:
            env=dict(os.environ,PORT=str(app_port))
            if edge:env['HOST']='127.0.0.1'
            commands.append(('web',[sys.executable,'-m','app.server'],env))
        if args.mode=='bot' or (args.mode=='all' and os.getenv('TELEGRAM_BOT_TOKEN') and os.getenv('TELEGRAM_MODE','polling')=='polling'):
            commands.append(('bot',[sys.executable,'-m','app.telegram_bot'],dict(os.environ)))
        if has_web and os.getenv('BACKUP_ENABLED','1')!='0':
            commands.append(('backup',[sys.executable,str(config.ROOT/'run.py'),'backup'],dict(os.environ)))
        if edge:
            env=dict(os.environ,PORT=str(public_port),APP_PORT=str(app_port),FORWARDED_SCHEME='https' if production else 'http')
            commands.append(('edge',['caddy','run','--config',str(config.ROOT/'Caddyfile'),'--adapter','caddyfile'],env))
        # The bot and backup are auxiliary when the website runs in the same service.
        restartable={'bot','backup'} if has_web else set()
        result=supervise(commands,stop,public_port if has_web else None,restartable=restartable)
        return 128+received[0] if received else result
    except Exception as exc:
        print('Startup failed ('+type(exc).__name__+'). Check configuration; secrets are not logged.',flush=True)
        return 1
    finally:restore_handlers(previous)
if __name__=='__main__':sys.exit(main())
