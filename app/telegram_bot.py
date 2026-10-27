"""Telegram long-polling adapter. Run exactly one worker per bot token."""
import json,os,re,time,urllib.request,urllib.error,threading,logging,secrets
from urllib.parse import urlsplit
from datetime import timedelta
from zoneinfo import ZoneInfo
from . import store,service,ai,config,security,notifications
from .context import scope,owner
from .dates import clock
log=logging.getLogger('orbita.bot')
class BotError(Exception):pass

def telegram_opener():
    """Optional proxy for Telegram API only; other providers keep direct routes."""
    proxy=os.getenv('TELEGRAM_PROXY','').strip()
    if not proxy:return urllib.request.urlopen
    parts=urlsplit(proxy)
    if parts.scheme not in ('http','https') or not parts.hostname or not parts.port or parts.path not in ('','/'):
        raise BotError('TELEGRAM_PROXY должен иметь вид http://логин:пароль@хост:порт.')
    return urllib.request.build_opener(urllib.request.ProxyHandler({'http':proxy,'https':proxy})).open

class Telegram:
    def __init__(self,token=None):
        self.token=token or os.getenv('TELEGRAM_BOT_TOKEN','')
        if not self.token:raise BotError('Задай TELEGRAM_BOT_TOKEN в .env.')
        self.open=telegram_opener()
    def call(self,method,payload=None,timeout=45):
        req=urllib.request.Request(f'https://api.telegram.org/bot{self.token}/{method}',data=json.dumps(payload or {},ensure_ascii=False).encode(),headers={'Content-Type':'application/json'})
        try:
            with self.open(req,timeout=timeout) as r:data=json.load(r)
            if not data.get('ok'):raise BotError('Telegram отклонил запрос.')
            return data['result']
        except BotError:raise
        except urllib.error.HTTPError as exc:
            code=exc.code;exc.close()
            raise BotError('Telegram API вернул HTTP '+str(code)+'.') from None
        except Exception:raise BotError('Telegram API временно недоступен. Проверь сеть и токен.') from None
    def document(self,chat_id,data,filename='orbita-events.csv',mime='text/csv',caption=None):
        if not re.fullmatch(r'[A-Za-z0-9._-]{1,100}',filename) or not re.fullmatch(r'[a-z]+/[a-z0-9.+-]+',mime):raise BotError('Некорректный файл.')
        boundary=secrets.token_hex(16)
        fields=f'--{boundary}\r\nContent-Disposition: form-data; name="chat_id"\r\n\r\n{int(chat_id)}\r\n'
        if caption:fields+=f'--{boundary}\r\nContent-Disposition: form-data; name="caption"\r\n\r\n{caption[:1000]}\r\n'
        body=(fields+f'--{boundary}\r\nContent-Disposition: form-data; name="document"; filename="{filename}"\r\nContent-Type: {mime}\r\n\r\n').encode()+data+f'\r\n--{boundary}--\r\n'.encode()
        req=urllib.request.Request(f'https://api.telegram.org/bot{self.token}/sendDocument',data=body,headers={'Content-Type':'multipart/form-data; boundary='+boundary})
        try:
            with self.open(req,timeout=45) as r:response=json.load(r)
            if not response.get('ok'):raise BotError('Экспорт не отправлен.')
        except Exception:raise BotError('Не удалось отправить экспорт.') from None
    def audio(self,file_id):
        info=self.call('getFile',{'file_id':file_id});path=info.get('file_path','')
        if not re.fullmatch(r'[A-Za-z0-9_./-]+',path) or '..' in path or info.get('file_size',0)>12*1024*1024:raise BotError('Слишком большая или некорректная запись.')
        try:
            with self.open(f'https://api.telegram.org/file/bot{self.token}/{path}',timeout=30) as r:data=r.read(12*1024*1024+1)
        except Exception:raise BotError('Не удалось скачать голосовое сообщение.') from None
        if len(data)>12*1024*1024:raise BotError('Максимальный размер записи — 12 МБ.')
        return data

