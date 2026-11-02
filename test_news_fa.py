import unittest,json,os,tempfile
from pathlib import Path
from unittest.mock import patch
from datetime import datetime,timezone
import news_fa as n
class Tests(unittest.TestCase):
 def test_post(self):
  p=n.make_post(dict(title='خبر فارسی',summary='خلاصه فارسی',url='https://example.com/a',source='ناشر'))
  self.assertNotIn('url',p);self.assertNotIn('ناشر',p['body'])
 def test_duplicates(self):
  self.assertTrue(n.similar('هوش مصنوعی گوگل منتشر شد!','هوش مصنوعي گوگل منتشر شد'))
 def test_html(self):self.assertEqual(n.plain('<p>خبر <b>فارسی</b></p><script>bad()</script>'),'خبر فارسی')
 def test_send_and_duplicate(self):
  c=dict(title='خبر فناوری',summary='خلاصه',url='https://example.com/a',source='test')
  with tempfile.TemporaryDirectory() as t,patch.object(n,'ROOT',Path(t)),patch.dict(os.environ,dict(TELEGRAM_TEST_CHANNEL_ID='@test',TELEGRAM_CHANNEL_ID='@main',TELEGRAM_BOT_TOKEN='fake',PERSIST_STATE='false')),patch.object(n,'collect',return_value=([c],[])) as collect,patch.object(n.transport,'send') as send:
   def check(*args):
    self.assertTrue(json.loads((Path(t)/'ai-news-state.json').read_text())['@test']['pending']);return 9
   send.side_effect=check;now=datetime(2026,11,3,10,tzinfo=timezone.utc)
   n.run('test',1,now);n.run('test',1,now)
   self.assertEqual(send.call_count,1);self.assertEqual(collect.call_count,1)
   n.run('test',2,now);self.assertEqual(send.call_count,1)
 def test_unknown_send_stops_retry(self):
  c=dict(title='خبر فناوری',summary='خلاصه',url='https://example.com/a',source='test')
  with tempfile.TemporaryDirectory() as t,patch.object(n,'ROOT',Path(t)),patch.dict(os.environ,dict(TELEGRAM_TEST_CHANNEL_ID='@test',TELEGRAM_CHANNEL_ID='@main',TELEGRAM_BOT_TOKEN='fake',PERSIST_STATE='false')),patch.object(n,'collect',return_value=([c],[])),patch.object(n.transport,'send',side_effect=RuntimeError('unknown')) as send:
   with self.assertRaises(RuntimeError):n.run('test',1)
   with self.assertRaises(ValueError):n.run('test',1)
   self.assertEqual(send.call_count,1)
if __name__=='__main__':unittest.main()
