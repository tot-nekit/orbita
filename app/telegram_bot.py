"""Telegram long-polling adapter. Run exactly one worker per bot token."""
import json,os,re,time,urllib.request,urllib.error,threading,logging,secrets
from datetime import timedelta
from zoneinfo import ZoneInfo
from . import store,service,ai,config,security,notifications
from .context import scope,owner
from .dates import clock
log=logging.getLogger('orbita.bot')
class BotError(Exception):pass
class Telegram:
    def __init__(self,token=None):
        self.token=token or os.getenv('TELEGRAM_BOT_TOKEN','')
        if not self.token:raise BotError('Задай TELEGRAM_BOT_TOKEN в .env.')
    def call(self,method,payload=None,timeout=45):
        req=urllib.request.Request(f'https://api.telegram.org/bot{self.token}/{method}',data=json.dumps(payload or {},ensure_ascii=False).encode(),headers={'Content-Type':'application/json'})
        try:
            with urllib.request.urlopen(req,timeout=timeout) as r:data=json.load(r)
            if not data.get('ok'):raise BotError('Telegram отклонил запрос.')
            return data['result']
        except BotError:raise
        except Exception:raise BotError('Telegram API временно недоступен. Проверь сеть и токен.') from None
    def document(self,chat_id,data):
        boundary=secrets.token_hex(16)
        body=(f'--{boundary}\r\nContent-Disposition: form-data; name="chat_id"\r\n\r\n{chat_id}\r\n--{boundary}\r\nContent-Disposition: form-data; name="document"; filename="orbita-events.csv"\r\nContent-Type: text/csv\r\n\r\n').encode()+data+f'\r\n--{boundary}--\r\n'.encode()
        req=urllib.request.Request(f'https://api.telegram.org/bot{self.token}/sendDocument',data=body,headers={'Content-Type':'multipart/form-data; boundary='+boundary})
        try:
            with urllib.request.urlopen(req,timeout=45) as r:response=json.load(r)
            if not response.get('ok'):raise BotError('Экспорт не отправлен.')
        except Exception:raise BotError('Не удалось отправить экспорт.') from None
    def audio(self,file_id):
        info=self.call('getFile',{'file_id':file_id});path=info.get('file_path','')
        if not re.fullmatch(r'[A-Za-z0-9_./-]+',path) or '..' in path or info.get('file_size',0)>12*1024*1024:raise BotError('Слишком большая или некорректная запись.')
        try:
            with urllib.request.urlopen(f'https://api.telegram.org/file/bot{self.token}/{path}',timeout=30) as r:data=r.read(12*1024*1024+1)
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
        nonce=st['confirmation'].setdefault('_nonce',secrets.token_hex(4));store.state(sid,st)
        rows=[[{'text':'Да, подтвердить','callback_data':'yes:'+nonce},{'text':'Отмена','callback_data':'cancel'}]]
    elif st.get('choices'):
        rows=[[{'text':f'#{e["id"]} {e["title"][:32]}','callback_data':f'choose:{e["id"]}'}] for e in result.get('events',[])[:8]]
    elif st.get('pending'):rows=[[{'text':'Отмена','callback_data':'cancel'}]]
    else:
        for e in result.get('events',[])[:8]:
            text+=f'\n\n#{e["id"]} · {e["title"]}\n{e.get("date") or "Без даты"} · {e.get("time") or "Весь день"} · {e["timezone"]}'
            rows.append([{'text':f'✓ #{e["id"]}','callback_data':f'done:{e["id"]}'},{'text':'Удалить','callback_data':f'askdel:{e["id"]}'}])
        rows.append([{'text':'☀ Сегодня','callback_data':'today'},{'text':'◷ Завтра','callback_data':'tomorrow'}])
    if result.get('notice'):text+='\n\n'+result['notice']
    return {'text':text,'reply_markup':{'inline_keyboard':rows}}

