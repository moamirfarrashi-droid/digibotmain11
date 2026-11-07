import json
from datetime import datetime
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch
import publish_due as q


class CatchupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.patcher = patch.object(q, 'ROOT', self.root)
        self.patcher.start()
        self.flags = patch.dict(q.os.environ, {'ENABLE_POSTING': 'true', 'ENABLE_AI_POSTING': 'true',
                                                'ENABLE_AI_NEWS': 'false', 'TELEGRAM_BOT_TOKEN': 'offline-test-only'})
        self.flags.start()
        self.now = datetime(2026, 11, 7, 14, 0, tzinfo=q.TZ)
        for prefix, clocks in [('campaign', ['09:17', '11:17']), ('ai', ['10:17', '20:17'])]:
            self.write(prefix + '-config.json', {'days': 20, 'times': clocks, 'start_date': ''})
            posts = [{'id': prefix + str(day) + str(slot), 'day': day, 'slot': slot,
                      'type': 'text', 'title': 'test', 'body': ''}
                     for day in range(1, 3) for slot in (1, 2)]
            self.write('campaign.json' if prefix == 'campaign' else 'ai-posts.json', posts)
            self.write(prefix + '-state.json', {'@main': {'start_date': '2026-11-07', 'sent': [], 'pending': None}})

    def tearDown(self):
        self.flags.stop()
        self.patcher.stop()
        self.tmp.cleanup()

    def write(self, name, value):
        (self.root / name).write_text(json.dumps(value))

    def test_due_only_chronological_no_future(self):
        tasks, blocked = q.queue('@main', self.now)
        self.assertEqual([x['post']['id'] for x in tasks], ['campaign11', 'ai11', 'campaign12'])
        self.assertFalse(blocked)

    def test_sent_is_never_queued_again(self):
        self.write('campaign-state.json', {'@main': {'start_date': '2026-11-07', 'sent': [{'id': 'campaign11'}], 'pending': None}})
        self.assertNotIn('campaign11', [x['post']['id'] for x in q.queue('@main', self.now)[0]])

    def test_past_days_retained_after_midnight(self):
        later = datetime(2026, 11, 8, 1, 0, tzinfo=q.TZ)
        self.assertEqual(len(q.queue('@main', later)[0]), 4)

    def test_pending_blocks_only_affected_category(self):
        self.write('campaign-state.json', {'@main': {'start_date': '2026-11-07', 'sent': [], 'pending': {'id': 'x'}}})
        tasks, blocked = q.queue('@main', self.now)
        self.assertEqual(blocked, ['normal'])
        self.assertEqual([x['post']['id'] for x in tasks], ['ai11'])

    def test_unstarted_ai_can_begin_after_missed_morning(self):
        self.write('ai-state.json', {})
        tasks = q.queue('@main', self.now)[0]
        task = next(x for x in tasks if x['kind'] == 'ai')
        self.assertEqual(task['start_date'], '2026-11-07')
        self.assertEqual(task['post']['id'], 'ai11')

    def fake_transport(self, error=None, fail_save=False):
        class Rejected(RuntimeError):
            pass
        def save(state):
            if fail_save:
                raise RuntimeError('git unavailable')
            self.write('campaign-state.json', state)
        from unittest.mock import Mock
        return types.SimpleNamespace(save=save, send=Mock(side_effect=error, return_value=77), TelegramRejected=Rejected)

    def test_persist_failure_never_sends(self):
        mod = self.fake_transport(fail_save=True)
        task = q.queue('@main', self.now)[0][0]
        with patch.object(q.importlib, 'import_module', return_value=mod), self.assertRaises(RuntimeError):
            q.deliver(task, '@main', 'offline-test-only', self.now)
        mod.send.assert_not_called()

    def test_unknown_network_result_keeps_pending(self):
        mod = self.fake_transport(error=RuntimeError('unknown'))
        task = q.queue('@main', self.now)[0][0]
        with patch.object(q.importlib, 'import_module', return_value=mod), self.assertRaises(RuntimeError):
            q.deliver(task, '@main', 'offline-test-only', self.now)
        self.assertIsNotNone(q.read('campaign-state.json')['@main']['pending'])
        self.assertEqual(q.read('campaign-state.json')['@main']['sent'], [])

    def test_success_saved_and_duplicate_skipped(self):
        mod = self.fake_transport()
        task = q.queue('@main', self.now)[0][0]
        with patch.object(q.importlib, 'import_module', return_value=mod):
            self.assertTrue(q.deliver(task, '@main', 'offline-test-only', self.now))
            self.assertFalse(q.deliver(task, '@main', 'offline-test-only', self.now))
        self.assertEqual(mod.send.call_count, 1)
        self.assertIsNone(q.read('campaign-state.json')['@main']['pending'])

    def test_disabled_flag_stops_category(self):
        with patch.dict(q.os.environ, {'ENABLE_POSTING': 'false'}):
            self.assertTrue(all(t['kind'] == 'ai' for t in q.queue('@main', self.now)[0]))

    def test_news_today_only_and_confirmed_slot_skipped(self):
        self.write('ai-news-state.json', {'@main': {'sent': [{'key': '2026-11-07-s1'}], 'pending': None}})
        with patch.dict(q.os.environ, {'ENABLE_AI_NEWS': 'true'}):
            self.assertEqual(q.news_due('@main', self.now), [])
            self.assertEqual(q.news_due('@main', self.now.replace(hour=20)), [2])

    def test_one_failed_category_does_not_block_other(self):
        def send(task, *args):
            if task['kind'] == 'normal':
                raise RuntimeError('rejected')
            return True
        with patch.object(q, 'deliver', side_effect=send) as mock, patch.object(q.time, 'sleep'):
            self.assertFalse(q.reconcile('@main', self.now))
        self.assertEqual([x.args[0]['kind'] for x in mock.call_args_list], ['normal', 'ai'])


if __name__ == '__main__':
    unittest.main()
