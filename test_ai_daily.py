import unittest,json,os,sys,tempfile
from pathlib import Path
from unittest.mock import patch
from datetime import datetime,timezone
import ai_daily as ai
from collect_ai_news import parse_feed
class Tests(unittest.TestCase):
 def test_plan(self):
  posts=ai.load('ai-posts.json')
  self.assertEqual(len(posts),40)
  self.assertEqual(len({p['id'] for p in posts}),40)
  self.assertEqual({(p['day'],p['slot']) for p in posts},{(d,s) for d in range(1,21) for s in (1,2)})
  for p in posts:
   self.assertTrue((ai.ROOT/p['media']).is_file())
   self.assertLess(len((p['title']+p['body']+'@digihinet').encode('utf-16-le'))//2,1024)
 def test_duplicate_is_not_sent(self):
  posts=ai.load('ai-posts.json');config=ai.load('ai-config.json')
  state={'@example_test':{'start_date':'2026-01-01','sent':[{'id':posts[0]['id']}],'pending':None}}
  with tempfile.TemporaryDirectory() as tmp,patch.object(ai,'ROOT',Path(tmp)),patch.object(ai,'load',side_effect=lambda n,default=None:config if n=='ai-config.json' else posts if n=='ai-posts.json' else state),patch.object(sys,'argv',['ai_daily.py','--mode','test','--day','1','--slot','1']),patch.dict(os.environ,{'TELEGRAM_TEST_CHANNEL_ID':'@example_test','TELEGRAM_CHANNEL_ID':'@main','TELEGRAM_BOT_TOKEN':'fake'}),patch.object(ai,'send') as send:
   ai.main();send.assert_not_called()
 def test_ambiguous_send_keeps_pending(self):
  posts=ai.load('ai-posts.json');config=ai.load('ai-config.json');state={}
  with tempfile.TemporaryDirectory() as tmp,patch.object(ai,'ROOT',Path(tmp)),patch.object(ai,'load',side_effect=lambda n,default=None:config if n=='ai-config.json' else posts if n=='ai-posts.json' else state),patch.object(sys,'argv',['ai_daily.py','--mode','test','--day','1','--slot','1']),patch.dict(os.environ,{'TELEGRAM_TEST_CHANNEL_ID':'@example_test','TELEGRAM_CHANNEL_ID':'@main','TELEGRAM_BOT_TOKEN':'fake'}),patch.object(ai,'save'),patch.object(ai,'send',side_effect=RuntimeError('unknown')):
   with self.assertRaises(RuntimeError):ai.main()
   self.assertEqual(state['@example_test']['pending']['id'],posts[0]['id'])
 def test_feed_age_and_dates(self):
  blob=b'<rss><channel><item><title>new</title><link>https://example.com/new</link><pubDate>Tue, 03 Nov 2026 10:00:00 GMT</pubDate></item><item><title>old</title><link>https://example.com/old</link><pubDate>Tue, 01 Jan 2020 10:00:00 GMT</pubDate></item><item><title>undated</title><link>https://example.com/none</link></item></channel></rss>'
  found=parse_feed(blob,'test',datetime(2026,11,3,11,tzinfo=timezone.utc),48)
  self.assertEqual([p['title'] for p in found],['new'])
if __name__=='__main__':unittest.main()