def handle(update,client):
    cb=update.get('callback_query');msg=cb.get('message',{}) if cb else update.get('message',{})
    user=cb.get('from',{}) if cb else msg.get('from',{});chat=msg.get('chat',{})
    if chat.get('type')!='private' or not isinstance(user.get('id'),int) or chat.get('id')!=user.get('id'):return None
    if msg.get('text','').strip()=='/id':return {'chat_id':chat['id'],'text':'Твой Telegram ID: '+str(user['id'])}
    if not security.allowed(user['id']):return {'chat_id':chat['id'],'text':'Доступ к боту ограничен. Твой Telegram ID: '+str(user['id'])+'. Владелец должен добавить его в TELEGRAM_ALLOWED_USER_IDS.'}
    from .auth import resolve_telegram
    with scope(resolve_telegram('tg:'+str(user['id']))):
        store.account({'chat_id':chat['id']});sid='telegram-conversation';acc=store.account();text=msg.get('text','').strip();data=cb.get('data','') if cb else ''
        receipt='telegram:'+str(update['update_id'])
        cached=store.cached_result(receipt,update)
        if cached is not None:return dict(cached,chat_id=chat['id'])
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
            planning_text=voice_state.get('voice_text','') if voice_state.get('voice_nonce')==data.split(':')[1] else ''
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
            if data:
                if data in ('today','tomorrow','week'):text={'today':'Что у меня сегодня?','tomorrow':'Что у меня завтра?','week':'Что запланировано на эту неделю?'}[data]
                elif data=='new':text='/new'
                elif data=='cancel':text='отмена'
                elif data.startswith('yes:'):
                    if store.state(sid).get('confirmation',{}).get('_nonce')!=data.split(':')[1]:return {'text':'Это подтверждение уже неактуально. Используй последнюю карточку.'}
                    text='да'
                elif data.startswith('choose:'):text='#'+data.split(':')[1]
                elif data.startswith('done:'):
                    i=int(data.split(':')[1]);e=store.get_event(i)
                    if not e:return {'text':'Запись уже удалена или недоступна.'}
                    parts=data.split(':')
                    if len(parts)>2 and int(parts[2])!=e['revision']:return {'text':'Запись уже изменилась. Используй актуальную карточку.'}
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
                    st=store.state(sid);st['confirmation']={'action':'delete','id':i,'_confirmed':True,'_revision':e['revision']};store.state(sid,st)
                    return render({'reply':f'Удалить «{e["title"]}»?','events':[e]},sid)
                elif data.startswith('voice_send:'):
                    st=store.state(sid)
                    if st.get('voice_nonce')!=data.split(':')[1]:return {'text':'Эта расшифровка устарела. Пришли голосовое заново.'}
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
                    return {'text':'Привет! Я Орбита — твой планировщик.\n\nНапиши «Напомни завтра в 10 позвонить Ивану», отправь голосовое или открой Mini App.\n\n/today · /tomorrow · /week — планы\n/find презентация — поиск\n/export — CSV-файл\n/id — мой Telegram ID\n/link — связать с сайтом\n/timezone Europe/Moscow — часовой пояс\n/notify on или off — напоминания\n/cancel — отмена\n\nГолос требует AI-ключа на сервере. Напоминания отправляются в этот чат, пока работает bot-worker.', 'reply_markup':keyboard()}
                if command in ('/today','/tomorrow','/week'):text={'/today':'Что у меня сегодня?','/tomorrow':'Что у меня завтра?','/week':'Что запланировано на эту неделю?'}[command]
                elif command=='/find':text='Найди '+arg if arg else 'Найди'
                elif command=='/cancel':text='отмена'
                elif command=='/new':return {'text':'Что запланируем? Например:\n«Добавь встречу завтра в 15 с командой»\n«Поставь задачу отправить отчет в пятницу»\n\nМожно отправить голосовое.'}
                elif command=='/link':return {'text':'Код привязки сайта: '+security.make_link()+'\n\nДействует 10 минут, одно использование. Введи его на сайте: Настройки → Telegram. Никому не пересылай код. Записи не переносятся автоматически: сайт переключится на Telegram-аккаунт.'}
                elif command=='/timezone':
                    try:ZoneInfo(arg)
                    except Exception:return {'text':'Укажи IANA-пояс: /timezone Europe/Moscow или /timezone Asia/Yekaterinburg.'}
                    store.account({'timezone':arg});return {'text':'Новые записи будут использовать '+arg+'. Старые записи не сдвигаются.'}
                elif command=='/notify':
                    if arg not in ('on','off'):return {'text':'Используй /notify on или /notify off.'}
                    store.account({'notify':int(arg=='on')});return {'text':'Напоминания '+('включены.' if arg=='on' else 'выключены.')}
                elif command=='/app':return {'text':'Открой планировщик кнопкой ниже.' if mini_url() else 'Для Mini App администратору нужно задать PUBLIC_BASE_URL с HTTPS.','reply_markup':keyboard()}
                elif command not in ('/today','/tomorrow','/week','/find','/cancel'):return {'text':'Не знаю эту команду. /help — что я умею.'}
            if msg.get('voice') and not data:
                if not config.API_KEY:return {'text':'Для распознавания голосовых нужен AI-ключ на сервере. Пока напиши запрос текстом.'}
                if msg['voice'].get('duration',0)>60:return {'text':'Запиши сообщение до 60 секунд.'}
                if not config.STT_ENABLED:return {'text':'Распознавание голосовых пока не подключено. Напиши запрос текстом; на сайте также доступен голосовой ввод поддерживаемого браузера.'}
                st=store.state(sid);st['voice_text']=transcript;st['voice_nonce']=secrets.token_hex(4);store.state(sid,st)
                return {'text':'Я услышал:\n\n'+transcript+'\n\nВыполнить этот запрос?','reply_markup':{'inline_keyboard':[[{'text':'Выполнить','callback_data':'voice_send:'+st['voice_nonce']},{'text':'Отмена','callback_data':'cancel'}]]}}
            if not text:return {'text':'Пришли текст или голосовое. Кнопка ниже открывает планировщик.','reply_markup':keyboard()}
            if len(text)>2000:return {'text':'Раздели длинный запрос на сообщения до 2000 символов.'}
            return render(service.chat(text,sid,store.account()['timezone'],prepared=prepared),sid)
        result=store.run_once(receipt,update,execute)
        return dict(result,chat_id=chat['id'])

