"""Web + Telegram Mini App HTTP adapter. Use HTTPS reverse proxy outside localhost."""
import csv,io,json,logging,os,re,secrets,time,threading
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from urllib.parse import urlsplit,parse_qs
from collections import defaultdict,deque
from . import config,store,service,ai,security,notifications,auth,database,notes
from .context import scope,owner
from .dates import clock
ROOT=config.ROOT/'app/static';VERSION='3.3.2';log=logging.getLogger('orbita')
AUTH_ATTEMPTS=defaultdict(deque);RATE_LOCK=threading.Lock()
STATIC={'/':'index.html','/mini':'index.html','/mini/':'index.html','/style.css':'style.css','/app.js':'app.js','/favicon.svg':'favicon.svg','/manifest.webmanifest':'manifest.webmanifest'}

def positive_id(value):
    # bool is an int subclass; SQLite also coerces strings and floats to IDs.
    if type(value) is not int or not 0<value<=2**63-1:
        raise ValueError('Некорректный ID или версия записи.')
    return value

def limited(key,limit=20,window=60):
    with RATE_LOCK:
        now=time.time();queue=AUTH_ATTEMPTS[key]
        while queue and queue[0]<now-window:queue.popleft()
        if len(queue)>=limit:return True
        queue.append(now)
        # Bound memory independently from client-supplied session IDs.
        if len(AUTH_ATTEMPTS)>5000:
            for k in list(AUTH_ATTEMPTS)[:1000]:
                if not AUTH_ATTEMPTS[k] or AUTH_ATTEMPTS[k][-1]<now-window:AUTH_ATTEMPTS.pop(k,None)
        return False
