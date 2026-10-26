"""Predictable Russian date resolution. Monday-based weeks; aware client-local time."""
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
WEEKDAYS = ['понедельник', 'вторник', 'сред', 'четверг', 'пятниц', 'суббот', 'воскресень']
NUMBERS = {'один':1,'одну':1,'два':2,'две':2,'три':3,'четыре':4,'пять':5,'шесть':6,'семь':7,'восемь':8,'девять':9,'десять':10,'одиннадцать':11,'двенадцать':12}
MONTHS = {name:i for i,name in enumerate(('января','февраля','марта','апреля','мая','июня','июля','августа','сентября','октября','ноября','декабря'),1)}
QUOTED = re.compile(r'«[^»]*»|“[^”]*”|"[^"\n]*"|‘[^’]*’|(?<!\w)\'[^\'\n]+\'(?!\w)')

def unquoted(text):
    """Calendar words inside a quoted title are data, not instructions."""
    return QUOTED.sub(lambda m: ' ' * len(m[0]), text)

def clock(tz='Europe/Moscow', value=None):
    zone = ZoneInfo(tz)
    if value:
        n = datetime.fromisoformat(value)
        return n.replace(tzinfo=zone) if n.tzinfo is None else n.astimezone(zone)
    return datetime.now(zone)

def resolve(text, now):
    s = unquoted(text).lower().replace('ё','е')
    day, tm, period = None, None, None
    rel = re.search(r'через\s+(\d+|'+ '|'.join(NUMBERS) + r')\s*(минут\w*|час\w*|дн\w*|день|недел\w*)',s)
    if rel:
        n = int(rel[1]) if rel[1].isdigit() else NUMBERS[rel[1]]
        unit = rel[2]
        delta = timedelta(minutes=n) if unit.startswith('минут') else timedelta(hours=n) if unit.startswith('час') else timedelta(weeks=n) if unit.startswith('недел') else timedelta(days=n)
        target = ((now.astimezone(timezone.utc) + delta).astimezone(now.tzinfo)
                  if unit.startswith(('минут','час')) else now + delta)
        day = target.date()
        if unit.startswith(('минут','час')): tm = target.strftime('%H:%M')
    if 'послезавтра' in s: day = (now+timedelta(days=2)).date()
    elif 'завтра' in s: day = (now+timedelta(days=1)).date()
    elif 'позавчера' in s: day = (now-timedelta(days=2)).date()
    elif 'вчера' in s: day = (now-timedelta(days=1)).date()
    elif 'сегодня' in s: day = now.date()
    explicit = re.search(r'\b(20\d{2}-\d{2}-\d{2})\b',s)
    if explicit: day = datetime.strptime(explicit[1],'%Y-%m-%d').date()
    dotted = re.search(r'\b(\d{1,2})\.(\d{1,2})(?:\.(20\d{2}))?\b',s)
    if dotted: day = datetime(int(dotted[3] or now.year),int(dotted[2]),int(dotted[1])).date()
    named = re.search(r'\b(\d{1,2})\s+('+ '|'.join(MONTHS)+r')\b(?:\s+(20\d{2})\b)?',s)
    if named: day = datetime(int(named[3] or now.year),MONTHS[named[2]],int(named[1])).date()
    if not explicit and not dotted and not named:
        for i, w in enumerate(WEEKDAYS):
            if w in s:
                diff = (i-now.weekday()) % 7
                if re.search(r'следующ\w*\s+'+w,s): diff = (7-now.weekday())+i
                day = (now+timedelta(days=diff)).date()
                break
    if 'недел' in s and not rel:
        monday = now.date()-timedelta(days=now.weekday())
        if 'следующ' in s: monday += timedelta(days=7)
        elif 'прошл' in s: monday -= timedelta(days=7)
        period = (monday.isoformat(),(monday+timedelta(days=6)).isoformat())
        if not explicit and not dotted and not named:
            for i, w in enumerate(WEEKDAYS):
                if w in s:
                    day = monday + timedelta(days=i)
                    break
        if 'конц' in s: day = monday+timedelta(days=6)
    if 'месяц' in s:
        first=now.date().replace(day=1)
        nxt=(first.replace(day=28)+timedelta(days=4)).replace(day=1)
        if 'следующ' in s:
            first=nxt; nxt=(first.replace(day=28)+timedelta(days=4)).replace(day=1)
        elif 'прошл' in s:
            nxt=first;first=(first-timedelta(days=1)).replace(day=1)
        period=(first.isoformat(),(nxt-timedelta(days=1)).isoformat())
    match = re.search(r'\b(?:в|к)\s*(\d{1,2})(?::(\d{2}))?(?:\s*(утра|вечера|дня|ночи|час\w*))?(?![\d.:])',s)
    if not match: match = re.search(r'\b(\d{1,2}):(\d{2})\b',s)
    if match:
        h=int(match[1]); m=int(match[2] or 0)
        suffix=match[3] if match.lastindex and match.lastindex>=3 else ''
        if suffix in ('вечера','дня') and h<12: h+=12
        if suffix=='ночи' and h==12: h=0
        if h>23 or m>59: raise ValueError('Укажите корректное время: от 00:00 до 23:59.')
        tm=f'{h:02}:{m:02}'
    else:
        wordtime = re.search(r'\bв\s+('+ '|'.join(sorted(NUMBERS,key=len,reverse=True))+r')\b\s*(утра|вечера|дня|ночи|час\w*)?',s)
        if wordtime:
            h=NUMBERS[wordtime[1]]
            if wordtime[2] in ('вечера','дня') and h<12:h+=12
            if wordtime[2]=='ночи' and h==12:h=0
            tm=f'{h:02}:00'
    if 'полдень' in s: tm='12:00'
    if 'полночь' in s: tm='00:00'
    return {'date':day.isoformat() if day else None,'time':tm,'period':period}

