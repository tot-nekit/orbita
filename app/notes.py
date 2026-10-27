"""Notes on boards: Markdown text, [[links]] between notes and attachments to plans.

Boards are ordered, named columns. A note lives on exactly one board, can link to
other notes with [[Title]] and can be attached to any number of the owner's plans.
Deleting is soft for ten minutes, like plans, so an accidental tap can be undone.
"""
import io
import json
import re
import unicodedata
import zipfile
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from . import store
from .context import owner

INBOX = 'Входящие'
DEFAULT_BOARDS = (INBOX, 'Идеи', 'Проекты')
COLORS = (None, 'yellow', 'green', 'blue', 'violet', 'rose')
MAX_NOTES, MAX_BOARDS, MAX_EVENTS = 2000, 30, 20
MAX_TITLE, MAX_BODY, MAX_BOARD = 200, 20000, 40
LINK = re.compile(r'\[\[([^\[\]|\n]{1,200})(?:\|[^\[\]\n]{0,200})?\]\]')
TAG = re.compile(r'(?<![\w#/&])#([^\W\d_][\w-]{0,39})')
TASK = re.compile(r'^\s*[-*] \[( |x|X)\]', re.M)


def _now():
    return store.utcnow()


@contextmanager
def _write():
    """Lock before reading revisions/limits, also for callers outside HTTP.

    A savepoint keeps a failed notes operation from leaking partial writes when
    an outer transaction catches the exception and continues.
    """
    with store.connect() as db:
        if not db.in_transaction:
            db.execute('BEGIN IMMEDIATE')
        db.execute('SAVEPOINT notes_write')
        token = store.TX.set(db)
        try:
            yield db
        except BaseException:
            db.execute('ROLLBACK TO notes_write')
            db.execute('RELEASE notes_write')
            raise
        else:
            db.execute('RELEASE notes_write')
        finally:
            store.TX.reset(token)


def _title_key(title):
    return ' '.join(title.split()).casefold()


def _title_index(rows):
    """Duplicate titles resolve to the oldest ID, independent of edits/pins."""
    result = {}
    for row in sorted(rows, key=lambda r: r['id']):
        result.setdefault(_title_key(row['title']), row['id'])
    return result


def _text(value, field, limit, required=True):
    if not isinstance(value, str):
        raise ValueError(field + ': ожидается текст.')
    if any(ord(c) < 32 for c in value):
        raise ValueError(field + ': уберите служебные символы.')
    value = ' '.join(value.split())
    if required and not value:
        raise ValueError(field + ': не может быть пустым.')
    if len(value) > limit:
        raise ValueError(f'{field}: не больше {limit} символов.')
    return value


def board_name(value):
    return _text(value, 'Название доски', MAX_BOARD)


def links(body):
    """Unique link targets in order of appearance."""
    seen, out = set(), []
    for match in LINK.finditer(body or ''):
        title = ' '.join(match.group(1).split())
        if title and _title_key(title) not in seen:
            seen.add(_title_key(title))
            out.append(title)
    return out


def tags(body):
    seen, out = set(), []
    for match in TAG.finditer(body or ''):
        tag = match.group(1).lower()
        if tag not in seen:
            seen.add(tag)
            out.append(tag)
    return out