def mini_url():
    url=os.getenv('PUBLIC_BASE_URL','').rstrip('/')
    return url+'/mini' if url.startswith('https://') else None

def keyboard():
    rows=[[{'text':'☀ Сегодня','callback_data':'today'},{'text':'◷ Завтра','callback_data':'tomorrow'}],[{'text':'▦ Неделя','callback_data':'week'},{'text':'＋ Новая запись','callback_data':'new'}]]
    if mini_url():rows.insert(0,[{'text':'Открыть планировщик ↗','web_app':{'url':mini_url()}}])
    return {'inline_keyboard':rows}

def render(result,sid):
    text=result['reply'];rows=[];st=store.state(sid)
    if st.get('confirmation'):
        nonce=st['confirmation'].setdefault('_nonce',secrets.token_hex(4));st['confirmation'].setdefault('_expires',time.time()+600);store.state(sid,st)
        rows=[[{'text':'Да, подтвердить','callback_data':'yes:'+nonce},{'text':'Отмена','callback_data':'cancel'}]]
    elif st.get('choices'):
        rows=[[{'text':f'#{e["id"]} {e["title"][:32]}','callback_data':f'choose:{e["id"]}'}] for e in result.get('events',[])[:8]]
    elif st.get('pending'):rows=[[{'text':'Отмена','callback_data':'cancel'}]]
    else:
        for e in result.get('events',[])[:8]:
            text+=f'\n\n#{e["id"]} · {e["title"]}\n{e.get("date") or "Без даты"} · {e.get("time") or "Весь день"} · {e["timezone"]}'
            rows.append([{'text':f'✓ #{e["id"]}','callback_data':f'done:{e["id"]}:{e["revision"]}'},{'text':'Удалить','callback_data':f'askdel:{e["id"]}:{e["revision"]}'}])
        rows.append([{'text':'☀ Сегодня','callback_data':'today'},{'text':'◷ Завтра','callback_data':'tomorrow'}])
    for note in result.get('notes',[])[:5]:
        text+='\n\n📝 '+note['title']
        if note.get('excerpt'):text+='\n'+note['excerpt'][:240]
    if result.get('notes') and mini_url():
        rows.append([{'text':'Открыть заметки в Орбите','web_app':{'url':mini_url()}}])
    if result.get('notice'):text+='\n\n'+result['notice']
    return {'text':text,'reply_markup':{'inline_keyboard':rows}}

