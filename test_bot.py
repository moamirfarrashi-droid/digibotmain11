import unittest
from unittest.mock import patch, Mock
import bot

class BotTests(unittest.TestCase):
    def test_ready_posts_fit_telegram(self):
        posts = bot.load('posts.json')
        self.assertEqual(len(posts), 32)
        self.assertEqual(len({p['id'] for p in posts}), 32)
        for post in posts: bot.caption(post)
    def test_html_escaping(self):
        self.assertIn('&lt;x&gt;', bot.caption({'title':'<x>', 'body':'A & B', 'type':'text'}))
    def test_used_are_scoped_to_channel(self):
        self.assertEqual(bot.used_ids({'sent':[{'channel':'a','id':'x'}]}, 'b'), set())
    def test_failed_article_falls_back(self):
        with patch('bot.article_candidates', side_effect=bot.requests.ConnectionError):
            p = bot.select({}, {'sent':[]}, 'a', 'article')
            self.assertEqual(p['type'], 'text')
    def test_exhaustion_does_not_repeat(self):
        sent = [{'channel':'a','id':p['id']} for p in bot.load('posts.json')]
        self.assertIsNone(bot.select({}, {'sent':sent}, 'a', 'text'))
    def test_success_message_id(self):
        r=Mock();r.json.return_value={'ok':True,'result':{'message_id':12}}
        with patch('bot.requests.post', return_value=r):
            self.assertEqual(bot.send({'title':'t','body':'b','type':'text'},'x','dummy'),12)
    def test_timeout_no_automatic_retry_or_secret(self):
        with patch('bot.requests.post', side_effect=bot.requests.Timeout('SECRET')) as request:
            with self.assertRaises(RuntimeError) as e:
                bot.send({'title':'t','body':'b','type':'text'},'x','dummy')
            self.assertNotIn('SECRET',str(e.exception));self.assertEqual(request.call_count,1)
    def test_video_detection(self):
        p=bot.Videos();p.feed('<video><source src="/sample.mp4"></video>')
        self.assertEqual(p.urls,['/sample.mp4'])
    def test_pending_blocks_before_sending(self):
        state={'sent':[],'pending':{'id':'x'}}
        with patch('sys.argv',['bot.py','--mode','publish']), patch.dict('os.environ',{'ENABLE_POSTING':'true','TELEGRAM_BOT_TOKEN':'dummy'}), patch('bot.load',side_effect=[{'week':{}},state]), patch('bot.send') as send:
            with self.assertRaises(ValueError): bot.main()
            send.assert_not_called()

if __name__=='__main__': unittest.main()
