"""Notes: boards, [[links]], plan attachments, owner isolation, undo and export."""
import io
import json
import tempfile
import threading
import unittest
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePosixPath
from unittest.mock import patch
from app import notes, service, store
from app.context import scope


class Notes(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = patch.object(store, 'DB_PATH', str(Path(tmp.name) / 'notes.db'))
        db.start()
        self.addCleanup(db.stop)
        store.init()
        self.owner = scope('u:alice')
        self.owner.__enter__()
        self.addCleanup(self.owner.__exit__, None, None, None)

    def make(self, **data):
        payload, status = notes.save(data)
        self.assertIn(status, (200, 201), payload)
        return payload['note']

    def test_default_boards_and_board_lifecycle(self):
        self.assertEqual([b['name'] for b in notes.listing()['boards']], ['Входящие', 'Идеи', 'Проекты'])
        notes.board_save({'name': 'Учёба'})
        self.assertEqual(notes.board_save({'name': 'учёба'})[1], 409)
        note = self.make(title='Конспект', board='Учёба')
        notes.board_save({'name': 'Курс', 'previous': 'Учёба'})
        self.assertEqual(notes.get(note['id'])['board'], 'Курс')
        result, status = notes.board_delete({'name': 'Курс', 'confirmed': True})
        self.assertEqual((status, result['target'], result['moved']), (200, 'Входящие', 1))
        self.assertEqual(notes.get(note['id'])['board'], 'Входящие')
        with self.assertRaises(ValueError):
            notes.board_delete({'name': 'Идеи'})
        order = ['Проекты', 'Идеи', 'Входящие']
        self.assertEqual([b['name'] for b in notes.board_order({'names': order})[0]['boards']], order)
        self.assertEqual(notes.board_order({'names': ['Идеи']})[1], 409)

    def test_links_backlinks_and_rename_rewrites_links(self):
        target = self.make(title='Книга')
        source = self.make(title='План чтения', body='Начать с [[Книга]] и [[Книга|этой]]. Потом [[Новая идея]] #чтение')
        full = notes.get(source['id'])
        self.assertEqual(full['outgoing'], [{'title': 'Книга', 'id': target['id']}, {'title': 'Новая идея', 'id': None}])
        self.assertEqual(full['tags'], ['чтение'])
        self.assertEqual([b['id'] for b in notes.get(target['id'])['backlinks']], [source['id']])
        self.make(id=target['id'], title='Любимая книга')
        body = notes.get(source['id'])['body']
        self.assertIn('[[Любимая книга]]', body)
        self.assertIn('[[Любимая книга|этой]]', body)

    def test_attach_to_plans_and_counts(self):
        plan = store.save(service.validate({'title': 'Созвон', 'kind': 'meeting', 'date': '2030-01-10', 'time': '10:00'}))
        note = self.make(title='Вопросы к созвону', body='- [ ] бюджет\n- [x] сроки', event_id=plan['id'])
        self.assertEqual(note['events'][0]['id'], plan['id'])
        self.assertEqual(note['checklist'], {'done': 1, 'total': 2})
        self.assertEqual(notes.counts([plan['id']]), {plan['id']: 1})
        self.assertEqual([n['id'] for n in notes.listing(event_id=plan['id'])['notes']], [note['id']])
        notes.attach({'note_id': note['id'], 'event_id': plan['id'], 'attached': False})
        self.assertEqual(notes.counts([plan['id']]), {})
        with scope('u:bob'):
            foreign = store.save(service.validate({'title': 'Чужое'}))
        self.assertEqual(notes.attach({'note_id': note['id'], 'event_id': foreign['id']})[1], 404)
        with self.assertRaises(ValueError):
            notes.save({'title': 'X', 'event_id': foreign['id']})
        self.assertEqual(notes.listing()['total'], 1)

    def test_failed_direct_save_rolls_back_boards_notes_and_attachments(self):
        for data in ({'color': 'red'}, {'pinned': 'yes'}, {'event_id': -1}, {'event_id': 999999}):
            with self.subTest(data=data), self.assertRaises(ValueError):
                notes.save({'title': 'Не сохранять', 'board': 'Новая доска', **data})
            with store.connect() as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM notes').fetchone()[0], 0)
                self.assertEqual(db.execute('SELECT COUNT(*) FROM note_events').fetchone()[0], 0)
                self.assertEqual(db.execute('SELECT COUNT(*) FROM note_boards').fetchone()[0], 0)

    def test_failed_save_has_savepoint_inside_existing_transaction(self):
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            token = store.TX.set(db)
            try:
                kept = self.make(title='Сохранить')
                with self.assertRaises(ValueError):
                    notes.save({'title': 'Откатить', 'board': 'Откатить', 'event_id': 999999})
                self.assertEqual(notes.listing()['total'], 1)
                self.assertNotIn('Откатить', [b['name'] for b in notes.listing()['boards']])
            finally:
                store.TX.reset(token)
        self.assertEqual(notes.get(kept['id'])['title'], 'Сохранить')

    def test_concurrent_direct_save_checks_revision_under_write_lock(self):
        note = self.make(title='Конкурентная')
        start = threading.Barrier(2)

        def update(body):
            with scope('u:alice'):
                start.wait(timeout=5)
                return notes.save({'id': note['id'], 'revision': note['revision'], 'body': body})

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(update, ('Первая', 'Вторая')))
        self.assertEqual(sorted(status for _, status in results), [200, 409])
        winner = next(payload['note'] for payload, status in results if status == 200)
        self.assertEqual(notes.get(note['id'])['body'], winner['body'])
        self.assertEqual(notes.get(note['id'])['revision'], note['revision'] + 1)

    def test_concurrent_creation_cannot_exceed_note_limit(self):
        start = threading.Barrier(2)

        def create(title):
            with scope('u:alice'):
                start.wait(timeout=5)
                try:
                    return notes.save({'title': title})[1]
                except ValueError:
                    return 'limit'

        with patch.object(notes, 'MAX_NOTES', 1), ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(create, ('Первая', 'Вторая')))
        self.assertCountEqual(results, [201, 'limit'])
        self.assertEqual(notes.listing()['total'], 1)

    def test_concurrent_attachment_cannot_exceed_limit(self):
        note = self.make(title='Вложения')
        plans = [store.save(service.validate({'title': f'План {i}'})) for i in range(2)]
        start = threading.Barrier(2)

        def attach(plan):
            with scope('u:alice'):
                start.wait(timeout=5)
                try:
                    return notes.attach({'note_id': note['id'], 'event_id': plan['id']})[1]
                except ValueError:
                    return 'limit'

        with patch.object(notes, 'MAX_EVENTS', 1), ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attach, plans))
        self.assertCountEqual(results, [200, 'limit'])
        self.assertEqual(len(notes.get(note['id'])['events']), 1)

    def test_attachment_limit_is_idempotent_and_counts_only_live_owner_plans(self):
        note = self.make(title='Вложения')
        plans = [store.save(service.validate({'title': f'План {i}'})) for i in range(3)]
        with patch.object(notes, 'MAX_EVENTS', 2):
            for plan in plans[:2]:
                self.assertEqual(notes.attach({'note_id': note['id'], 'event_id': plan['id']})[1], 200)
            self.assertEqual(notes.attach({'note_id': note['id'], 'event_id': plans[0]['id']})[1], 200)
            with self.assertRaises(ValueError):
                notes.attach({'note_id': note['id'], 'event_id': plans[2]['id']})
            store.delete(plans[0]['id'])
            self.assertEqual(notes.counts([plans[0]['id']]), {})
            self.assertEqual(notes.attach({'note_id': note['id'], 'event_id': plans[2]['id']})[1], 200)
            self.assertEqual(notes.attach({'note_id': note['id'], 'event_id': plans[0]['id'], 'attached': False})[1], 200)
        self.assertEqual(len(notes.get(note['id'])['events']), 2)

    def test_owner_isolation(self):
        note = self.make(title='Секрет')
        with scope('u:bob'):
            self.assertIsNone(notes.get(note['id']))
            self.assertEqual(notes.listing()['total'], 0)
            self.assertEqual(notes.save({'id': note['id'], 'title': 'Взлом'})[1], 404)
            self.assertEqual(notes.delete({'id': note['id'], 'confirmed': True})[1], 404)
        self.assertEqual(notes.get(note['id'])['title'], 'Секрет')

    def test_revision_conflict_delete_and_restore(self):
        note = self.make(title='Черновик', body='a')
        self.make(id=note['id'], revision=note['revision'], body='b')
        stale, status = notes.save({'id': note['id'], 'revision': note['revision'], 'body': 'c'})
        self.assertEqual((status, stale['code'], stale['note']['body']), (409, 'stale', 'b'))
        with self.assertRaises(ValueError):
            notes.delete({'id': note['id']})
        self.assertEqual(notes.delete({'id': note['id'], 'confirmed': True})[1], 200)
        self.assertEqual(notes.listing()['total'], 0)
        self.assertEqual(notes.restore({'id': note['id']})[0]['note']['body'], 'b')

    def test_restore_cannot_bypass_note_limit_or_create_board_on_failure(self):
        old = self.make(title='Удалённая', board='Старая доска')
        notes.delete({'id': old['id'], 'confirmed': True})
        with store.connect() as db:
            db.execute('DELETE FROM note_boards WHERE owner=? AND name=?', ('u:alice', 'Старая доска'))
        with patch.object(notes, 'MAX_NOTES', 1):
            self.make(title='На месте')
            with self.assertRaises(ValueError):
                notes.restore({'id': old['id']})
        self.assertIsNone(notes.get(old['id']))
        self.assertNotIn('Старая доска', [b['name'] for b in notes.listing()['boards']])

    def test_board_changes_invalidate_revision_without_touching_edit_time(self):
        note = self.make(title='На доске', board='Черновики')
        notes.board_save({'name': 'Работа', 'previous': 'Черновики'})
        moved = notes.get(note['id'])
        self.assertEqual(moved['updated_at'], note['updated_at'])
        self.assertGreater(moved['revision'], note['revision'])
        self.assertEqual(notes.save({'id': note['id'], 'revision': note['revision'], 'board': 'Черновики'})[1], 409)
        notes.board_delete({'name': 'Работа', 'confirmed': True})
        self.assertGreater(notes.get(note['id'])['revision'], moved['revision'])

    def test_duplicate_titles_have_stable_links_and_no_ambiguous_rename(self):
        first = self.make(title='Книга')
        second = self.make(title='книга', body='[[КНИГА]]')
        source = self.make(title='Ссылки', body='[[ Книга ]] и [[КНИГА|читать]]')
        self.make(id=second['id'], body='Изменено [[Книга]]')
        self.assertEqual(notes.get(source['id'])['outgoing'][0]['id'], first['id'])
        self.assertEqual(notes.get(second['id'])['outgoing'][0]['id'], first['id'])
        self.assertEqual(notes.get(second['id'])['backlinks'], [])
        self.assertEqual({n['id'] for n in notes.get(first['id'])['backlinks']}, {source['id'], second['id']})
        self.make(id=first['id'], title='Другая книга')
        self.assertIn('[[ Книга ]]', notes.get(source['id'])['body'])
        self.assertEqual(notes.get(source['id'])['outgoing'][0]['id'], second['id'])

    def test_rename_rewrites_self_links_whitespace_aliases_and_only_owner(self):
        target = self.make(title='Моя книга', body='[[Моя   книга|я]]')
        source = self.make(title='План', body='[[ Моя   книга |читать]]')
        with scope('u:bob'):
            foreign = self.make(title='Чужая', body='[[Моя книга]]')
        self.make(id=target['id'], title='Новая книга')
        self.assertEqual(notes.get(target['id'])['body'], '[[Новая книга|я]]')
        self.assertEqual(notes.get(target['id'])['revision'], target['revision'] + 1)
        self.assertEqual(notes.get(source['id'])['body'], '[[Новая книга|читать]]')
        self.assertGreater(notes.get(source['id'])['revision'], source['revision'])
        with scope('u:bob'):
            self.assertEqual(notes.get(foreign['id'])['body'], '[[Моя книга]]')

    def test_rename_keeps_links_in_restored_notes(self):
        target = self.make(title='До')
        source = self.make(title='В корзине', body='[[До]]')
        notes.delete({'id': source['id'], 'confirmed': True})
        self.make(id=target['id'], title='После')
        restored = notes.restore({'id': source['id']})[0]['note']
        self.assertEqual(restored['body'], '[[После]]')
        self.assertEqual(restored['outgoing'][0]['id'], target['id'])

    def test_rename_over_body_limit_rolls_back_every_note_and_board(self):
        target = self.make(title='A', body='[[A]]')
        source = self.make(title='Длинная', body='[[A]]' * (notes.MAX_BODY // 5))
        target = notes.get(target['id'])
        with self.assertRaises(ValueError):
            notes.save({'id': target['id'], 'title': 'Намного длиннее', 'board': 'Не сохранять'})
        self.assertEqual(notes.get(target['id']), target)
        self.assertEqual(notes.get(source['id'])['body'], source['body'])
        self.assertEqual(notes.get(source['id'])['revision'], source['revision'])
        self.assertNotIn('Не сохранять', [b['name'] for b in notes.listing()['boards']])

    def test_rename_into_duplicate_or_unrepresentable_title_does_not_hijack_links(self):
        self.make(title='Уже есть')
        target = self.make(title='До')
        source = self.make(title='Ссылка', body='[[До]]')
        self.make(id=target['id'], title='Уже есть')
        self.assertEqual(notes.get(source['id'])['body'], '[[До]]')
        self.make(id=target['id'], title='До')
        self.make(id=target['id'], title='Нельзя|ссылка')
        self.assertEqual(notes.get(source['id'])['body'], '[[До]]')

    def test_graph_is_unfiltered_compact_owner_live_and_bounded(self):
        first = self.make(title='Книга', board='Идеи')
        duplicate = self.make(title='книга', body='[[Книга]]')
        deleted = self.make(title='Удалено')
        source = self.make(title='План', body='[[Книга]] [[КНИГА|ещё]] [[Нет]] [[Удалено]] [[Чужое]] [[План]]')
        notes.delete({'id': deleted['id'], 'confirmed': True})
        with scope('u:bob'):
            self.make(title='Чужое', body='[[Книга]]')
        result = notes.listing(q='нет результата', board='Идеи', tag='нет', event_id=999)
        self.assertEqual(result['notes'], [])
        graph = result['graph']
        self.assertEqual({n['id'] for n in graph['nodes']}, {first['id'], duplicate['id'], source['id']})
        self.assertTrue(all(set(n) == {'id', 'title', 'board'} for n in graph['nodes']))
        self.assertEqual({(e['source'], e['target']) for e in graph['edges']},
                         {(source['id'], first['id']), (source['id'], source['id']), (duplicate['id'], first['id'])})
        self.assertEqual(len(graph['edges']), 3)
        with patch.object(notes, 'MAX_NOTES', 2):
            graph = notes.listing()['graph']
        self.assertEqual(len(graph['nodes']), 2)
        ids = {n['id'] for n in graph['nodes']}
        self.assertTrue(all(e['source'] in ids and e['target'] in ids for e in graph['edges']))

    def test_search_title_first_body_and_tags(self):
        self.make(title='Отпуск', body='Билеты и #путешествия')
        self.make(title='Покупки', body='Купить чемодан для отпуска')
        self.make(title='Работа', body='Отчёт')
        found = notes.listing(q='отпуск')['notes']
        self.assertEqual([n['title'] for n in found], ['Отпуск', 'Покупки'])
        self.assertEqual([n['title'] for n in notes.listing(q='#путеш')['notes']], ['Отпуск'])
        self.assertEqual([n['title'] for n in notes.listing(tag='путешествия')['notes']], ['Отпуск'])
        self.assertEqual(notes.listing(q='zzz')['total'], 0)

    def test_validation_and_untitled_note(self):
        for bad in ({'title': 'x' * 201}, {'title': 'ok', 'body': 1}, {'title': 'ok', 'color': 'red'},
                    {'title': 'ok', 'pinned': 'yes'}, {'title': 'ok', 'board': ''}, {'title': 'a\nb'}):
            with self.assertRaises(ValueError):
                notes.save(bad)
        self.assertEqual(self.make(title='', body='# Мысль на ходу\nтекст')['title'], 'Мысль на ходу')
        self.assertEqual(self.make(title='', body='')['title'], 'Без названия')

    def test_moving_and_pinning_keep_edit_time(self):
        note = self.make(title='Идея')
        moved = self.make(id=note['id'], board='Идеи', pinned=True)
        self.assertEqual((moved['board'], moved['pinned'], moved['updated_at']), ('Идеи', True, note['updated_at']))
        self.assertTrue(notes.listing()['notes'][0]['pinned'])

    def test_export_is_markdown_vault(self):
        plan = store.save(service.validate({'title': 'Сдать отчёт', 'date': '2030-01-10'}))
        self.make(title='Итоги: квартал', body='Текст', board='Проекты', event_id=plan['id'])
        self.make(title='Итоги: квартал', body='Дубль', board='Проекты')
        archive = zipfile.ZipFile(io.BytesIO(notes.export_zip()))
        names = sorted(archive.namelist())
        self.assertEqual(names, ['Проекты/Итоги  квартал (2).md', 'Проекты/Итоги  квартал.md'])
        text = archive.read('Проекты/Итоги  квартал.md').decode()
        self.assertIn('board: "Проекты"', text)
        self.assertIn('Сдать отчёт', text)
        self.assertIn('title: "Итоги: квартал"', text)
        self.assertIn('# Итоги: квартал\n', text)

    def test_export_maps_wikilinks_to_sanitized_unique_paths(self):
        target = self.make(title='Итоги: квартал', board='A/B')
        self.make(title='Итоги: квартал', board='A/B', body='Дубль')
        self.make(title='Итоги? квартал', board='A\\B')
        self.make(title='Другой', board='A/B', body='[[Итоги: квартал]] [[Итоги: квартал|итоги]] [[Итоги? квартал]] [[Нет]]')
        self.make(id=target['id'], body='Обновлено [[Итоги: квартал]]')
        with zipfile.ZipFile(io.BytesIO(notes.export_zip())) as archive:
            paths = archive.namelist()
            self.assertEqual(len(paths), len({p.casefold() for p in paths}))
            self.assertEqual(len({str(PurePosixPath(p).parent) for p in paths}), 2)
            text = next(archive.read(p).decode() for p in paths if p.endswith('/Другой.md'))
            destinations = {p[:-3]: archive.read(p).decode() for p in paths}
            for match in notes.LINK.finditer(text):
                name = match.group(1)
                if name == 'Нет':
                    continue
                self.assertIn(name, destinations)
                if 'Итоги: квартал' in match.group(0) or match.group(0).endswith('|итоги]]'):
                    self.assertIn('Обновлено', destinations[name])
            self.assertIn('|Итоги: квартал]]', text)
            self.assertIn('|итоги]]', text)

    def test_export_keeps_metadata_escaped_and_paths_inside_vault(self):
        board = '../A\\B'
        title = '../CON: [текст] #раздел ^блок'
        plan = store.save(service.validate({'title': 'Путь \\"и кавычки"', 'date': '2030-01-10'}))
        self.make(title=title, board=board, body='Содержимое [[../CON: [текст] #раздел ^блок]]', event_id=plan['id'])
        self.make(title='../../за пределы', board='..', body='[[../../за пределы]] [[../missing]]')
        self.make(title='CON', board='..')
        self.make(title='x' * 79 + '.suffix', board='..')
        with zipfile.ZipFile(io.BytesIO(notes.export_zip())) as archive:
            for path in archive.namelist():
                self.assertFalse(PurePosixPath(path).is_absolute())
                for part in PurePosixPath(path).parts:
                    self.assertNotIn(part, ('.', '..'))
                    self.assertNotEqual(part.upper(), 'CON.md'.upper())
                    self.assertFalse(part.endswith((' ', '.')))
                    self.assertFalse(any(c in part for c in '\\[]#^'))
                text = archive.read(path).decode()
                if 'Содержимое' in text:
                    metadata = dict(line.split(': ', 1) for line in text.splitlines()[1:5])
                    self.assertEqual(json.loads(metadata['title']), title)
                    self.assertEqual(json.loads(metadata['board']), board)
                    self.assertIn('# ' + title, text)
                if '[[../missing]]' in text:
                    self.fail('Unresolved wiki link escapes the vault')

    def test_export_excludes_deleted_and_foreign_notes_and_plans(self):
        self.make(title='Своя')
        deleted = self.make(title='Удалённая')
        notes.delete({'id': deleted['id'], 'confirmed': True})
        with scope('u:bob'):
            self.make(title='Чужая секретная')
        with zipfile.ZipFile(io.BytesIO(notes.export_zip())) as archive:
            self.assertEqual(archive.namelist(), ['Входящие/Своя.md'])

    def test_export_missing_links_do_not_bind_to_sanitized_existing_names(self):
        self.make(title='A:B')
        self.make(title='Источник', body='[[A B]] [[A?B]] [[../A B]] [[A B.md]] [[Нет]]')
        with zipfile.ZipFile(io.BytesIO(notes.export_zip())) as archive:
            text = archive.read('Входящие/Источник.md').decode()
            destinations = {match.group(1) for match in notes.LINK.finditer(text)}
            self.assertEqual(len(destinations), 5)
            self.assertIn('Нет', destinations)
            self.assertNotIn('A B', destinations)
            self.assertNotIn('A B.md', destinations)
            self.assertTrue(all('/' not in name and '\\' not in name for name in destinations))

    def test_export_long_unicode_and_natural_suffix_collisions(self):
        self.make(title='😀' * 100)
        self.make(title='😀' * 101)
        self.make(title='A:B')
        self.make(title='A?B')
        self.make(title='A B (2)')
        self.make(title='é')
        self.make(title='e\u0301')
        with zipfile.ZipFile(io.BytesIO(notes.export_zip())) as archive:
            names = archive.namelist()
            self.assertEqual(len(names), 7)
            self.assertEqual(len({n.casefold() for n in names}), 7)
            self.assertTrue(all(len(part.encode('utf-8')) <= 255 for name in names for part in PurePosixPath(name).parts))

    def test_quick_note_from_telegram(self):
        note = notes.quick('Купить батарейки\nдля пульта и мыши')
        self.assertEqual((note['title'], note['body'], note['board']), ('Купить батарейки', 'для пульта и мыши', 'Входящие'))
        with self.assertRaises(ValueError):
            notes.quick('  ')
        self.assertEqual(notes.recent()[0]['title'], 'Купить батарейки')


if __name__ == '__main__':
    unittest.main()
