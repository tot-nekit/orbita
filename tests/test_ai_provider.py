"""No real API calls: provider failures must not become accidental mutations."""
import json, unittest, urllib.error
from datetime import datetime
from unittest.mock import patch, MagicMock
from app import ai, config

class Provider(unittest.TestCase):
    def setUp(self):ai._cooldowns.clear()
    def tearDown(self):ai._cooldowns.clear()
    def test_rejects_malformed_and_invalid_plans(self):
        for plan in ([], {'action':'destroy'}, {'action':'create','duration':True}, {'action':'update','id':'7'}, {'action':'create','date':'2026-02-31'}, {'action':'list','start':12}, {'action':'create','title':{'x':1}}):
            with self.subTest(plan=plan),self.assertRaises(ValueError):ai.parse_plan(json.dumps(plan))
    def test_fenced_json_and_null_metadata(self):
        p=ai.parse_plan('```json\n{"action":"create","title":"Тест","duration":null,"date":null,"_confirmed":true}\n```')
        self.assertNotIn('duration',p);self.assertNotIn('_confirmed',p);self.assertIsNone(p['date'])
    def test_fallback_and_user_agent(self):
        response=MagicMock();response.__enter__.return_value.read.return_value=json.dumps({'choices':[{'finish_reason':'stop','message':{'content':'{"action":"search","query":"отчёт"}'}}]}).encode()
        with patch.object(config,'MODEL','primary'),patch.object(config,'FALLBACK_MODEL','backup'),patch.object(ai.urllib.request,'urlopen',side_effect=[urllib.error.HTTPError('https://example.invalid',429,'rate limit',{},None),response]) as send:
            plan=ai.plan('Найди отчёт',datetime.fromisoformat('2026-10-22T09:00:00+03:00'),'Europe/Moscow',{})
            self.assertEqual(plan['action'],'search');self.assertEqual(send.call_count,2)
            self.assertEqual(send.call_args.args[0].get_header('User-agent'),'Orbita/2.1')
            self.assertIn('calendar_hints',json.loads(send.call_args.args[0].data)['messages'][1]['content'])
    def test_refuses_truncated_valid_json(self):
        response=MagicMock();response.__enter__.return_value.read.return_value=b'{"choices":[{"finish_reason":"length","message":{"content":"{\\"action\\":\\"delete\\",\\"id\\":7}"}}]}'
        with patch.object(config,'MODEL','primary'),patch.object(config,'FALLBACK_MODEL',''),patch.object(ai.urllib.request,'urlopen',return_value=response),self.assertRaises(ValueError):
            ai.plan('удали',datetime.fromisoformat('2026-10-22T09:00:00+03:00'),'Europe/Moscow',{})
    def test_stt_disabled_has_no_network_request(self):
        with patch.object(config,'STT_ENABLED',False),patch.object(ai.urllib.request,'urlopen') as send,self.assertRaises(ValueError):
            ai.transcribe(b'audio','audio/ogg')
        send.assert_not_called()
    def test_provider_outage_uses_local_cooldown_for_following_requests(self):
        now=datetime.fromisoformat('2026-10-24T09:00:00+03:00')
        with patch.object(config,'MODEL','primary'),patch.object(config,'FALLBACK_MODEL','backup'),patch.object(ai.urllib.request,'urlopen',side_effect=TimeoutError) as send:
            for _ in range(2):
                with self.assertRaises(ValueError):ai.plan('Найди отчёт',now,'Europe/Moscow',{})
            self.assertEqual(send.call_count,2,'Second request must not retry both broken models')

if __name__=='__main__':unittest.main()
