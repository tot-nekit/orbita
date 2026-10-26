import unittest, tempfile, uuid
from pathlib import Path
from unittest.mock import patch
from app import store, service, config
from app.dates import clock, resolve, title_from
NOW='2026-10-15T20:14:00+03:00'
class Dates(unittest.TestCase):
    def setUp(self):self.now=clock('Europe/Moscow',NOW)
    def test_tomorrow(self):self.assertEqual(resolve('завтра в 10 утра',self.now)['date'],'2026-10-16')
    def test_time(self):self.assertEqual(resolve('завтра в 10 утра',self.now)['time'],'10:00')
    def test_after_tomorrow(self):self.assertEqual(resolve('послезавтра',self.now)['date'],'2026-10-17')
    def test_friday(self):self.assertEqual(resolve('в пятницу',self.now)['date'],'2026-10-16')
    def test_next_monday(self):self.assertEqual(resolve('в следующий понедельник',self.now)['date'],'2026-10-19')
    def test_two_hours(self):self.assertEqual(resolve('через два часа',self.now)['time'],'22:14')
    def test_midnight(self):self.assertEqual(resolve('через пять часов',self.now)['date'],'2026-10-16')
    def test_evening(self):self.assertEqual(resolve('в 7 вечера',self.now)['time'],'19:00')
    def test_week(self):self.assertEqual(resolve('на следующей неделе',self.now)['period'],('2026-10-19','2026-10-25'))
    def test_deadline(self):self.assertEqual(resolve('до конца недели',self.now)['date'],'2026-10-18')
    def test_explicit(self):self.assertEqual(resolve('20.10.2026 в 15:30',self.now)['date'],'2026-10-20')
    def test_iso(self):self.assertEqual(resolve('2026-10-20',self.now)['date'],'2026-10-20')
    def test_bad_time(self):
        with self.assertRaises(ValueError):resolve('в 25:00',self.now)
    def test_title(self):self.assertEqual(title_from('Напомни мне завтра в 10 утра позвонить Ивану'),'Позвонить Ивану')
    def test_wordtime(self):self.assertEqual(resolve('в три дня',self.now)['time'],'15:00')