def title_from(text):
    quoted=[]
    def protect(match):
        quoted.append(match[0])
        return '\ue000'+str(len(quoted)-1)+'\ue001'
    s=QUOTED.sub(protect,text).strip().rstrip('.!?')
    s=re.sub(r'^(?:пожалуйста[, ]+)?(?:добавь|создай|поставь|запланируй|напомни(?:\s+мне)?)(?:\s+(?:задачу|напоминание))?\s*','',s,flags=re.I)
    s=re.sub(r'\b(?:послезавтра|завтра|сегодня)\b','',s,flags=re.I)
    s=re.sub(r'\b(?:в|к)\s*\d{1,2}(?::\d{2})?(?:\s*(?:утра|вечера|дня|ночи|час\w*))?(?![\d.:])','',s,flags=re.I)
    s=re.sub(r'\b\d{1,2}:\d{2}\b','',s)
    s=re.sub(r'\b(?:в|на)\s+(?:следующ\w*\s+)?(?:понедельник\w*|вторник\w*|сред\w*|четверг\w*|пятниц\w*|суббот\w*|воскресень\w*)','',s,flags=re.I)
    s=re.sub(r'\bчерез\s+(?:\d+|'+ '|'.join(NUMBERS)+r')\s*(?:минут\w*|час\w*|дн\w*|день|недел\w*)','',s,flags=re.I)
    s=re.sub(r'\bдо конца недели\b|\b20\d{2}-\d{2}-\d{2}\b|\b\d{1,2}\.\d{1,2}(?:\.20\d{2})?\b','',s,flags=re.I)
    s=re.sub(r'\b\d{1,2}\s+(?:'+ '|'.join(MONTHS)+r')\b(?:\s+20\d{2}\b)?','',s,flags=re.I)
    s=re.sub(r'\b(?:на|в|во)\s+(?:(?:следующ|эт|текущ|прошл)\w*\s+)?(?:недел\w*|месяц\w*)\b','',s,flags=re.I)
    s=re.sub(r'\bна\s+\d+\s*(?:минут\w*|час\w*)\b','',s,flags=re.I)
    s=re.sub(r'^встречу\b','Встреча',s,flags=re.I)
    s=re.sub(r'\s+',' ',s).strip(' ,—-')
    s=re.sub(r'\ue000(\d+)\ue001',lambda m:quoted[int(m[1])],s)
    return (s[0].upper()+s[1:])[:500] if s else ''
