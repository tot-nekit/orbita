import re
from datetime import datetime,timezone,timedelta
from zoneinfo import ZoneInfo
from . import store, ai, config
from .dates import clock, resolve, title_from, unquoted, WEEKDAYS, QUOTED
from .context import owner
KINDS={'task','meeting','reminder'}
NEW_INTENT = r'^(?:пожалуйста[, ]+)?(?:что|какие|покажи|найди|создай|добавь|удали|удалить|напомни|поставь|запланируй|перенеси|измени|переименуй|заверши|отметь|есть ли)\b'
RECURRING = r'\b(?:кажд\w*|ежеднев\w*|еженедел\w*|ежемесяч\w*|повторя\w*|по\s+(?:понедельникам|вторникам|средам|четвергам|пятницам|субботам|воскресеньям))\b'

def _intent(text):
    """A model may interpret an instruction, but cannot invent permission."""
    s=unquoted(text).lower().replace('ё','е').strip()
    s=re.sub(r'^(?:(?:привет|орбита|пожалуйста|давай|можешь|можете|можно|ты можешь)[,!\s]+)+','',s)
    if re.match(r'^(?:не\b|как\b|зачем\b|почему\b|что значит\b|расскажи\b|объясни\b)',s):return None
    if re.match(r'^(?:что (?:у меня )?(?:сегодня|завтра|послезавтра|на\b)|какие (?:у меня )?(?:встречи|задачи|планы|напоминания))\b',s):return 'list'
    if re.match(r'^(?:запиши(?:те)?|мне (?:нужно|надо)|(?:я )?(?:хочу|хотел(?:а)? бы)\s+(?:(?:сегодня|завтра|послезавтра)\s+)?(?:добавить|создать|записать|купить|отправить|позвонить|подготовить|сделать|встретиться|встречу|созвон|задачу|напоминание))\b',s):return 'create'
    if re.match(r'^(?:(?:у меня|в\s+\S+|сегодня|завтра|послезавтра)\s+)*(?:встреча|созвон)\b',s) and re.search(r'\b(?:сегодня|завтра|послезавтра|понедельник\w*|вторник\w*|сред\w*|четверг\w*|пятниц\w*|суббот\w*|воскресень\w*|через)\b|\d',s):return 'create'
    for action,pattern in (
        ('delete',r'^(?:удали|удалить)\b'),
        ('complete',r'^(?:заверши|выполнил|отметь)\b'),
        ('update',r'^(?:перенеси|перенести|измени|переименуй)\b'),
        ('search',r'^(?:найди|найти)\b|^поиск\s*:|^когда\b.*\b(?:был\w*|встреч\w*|созвон\w*)\b'),
        ('list',r'^(?:что у меня|какие планы|что.*запланировано|какие задачи|покажи (?:(?:мои|мне|все) )?(?:планы|задачи|встречи|напоминания|расписание|сегодня|завтра|следующую неделю)|есть ли у меня|планы на|расписание(?: на)?|список (?:задач|встреч|напоминаний)|встречи (?:сегодня|завтра))\b'),
        ('create',r'^(?:добавь|создай|создать|поставь|напомни|запланируй|нужно|надо|купить|отправить|позвонить|займусь)\b|^(?:в\s+\S+\s+)?(?:сегодня|завтра|послезавтра|в пятницу|в понедельник)\b.*\b(?:нужно|надо|встреча|созвон|купить|отправить|позвонить)\b'),
    ):
        if re.search(pattern,s):return action
    return None