def excerpt(body, limit=240):
    text = LINK.sub(lambda m: m.group(0)[2:-2].split('|')[-1], body or '')
    text = re.sub(r'^\s*[-*] \[[xX]\]\s*', '☑ ', text, flags=re.M)
    text = re.sub(r'^\s*[-*] \[ \]\s*', '☐ ', text, flags=re.M)
    text = re.sub(r'^\s*#{1,6}\s+', '', text, flags=re.M)
    text = re.sub(r'^\s*>\s?', '', text, flags=re.M)
    text = re.sub(r'(\*\*|__|`|~~)', '', text)
    text = ' '.join(text.split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + '…'


def checklist(body):
    marks = TASK.findall(body or '')
    return {'done': sum(m in 'xX' for m in marks), 'total': len(marks)}


def _boards(db):
    rows = db.execute('SELECT name FROM note_boards WHERE owner=? ORDER BY position,created_at,name', (owner(),)).fetchall()
    if not rows:
        stamp = _now()
        for i, name in enumerate(DEFAULT_BOARDS):
            db.execute('INSERT OR IGNORE INTO note_boards(owner,name,position,created_at) VALUES(?,?,?,?)', (owner(), name, i, stamp))
        rows = db.execute('SELECT name FROM note_boards WHERE owner=? ORDER BY position,created_at,name', (owner(),)).fetchall()
    return [r[0] for r in rows]


def _ensure_board(db, name):
    boards = _boards(db)
    match = next((b for b in boards if b.lower() == name.lower()), None)
    if match:
        return match
    if len(boards) >= MAX_BOARDS:
        raise ValueError(f'Можно создать до {MAX_BOARDS} досок.')
    db.execute('INSERT INTO note_boards(owner,name,position,created_at) VALUES(?,?,?,?)', (owner(), name, len(boards), _now()))
    return name


def _events(db, note_ids=None):
    sql = ('SELECT ne.note_id,e.id,e.title,e.kind,e.date,e.time,e.status FROM note_events ne '
           'JOIN events e ON e.id=ne.event_id AND e.owner=ne.owner AND e.deleted_at IS NULL WHERE ne.owner=?')
    args = [owner()]
    if note_ids is not None:
        sql += (' AND ne.note_id IN (' + ','.join('?' * len(note_ids)) + ')') if note_ids else ' AND 0'
        args += list(note_ids)
    result = {}
    for r in db.execute(sql + ' ORDER BY e.date IS NULL,e.date,e.time,e.id', args):
        result.setdefault(r['note_id'], []).append({k: r[k] for k in ('id', 'title', 'kind', 'date', 'time', 'status')})
    return result


def _summary(row, events):
    body = row['body']
    return {'id': row['id'], 'board': row['board'], 'title': row['title'], 'excerpt': excerpt(body),
            'tags': tags(body), 'links': len(links(body)), 'checklist': checklist(body),
            'pinned': bool(row['pinned']), 'color': row['color'], 'created_at': row['created_at'],
            'updated_at': row['updated_at'], 'revision': row['revision'], 'events': events.get(row['id'], [])}


def _rows(db):
    return [dict(r) for r in db.execute('SELECT * FROM notes WHERE owner=? AND deleted_at IS NULL ORDER BY pinned DESC,updated_at DESC,id DESC', (owner(),))]


def _graph(rows):
    by_title = _title_index(rows)
    picked = rows[:MAX_NOTES]
    ids = {r['id'] for r in picked}
    return {'nodes': [{k: r[k] for k in ('id', 'title', 'board')} for r in picked],
            'edges': [{'source': r['id'], 'target': by_title[_title_key(t)]}
                      for r in picked for t in links(r['body'])
                      if by_title.get(_title_key(t)) in ids]}


def _matches(row, words, phrase):
    title, body = store.normalize(row['title']), store.normalize(row['body'])
    if phrase and phrase in title:
        return 10
    title_words, body_words = title.split(), body.split()
    score = 0
    for word in words:
        if any(w.startswith(word) for w in title_words):
            score += 2
        elif any(w.startswith(word) for w in body_words):
            score += 1
        else:
            return 0
    return score + (1 if phrase and phrase in body else 0)


def listing(q=None, board=None, event_id=None, tag=None):
    with store.connect() as db:
        boards = _boards(db)
        if not db.in_transaction:
            db.execute('BEGIN')
        rows = _rows(db)
        events = _events(db)
    counts = {}
    tag_counts = {}
    for row in rows:
        counts[row['board']] = counts.get(row['board'], 0) + 1
        for t in tags(row['body']):
            tag_counts[t] = tag_counts.get(t, 0) + 1
    picked = rows
    if board:
        picked = [r for r in picked if r['board'] == board]
    if event_id is not None:
        picked = [r for r in picked if any(e['id'] == event_id for e in events.get(r['id'], []))]
    if tag:
        tag = tag.lstrip('#').lower()
        picked = [r for r in picked if tag in tags(r['body'])]
    query = (q or '').strip()
    if query.startswith('#') and len(query) > 1 and ' ' not in query:
        wanted = query[1:].lower()
        picked = [r for r in picked if any(t.startswith(wanted) for t in tags(r['body']))]
    elif query:
        phrase = store.normalize(query)
        words = [w for w in phrase.split() if len(w) >= 2] or phrase.split()
        scored = [(_matches(r, words, phrase), r) for r in picked]
        picked = [r for s, r in sorted(scored, key=lambda x: -x[0]) if s > 0]
    return {'boards': [{'name': b, 'count': counts.get(b, 0)} for b in boards],
            'notes': [_summary(r, events) for r in picked], 'total': len(picked),
            'tags': [{'tag': t, 'count': n} for t, n in sorted(tag_counts.items(), key=lambda x: (-x[1], x[0]))[:30]],
            'titles': [{'id': r['id'], 'title': r['title']} for r in rows], 'graph': _graph(rows)}


def get(note_id):
    with store.connect() as db:
        if not db.in_transaction:
            db.execute('BEGIN')
        row = db.execute('SELECT * FROM notes WHERE owner=? AND id=? AND deleted_at IS NULL', (owner(), note_id)).fetchone()
        if not row:
            return None
        row = dict(row)
        others = [dict(r) for r in db.execute('SELECT id,title,board,body FROM notes WHERE owner=? AND deleted_at IS NULL AND id<>? ORDER BY updated_at DESC,id DESC', (owner(), note_id))]
        events = _events(db, [note_id]).get(note_id, [])
    by_title = _title_index([row] + others)
    note = dict(row)
    note.pop('owner', None)
    note.pop('deleted_at', None)
    note['pinned'] = bool(note['pinned'])
    note.update(tags=tags(row['body']), checklist=checklist(row['body']), events=events,
                outgoing=[{'title': t, 'id': by_title.get(_title_key(t))} for t in links(row['body'])],
                backlinks=[{'id': o['id'], 'title': o['title'], 'board': o['board']} for o in others
                           if any(by_title.get(_title_key(t)) == note_id for t in links(o['body']))])
    return note


def _bool(value, field):
    if type(value) is not bool:
        raise ValueError(field + ' должен быть true/false.')
    return value


def _id(value, field='ID'):
    if type(value) is not int or not 0 < value < 2 ** 63:
        raise ValueError('Некорректный ' + field + '.')
    return value


def _attach(db, note_id, event_id, attached=True):
    if not attached:
        # Hidden/deleted plans must still be detachable; this cannot expose them.
        db.execute('DELETE FROM note_events WHERE owner=? AND note_id=? AND event_id=?', (owner(), note_id, event_id))
        return
    if not db.execute('SELECT 1 FROM events WHERE owner=? AND id=? AND deleted_at IS NULL', (owner(), event_id)).fetchone():
        raise LookupError('План не найден.')
    if db.execute('SELECT 1 FROM note_events WHERE owner=? AND note_id=? AND event_id=?', (owner(), note_id, event_id)).fetchone():
        return
    linked = db.execute('SELECT COUNT(*) FROM note_events ne JOIN events e ON e.id=ne.event_id AND e.owner=ne.owner '
                        'AND e.deleted_at IS NULL WHERE ne.owner=? AND ne.note_id=?', (owner(), note_id)).fetchone()[0]
    if linked >= MAX_EVENTS:
        raise ValueError(f'К заметке можно прикрепить до {MAX_EVENTS} планов.')
    db.execute('INSERT INTO note_events(owner,note_id,event_id,created_at) VALUES(?,?,?,?)', (owner(), note_id, event_id, _now()))


def _rename_links(db, old, new, edited_id):
    def replace(match):
        if _title_key(match.group(1)) != _title_key(old):
            return match.group(0)
        alias = match.group(0)[2:-2].partition('|')[1:]
        return '[[' + new + ''.join(alias) + ']]'

    # Include trash so undo does not bring back links to the previous title.
    for row in db.execute('SELECT id,body FROM notes WHERE owner=? AND body LIKE ?', (owner(), '%[[%')).fetchall():
        body = LINK.sub(replace, row['body'])
        if body != row['body']:
            if len(body) > MAX_BODY:
                raise ValueError(f'После замены ссылок заметка превысит {MAX_BODY} символов.')
            db.execute('UPDATE notes SET body=?,updated_at=?,revision=revision+? WHERE owner=? AND id=?',
                       (body, _now(), int(row['id'] != edited_id), owner(), row['id']))


def save(data):
    note_id = data.get('id')
    current = None
    with _write() as db:
        if note_id is not None:
            _id(note_id)
            current = db.execute('SELECT * FROM notes WHERE owner=? AND id=? AND deleted_at IS NULL', (owner(), note_id)).fetchone()
            if not current:
                return {'error': 'Заметка не найдена.'}, 404
            current = dict(current)
            if data.get('revision') is not None and _id(data['revision'], 'номер версии') != current['revision']:
                return {'error': 'Заметка уже изменилась в другом окне.', 'code': 'stale', 'note': get(note_id)}, 409
        body = data.get('body', current['body'] if current else '')
        if not isinstance(body, str):
            raise ValueError('Текст заметки: ожидается текст.')
        body = body.replace('\r\n', '\n').replace('\r', '\n')
        if len(body) > MAX_BODY:
            raise ValueError(f'Заметка: не больше {MAX_BODY} символов.')
        raw_title = data.get('title', current['title'] if current else '')
        if isinstance(raw_title, str) and not raw_title.strip():
            first = next((line for line in body.splitlines() if line.strip()), '')
            raw_title = re.sub(r'^[#>*\-\s\[\]xX]+', '', first).strip()[:80] or 'Без названия'
        title = _text(raw_title, 'Название заметки', MAX_TITLE)
        board = _ensure_board(db, board_name(data.get('board', current['board'] if current else INBOX)))
        pinned = _bool(data.get('pinned', bool(current['pinned']) if current else False), 'pinned')
        color = data.get('color', current['color'] if current else None)
        if color not in COLORS:
            raise ValueError('Неизвестный цвет заметки.')
        stamp = _now()
        if current is None:
            if db.execute('SELECT COUNT(*) FROM notes WHERE owner=? AND deleted_at IS NULL', (owner(),)).fetchone()[0] >= MAX_NOTES:
                raise ValueError(f'Можно хранить до {MAX_NOTES} заметок.')
            note_id = db.execute('INSERT INTO notes(owner,board,title,body,pinned,color,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)',
                                 (owner(), board, title, body, int(pinned), color, stamp, stamp)).lastrowid
            if data.get('event_id') is not None:
                try:
                    _attach(db, note_id, _id(data['event_id'], 'ID плана'))
                except LookupError as exc:
                    raise ValueError(str(exc)) from None
        else:
            changed = (title, body, board, int(pinned), color) != (current['title'], current['body'], current['board'], current['pinned'], current['color'])
            if changed:
                # Pinning or moving between boards is not an edit of the text.
                touched = stamp if (title, body) != (current['title'], current['body']) else current['updated_at']
                db.execute('UPDATE notes SET title=?,body=?,board=?,pinned=?,color=?,updated_at=?,revision=revision+1 WHERE owner=? AND id=?',
                           (title, body, board, int(pinned), color, touched, owner(), note_id))
                if title != current['title'] and data.get('rename_links', True) is not False:
                    # A bare [[title]] cannot identify which duplicate was meant.
                    live = _rows(db)
                    old_is_unique = not any(r['id'] != note_id and _title_key(r['title']) == _title_key(current['title']) for r in live)
                    new_target = _title_index(live).get(_title_key(title))
                    representable = LINK.fullmatch('[[' + title + ']]')
                    if old_is_unique and new_target == note_id and representable and representable.group(1) == title:
                        _rename_links(db, current['title'], title, note_id)
        return {'note': get(note_id)}, 200 if current else 201


def delete(data):
    note_id = _id(data.get('id'))
    if data.get('confirmed') is not True:
        raise ValueError('Удаление требует подтверждения.')
    with _write() as db:
        current = db.execute('SELECT revision FROM notes WHERE owner=? AND id=? AND deleted_at IS NULL', (owner(), note_id)).fetchone()
        if not current:
            return {'error': 'Заметка не найдена.'}, 404
        if data.get('revision') is not None and _id(data['revision'], 'номер версии') != current[0]:
            return {'error': 'Заметка уже изменилась. Обнови её перед удалением.', 'code': 'stale'}, 409
        db.execute('UPDATE notes SET deleted_at=?,revision=revision+1 WHERE owner=? AND id=?', (_now(), owner(), note_id))
    return {'ok': True, 'undo_id': note_id}, 200


def restore(data):
    note_id = _id(data.get('id'))
    with _write() as db:
        row = db.execute('SELECT deleted_at,board FROM notes WHERE owner=? AND id=?', (owner(), note_id)).fetchone()
        if not row or not row['deleted_at']:
            return {'error': 'Удалённая заметка не найдена.'}, 404
        if datetime.fromisoformat(row['deleted_at']) < datetime.now(timezone.utc) - timedelta(minutes=10):
            return {'error': 'Срок отмены удаления истёк (10 минут).'}, 410
        if db.execute('SELECT COUNT(*) FROM notes WHERE owner=? AND deleted_at IS NULL', (owner(),)).fetchone()[0] >= MAX_NOTES:
            raise ValueError(f'Можно хранить до {MAX_NOTES} заметок.')
        board = _ensure_board(db, row['board'])
        db.execute('UPDATE notes SET deleted_at=NULL,board=?,revision=revision+1 WHERE owner=? AND id=?', (board, owner(), note_id))
        return {'note': get(note_id)}, 200


def attach(data):
    note_id, event_id = _id(data.get('note_id'), 'ID заметки'), _id(data.get('event_id'), 'ID плана')
    attached = _bool(data.get('attached', True), 'attached')
    with _write() as db:
        if not db.execute('SELECT 1 FROM notes WHERE owner=? AND id=? AND deleted_at IS NULL', (owner(), note_id)).fetchone():
            return {'error': 'Заметка не найдена.'}, 404
        try:
            _attach(db, note_id, event_id, attached)
        except LookupError as exc:
            return {'error': str(exc)}, 404
        return {'note': get(note_id)}, 200


def board_save(data):
    name = board_name(data.get('name'))
    previous = data.get('previous')
    with _write() as db:
        boards = _boards(db)
        clash = next((b for b in boards if b.lower() == name.lower()), None)
        if previous is None:
            if clash:
                return {'error': 'Доска с таким названием уже есть.'}, 409
            _ensure_board(db, name)
        else:
            previous = board_name(previous)
            if previous not in boards:
                return {'error': 'Доска не найдена.'}, 404
            if clash and clash != previous:
                return {'error': 'Доска с таким названием уже есть.'}, 409
            if name != previous:
                db.execute('UPDATE note_boards SET name=? WHERE owner=? AND name=?', (name, owner(), previous))
                db.execute('UPDATE notes SET board=?,revision=revision+1 WHERE owner=? AND board=?', (name, owner(), previous))
        return {'boards': listing()['boards'], 'name': name}, 200


def board_delete(data):
    name = board_name(data.get('name'))
    if data.get('confirmed') is not True:
        raise ValueError('Удаление доски требует подтверждения.')
    with _write() as db:
        boards = _boards(db)
        if name not in boards:
            return {'error': 'Доска не найдена.'}, 404
        if len(boards) == 1:
            return {'error': 'Нельзя удалить последнюю доску.'}, 409
        target = INBOX if name != INBOX and INBOX in boards else next(b for b in boards if b != name)
        moved = db.execute('UPDATE notes SET board=?,revision=revision+1 WHERE owner=? AND board=?', (target, owner(), name)).rowcount
        db.execute('DELETE FROM note_boards WHERE owner=? AND name=?', (owner(), name))
        return {'boards': listing()['boards'], 'moved': moved, 'target': target}, 200


def board_order(data):
    names = data.get('names')
    if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
        raise ValueError('Ожидается список досок.')
    with _write() as db:
        boards = _boards(db)
        if sorted(names) != sorted(boards):
            return {'error': 'Список досок изменился. Обнови страницу.', 'code': 'stale'}, 409
        for position, name in enumerate(names):
            db.execute('UPDATE note_boards SET position=? WHERE owner=? AND name=?', (position, owner(), name))
        return {'boards': listing()['boards']}, 200


def counts(event_ids):
    """Number of live notes attached to each plan."""
    ids = [i for i in event_ids if type(i) is int]
    if not ids:
        return {}
    with store.connect() as db:
        rows = db.execute('SELECT ne.event_id,COUNT(*) FROM note_events ne JOIN notes n ON n.id=ne.note_id AND n.owner=ne.owner AND n.deleted_at IS NULL '
                          'JOIN events e ON e.id=ne.event_id AND e.owner=ne.owner AND e.deleted_at IS NULL '
                          'WHERE ne.owner=? AND ne.event_id IN (' + ','.join('?' * len(ids)) + ') GROUP BY ne.event_id', [owner()] + ids).fetchall()
    return {r[0]: r[1] for r in rows}


def quick(text):
    """Telegram /note: the first line is the title, the rest is the body."""
    text = (text or '').strip()
    if not text:
        raise ValueError('Напиши текст после команды, например: /note Купить батарейки для пульта')
    first, _, rest = text.partition('\n')
    title = first.strip()
    if len(title) > 80:
        title, rest = title[:80].rsplit(' ', 1)[0] or title[:80], text
    payload, _ = save({'title': title, 'body': rest.strip(), 'board': INBOX})
    return payload['note']


def recent(limit=5):
    with store.connect() as db:
        return [dict(r) for r in db.execute('SELECT id,title,board,updated_at FROM notes WHERE owner=? AND deleted_at IS NULL ORDER BY pinned DESC,updated_at DESC LIMIT ?', (owner(), limit))]


def _filename(value):
    # Wiki delimiters must not turn a filename into a heading/block reference.
    value = unicodedata.normalize('NFC', value)
    value = re.sub(r'[\\/:*?"<>|\[\]#^\x00-\x1f\x7f]', ' ', value)[:80].strip(' .') or 'Без названия'
    # Leave room for duplicate suffixes under common 255-byte filename limits.
    value = value.encode('utf-8')[:220].decode('utf-8', errors='ignore').rstrip(' .') or 'Без названия'
    if re.fullmatch(r'(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', value):
        value = '_' + value
    return value


def _unique_name(base, used, suffix=''):
    candidate, number = base + suffix, 2
    while candidate.casefold() in used:
        candidate, number = f'{base} ({number}){suffix}', number + 1
    used.add(candidate.casefold())
    return candidate


def _export_body(body, by_title, paths, missing):
    def replace(match):
        note_id = by_title.get(_title_key(match.group(1)))
        target = paths[note_id][:-3] if note_id is not None else missing[_title_key(match.group(1))]
        original = match.group(0)[2:-2]
        _, separator, alias = original.partition('|')
        if note_id is None and target == match.group(1):
            return match.group(0)
        return '[[' + target + '|' + (alias if separator else match.group(1)) + ']]'

    return LINK.sub(replace, body)


def export_zip():
    """Plain Markdown files in board folders — the archive opens as an Obsidian vault."""
    with store.connect() as db:
        if not db.in_transaction:
            db.execute('BEGIN')
        rows = _rows(db)
        events = _events(db)
    # Allocate every path first: both sanitization and duplicates can change it.
    folders, used_folders = {}, set()
    for board in sorted({r['board'] for r in rows}):
        folders[board] = _unique_name(_filename(board), used_folders)
    paths, used_paths = {}, set()
    ordered = sorted(rows, key=lambda r: (r['board'], r['title'].casefold(), r['id']))
    for row in ordered:
        base = folders[row['board']] + '/' + _filename(row['title'])
        paths[row['id']] = _unique_name(base, used_paths, '.md')
    by_title = _title_index(rows)
    # Missing links must not accidentally bind to another note's sanitized name.
    # Reserve basenames globally because Obsidian resolves bare filenames across
    # folders, and reserve other missing targets to avoid merging distinct links.
    missing_titles = {}
    for row in ordered:
        for title in links(row['body']):
            if _title_key(title) not in by_title:
                missing_titles.setdefault(_title_key(title), title)
    used_names = {name.casefold() for p in paths.values() for name in (p.rsplit('/', 1)[-1], p.rsplit('/', 1)[-1][:-3])}
    missing = {}
    for key, title in sorted(missing_titles.items()):
        missing[key] = _unique_name(_filename(title), used_names)
        used_names.add((missing[key] + '.md').casefold())
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        for row in ordered:
            plans = events.get(row['id'], [])
            front = ['---', 'title: ' + json.dumps(row['title'], ensure_ascii=False),
                     'board: ' + json.dumps(row['board'], ensure_ascii=False),
                     'created: ' + row['created_at'][:19], 'updated: ' + row['updated_at'][:19]]
            if plans:
                front.append('plans:')
                front += ['  - ' + json.dumps((p['date'] or 'без даты') + (' ' + p['time'] if p['time'] else '') + ' · ' + p['title'], ensure_ascii=False) for p in plans]
            front.append('---')
            archive.writestr(paths[row['id']], '\n'.join(front) + '\n\n# ' + row['title'] + '\n\n' + _export_body(row['body'], by_title, paths, missing) + '\n')
    return buffer.getvalue()


def post(path, data):
    handlers = {'/api/notes/save': save, '/api/notes/delete': delete, '/api/notes/restore': restore,
                '/api/notes/attach': attach, '/api/notes/boards/save': board_save,
                '/api/notes/boards/delete': board_delete, '/api/notes/boards/order': board_order}
    if path not in handlers:
        return {'error': 'Не найдено.'}, 404
    return handlers[path](data)