def deliver(client,result):
    if not result:return
    if result.get('document'):
        client.document(result['chat_id'],result['document'].encode('utf-8'))
    text=result['text'];pieces=[text[i:i+3500] for i in range(0,len(text),3500)] or ['Готово.']
    for n,piece in enumerate(pieces):
        payload={'chat_id':result['chat_id'],'text':piece}
        if n==len(pieces)-1 and result.get('reply_markup'):payload['reply_markup']=result['reply_markup']
        client.call('sendMessage',payload)

def run():
    logging.basicConfig(level=logging.INFO);store.init();client=Telegram();client.call('getMe')
    webhook=client.call('getWebhookInfo')
    if webhook.get('url'):raise SystemExit('У бота настроен webhook. Удали его через deleteWebhook перед long polling; автоматически настройки не меняем.')
    client.call('setMyCommands',{'commands':[{'command':'start','description':'Начать и открыть планировщик'},{'command':'today','description':'Планы на сегодня'},{'command':'new','description':'Новая запись'},{'command':'link','description':'Связать с веб-сайтом'},{'command':'help','description':'Помощь'}]})
    if mini_url():client.call('setChatMenuButton',{'menu_button':{'type':'web_app','text':'Орбита','web_app':{'url':mini_url()}}})
    stop=threading.Event()
    def reminders():
        while not stop.is_set():
            try:notifications.tick(client);store.runtime('bot_heartbeat',time.time())
            except Exception:log.warning('Reminder tick failed (credentials redacted)')
            stop.wait(15)
    thread=threading.Thread(target=reminders,daemon=True);thread.start();offset=int(store.runtime('telegram_offset') or '0');backoff=1
    try:
        while True:
            try:
                updates=client.call('getUpdates',{'offset':offset,'timeout':25,'allowed_updates':['message','callback_query']})
                for update in updates:
                    try:result=handle(update,client)
                    except (ValueError,BotError):
                        msg=update.get('message') or update.get('callback_query',{}).get('message',{})
                        result={'chat_id':msg['chat']['id'],'text':'Не удалось выполнить запрос. Проверь дату или время и попробуй ещё раз.'} if msg.get('chat',{}).get('type')=='private' else None
                    deliver(client,result);offset=update['update_id']+1;store.runtime('telegram_offset',offset)
                backoff=1
            except KeyboardInterrupt:break
            except Exception:log.warning('Polling failed; retrying (credentials redacted)');time.sleep(backoff);backoff=min(backoff*2,30)
    finally:stop.set();thread.join(timeout=2)
if __name__=='__main__':run()