def handle(update,client):
    cb=update.get('callback_query');msg=cb.get('message',{}) if cb else update.get('message',{})
    user=cb.get('from',{}) if cb else msg.get('from',{});chat=msg.get('chat',{})
    if chat.get('type')!='private' or not isinstance(user.get('id'),int) or chat.get('id')!=user.get('id'):return None
    if msg.get('text','').strip()=='/id':return {'chat_id':chat['id'],'text':'Твой Telegram ID: '+str(user['id'])}
    if not security.allowed(user['id']):return {'chat_id':chat['id'],'text':'Доступ к боту ограничен. Твой Telegram ID: '+str(user['id'])+'. Владелец должен добавить его в TELEGRAM_ALLOWED_USER_IDS.'}
    from . import auth
    telegram_owner='tg:'+str(user['id'])
    auth.record_telegram_identity(user)
    resolved=auth.resolve_telegram(telegram_owner)
    generation=auth.telegram_epoch(telegram_owner)
    with scope(resolved):
        with store.atomic():
            if auth.resolve_telegram(telegram_owner)!=resolved or auth.telegram_epoch(telegram_owner)!=generation:
                raise PermissionError('Подключение Telegram изменилось. Отправь запрос снова.')
            store.account({'chat_id':chat['id']})
        sid='telegram-conversation';acc=store.account();text=msg.get('text','').strip();data=cb.get('data','') if cb else ''
        receipt='telegram:'+str(update['update_id'])
        cached=store.cached_result(receipt,update)
        if cached is not None:return dict(cached,chat_id=chat['id'],_telegram_owner=telegram_owner,_telegram_epoch=generation,_account_owner=resolved)
        if cb:
            try:client.call('answerCallbackQuery',{'callback_query_id':cb['id']})
            except BotError:pass
        prepared=None;transcript=None
        planning_text=text
        if data in ('today','tomorrow','week'):planning_text={'today':'Что у меня сегодня?','tomorrow':'Что у меня завтра?','week':'Что запланировано на эту неделю?'}[data]
        elif data=='cancel':planning_text='отмена'
        elif data.startswith('yes:'):planning_text='да'
        elif data.startswith('choose:'):planning_text='#'+data.split(':')[1]
        elif data.startswith('voice_send:'):
            voice_state=store.state(sid)
            planning_text=voice_state.get('voice_text','') if voice_state.get('voice_nonce')==data.split(':')[1] and voice_state.get('voice_expires',0)>time.time() else ''
        elif data:planning_text=''
        if planning_text.startswith('/'):
            command,*args=planning_text.split(maxsplit=1);command=command.split('@')[0]
            planning_text={'/today':'Что у меня сегодня?','/tomorrow':'Что у меня завтра?','/week':'Что запланировано на эту неделю?','/cancel':'отмена'}.get(command,'Найди '+(args[0] if args else '') if command=='/find' else '')
        if planning_text and len(planning_text)<=2000:
            prepared=service.prepare_chat(planning_text,sid,acc['timezone'])
            if data.startswith('voice_send:'):
                prepared['state'].pop('voice_text',None);prepared['state'].pop('voice_nonce',None)
        if msg.get('voice') and not data and acc['use_ai'] and config.API_KEY and config.STT_ENABLED and msg['voice'].get('duration',0)<=60:
            store.reserve_ai_request()
            transcript=ai.transcribe(client.audio(msg['voice']['file_id']),'audio/ogg')[:2000]
        def execute():
            nonlocal text
            if auth.resolve_telegram(telegram_owner)!=resolved or auth.telegram_epoch(telegram_owner)!=generation:
                raise PermissionError('Подключение Telegram изменилось. Отправь запрос снова.')
            if data:
                if data in ('today','tomorrow','week'):text={'today':'Что у меня сегодня?','tomorrow':'Что у меня завтра?','week':'Что запланировано на эту неделю?'}[data]
                elif data=='new':text='/new'
                elif data=='cancel':text='отмена'
                elif data.startswith('yes:'):
                    confirmation=store.state(sid).get('confirmation',{})
                    if confirmation.get('_nonce')!=data.split(':')[1] or confirmation.get('_expires',0)<=time.time():return {'text':'Это подтверждение уже неактуально. Используй последнюю карточку.'}
                    text='да'
                elif data.startswith('choose:'):text='#'+data.split(':')[1]
                elif data.startswith('done:'):
                    i=int(data.split(':')[1]);e=store.get_event(i)
                    if not e:return {'text':'Запись уже удалена или недоступна.'}
                    parts=data.split(':')
                    if len(parts)!=3 or int(parts[2])!=e['revision']:return {'text':'Запись уже изменилась. Используй актуальную карточку.'}
                    e['status']='done';e=store.save(e,i)
                    return render({'reply':'Готово! Отмечено выполненным.','events':[e]},sid)
                elif data.startswith('snooze:'):
                    _,event_id,revision,minutes=data.split(':')
                    try:e=notifications.snooze(int(event_id),int(minutes),int(revision))
                    except ValueError as err:return {'text':str(err)}
                    return render({'reply':'Напоминание перенесено: '+service.label(e)+'.','events':[e]},sid)
                elif data.startswith('askdel:'):
                    i=int(data.split(':')[1]);e=store.get_event(i)
                    if not e:return {'text':'Запись не найдена.'}
                    parts=data.split(':')
                    if len(parts)!=3 or int(parts[2])!=e['revision']:return {'text':'Запись уже изменилась. Используй актуальную карточку.'}
                    st=store.state(sid);st['confirmation']={'action':'delete','id':i,'_confirmed':True,'_revision':e['revision']};store.state(sid,st)
                    return render({'reply':f'Удалить «{e["title"]}»?','events':[e]},sid)
                elif data.startswith('voice_send:'):
                    st=store.state(sid)
                    if st.get('voice_nonce')!=data.split(':')[1] or st.get('voice_expires',0)<=time.time():return {'text':'Эта расшифровка устарела. Пришли голосовое заново.'}
                    text=st.pop('voice_text','');st.pop('voice_nonce',None);store.state(sid,st)
                    if not text:return {'text':'Эта расшифровка уже обработана. Пришли голосовое ещё раз.'}
                else:return {'text':'Эта кнопка устарела. Напиши /start.'}
            if text.startswith('/'):
                command,*args=text.split(maxsplit=1);arg=args[0] if args else '';command=command.split('@')[0]
                if command=='/export' or (command=='/start' and arg=='export'):
                    import csv,io
                    output=io.StringIO();writer=csv.writer(output);writer.writerow(['id','title','kind','date','time','timezone','status'])
                    with store.connect() as db:records=db.execute('SELECT id,title,kind,date,time,timezone,status FROM events WHERE owner=? AND deleted_at IS NULL ORDER BY id',(owner(),)).fetchall()
                    for row in records:writer.writerow(["'"+v if isinstance(v,str) and v.startswith(('=','+','-','@','\t','\r')) else v for v in row])
                    return {'text':'Твой экспорт готов.','document':'\ufeff'+output.getvalue()}
                if command in ('/start','/help'):
                    return {'text':'Привет! Я Орбита — твой планировщик.\n\nНапиши «Напомни завтра в 10 позвонить Ивану», отправь голосовое или открой Mini App.\n\n/today · /tomorrow · /week — планы\n/find презентация — поиск\n/note текст — быстрая заметка\n/notes — последние заметки\n/export — CSV-файл\n/id — мой Telegram ID\n/link — связать с сайтом\n/account или /status — подключение\n/timezone Europe/Moscow — часовой пояс\n/notify on или off — напоминания\n/cancel — отмена\n\nГолос требует AI-ключа на сервере. Напоминания отправляются в этот чат, пока работает bot-worker.', 'reply_markup':keyboard()}
                if command in ('/today','/tomorrow','/week'):text={'/today':'Что у меня сегодня?','/tomorrow':'Что у меня завтра?','/week':'Что запланировано на эту неделю?'}[command]
                elif command=='/find':text='Найди '+arg if arg else 'Найди'
                elif command=='/cancel':text='отмена'
                elif command=='/new':return {'text':'Что запланируем? Например:\n«Добавь встречу завтра в 15 с командой»\n«Поставь задачу отправить отчет в пятницу»\n\nМожно отправить голосовое.'}
                elif command in ('/account','/status'):
                    linked=auth.user()
                    return {'text':('Telegram связан с аккаунтом '+linked['username']+'.' if linked else 'Telegram пока не связан с зарегистрированным аккаунтом.')+'\nTelegram ID: '+str(user['id'])+'\nНапоминания: '+('включены' if acc['notify'] else 'выключены')+'\nЧасовой пояс: '+acc['timezone']+'\nОтключение Telegram — на сайте: Настройки → Telegram; потребуется пароль аккаунта.'}
                elif command=='/link':
                    if auth.user():
                        return {'text':'Этот Telegram уже связан с аккаунтом '+auth.user()['username']+'. Сайт, бот и Mini App используют общие записи. Для отключения открой настройки сайта и подтверди действие паролем. /account — статус.'}
                    return {'text':'Код привязки сайта: '+security.make_link()+'\n\nДействует 10 минут, одно использование. Введи его на сайте: Настройки → Telegram. Никому не пересылай код. В зарегистрированном аккаунте записи Telegram будут перенесены в него. В локальном веб-пространстве сайт только переключится на Telegram.'}
                elif command=='/timezone':
                    try:ZoneInfo(arg)
                    except Exception:return {'text':'Укажи IANA-пояс: /timezone Europe/Moscow или /timezone Asia/Yekaterinburg.'}
                    store.account({'timezone':arg});return {'text':'Новые записи будут использовать '+arg+'. Старые записи не сдвигаются.'}
                elif command=='/notify':
                    if arg not in ('on','off'):return {'text':'Используй /notify on или /notify off.'}
                    store.account({'notify':int(arg=='on')});return {'text':'Напоминания '+('включены.' if arg=='on' else 'выключены.')}
                elif command=='/app':return {'text':'Открой планировщик кнопкой ниже.' if mini_url() else 'Для Mini App администратору нужно задать PUBLIC_BASE_URL с HTTPS.','reply_markup':keyboard()}
                elif command=='/note':
                    if not arg:return {'text':'Напиши текст после команды: /note Идея для подарка'}
                    return render(service.chat('Сохрани заметку: '+arg,sid,acc['timezone']),sid)
                elif command=='/notes':
                    from . import notes
                    rows=notes.recent(7)
                    if not rows:return {'text':'Заметок пока нет. Добавь первую: /note Идея для подарка'}
                    return {'text':'Последние заметки:\n\n'+'\n'.join('• '+r['title']+' · '+r['board'] for r in rows)}
                elif command not in ('/today','/tomorrow','/week','/find','/cancel'):return {'text':'Не знаю эту команду. /help — что я умею.'}
            if msg.get('voice') and not data:
                if not acc['use_ai']:return {'text':'Разреши обработку голосовых в настройках сайта или напиши запрос текстом.'}
                if not config.API_KEY:return {'text':'Для распознавания голосовых нужен AI-ключ на сервере. Пока напиши запрос текстом.'}
                if msg['voice'].get('duration',0)>60:return {'text':'Запиши сообщение до 60 секунд.'}
                if not config.STT_ENABLED:return {'text':'Распознавание голосовых пока не подключено. Напиши запрос текстом; на сайте также доступен голосовой ввод поддерживаемого браузера.'}
                st=store.state(sid);st['voice_text']=transcript;st['voice_nonce']=secrets.token_hex(4);st['voice_expires']=time.time()+600;store.state(sid,st)
                return {'text':'Я услышал:\n\n'+transcript+'\n\nВыполнить этот запрос?','reply_markup':{'inline_keyboard':[[{'text':'Выполнить','callback_data':'voice_send:'+st['voice_nonce']},{'text':'Отмена','callback_data':'cancel'}]]}}
            if not text:return {'text':'Пришли текст или голосовое. Кнопка ниже открывает планировщик.','reply_markup':keyboard()}
            if len(text)>2000:return {'text':'Раздели длинный запрос на сообщения до 2000 символов.'}
            return render(service.chat(text,sid,store.account()['timezone'],prepared=prepared),sid)
        result=store.run_once(receipt,update,execute)
        return dict(result,chat_id=chat['id'],_telegram_owner=telegram_owner,_telegram_epoch=generation,_account_owner=resolved)