def _conversation(text,st):
    """Cheap social replies and deterministic refusals, also during confirmation."""
    s=re.sub(r'\s+',' ',text.lower().replace('ё','е')).strip(' \t\n.,!?🙂👋')
    help_text=('Я — Орбита. Помогу создать задачу, встречу или напоминание, показать планы, найти, изменить или удалить запись. '
               'Например: «Напомни завтра в 10 позвонить Ивану» или «Что у меня сегодня?»')
    msg=None
    if re.fullmatch(r'(?:привет(?:ик)?|здравствуй(?:те)?|доброе утро|добрый (?:день|вечер)|хай|hello|hi)(?:[, ]+орбита)?',s):
        msg='Привет! Что запишем?'
    elif re.fullmatch(r'(?:спасибо(?: большое| тебе)?|благодарю|спс|thanks|thank you|круто|отлично|супер|понятно|ок(?:ей)?|хорошо)',s):
        msg='Пожалуйста.'
    elif re.fullmatch(r'(?:/?help|/?start|помощь|помоги|что (?:ты )?(?:умеешь|можешь)(?: делать)?|как (?:тобой пользоваться|ты работаешь)|кто ты|что ты такое)',s):
        msg=help_text
    elif re.fullmatch(r'(?:как (?:твои )?дела|как ты|что нового|пока|до свидания|доброй ночи)',s):
        msg='Всё в порядке. Что нужно записать или найти?'
    elif re.match(r'^(?:расскажи (?:анекдот|шутку|сказку)|какая погода|сколько будет|кто (?:президент|выиграл)|напиши (?:стих|код))\b',s):
        msg='Здесь я могу помочь с задачами, встречами и напоминаниями.'
    # Examine unquoted instruction text, not the title of a task to remember.
    command=unquoted(s)
    if msg is None and re.search(r'\b(?:отправь|отправить|отошли|пошли|разошли)\b.*\b(?:письм\w*|email|e-mail|сообщени\w*)|\b(?:позвони|закажи|забронируй|оплати|скачай)\b|\b(?:синхронизируй|подключи|экспортируй|импортируй)\b.*\b(?:календар\w*|google|outlook)\b',command) and _intent(text)!='create':
        msg='Я пока не выполняю внешние действия: не отправляю письма, не звоню и не управляю внешними календарями. Могу сохранить задачу или напоминание об этом.'
    if msg is None and re.search(r'\b(?:удали|очисти|заверши|перенеси)\b.*\b(?:все|всё|весь|всю|всех)\b',command):
        msg='Массовые изменения пока не поддерживаются. Укажи одну конкретную запись.'
    instructions=re.sub(r'^\s*привет\s*[,!]\s*','',unquoted(text).lower())
    if msg is None and (re.search(r'(?:\b(?:и|а также|затем|потом)\s+|[,;.!?]\s*|\n\s*)(?:добавь|создай|поставь|напомни|запланируй|удали|удалить|измени|переименуй|перенеси|заверши|отметь|покажи|найди|купить|позвонить|отправить|подготовить)\b',instructions) or re.search(r'\b(?:создай|добавь)\s+(?:две|два|три|несколько|\d+)\s+(?:задач\w*|встреч\w*|напоминани\w*)',command)):
        msg='Вижу несколько действий. Отправь каждое отдельным сообщением, чтобы я сохранил их правильно.'
    if msg is not None:
        if st.get('confirmation'):msg+=' Незавершённое действие сохранено. Подтверди его словом «да» или напиши «отмена».'
        elif st.get('choices'):msg+=' Продолжим выбор записи? Укажи её ID или напиши «отмена».'
        elif st.get('pending'):
            p=st['pending']
            field='новое название' if p.get('_field')=='title' else 'конкретный день' if p.get('_needs_day') or not p.get('date') else 'время' if not p.get('time') else 'недостающие параметры'
            msg+=' Незавершённое действие сохранено. Продолжим? Укажи '+field+' или напиши «отмена».'
        return {'action':'reply','reply':msg}
    return None