class Handler(BaseHTTPRequestHandler):
    server_version='Orbita/2.0'
    def log_message(self,*args):pass
    def output(self,data,status=200,mime='application/json; charset=utf-8',headers=None):
        if isinstance(data,(dict,list)):data=json.dumps(data,ensure_ascii=False).encode()
        elif isinstance(data,str):data=data.encode()
        self.send_response(status)
        # Even a malformed request target must be able to receive an error.
        mini=self.path.split('?',1)[0].rstrip('/')=='/mini'
        csp="default-src 'self'; script-src 'self'"+(' https://telegram.org' if mini else '')+"; style-src 'self'; img-src 'self' data:; connect-src 'self'; media-src 'self' blob:; frame-ancestors "+("'self' https://web.telegram.org https://*.telegram.org https://*.telegram.me" if mini else "'none'")+"; base-uri 'none'; form-action 'self'"
        values={'Content-Type':mime,'Content-Length':str(len(data)),'X-Content-Type-Options':'nosniff','Referrer-Policy':'same-origin','Cache-Control':'no-store','Content-Security-Policy':csp,'Permissions-Policy':'camera=(), geolocation=()'}
        if not mini:values['X-Frame-Options']='DENY'
        values.update(headers or {})
        for k,v in values.items():self.send_header(k,v)
        self.end_headers();self.wfile.write(data)
    def cookie(self,token=None):return 'orbita_session='+(token or '')+'; HttpOnly; SameSite=Lax; Path=/; Max-Age='+(str(auth.SESSION_TTL if token.startswith('s2_') else 43200) if token else '0')+('; Secure' if os.getenv('COOKIE_SECURE')=='1' else '')
    def remote(self):
        # Production Caddy overwrites this header; Python only binds loopback.
        value=self.headers.get('X-Orbita-Client-IP','')
        if database.production() and self.client_address[0] in ('127.0.0.1','::1'):
            import ipaddress
            try:return str(ipaddress.ip_address(value))
            except ValueError:pass
        return self.client_address[0]
    def identity(self):
        authorization=self.headers.get('Authorization','')
        if authorization.startswith('Bearer '):
            try:return security.verify(authorization[7:])
            except ValueError:raise PermissionError('Сессия истекла. Войди снова.') from None
        if self.headers.get('X-Orbita-Client')=='mini':raise PermissionError('Открой Mini App из Telegram заново.')
        token=security.cookie_token(self.headers.get('Cookie'))
        if token:
            if token.startswith('s2_'):return auth.session_owner(token)
            try:return security.verify(token)
            except ValueError:raise PermissionError('Привязка пространства истекла. Вернись к веб-записям и заново свяжи Telegram в настройках.') from None
        if auth.required():raise PermissionError('Войди в аккаунт, чтобы открыть записи.')
        return 'web'
    def sid(self,s):
        if not isinstance(s,str) or not re.fullmatch(r'[a-zA-Z0-9-]{16,80}',s):raise ValueError('Некорректный ID диалога.')
        return s
    def do_GET(self):self.dispatch('GET')
    def do_POST(self):self.dispatch('POST')
    def dispatch(self,method):
        try:
            u=urlsplit(self.path);q={k:v[0] for k,v in parse_qs(u.query,max_num_fields=100).items()}
            if database.production():
                expected=urlsplit(os.getenv('PUBLIC_BASE_URL','')).netloc
                if self.headers.get('Host')!=expected and not (u.path=='/api/health' and self.client_address[0] in ('127.0.0.1','::1')):
                    return self.output({'error':'Недопустимый адрес сервера.'},400)
            if method=='GET' and u.path in ('/login','/login.html'):return self.output('',302,headers={'Location':'/'})
            if method=='GET' and u.path in STATIC:
                data=(ROOT/STATIC[u.path]).read_bytes()
                if u.path.rstrip('/')=='/mini':data=data.replace(b'<script src="/app.js"',b'<script src="https://telegram.org/js/telegram-web-app.js"></script><script src="/app.js"')
                suffix=STATIC[u.path].rsplit('.',1)[-1];mime={'html':'text/html; charset=utf-8','css':'text/css; charset=utf-8','js':'text/javascript; charset=utf-8','svg':'image/svg+xml','webmanifest':'application/manifest+json'}[suffix]
                return self.output(data,mime=mime)
            if method=='GET' and u.path=='/api/health':
                # Check a real read, not just whether this process can return JSON.
                with store.connect() as db:db.execute('SELECT 1 FROM runtime LIMIT 1').fetchone()
                return self.output({'ok':True,'version':VERSION})
            if method=='GET' and u.path=='/api/auth/status':
                try:ident=self.identity()
                except (PermissionError,ValueError):ident=None
                account=auth.user(ident) if ident else None
                return self.output({'required':auth.required(),'authenticated':ident is not None,'username':account['username'] if account else None})
            data=None
            if method=='POST':
                origin=self.headers.get('Origin')
                if self.headers.get('Sec-Fetch-Site')=='cross-site':return self.output({'error':'Cross-origin запрос запрещён.'},403)
                expected=os.getenv('PUBLIC_BASE_URL','').rstrip('/') if database.production() else None
                if origin and (origin!=expected if expected else urlsplit(origin).netloc!=self.headers.get('Host')):return self.output({'error':'Cross-origin запрос запрещён.'},403)
                length=self.headers.get('Content-Length','0')
                if not re.fullmatch(r'[0-9]{1,12}',length):return self.output({'error':'Некорректная длина запроса.'},400)
                n=int(length)
                maximum=config.request_body_limit()
                if n<=0 or n>maximum:return self.output({'error':f'Максимум {maximum//(1024*1024)} МБ.'},413)
                if u.path=='/api/telegram/webhook' and n>256*1024:return self.output({'error':'Слишком большое обновление.'},413)
                raw=self.rfile.read(n)
                if len(raw)!=n:return self.output({'error':'Неполный запрос.'},400)
                if u.path!='/api/transcribe':
                    if self.headers.get('Content-Type','').split(';',1)[0].strip().lower()!='application/json':return self.output({'error':'Ожидается JSON.'},415)
                    try:data=json.loads(raw)
                    except (ValueError,UnicodeError,RecursionError):return self.output({'error':'Некорректный JSON.'},400)
                    if not isinstance(data,dict):raise ValueError('Ожидается JSON-объект.')
                if u.path=='/api/telegram/webhook':
                    if os.getenv('TELEGRAM_MODE','polling')!='webhook' or not os.getenv('TELEGRAM_BOT_TOKEN'):return self.output({'error':'Webhook не настроен.'},404)
                    from . import webhooks
                    try:accepted=webhooks.receive(data,self.headers.get('X-Telegram-Bot-Api-Secret-Token',''))
                    except webhooks.QueueFull as exc:return self.output({'error':str(exc)},503)
                    if accepted:webhooks.wake()
                    return self.output({'ok':True})
                if u.path=='/api/logout':
                    auth.revoke(security.cookie_token(self.headers.get('Cookie')))
                    return self.output({'ok':True},headers={'Set-Cookie':self.cookie()})
                if u.path in ('/api/auth/register','/api/auth/login','/api/auth/recover') or u.path=='/api/login' and auth.required():
                    action=u.path.rsplit('/',1)[-1]
                    auth.limit(action,self.remote(),str(data.get('username',''))[:32])
                    result,token={'register':auth.register,'login':auth.login,'recover':auth.recover}[action](data)
                    return self.output(result,headers={'Set-Cookie':self.cookie(token)})
                if u.path in ('/api/login','/api/telegram/auth'):
                    # Behind the proxies every socket peer is loopback; limit the real client.
                    if limited(('login',self.remote()),20):return self.output({'error':'Слишком много попыток. Подожди минуту.'},429)
                    if u.path=='/api/login':
                        return self.output({'ok':True},headers={'Set-Cookie':self.cookie(security.issue('web'))})
                    # Validate initData and bind its token to the same epoch.
                    # Unlink/relink cannot interleave between proof and issuance.
                    with store.atomic():
                        user=security.telegram_user(data.get('initData',''));uid='tg:'+str(user['id'])
                        token=security.issue(uid)
                    return self.output({'token':token,'name':str(user.get('first_name','Друг'))[:100]})
            identity=self.identity()
            with scope(identity):
                if method=='GET':return self.get_api(u.path,q)
                if u.path in ('/api/auth/password','/api/auth/delete','/api/telegram/unlink'):
                    auth.limit('sensitive',self.remote(),identity)
                    if u.path=='/api/telegram/unlink':return self.output(auth.unlink_telegram(data))
                    if u.path.endswith('/delete'):
                        result=auth.delete_account(data)
                        return self.output(result,headers={'Set-Cookie':self.cookie()})
                    result,token=auth.change_password(data)
                    return self.output(result,headers={'Set-Cookie':self.cookie(token)})
                if u.path in ('/api/chat','/api/transcribe') and limited(('ai',identity),30):return self.output({'error':'Слишком много запросов. Подожди минуту.'},429)
                if u.path=='/api/transcribe':
                    if not store.account()['use_ai']:return self.output({'error':'Разреши отправку запросов провайдеру в настройках или используй текст.'},403)
                    if not config.STT_ENABLED or not config.STT_API_KEY:return self.output({'error':'Серверное распознавание речи не подключено. Используй текст или голосовой ввод браузера.'},503)
                    mime=self.headers.get('Content-Type','')
                    if not mime.startswith(('audio/webm','audio/ogg','audio/mp4')):raise ValueError('Неподдерживаемый аудиоформат.')
                    store.reserve_ai_request()
                    try:return self.output({'text':ai.transcribe(raw,mime)})
                    except Exception:return self.output({'error':'Распознавание недоступно. Используй текст.'},502)
                if u.path=='/api/link':
                    # Commit attempts before claiming a code; failed claims must
                    # survive transaction rollback and process restarts.
                    auth.limit('link',self.remote(),identity)
                    if identity.startswith('u:'):
                        auth.link_telegram(data.get('code',''));return self.output({'ok':True,'reload':True})
                    if auth.required():return self.output({'error':'Сначала войди в зарегистрированный аккаунт.'},403)
                    target=security.claim_link(data.get('code',''))
                    if target.startswith('u:'):return self.output({'error':'Telegram связан с личным аккаунтом. Войди в него через форму входа.'},403)
                    return self.output({'ok':True,'reload':True},headers={'Set-Cookie':self.cookie(security.issue(target))})
                if u.path=='/api/logout':return self.output({'ok':True},headers={'Set-Cookie':self.cookie()})
                key=self.headers.get('Idempotency-Key')
                if key and not re.fullmatch(r'[A-Za-z0-9_-]{16,100}',key):raise ValueError('Некорректный Idempotency-Key.')
                payload={'path':u.path,'data':data}
                if key:
                    cached=store.cached_result('http:'+key,payload)
                    if cached is not None:return self.output(cached[0],cached[1])
                prepared=None
                if u.path=='/api/chat':
                    text=data.get('text','')
                    if not isinstance(text,str) or not 1<=len(text.strip())<=2000:raise ValueError('Запрос: 1–2000 символов.')
                    sid=self.sid(data.get('session'))
                    expected=data.get('expected_revision')
                    if expected is not None:
                        if type(expected) is not int:raise ValueError('Некорректная версия диалога.')
                        if expected!=store.state_snapshot(sid)[1]:return self.output({'reply':'Действие уже изменилось в другом окне. Проверь актуальное подтверждение.','events':[],'actions':service.dialog_actions(sid),'mode':'local','changed':False,'code':'stale_dialog'})
                    prepared=service.prepare_chat(text.strip(),sid,store.account()['timezone'])
                result=store.run_once('http:'+key,payload,lambda:self.post_api(u.path,data,prepared)) if key else self.in_transaction(u.path,data,prepared)
                return self.output(result[0],result[1])
        except auth.AuthError as e:return self.output({'error':str(e)},e.status)
        except PermissionError as e:return self.output({'error':str(e),'code':'session_expired' if security.cookie_token(self.headers.get('Cookie')) and self.headers.get('X-Orbita-Client')!='mini' else 'unauthorized'},401)
        except (TypeError,KeyError,OverflowError,UnicodeError):return self.output({'error':'Некорректный запрос.'},400)
        except ValueError as e:return self.output({'error':str(e) or 'Некорректный запрос.'},400)
        except (BrokenPipeError,ConnectionResetError):return
        except Exception:log.error('Request failed; contents and credentials redacted');return self.output({'error':'Внутренняя ошибка сервера.'},500)
    def in_transaction(self,path,data,prepared=None):
        with store.atomic():return self.post_api(path,data,prepared)
    def get_api(self,path,q):
        if path=='/api/focus':
            from . import focus
            return self.output(focus.get())
        if path=='/api/notes':
            event_id=q.get('event_id')
            if event_id is not None:
                if not re.fullmatch(r'[1-9][0-9]{0,18}',event_id):raise ValueError('Некорректный ID плана.')
                event_id=int(event_id)
            return self.output(notes.listing(q.get('q','')[:200],q.get('board') or None,event_id,q.get('tag') or None))
        if path=='/api/notes/get':
            if not re.fullmatch(r'[1-9][0-9]{0,18}',q.get('id','')):raise ValueError('Некорректный ID заметки.')
            note=notes.get(int(q['id']))
            return self.output({'note':note} if note else {'error':'Заметка не найдена.'},200 if note else 404)
        if path=='/api/events/get':
            if not re.fullmatch(r'[1-9][0-9]{0,18}',q.get('id','')):raise ValueError('Некорректный ID записи.')
            event=store.get_event(int(q['id']))
            return self.output({'event':event} if event else {'error':'Запись не найдена.'},200 if event else 404)
        if path=='/api/health':return self.output({'ok':True,'version':VERSION})
        if path=='/api/config':
            acc=store.account();registered=auth.user();telegram=auth.telegram_status();linked=bool(acc['chat_id']) or owner().startswith('tg:') or telegram['linked']
            # Platform limits are public settings, never credentials.
            limits={'upload_bytes':config.request_body_limit()}
            return self.output({'llm':bool(config.API_KEY),'stt':bool(config.STT_ENABLED and config.STT_API_KEY),'model':config.MODEL if config.API_KEY else None,'fallback_model':config.FALLBACK_MODEL if config.API_KEY else None,'version':VERSION,'limits':limits,'account':{'linked':linked,'timezone':acc['timezone'],'notify':bool(acc['notify']),'chat_connected':bool(acc['chat_id']),'username':registered['username'] if registered else None,'private':bool(registered),'use_ai':bool(acc['use_ai']),'telegram':telegram,'telegram_identity':telegram},'privacy':{'encrypted':database.encryption_enabled(),'ai_enabled':bool(config.API_KEY and acc['use_ai'])},'telegram':{'configured':bool(os.getenv('TELEGRAM_BOT_TOKEN')),'username':os.getenv('TELEGRAM_BOT_USERNAME','').lstrip('@'),'worker_online':notifications.health()}})
        if path=='/api/history':
            sid=self.sid(q.get('session',''))
            return self.output({'messages':store.history(sid),'actions':service.dialog_actions(sid),'preview':service.dialog_preview(sid)})
        if path=='/api/notifications':
            return self.output({'notifications':notifications.delivery_status(max(1,min(200,int(q.get('limit','100'))))),'worker_online':notifications.health(),'interval_seconds':60 if os.getenv('TELEGRAM_MODE')=='webhook' else 15,'linked':bool(store.account()['chat_id']) or owner().startswith('tg:')})
        if path=='/api/events':
            limit=max(1,min(200,int(q.get('limit','50'))));offset=max(0,int(q.get('offset','0')))
            bucket=q.get('bucket')
            if bucket not in (None,'inbox','overdue'):raise ValueError('Неизвестный список задач.')
            if q.get('q'):rows,total=store.search(q['q'],q.get('kind'),limit,offset)
            else:rows,total=store.list_events(q.get('start'),q.get('end'),q.get('kind'),q.get('status'),limit,offset,bucket=bucket,day=clock(store.account()['timezone']).date().isoformat())
            attached=notes.counts([r['id'] for r in rows])
            for r in rows:r['notes']=attached.get(r['id'],0)
            return self.output({'events':rows,'total':total,'offset':offset,'has_more':offset+len(rows)<total})
        if path=='/api/trash':
            from datetime import datetime,timezone,timedelta
            cutoff=(datetime.now(timezone.utc)-timedelta(minutes=10)).isoformat()
            with store.connect() as db:rows=[dict(r) for r in db.execute('SELECT * FROM events WHERE owner=? AND deleted_at>=? ORDER BY deleted_at DESC',(owner(),cutoff))]
            return self.output({'events':rows})
        if path=='/api/stats':return self.output(store.stats(clock(q.get('tz',store.account()['timezone'])).date().isoformat()))
        if path=='/api/export':
            if q.get('format')=='notes':
                return self.output(notes.export_zip(),mime='application/zip',headers={'Content-Disposition':'attachment; filename="orbita-notes.zip"'})
            with store.connect() as db:rows=[dict(r) for r in db.execute('SELECT * FROM events WHERE owner=? AND deleted_at IS NULL ORDER BY id',(owner(),))]
            for r in rows:r.pop('owner',None);r.pop('deleted_at',None)
            if q.get('format')=='csv':
                f=io.StringIO();keys=['id','title','kind','date','time','duration','timezone','status','created_at','source_id','revision'];w=csv.DictWriter(f,fieldnames=keys);w.writeheader()
                for row in rows:w.writerow({k:("'"+v if isinstance(v,str) and v.startswith(('=','+','-','@','\t','\r')) else v) for k,v in row.items()})
                return self.output('\ufeff'+f.getvalue(),mime='text/csv; charset=utf-8',headers={'Content-Disposition':'attachment; filename="orbita-events.csv"'})
            return self.output(rows,headers={'Content-Disposition':'attachment; filename="orbita-events.json"'})
        return self.output({'error':'Не найдено.'},404)
    def post_api(self,path,data,prepared=None):
        if owner().startswith('u:') and not auth.user():raise PermissionError('Аккаунт удалён. Войди снова.')
        if path in ('/api/focus','/api/focus/start','/api/focus/stop'):
            from . import focus
            return focus.post(path,data)
        if path.startswith('/api/notes/'):return notes.post(path,data)
        if path in ('/api/events/save','/api/events/delete','/api/events/restore','/api/events/snooze'):
            if path!='/api/events/save' or data.get('id') is not None:positive_id(data.get('id'))
            if data.get('revision') is not None:positive_id(data['revision'])
        if path=='/api/chat':
            text=data.get('text','')
            if not isinstance(text,str) or not 1<=len(text.strip())<=2000:raise ValueError('Запрос: 1–2000 символов.')
            return service.chat(text.strip(),self.sid(data.get('session')),store.account()['timezone'],prepared=prepared),200
        if path=='/api/events/save':
            i=data.get('id');current=store.get_event(i) if type(i) is int else None
            if i is not None and not current:return {'error':'Запись не найдена.'},404
            if current and data.get('revision') is not None and data['revision']!=current['revision']:return {'error':'Запись уже изменилась в другом окне. Обнови список.','code':'stale'},409
            e=service.validate(data,current);collisions=store.conflicts(e,i)
            if collisions and data.get('confirm_conflicts') is not True:return {'error':'Есть пересечение со встречами. Сохранить всё равно?','conflicts':collisions},409
            return {'event':store.save(e,i)},200
        if path=='/api/events/delete':
            if data.get('confirmed') is not True:raise ValueError('Удаление требует подтверждения.')
            if type(data.get('id')) is not int:raise ValueError('Некорректный ID.')
            current=store.get_event(data['id'])
            if not current:return {'error':'Запись не найдена.'},404
            if data.get('revision') is not None and data['revision']!=current['revision']:return {'error':'Запись уже изменилась. Обнови список перед удалением.','code':'stale'},409
            if not store.delete(data['id'],revision=current['revision']):return {'error':'Запись уже изменилась. Обнови список.','code':'stale'},409
            return {'ok':True,'undo_id':data['id']},200
        if path=='/api/events/snooze':
            return {'event':notifications.snooze(data.get('id'),data.get('minutes'),data.get('revision'))},200
        if path=='/api/events/restore':
            e=store.get_event(data.get('id'),True)
            if not e or not e['deleted_at']:return {'error':'Удалённая запись не найдена.'},404
            conflicts=store.conflicts(e,e['id'])
            if conflicts and data.get('confirm_conflicts') is not True:return {'error':'При восстановлении есть пересечение.','conflicts':conflicts},409
            return {'event':store.restore(e['id'])},200
        if path=='/api/import':return {'imported':store.import_csv()},200
        if path=='/api/account':
            if 'timezone' in data:
                from zoneinfo import ZoneInfo
                try:ZoneInfo(data['timezone'])
                except Exception:raise ValueError('Неизвестный часовой пояс.')
            for field in ('notify','use_ai'):
                if field in data and type(data[field]) is not bool:raise ValueError(field+' должен быть true/false.')
            acc=store.account({k:v for k,v in data.items() if k in ('timezone','notify','use_ai')});return {'account':{'timezone':acc['timezone'],'notify':bool(acc['notify']),'use_ai':bool(acc['use_ai'])}},200
        return {'error':'Не найдено.'},404

def main():
    logging.basicConfig(level=logging.INFO);store.init();security.secret()
    host=os.getenv('HOST','127.0.0.1');port=int(os.getenv('PORT','8000'))
    if not 1<=port<=65535:raise ValueError('Неверный порт веб-сервера.')
    if database.production() and host!='127.0.0.1':raise ValueError('Production HOST must be loopback')
    print(f'Орбита v2 → http://{host}:{port}',flush=True)
    if database.production():
        from .http_runtime import serve
        serve(host,port)
    else:ThreadingHTTPServer((host,port),Handler).serve_forever()
if __name__=='__main__':main()