def deliver(client,result):
    if not result:return
    if result.get('_telegram_owner'):
        from . import auth
        # Serialize delivery with unlink/relink. No previously prepared private
        # response is sent after revocation has committed. Network time is bounded.
        with store.atomic():
            target=result['_telegram_owner']
            if auth.resolve_telegram(target)!=result['_account_owner'] or auth.telegram_epoch(target)!=result['_telegram_epoch'] or not security.allowed(target[3:]):
                return
            return _deliver(client,result)
    return _deliver(client,result)

def _deliver(client,result):
    if result.get('document'):
        client.document(result['chat_id'],result['document'].encode('utf-8'))
    text=result['text'];pieces=[text[i:i+3500] for i in range(0,len(text),3500)] or ['Готово.']
    for n,piece in enumerate(pieces):
        payload={'chat_id':result['chat_id'],'text':piece}
        if n==len(pieces)-1 and result.get('reply_markup'):payload['reply_markup']=result['reply_markup']
        client.call('sendMessage',payload)

def connect(client,sleep=time.sleep,attempts=None):
    """Wait for Telegram without exiting: an outage must never restart the website."""
    delay=5;tried=0
    while True:
        try:
            client.call('getMe',timeout=20)
            return client.call('getWebhookInfo',timeout=20)
        except BotError:
            tried+=1
            if attempts is not None and tried>=attempts:raise
            log.warning('Telegram API unreachable; retrying in %ss',delay)
            sleep(delay);delay=min(delay*2,300)