def validate(data, current=None):
    e=dict(current or {'title':'','kind':'task','date':None,'time':None,'duration':60,'status':'active','timezone':'Europe/Moscow'})
    for k in ('title','kind','date','time','duration','status','timezone'):
        if k in data:e[k]=data[k]
    if not isinstance(e['title'],str) or not e['title'].strip():raise ValueError('Как назвать запись?')
    e['title']=e['title'].strip()
    if len(e['title'])>500:raise ValueError('Название должно быть короче 500 символов.')
    if e['kind'] not in KINDS:raise ValueError('Неизвестный тип записи.')
    if e['status'] not in ('active','done'):raise ValueError('Некорректный статус.')
    if e['date']:
        if not isinstance(e['date'],str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',e['date']):raise ValueError('Дата должна быть в формате ГГГГ-ММ-ДД.')
        datetime.strptime(e['date'],'%Y-%m-%d')
    if e['time']:
        if not isinstance(e['time'],str) or not re.fullmatch(r'\d{2}:\d{2}',e['time']):raise ValueError('Время должно быть в формате ЧЧ:ММ.')
        datetime.strptime(e['time'],'%H:%M')
        if not e['date']:raise ValueError('На какую дату поставить запись?')
    if e['kind'] in ('meeting','reminder'):
        if not e['date']:raise ValueError('На какую дату поставить '+('встречу' if e['kind']=='meeting' else 'напоминание')+'?')
        if not e['time']:raise ValueError('На какое время поставить '+('встречу' if e['kind']=='meeting' else 'напоминание')+'?')
    if isinstance(e['duration'],bool) or not isinstance(e['duration'],int) or not 5<=e['duration']<=1440:raise ValueError('Длительность должна быть от 5 до 1440 минут.')
    try: ZoneInfo(e['timezone'])
    except Exception:raise ValueError('Неизвестный часовой пояс.')
    if e.get('date') and e.get('time'):
        naive=datetime.fromisoformat(e['date']+'T'+e['time']);zone=ZoneInfo(e['timezone']);aware=naive.replace(tzinfo=zone)
        if aware.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None)!=naive:
            raise ValueError('Такого локального времени нет из-за перевода часов. Выбери другое время.')
        if aware.utcoffset()!=naive.replace(tzinfo=zone,fold=1).utcoffset():
            raise ValueError('Это время неоднозначно из-за перевода часов. Выбери время после перехода.')
    return e

def local_plan(text,now,state):
    s=unquoted(text).lower().replace('ё','е')
    s=re.sub(r'^(?:(?:привет|орбита|пожалуйста|давай|можешь|можете|можно|ты можешь)[,!\s]+)+','',s)
    renaming=bool(re.match(r'^(?:пожалуйста[, ]+)?переименуй\b',s)) or state.get('pending',{}).get('_field')=='title' and _intent(text) is None
    d=resolve('' if renaming else text,now)
    if state.get('pending') and state['pending'].get('action') in ('create','update') and _intent(text) is None and not re.search(NEW_INTENT,s):
        p=dict(state['pending'])
        if p.get('_field')=='title':
            p['title']=text.strip().strip('«»"“”');p.pop('_field',None)
            return p
        if d['date']:p['date']=d['date']
        if p.get('_period') and not re.search(r'\d|сегодня|завтра|вчера|недел',s):
            for i,w in enumerate(WEEKDAYS):
                if w in s:
                    start=datetime.fromisoformat(p['_period'][0]).date()
                    p['date']=(start+timedelta(days=(i-start.weekday())%7)).isoformat()
                    break
        if d['time']:p['time']=d['time']
        if d['date']:p.pop('_needs_day',None)
        if not p.get('title') and p['action']=='create':p['title']=title_from(text)
        return p
    mutation=re.match(r'^(?:пожалуйста[, ]+)?(перенеси|перенести|измени|переименуй|удали|удалить|заверши|выполнил|отметь)',s)
    if mutation:
        verb=mutation[1];action='delete' if verb.startswith('удал') else 'complete' if verb in ('заверши','выполнил','отметь') else 'update'
        p={'action':action}
        if re.search(r'\b(ее|его|это|эту|последн\w*)\b',s) and state.get('last_id'):p['id']=state['last_id']
        else:
            q=re.sub(r'^\S+\s*','',s)
            q=re.split(r'\s+на\s+(?:завтра|сегодня|послезавтра|\d|пятниц|понедельник|вторник|сред|четверг|суббот|воскресень)',q)[0]
            p['query']=q
        if d['date']:p['date']=d['date']
        if d['time']:p['time']=d['time']
        if verb=='переименуй':
            match=re.search(r'\s+в\s+[«"]?(.+?)[»"]?$',text)
            p.pop('date',None);p.pop('time',None)
            if match:p['title']=match[1].strip('«»"“”')
            else:p['_field']='title'
        return p
    intent=_intent(text)
    if intent=='search':return {'action':'search','query':text}
    if intent=='list':
        start,end=d['period'] or (d['date'],d['date'])
        if not start:
            return {'action':'clarify','question':'За какой период показать планы: сегодня, завтра или на этой неделе?'}
        p={'action':'list','start':start,'end':end}
        if 'встреч' in s:p['kind']='meeting'
        elif 'задач' in s:p['kind']='task'
        return p
    if re.search(RECURRING,s):
        return {'action':'clarify','question':'Повторяющиеся события пока не поддерживаются. Можно создать одну запись на конкретную дату.'}
    if intent=='create':
        kind='meeting' if re.search(r'встреч|созвон|демо|планер',s) else 'reminder' if re.search(r'напомни|напоминани',s) else 'task'
        title_text=re.sub(r'^(?:(?:привет|орбита|пожалуйста|давай|можешь|можете|можно|ты можешь)[,!\s]+)+','',text,flags=re.I)
        title_text=re.sub(r'^(?:запиши(?:те)?|(?:я )?(?:хочу|хотел(?:а)? бы|мне нужно|мне надо)(?:\s+(?:добавить|создать|записать))?|у меня)\s+','',title_text,flags=re.I)
        title_text=re.sub(r'^(?:задачу|напоминание)\s+','',title_text,flags=re.I)
        p={'action':'create','title':title_from(title_text),'kind':kind,'date':d['date'],'time':d['time']}
        dur=re.search(r'на\s+(\d+)\s*(минут|час)',s)
        if dur:p['duration']=int(dur[1])*(60 if dur[2]=='час' else 1)
        return p
    return {'action':'clarify','question':'Что нужно сделать с планами? Можно создать задачу, встречу или напоминание, показать планы или найти запись. Например: «Напомни завтра в 10 позвонить Ивану».'}

