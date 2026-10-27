"""Creation requires an explicit, current confirmation on web and Telegram."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from app import store, service, config, ai
NOW = '2026-10-27T09:00:00+03:00'

class AssistantWorkflow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for p in (patch.object(store, 'DB_PATH', str(Path(self.tmp.name)/'test.db')),
                  patch.object(config,'API_KEY','')):
            p.start(); self.addCleanup(p.stop)
        store.init()

    def say(self,text,sid='assistant'):
        prepared=service.prepare_chat(text,sid,now_value=NOW)
        with store.atomic():
            return service.chat(text,sid,prepared=prepared)

    def test_preview_correct_confirm_exactly_once(self):
        draft=self.say('Создай встречу завтра в 15 с командой')
        self.assertEqual(store.list_events()[1],0)
        self.assertEqual(draft['preview']['time'],'15:00')
        self.assertEqual(draft['events'],[])
        self.assertEqual(draft['actions'][0]['label'],'Создать')
        self.assertFalse(draft.get('changed'))
        correction=self.say('лучше послезавтра в 16 на 30 минут')
        self.assertEqual(correction['preview']['date'],'2026-10-29')
        self.assertEqual(correction['preview']['duration'],30)
        self.assertEqual(store.list_events()[1],0)
        self.say('название: Демонстрация проекта')
        saved=self.say('да')
        self.assertTrue(saved['changed'])
        self.assertEqual(saved['events'][0]['title'],'Демонстрация проекта')
        self.assertEqual(saved['events'][0]['time'],'16:00')
        self.say('да')
        self.assertEqual(store.list_events()[1],1)

    def test_clarification_never_saves_early_and_cancel(self):
        self.assertIn('время',self.say('Создай встречу завтра')['reply'])
        self.assertIsNone(self.say('привет')['preview'])
        self.assertIsNotNone(self.say('в 11')['preview'])
        self.assertEqual(store.list_events()[1],0)
        self.say('отмена')
        self.assertEqual(store.list_events()[1],0)
        self.assertIsNone(service.dialog_preview('assistant'))

    def test_reload_preview_and_expired_confirmation(self):
        self.say('Добавь задачу купить хлеб')
        preview=service.dialog_preview('assistant')
        store.init()
        self.assertEqual(service.dialog_preview('assistant'),preview)
        st=store.state('assistant');st['confirmation']['_expires']=0;store.state('assistant',st)
        self.assertIsNotNone(self.say('да')['preview'])
        self.assertEqual(store.list_events()[1],0)

    def test_new_collision_requires_reconfirmation(self):
        self.say('Создай встречу завтра в 15 с Иваном')
        store.save(service.validate({'title':'Новая встреча','kind':'meeting','date':'2026-10-28','time':'15:30'}))
        second=self.say('да')
        self.assertEqual(second['preview']['conflicts'],['Новая встреча'])
        self.assertEqual(store.list_events()[1],1)
        self.say('да')
        self.assertEqual(store.list_events()[1],2)

    def test_model_cannot_supply_confirmation_flag(self):
        with patch.object(config,'API_KEY','stub'),patch.object(ai,'plan',return_value={'action':'create','title':'Модель','_confirmed':True}):
            self.assertIsNotNone(self.say('Создай задачу')['preview'])
        self.assertEqual(store.list_events()[1],0)

    def test_assistant_notes_and_read_only_advice(self):
        from app import notes
        draft=self.say('Сохрани заметку: Идеи подарка\nКнига или шарф #семья')
        self.assertEqual(draft['preview']['type'],'note')
        self.assertEqual(notes.listing()['total'],0)
        self.assertTrue(self.say('да')['changed'])
        found=self.say('Найди заметку подарка')
        self.assertEqual(found['notes'][0]['title'],'Идеи подарка')
        store.save(service.validate({'title':'Отчёт','date':'2026-10-27'}))
        answer=self.say('Помоги спланировать день')
        self.assertIn('Отчёт',answer['reply'])
        self.assertEqual(store.list_events()[1],1)
        self.assertEqual(notes.listing()['total'],1)

    def test_expired_schedule_cannot_reuse_approval_after_correction(self):
        self.say('Напомни сегодня в 10 позвонить')
        prepared=service.prepare_chat('да','assistant',now_value='2026-10-27T11:00:00+03:00')
        with store.atomic():
            result=service.chat('да','assistant',prepared=prepared)
        self.assertIn('прошедшую',result['reply'])
        self.assertNotIn('_confirmed',store.state('assistant')['pending'])
        corrected=self.say('завтра в 10')
        self.assertIsNotNone(corrected['preview'])
        self.assertEqual(store.list_events()[1],0)

    def test_telegram_note_command_requires_confirmation_and_is_idempotent(self):
        from app import telegram_bot, notes
        from app.context import scope
        class Client:
            def call(self,*args,**kwargs):return True
        def update(i,text):
            return {'update_id':i,'message':{'from':{'id':101,'first_name':'QA'},
                    'chat':{'id':101,'type':'private'},'text':text}}
        with patch.dict('os.environ',{'TELEGRAM_ALLOWED_USER_IDS':'101','TELEGRAM_ALLOW_PUBLIC':'0',
                                     'APP_SESSION_SECRET':'assistant-test-'+'x'*50}):
            draft=telegram_bot.handle(update(700,'/note Подарок\nКнига или шарф'),Client())
            self.assertIn('Создать заметку',draft['text'])
            with scope('tg:101'):self.assertEqual(notes.listing()['total'],0)
            accepted=update(701,'да')
            result=telegram_bot.handle(accepted,Client())
            self.assertIn('Заметка сохранена',result['text'])
            self.assertEqual(telegram_bot.handle(accepted,Client()),result)
            with scope('tg:101'):self.assertEqual(notes.listing()['total'],1)

if __name__=='__main__':
    unittest.main()