def run():
    if os.getenv('TELEGRAM_MODE','polling')!='polling' or os.getenv('HOSTING_PROFILE')=='alwaysdata':
        raise SystemExit('Webhook использует сайт и scheduled.py. Постоянный polling отключён.')
    logging.basicConfig(level=logging.INFO);store.init();client=Telegram()
    webhook=connect(client)
    if webhook.get('url'):raise SystemExit('У бота настроен webhook. Удали его через deleteWebhook перед long polling; автоматически настройки не меняем.')
    try:client.call('setMyCommands',{'commands':[{'command':'start','description':'Начать и открыть планировщик'},{'command':'today','description':'Планы на сегодня'},{'command':'new','description':'Новая запись'},{'command':'note','description':'Быстрая заметка'},{'command':'notes','description':'Последние заметки'},{'command':'link','description':'Связать с веб-сайтом'},{'command':'account','description':'Статус привязки'},{'command':'help','description':'Помощь'}]})
    except BotError:log.warning('Telegram commands were not updated; polling continues')
    try:
        if mini_url():client.call('setChatMenuButton',{'menu_button':{'type':'web_app','text':'Орбита','web_app':{'url':mini_url()}}})
    except BotError:log.warning('Telegram menu button was not updated; polling continues')
    stop=threading.Event()
    def reminders():
        while not stop.is_set():
            try:notifications.tick(client)
            except Exception:log.warning('Reminder tick failed (credentials redacted)')
            stop.wait(15)
    thread=threading.Thread(target=reminders,daemon=True);thread.start();offset=int(store.runtime('telegram_offset') or '0');backoff=1
    try:
        while True:
            try:
                updates=client.call('getUpdates',{'offset':offset,'timeout':25,'allowed_updates':['message','callback_query']})
                # Heartbeat proves a real round trip to Telegram, not just a live thread.
                store.runtime('bot_heartbeat',time.time())
                for update in updates:
                    try:result=handle(update,client)
                    except PermissionError:
                        result=None
                    except (ValueError,BotError):
                        msg=update.get('message') or update.get('callback_query',{}).get('message',{})
                        result={'chat_id':msg['chat']['id'],'text':'Не удалось выполнить запрос. Проверь дату или время и попробуй ещё раз.'} if msg.get('chat',{}).get('type')=='private' else None
                    deliver(client,result);offset=update['update_id']+1;store.runtime('telegram_offset',offset)
                backoff=1
            except KeyboardInterrupt:break
            except Exception as exc:
                # Type and Telegram status only; messages can contain URLs with the token.
                if isinstance(exc,BotError) and 'HTTP 409' in str(exc):
                    # Another process polls with the same token (for example a local copy).
                    log.warning('Another bot instance uses this token; stop it. Retrying in 60s')
                    time.sleep(60);continue
                log.warning('Polling failed (%s%s); retrying',type(exc).__name__,': '+str(exc) if isinstance(exc,BotError) else '')
                time.sleep(backoff);backoff=min(backoff*2,30)
    finally:stop.set();thread.join(timeout=2)
if __name__=='__main__':run()