def _model_plan(raw):
    """Defense at the executor boundary, including mocked/custom adapters."""
    if not isinstance(raw,dict) or not isinstance(raw.get('action'),str):raise ValueError('Некорректный план.')
    if raw['action'] not in ('create','update','complete','delete','list','search','clarify','reply'):raise ValueError('Неизвестное действие.')
    allowed={'action','title','kind','date','time','duration','start','end','query','id','question','status','reply'}
    p={k:v for k,v in raw.items() if k in allowed}
    for k in ('title','kind','date','time','start','end','query','question','status','reply'):
        if p.get(k) is not None and not isinstance(p[k],str):raise ValueError('Некорректное поле '+k)
    for k in ('id','duration'):
        if k in p and type(p[k]) is not int:raise ValueError('Некорректное поле '+k)
    return p

def _make_plan(text,now,tz,st):
    s=unquoted(text).lower().replace('ё','е')
    conversational=_conversation(text,st)
    if conversational:return conversational,'local',None
    if re.search(RECURRING,s):
        return {'action':'clarify','question':'Повторяющиеся события пока не поддерживаются. Можно создать одну запись на конкретную дату.'},'local',None
    # A new title must not accidentally reschedule an existing record.
    renaming=bool(re.match(r'^(?:пожалуйста[, ]+)?переименуй\b',s)) or st.get('pending',{}).get('_field')=='title' and _intent(text) is None
    try:d=resolve('' if renaming else text,now)
    except (ValueError,OverflowError):
        return {'action':'clarify','question':'Укажите корректную дату и время.'},'local',None
    mode='local';notice=None
    intent=_intent(text)
    pending=st.get('pending',{})
    continuation=bool(pending) and intent is None and not re.search(NEW_INTENT,s)
    # Only a requested field is a continuation. Chatter must not save a draft.
    remainder=title_from(text).lower().strip(' .,!?:')
    remainder=re.sub(r'\b(?:пожалуйста|давай|в|к|на|утра|дня|вечера|ночи|час\w*|один|одну|два|две|три|четыре|пять|шесть|семь|восемь|девять|десять|одиннадцать|двенадцать)\b','',remainder).strip()
    remainder=re.sub(r'\b(?:'+ '|'.join(WEEKDAYS)+r')\w*\b','',remainder).strip()
    if continuation and (pending.get('_field')!='title' and (not (d['date'] or d['time'] or d['period']) or remainder) or pending.get('_field')=='title' and re.match(r'^(?:как|почему|зачем|кто|что)\b',s)):
        return {'action':'clarify','question':'Незавершённое действие сохранено. Укажи дату или время, либо напиши «отмена».'},'local',None
    if not continuation and intent is None and not re.search(r'\b(?:задач\w*|встреч\w*|напоминани\w*|план\w*|запис\w*|расписани\w*|созда\w*|удал\w*)\b',s):
        return local_plan(text,now,{}),'local',None
    if config.API_KEY and store.account()['use_ai'] and not continuation:
        try:
            store.reserve_ai_request()
            p=_model_plan(ai.plan(text,now,tz,st));mode='llm'
            actual=getattr(ai,'last_model',None)
            actual=actual.get() if actual is not None else None
            if actual and actual!=config.MODEL:notice='Запрос обработан резервной моделью: '+actual+'.'
        except Exception:
            p=local_plan(text,now,st);notice='LLM недоступна. Запрос обработан локально; сложную формулировку лучше упростить.'
    else:p=local_plan(text,now,st)
    # A model must not turn a dated list into an unbounded text search.
    if intent=='list' and p.get('action')=='search' and (d['date'] or d['period']):
        p=local_plan(text,now,{})
    elif intent=='search' and p.get('action')=='list':
        p={'action':'search','query':text}
    if p.get('action') not in ('reply','clarify') and not continuation and p.get('action')!=intent and not (intent in ('list','search') and p.get('action') in ('list','search')):
        p={'action':'clarify','question':'Уточни намерение: нужно создать запись, показать планы, найти или изменить существующую запись?'}
    if continuation and st.get('pending',{}).get('_period') and not re.search(r'\d|сегодня|завтра|вчера|недел',s):
        d['date']=p.get('date')
    if d['period'] and not d['date'] and p.get('action')=='clarify':
        local=local_plan(text,now,st)
        if local.get('action')=='create':p=local
    if p.get('action') in ('create','update'):
        for k in ('date','time'):
            if d[k] is not None:p[k]=d[k]
        if p['action']=='create':
            if d['period'] and not d['date']:
                p['date']=None;p['_needs_day']=True;p['_period']=d['period']
            elif d['date']:p.pop('_needs_day',None)
            if QUOTED.search(text) and not renaming:
                # Do not let a model treat a quoted calendar title as a deadline.
                quoted=' '.join(m[0][1:-1] for m in QUOTED.finditer(text))
                try:qd=resolve(quoted,now)
                except (ValueError,OverflowError):qd={'date':None,'time':None,'period':None}
                if (qd['date'] or qd['period']) and d['date'] is None and d['period'] is None:p.pop('date',None)
                if qd['time'] and d['time'] is None:p.pop('time',None)
    elif p.get('action')=='list':
        if d['period']:p['start'],p['end']=d['period']
        elif d['date']:p['start']=p['end']=d['date']
    return p,mode,notice

