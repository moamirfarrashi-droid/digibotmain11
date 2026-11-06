import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch
import telegram_reports as r


class FakeAPI:
    def __init__(self, updates=None, roles=None, webhook=''):
        self.updates = updates or []
        self.roles = roles or {}
        self.webhook = webhook
        self.messages = []

    def call(self, method, **payload):
        if method == 'getWebhookInfo':
            return {'url': self.webhook}
        if method == 'getUpdates':
            return self.updates
        if method == 'getChatMember':
            return {'status': self.roles.get(payload['user_id'], 'member')}
        raise AssertionError(method)

    def send(self, chat, text):
        self.messages.append((chat, text))
        return {'message_id': 1}


class ReportsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = patch.object(r, 'ROOT', Path(self.tmp.name))
        self.root.start()
        self.now = datetime(2026, 11, 7, 22, 40, tzinfo=r.TZ)
        self.fixtures = {
            'campaign.json': [{'id': 'p1', 'title': '<hello>', 'body': '', 'day': 1, 'slot': 1, 'type': 'text'}],
            'ai-posts.json': [],
            'campaign-config.json': {'times': ['09:17'], 'days': 20, 'start_date': ''},
            'ai-config.json': {'times': ['10:17', '20:17'], 'days': 20, 'start_date': ''},
            'campaign-state.json': {'@digihinet': {'start_date': '2026-11-07', 'sent': [
                {'id': 'p1', 'message_id': 1226, 'attempted_at': '2026-11-06T22:25:00+00:00'}], 'pending': None}},
            'ai-state.json': {'@digihinet': {'sent': [], 'pending': {'id': 'unknown'}}},
            'ai-news-state.json': {'@digihinet': {'sent': []}},
        }
        self.reader = patch.object(r, 'read', side_effect=lambda name, default=None: self.fixtures.get(name, default))
        self.reader.start()

    def tearDown(self):
        self.reader.stop()
        self.root.stop()
        self.tmp.cleanup()

    def test_timezone_actual_sends_and_escaping(self):
        text = '\n'.join(r.report('@digihinet', date(2026, 11, 7), self.now))
        self.assertIn('ارسال تأییدشده: 1', text)
        self.assertIn('&lt;hello&gt;', text)
        self.assertIn('https://t.me/digihinet/1226', text)
        self.assertIn('آموزش AI', text)
        self.assertEqual(r.rows_for('@testdigi1231', date(2026, 11, 7))[0], [])

    def test_plan_no_false_success(self):
        text = '\n'.join(r.plan('@digihinet', self.now.date(), self.now))
        self.assertIn('✅ ارسال‌شده', text)
        self.assertIn('تاریخ شروع هنوز ثبت نشده', text)
        self.assertIn('مشروط', text)

    def update(self, uid, kind='private', text='/today'):
        return {'update_id': uid, 'message': {'chat': {'type': kind, 'id': uid},
                'from': {'id': uid}, 'text': text, 'date': int(self.now.timestamp())}}

    def test_authorization_and_no_group_disclosure(self):
        api = FakeAPI([self.update(11), self.update(12), self.update(13, 'group')], {11: 'administrator'})
        state = {'offset': 0}
        with patch.object(r, 'persist') as saved:
            r.poll(api, '@digihinet', state, self.now)
        self.assertEqual(state['offset'], 14)
        self.assertTrue(saved.called)
        self.assertTrue(any('1226' in t for uid, t in api.messages if uid == 11))
        self.assertEqual([t for uid, t in api.messages if uid == 12], ['گزارش فقط برای مدیرهای فعلی کانال در دسترس است.'])
        self.assertFalse(any(uid == 13 for uid, t in api.messages))

    def test_webhook_is_not_removed(self):
        with self.assertRaises(RuntimeError):
            r.poll(FakeAPI(webhook='https://example.com'), '@digihinet', {}, self.now)

    def test_persistence_failure_blocks_reply(self):
        api = FakeAPI([self.update(11)], {11: 'creator'})
        with patch.object(r, 'persist', side_effect=RuntimeError('git failure')):
            with self.assertRaises(RuntimeError):
                r.poll(api, '@digihinet', {}, self.now)
        self.assertFalse(api.messages)

    def test_daily_deduplicated_and_no_raw_manager_id_in_ledger(self):
        api = FakeAPI(roles={987654321: 'administrator'})
        state = {}
        with patch.dict(r.os.environ, {'ENABLE_DAILY_REPORT': 'true', 'TELEGRAM_REPORT_ADMIN_IDS': '987654321'}), patch.object(r, 'persist'):
            r.daily(api, '@digihinet', state, self.now)
            count = len(api.messages)
            r.daily(api, '@digihinet', state, self.now)
        self.assertGreater(count, 0)
        self.assertEqual(count, len(api.messages))
        self.assertNotIn('987654321', str(state))

    def test_unknown_daily_delivery_does_not_retry(self):
        api = FakeAPI(roles={987654321: 'creator'})
        state = {}
        with patch.dict(r.os.environ, {'ENABLE_DAILY_REPORT': 'true', 'TELEGRAM_REPORT_ADMIN_IDS': '987654321'}), patch.object(r, 'persist'), patch.object(api, 'send', side_effect=RuntimeError('timeout')) as send:
            r.daily(api, '@digihinet', state, self.now)
            r.daily(api, '@digihinet', state, self.now)
        self.assertEqual(send.call_count, 1)
        self.assertIn('pending', state['daily'].values())

    def test_daily_late_window_and_chunk_size(self):
        self.assertIsNone(r.daily_target(datetime(2026, 11, 7, 21, 0, tzinfo=r.TZ)))
        self.assertEqual(r.daily_target(datetime(2026, 11, 8, 1, 0, tzinfo=r.TZ)), date(2026, 11, 7))
        self.assertEqual(len(r.split_blocks(['x' * 2000] * 3)), 3)

    def test_invalid_date_and_farsi_command(self):
        self.assertIn('تاریخ را', r.command('/report wrong', '@digihinet', self.now)[0])
        self.assertIn('ارسال تأییدشده: 1', r.command('گزارش الان', '@digihinet', self.now)[0])


if __name__ == '__main__':
    unittest.main()
