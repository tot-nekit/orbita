"""Plan search: phrases, word starts, forms, synonyms and owner isolation."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from app import service, store
from app.context import scope


class Search(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = patch.object(store, 'DB_PATH', str(Path(tmp.name) / 'search.db'))
        db.start()
        self.addCleanup(db.stop)
        store.init()
        for title in ('Встреча', 'Созвон с Иваном', 'Отчёт по проекту', 'Купить молоко',
                      'Подготовить презентацию', 'Встреча с командой по релизу'):
            store.save(service.validate({'title': title}))

    def titles(self, query, **kw):
        return [row['title'] for row in store.search(query, **kw)[0]]

    def test_title_made_of_a_service_word_is_found(self):
        # Regression: «Встреча» was dropped as a stop word on both sides.
        self.assertEqual(self.titles('встреча')[:2], ['Встреча', 'Встреча с командой по релизу'])
        self.assertIn('Встреча', self.titles('ВСТРЕЧА'))

    def test_partly_typed_word_and_yo(self):
        self.assertEqual(self.titles('отч'), ['Отчёт по проекту'])
        self.assertEqual(self.titles('отчет'), ['Отчёт по проекту'])
        self.assertEqual(self.titles('молок'), ['Купить молоко'])

    def test_forms_typos_and_command_words(self):
        self.assertEqual(self.titles('ивана'), ['Созвон с Иваном'])
        self.assertEqual(self.titles('презинтацию'), ['Подготовить презентацию'])
        self.assertEqual(self.titles('найди встречу с командой'), ['Встреча с командой по релизу'])

    def test_no_match_short_query_and_isolation(self):
        self.assertEqual(self.titles('zzz'), [])
        self.assertEqual(self.titles('в'), [])
        with scope('u:other'):
            self.assertEqual(self.titles('встреча'), [])

    def test_kind_filter_and_done_after_active(self):
        store.save(service.validate({'title': 'Встреча с банком', 'kind': 'meeting',
                                     'date': '2030-01-10', 'time': '10:00'}))
        self.assertEqual(self.titles('встреча', kind='meeting'), ['Встреча с банком'])
        store.save(service.validate({'title': 'Встреча итоговая', 'status': 'done'}))
        found = self.titles('встреча')
        # Equally relevant: active «Встреча» comes before the completed one.
        self.assertLess(found.index('Встреча'), found.index('Встреча итоговая'))


if __name__ == '__main__':
    unittest.main()