def prepare_chat(text,sid,tz='Europe/Moscow',now_value=None):
    """Plan outside the caller's write transaction; chat verifies the snapshot."""
    if store.TX.get() is not None:raise RuntimeError('prepare_chat must run outside a write transaction.')
    now=clock(tz,now_value);st,revision=store.state_snapshot(sid)
    # Confirmation/cancel/choice handling is deterministic and needs no LLM.
    deterministic=(text.lower().strip(' .!') in ('отмена','отмени','нет','не надо') or st.get('confirmation') or
                   st.get('choices') and re.fullmatch(r'\s*#?\d+\s*',text))
    p,mode,notice=(None,'local',None) if deterministic else _make_plan(text,now,tz,st)
    actual=getattr(ai,'last_model',None)
    return {'text':text,'sid':sid,'tz':tz,'owner':owner(),'now':now.isoformat(),'state':st,'state_revision':revision,'plan':p,'mode':mode,'notice':notice,
            'model':actual.get() if mode=='llm' and actual is not None else None}

def label(e):
    date_text='без даты'
    if e.get('date'):
        day=datetime.fromisoformat(e['date'])
        months=('января','февраля','марта','апреля','мая','июня','июля','августа','сентября','октября','ноября','декабря')
        date_text=f'{day.day} {months[day.month-1]} {day.year}'
    return f'«{e["title"]}» — {date_text}'+(f' в {e["time"]}' if e.get('time') else '')

