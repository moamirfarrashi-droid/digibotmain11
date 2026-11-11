import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch
import unittest
import campaign20, ai_daily, news_fa, publish_due

ROOT = Path(__file__).resolve().parent
TZ = timezone(timedelta(hours=3, minutes=30))


class HoursTests(unittest.TestCase):
    def test_crons_match_local_config(self):
        for module, cfg in [(campaign20, 'campaign-config.json'), (ai_daily, 'ai-config.json')]:
            times = json.loads((ROOT / cfg).read_text())['times']
            for expression, slot in module.CRON_SLOTS.items():
                minute, hour, *_ = expression.split()
                utc = datetime(2026, 11, 11, int(hour), int(minute), tzinfo=timezone.utc)
                self.assertEqual(utc.astimezone(TZ).strftime('%H:%M'), times[slot - 1])

    def test_all_posts_match_new_slots_and_keep_ids(self):
        for filename, cfg, count in [('campaign.json', 'campaign-config.json', 120), ('ai-posts.json', 'ai-config.json', 40)]:
            posts = json.loads((ROOT / filename).read_text())
            times = json.loads((ROOT / cfg).read_text())['times']
            self.assertEqual(len(posts), count)
            self.assertEqual(len({p['id'] for p in posts}), count)
            for p in posts:
                self.assertEqual(p['time'], times[p['slot'] - 1])
                self.assertTrue('07:00' <= p['time'] < '19:00')

    def test_publish_main_never_sends_after_19_or_before_7(self):
        for module in [campaign20, ai_daily]:
            for hour in [0, 6, 19, 23]:
                with patch.object(module, 'datetime') as clock, patch.object(module, 'send') as send, patch('sys.argv', ['bot.py', '--mode', 'publish', '--day', '0', '--slot', '1']):
                    clock.now.return_value = datetime(2026, 11, 11, hour, tzinfo=TZ)
                    module.main()
                    send.assert_not_called()

    def test_catchup_pauses_after_19_including_direct_delivery(self):
        for hour in [6, 19, 23]:
            now = datetime(2026, 11, 11, hour, tzinfo=TZ)
            with patch.object(publish_due, 'queue') as queue:
                self.assertTrue(publish_due.reconcile('@main', now))
                queue.assert_not_called()
            self.assertFalse(publish_due.deliver({}, '@main', 'test', now))

    def test_news_publish_pauses_outside_window(self):
        for hour in [6, 19, 23]:
            with patch.dict(news_fa.os.environ, {'ENABLE_AI_NEWS': 'true'}), patch.object(news_fa, 'collect') as collect:
                news_fa.run('publish', 1, datetime(2026, 11, 11, hour, tzinfo=TZ))
                collect.assert_not_called()


if __name__ == '__main__':
    unittest.main()