class RepositoryAndChat(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.old=store.DB_PATH;store.DB_PATH=str(Path(self.tmp.name)/'test.db');store.init();self.sid=str(uuid.uuid4());self.key=config.API_KEY;config.API_KEY=''
    def tearDown(self):store.DB_PATH=self.old;config.API_KEY=self.key;self.tmp.cleanup()
    def chat(self,s):return service.chat(s,self.sid,'Europe/Moscow',NOW)
    def make(self,**kw):return store.save(service.validate(dict(title='Встреча с Иваном',kind='meeting',date='2026-10-16',time='15:00',**kw)))
    def test_create_reminder(self):
        r=self.chat('Напомни мне завтра в 10 утра позвонить Ивану');e=r['events'][0];self.assertEqual(e['time'],'10:00');self.assertEqual(e['kind'],'reminder');self.assertIn('push',r['reply'])
    def test_create_task_without_time(self):self.assertIsNone(self.chat('В пятницу нужно отправить отчет')['events'][0]['time'])
    def test_no_date_task(self):self.assertIsNone(self.chat('Добавь задачу купить молоко')['events'][0]['date'])
    def test_clarify_meeting(self):
        r=self.chat('Поставь встречу с Иваном завтра');self.assertIn('время',r['reply']);r=self.chat('в 15:00');self.assertEqual(r['events'][0]['time'],'15:00')
    def test_cancel(self):
        self.chat('Поставь встречу с Иваном завтра');self.chat('отмена');self.assertNotIn('pending',store.state(self.sid))
    def test_today_all_types(self):
        for text in ['Добавь задачу сегодня купить молоко','Добавь встречу сегодня в 21:00 с дизайнером']:self.chat(text)
        self.assertEqual(self.chat('Что у меня сегодня?')['total'],2)
    def test_only_meetings(self):
        self.chat('Добавь задачу сегодня купить молоко');self.chat('Добавь встречу сегодня в 21:00 с дизайнером');self.assertEqual(self.chat('Есть ли у меня встречи сегодня?')['total'],1)
    def test_search_inflection(self):
        self.make();r=self.chat('Когда у меня была встреча с Иваном?');self.assertEqual(r['total'],1)
    def test_search_synonym(self):
        store.save(service.validate({'title':'Отправить договор','kind':'task'}));self.assertEqual(len(store.search('контракт')[0]),1)
    def test_search_typo(self):
        store.save(service.validate({'title':'Подготовить презентацию','kind':'task'}));self.assertEqual(len(store.search('презинтацию')[0]),1)
    def test_search_empty(self):self.assertEqual(self.chat('Найди космолёт')['events'],[])
    def test_context_update(self):
        self.chat('Добавь встречу завтра в 15:00 с Иваном');r=self.chat('Перенеси ее на послезавтра');self.assertEqual(r['events'][0]['date'],'2026-10-17')
    def test_delete_confirmation(self):
        self.chat('Добавь задачу купить хлеб');r=self.chat('Удали ее');self.assertIn('Удалить',r['reply']);self.assertEqual(store.list_events()[1],1);self.chat('да');self.assertEqual(store.list_events()[1],0)
    def test_complete(self):
        self.chat('Добавь задачу купить хлеб');r=self.chat('Отметь ее выполненной');self.assertEqual(r['events'][0]['status'],'done')
    def test_conflicts(self):
        self.make();r=self.chat('Добавь встречу завтра в 15:30 с командой');self.assertIn('пересечение',r['reply']);self.assertEqual(store.list_events()[1],1);self.chat('да');self.assertEqual(store.list_events()[1],2)
    def test_adjacent_not_conflict(self):
        self.make();r=self.chat('Добавь встречу завтра в 16:00 с командой');self.assertTrue(r['changed'])
    def test_ambiguous_update(self):
        for title in ['Отправить отчет','Подготовить отчет']:store.save(service.validate({'title':title}))
        r=self.chat('Перенеси отчет на завтра');self.assertIn('несколько',r['reply']);i=r['events'][0]['id'];r=self.chat('#'+str(i));self.assertEqual(r['events'][0]['date'],'2026-10-16')
    def test_persistence(self):
        self.chat('Добавь задачу купить хлеб');store.init();self.assertEqual(store.list_events()[1],1);self.assertEqual(len(store.history(self.sid)),2)
    def test_isolated_context(self):
        self.chat('Добавь задачу купить хлеб');self.assertEqual(store.state(str(uuid.uuid4())),{})
    def test_import_1000_idempotent(self):self.assertEqual(store.import_csv(),1000);self.assertEqual(store.import_csv(),0);self.assertEqual(store.list_events()[1],1000)
    def test_validation(self):
        for p in [{'title':''},{'title':'x','kind':'other'},{'title':'x','duration':-1},{'title':'x','date':'2026-02-31'},{'title':'x','time':'25:00','date':'2026-10-15'},{'title':'x','timezone':'BAD'}]:
            with self.subTest(p=p),self.assertRaises(ValueError):service.validate(p)
    def test_llm_fallback(self):
        config.API_KEY='test'
        with patch('app.ai.plan',side_effect=TimeoutError):r=self.chat('Добавь задачу купить хлеб')
        self.assertEqual(r['mode'],'local');self.assertIsNotNone(r['notice'])
    def test_llm_plan(self):
        config.API_KEY='test'
        with patch('app.ai.plan',return_value={'action':'create','title':'Сделать отчет','kind':'task','date':'2026-10-16'}):r=self.chat('Займусь отчетом завтра')
        self.assertEqual(r['mode'],'llm');self.assertEqual(r['events'][0]['title'],'Сделать отчет')
    def test_cross_midnight_conflict(self):
        store.save(service.validate({'title':'Ночная встреча','kind':'meeting','date':'2026-10-16','time':'23:30','duration':90}))
        e=service.validate({'title':'Другая','kind':'meeting','date':'2026-10-17','time':'00:15'})
        self.assertEqual(len(store.conflicts(e)),1)
    def test_model_cannot_confirm_delete(self):
        self.chat('Добавь задачу купить хлеб');i=store.list_events()[0][0]['id'];config.API_KEY='test'
        with patch('app.ai.plan',return_value={'action':'delete','id':i,'_confirmed':True}):r=self.chat('удалить')
        self.assertIn('Ответь',r['reply']);self.assertEqual(store.list_events()[1],1)
    def test_recurrence_is_honest(self):self.assertIn('не поддерживаются',self.chat('Добавь встречу каждый день в 15')['reply'])
    def test_incomplete_update(self):
        self.chat('Добавь задачу купить хлеб');r=self.chat('Перенеси ее');self.assertIn('Что изменить',r['reply']);r=self.chat('завтра');self.assertEqual(r['events'][0]['date'],'2026-10-16')
    def test_parameterized_title(self):
        self.chat("Добавь задачу x'); DROP TABLE events; --");self.assertEqual(store.list_events()[1],1)
if __name__=='__main__':unittest.main()