def dialog_actions(sid):
    """Rebuild safe chat shortcuts from persisted, owner-scoped dialog state."""
    st,revision=store.state_snapshot(sid)
    if st.get('confirmation'):
        actions=[{'label':'Подтвердить','text':'да'},{'label':'Отменить','text':'отмена'}]
    elif st.get('choices'):
        actions=[{'label':'Выбрать #'+str(i),'text':'#'+str(i)} for i in st['choices']['ids']]+[{'label':'Отменить','text':'отмена'}]
    elif st.get('pending'):actions=[{'label':'Отменить','text':'отмена'}]
    else:actions=[]
    return [dict(a,revision=revision) for a in actions]

def chat(text,sid,tz='Europe/Moscow',now_value=None,prepared=None):
    now=clock(tz,prepared['now'] if prepared is not None else now_value);st,revision=store.state_snapshot(sid);mode='local';notice=None;model=None
    if prepared is not None and (prepared['text']!=text or prepared['sid']!=sid or prepared['tz']!=tz or prepared['owner']!=owner() or prepared['state']!=st or prepared['state_revision']!=revision):
        return dict(reply='Диалог изменился в другом окне. Повтори запрос.',events=[],actions=dialog_actions(sid),mode='local',notice=None,model=None,changed=False,code='stale_dialog')
    store.message(sid,'user',text)
    def reply(msg,events=None,**extras):
        store.state(sid,st);store.message(sid,'assistant',msg)
        return dict(reply=msg,events=events or [],actions=dialog_actions(sid),mode=mode,notice=notice,model=model,**extras)
    if text.lower().strip(' .!') in ('отмена','отмени','нет','не надо'):
        st.pop('pending',None);st.pop('confirmation',None);st.pop('choices',None);st.pop('voice_text',None);st.pop('voice_nonce',None)
        return reply('Хорошо, действие отменено.')
    conversational=_conversation(text,st)
    if conversational:return reply(conversational['reply'])
    if st.get('confirmation'):
        if text.lower().strip(' .!') not in ('да','подтверждаю','давай','удали','все равно добавь'):
            return reply('Ответь «да», чтобы подтвердить, или «отмена».')
        p=st.pop('confirmation')
    elif st.get('choices') and re.fullmatch(r'\s*#?\d+\s*',text):
        i=int(text.strip().lstrip('#'))
        c=st['choices']
        if i not in c['ids']:return reply('Выбери ID из предложенного списка или напиши «отмена».')
        p=c['plan'];p['id']=i;st.pop('choices',None)
    else:
        if prepared is not None:
            p=dict(prepared['plan']);mode=prepared['mode'];notice=prepared['notice'];model=prepared['model']
        else:
            p,mode,notice=_make_plan(text,now,tz,st)
            actual=getattr(ai,'last_model',None)
            model=actual.get() if mode=='llm' and actual is not None else None
    action=p.get('action')
    if action=='clarify':return reply(str(p.get('question') or 'Уточни, что нужно сделать.')[:1000])
    if action=='reply':return reply(str(p.get('reply') or p.get('question') or 'Чем помочь с планами?')[:1000])
    st.pop('choices',None)
    if action in ('list','search'):
        st.pop('pending',None)
        if action=='list':
            for k in ('start','end'):
                if p.get(k):datetime.strptime(p[k],'%Y-%m-%d')
            rows,total=store.list_events(p.get('start'),p.get('end'),p.get('kind') if p.get('kind') in KINDS else None,'active',100)
            msg=f'Планы за период {p.get("start") or "любой"} — {p.get("end") or "любой"}.'
        else:
            rows,total=store.search(str(p.get('query') or text),p.get('kind') if p.get('kind') in KINDS else None)
            msg='Вот что удалось найти.'
        if not total:
            if action=='search':msg='Не нашла запись по «'+str(p.get('query') or text)[:200]+'». Попробуй часть названия или другое слово.'
            else:
                today=now.date().isoformat();tomorrow=(now.date()+timedelta(days=1)).isoformat()
                period='Сегодня' if p.get('start')==p.get('end')==today else 'Завтра' if p.get('start')==p.get('end')==tomorrow else 'За период с '+str(p.get('start') or 'начала')+' по '+str(p.get('end') or 'сегодня')
                msg=period+': подходящих записей нет. Можно выбрать другой период.'
        else:msg+=f' Найдено: {total}.'
        if total>len(rows):msg+=f' Показаны первые {len(rows)}; уточни запрос.'
        if len(rows)==1:st['last_id']=rows[0]['id']
        return reply(msg,rows,total=total)
    if action not in ('create','update','complete','delete'):return reply('Не удалось определить действие. Попробуй переформулировать запрос.')
    current=None
    if action!='create':
        i=p.get('id')
        if i is None:
            rows,total=store.search(str(p.get('query') or text))
            if not total:return reply('Не нашёл запись для изменения. Укажи часть названия.')
            if total!=1:
                st['choices']={'plan':p,'ids':[r['id'] for r in rows]}
                return reply('Нашлось несколько записей. Выбери нужную ниже или напиши её номер.',rows)
            i=rows[0]['id'];p['id']=i
        if isinstance(i,bool) or not isinstance(i,int):return reply('Некорректный ID записи.')
        current=store.get_event(i)
        if not current:return reply('Запись уже удалена или не существует.')
    if action=='delete':
        if not p.get('_confirmed') or p.get('_revision')!=current['revision']:
            p['_confirmed']=True;p['_revision']=current['revision'];st['confirmation']=p
            return reply('Удалить '+label(current)+'? Ответь «да» или «отмена».',[current])
        if not store.delete(current['id'],revision=p['_revision']):return reply('Запись изменилась. Повтори запрос удаления.')
        st.pop('last_id',None);st.pop('pending',None)
        return reply('Запись удалена.')
    if action=='complete':
        current['status']='done';e=store.save(current,current['id']);st['last_id']=e['id']
        st.pop('pending',None)
        return reply('Готово, отмечено выполненным: '+label(e),[e],changed=True)
    if action=='update' and not any(k in p for k in ('title','date','time','duration','kind','status')):
        st['pending']=p
        return reply('Какое новое название?' if p.get('_field')=='title' else 'Что изменить? Например, укажи новую дату или время. Можно написать «отмена».')
    if action=='create' and p.get('_needs_day'):
        st['pending']=p
        return reply('Укажи конкретный день для записи. Название и остальные параметры сохранены; можно ответить коротко или написать «отмена».')
    p['timezone']=current['timezone'] if current else tz
    try:e=validate(p,current)
    except (ValueError,TypeError) as err:
        if action=='create' and not str(p.get('title') or '').strip():p['_field']='title'
        st['pending']=p
        return reply(str(err)+' Можно ответить коротко или написать «отмена».')
    collisions=store.conflicts(e,current['id'] if current else None)
    if collisions and not p.get('_confirmed'):
        p['_confirmed']=True;st['confirmation']=p;st.pop('pending',None)
        return reply('В это время есть пересечение с другой встречей. Всё равно сохранить? Ответь «да» или «отмена».',collisions)
    saved=store.save(e,current['id'] if current else None)
    st.pop('pending',None);st['last_id']=saved['id']
    msg=('Сохранено: ' if action=='create' else 'Изменено: ')+label(saved)+'.'
    if e.get('date') and datetime.fromisoformat(e['date']+'T'+(e.get('time') or '23:59')).replace(tzinfo=ZoneInfo(e['timezone']))<now:msg+=' Обрати внимание: дата или время уже в прошлом.'
    if e['kind']=='reminder':
        from .notifications import health
        if owner().startswith('tg:') and store.account()['chat_id'] and store.account()['notify'] and health():msg+=' Доставка в Telegram включена. Для отправки бот должен работать.'
        else:msg+=' Для уведомления подключи Telegram в настройках. В браузере push-уведомлений нет.'
    return reply(msg,[saved],changed=True)
