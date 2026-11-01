import unittest
from unittest.mock import patch,Mock
from datetime import date
import requests
import campaign20 as app

class CampaignTests(unittest.TestCase):
    def test_exact_120_unique_slots(self):
        posts=app.load('campaign.json')
        self.assertEqual(len(posts),120)
        self.assertEqual(len({p['id'] for p in posts}),120)
        for day in range(1,21):
            for slot in range(1,7):self.assertEqual(app.select_post(posts,day,slot)['day'],day)
        for p in posts:
            if 'media' in p and p['type']!='video':self.assertGreater((app.ROOT/p['media']).stat().st_size,100)
    def test_day_boundary(self):
        self.assertEqual(app.plan_day('2026-11-03',date(2026,11,3)),1)
        self.assertEqual(app.plan_day('2026-11-03',date(2026,11,22)),20)
        self.assertEqual(app.plan_day('2026-11-03',date(2026,11,23)),21)
    def test_poll_and_animation_requests(self):
        for kind,method in [('poll','sendPoll'),('animation','sendAnimation'),('video','sendVideo')]:
            p=next(p for p in app.load('campaign.json') if p['type']==kind)
            with patch.object(app.requests,'post',return_value=Mock(json=lambda:{'ok':True,'result':{'message_id':42}})) as call:
                self.assertEqual(app.send(p,'@test','TOKEN'),42)
                self.assertTrue(call.call_args.args[0].endswith(method))
    def test_unknown_send_never_retries(self):
        p=app.load('campaign.json')[0]
        with patch.object(app.requests,'post',side_effect=requests.Timeout('TOKEN')) as call:
            with self.assertRaises(RuntimeError) as err:app.send(p,'@test','TOKEN')
            self.assertNotIn('TOKEN',str(err.exception));self.assertEqual(call.call_count,1)
    def test_pending_blocks_send(self):
        real_load=app.load
        def load(name,default=None):
            if name=='campaign-state.json':return {'@test':{'start_date':'2026-11-01','sent':[],'pending':{'id':'uncertain'}}}
            return real_load(name,default)
        with patch.object(app,'load',side_effect=load),patch.object(app,'send') as send,patch.dict(app.os.environ,{'TELEGRAM_TEST_CHANNEL_ID':'@test','TELEGRAM_BOT_TOKEN':'token'}),patch('sys.argv',['campaign20.py','--mode','test','--day','1','--slot','1']):
            with self.assertRaisesRegex(ValueError,'تعیین'):app.main()
            send.assert_not_called()
    def test_sent_slot_is_not_sent_twice(self):
        real_load=app.load
        def load(name,default=None):
            if name=='campaign-state.json':return {'@test':{'start_date':'2026-11-01','sent':[{'id':'dh20-d01-s1'}],'pending':None}}
            return real_load(name,default)
        with patch.object(app,'load',side_effect=load),patch.object(app,'send') as send,patch.dict(app.os.environ,{'TELEGRAM_TEST_CHANNEL_ID':'@test','TELEGRAM_BOT_TOKEN':'token'}),patch('sys.argv',['campaign20.py','--mode','test','--day','1','--slot','1']):
            app.main();send.assert_not_called()

if __name__=='__main__':unittest.main()
