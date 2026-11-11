"""Six-slot, finite, reviewed DigiHinet content campaign. No AI API required."""
import argparse
from datetime import date, datetime, timedelta
import html
import json
import os
from pathlib import Path
import subprocess
import sys
from zoneinfo import ZoneInfo
import requests

ROOT = Path(__file__).resolve().parent
CRON_SLOTS = {'30 3 * * *':1, '30 5 * * *':2, '30 7 * * *':3, '30 9 * * *':4, '30 11 * * *':5, '0 15 * * *':6}

class TelegramRejected(RuntimeError):
    """Telegram explicitly rejected the request; no message was sent."""

def load(name, default=None):
    path = ROOT / name
    if not path.exists() and default is not None:
        return default
    return json.loads(path.read_text(encoding='utf-8'))

def save(state):
    path=ROOT/'campaign-state.json'
    tmp=path.with_suffix('.tmp')
    tmp.write_text(json.dumps(state,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    tmp.replace(path)
    if os.getenv('PERSIST_STATE')=='true':
        subprocess.run(['git','add',path.name],cwd=ROOT,check=True)
        changed=subprocess.run(['git','diff','--cached','--quiet'],cwd=ROOT)
        if changed.returncode==1:
            subprocess.run(['git','commit','-m','Update campaign state [skip ci]'],cwd=ROOT,check=True,stdout=subprocess.DEVNULL)
            subprocess.run(['git','push','origin','HEAD'],cwd=ROOT,check=True,stdout=subprocess.DEVNULL)
        elif changed.returncode!=0:
            raise RuntimeError('بررسی وضعیت گیت ناموفق بود؛ ارسال انجام نشد.')

def plan_day(start, today):
    return (today-date.fromisoformat(start)).days+1

def caption(post):
    return '<b>'+html.escape(post['title'])+'</b>\n\n'+html.escape(post['body'])+'\n\n@digihinet'

def send(post,channel,token):
    if post['type']=='poll':
        method='sendPoll'
        payload={'chat_id':channel,'question':post['body'],
            'options':json.dumps(post['options'],ensure_ascii=False),
            'type':'quiz','correct_option_id':post['correct_option_id'],
            'is_anonymous':'true','explanation':post['explanation']}
    else:
        method={'text':'sendMessage','photo':'sendPhoto','animation':'sendAnimation','video':'sendVideo'}[post['type']]
        payload={'chat_id':channel,'parse_mode':'HTML',
                 'text' if post['type']=='text' else 'caption':caption(post)}
        if post.get('url'):
            payload['reply_markup']=json.dumps({'inline_keyboard':[[{'text':post.get('button','مطالعه'),'url':post['url']}]]})
            if post['type']=='text':
                payload['link_preview_options']=json.dumps({'is_disabled':True})
        if post['type']=='video':
            payload['video']=post['media']
            payload['supports_streaming']='true'
    stream=None
    try:
        files=None
        if post['type'] in ('photo','animation'):
            path=ROOT/post['media']
            stream=path.open('rb')
            files={post['type']:(path.name,stream,'image/gif' if post['type']=='animation' else 'image/png')}
        response=requests.post('https://api.telegram.org/bot'+token+'/'+method,
                               data=payload,files=files,timeout=(10,100))
        try:
            data=response.json()
        except ValueError:
            raise RuntimeError('پاسخ نامشخص؛ کانال و pending را بررسی کنید.') from None
        if data.get('ok') is False:
            reason=str(data.get('description','')).replace(token,'[REDACTED]')[:300]
            raise TelegramRejected(str(data.get('error_code',response.status_code))+' | '+reason)
        if data.get('ok') is not True or 'result' not in data:
            raise RuntimeError('وضعیت ارسال نامشخص؛ pending را بررسی کنید.')
        return data['result']['message_id']
    except requests.RequestException:
        # Do not retry: a timed-out request may already have reached Telegram.
        raise RuntimeError('وضعیت ارسال نامشخص؛ ابتدا کانال را بررسی کنید. تکرار خودکار متوقف شد.') from None
    finally:
        if stream:stream.close()

def select_post(posts,day,slot):
    matches=[p for p in posts if p['day']==day and p['slot']==slot]
    if len(matches)!=1:raise ValueError('روز یا نوبت معتبر نیست.')
    return matches[0]

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--mode',choices=['preview','test','publish'],default='preview')
    parser.add_argument('--day',type=int,default=0)
    parser.add_argument('--slot',type=int,default=0)
    args=parser.parse_args()
    config=load('campaign-config.json')
    posts=load('campaign.json')
    state=load('campaign-state.json',{})
    now=datetime.now(ZoneInfo(config['timezone']))
    if args.mode=='publish' and not (7 <= now.hour < 19):
        print('خارج از بازه انتشار ۰۷:۰۰ تا ۱۹:۰۰ تهران؛ ارسال به بازه بعد موکول شد.');return
    channel=os.getenv('TELEGRAM_TEST_CHANNEL_ID' if args.mode=='test' else 'TELEGRAM_CHANNEL_ID','').strip()
    slot=args.slot or CRON_SLOTS.get(os.getenv('EVENT_SCHEDULE',''),1)
    # Preview is read-only and can inspect any day; manual sends use explicit day.
    first_date=now.date()
    if args.mode=='publish' and slot!=1:
        first_date+=timedelta(days=1)
    channel_state=state.get(channel,{'start_date':config.get('start_date') or first_date.isoformat(),'sent':[],'pending':None})
    day=args.day or (1 if args.mode=='preview' else plan_day(channel_state['start_date'],now.date()))
    if args.mode=='publish' and args.day:
        raise ValueError('روز انتشار اصلی خودکار محاسبه می‌شود؛ day را صفر بگذارید.')
    if day<1:
        print('کمپین هنوز شروع نشده است.');return
    if day>config['days']:
        print('۲۰ روز تمام شده است؛ ارسال متوقف شد.');return
    p=select_post(posts,day,slot)
    out=ROOT/'out';out.mkdir(exist_ok=True)
    (out/'campaign-preview.txt').write_text(p['title']+'\n\n'+p['body']+'\n'+p.get('url',''),encoding='utf-8')
    print(f'روز {day} | نوبت {slot} | {p["type"]} | {p["title"]}')
    if args.mode=='preview':
        print('فقط پیش‌نمایش؛ بدون ارسال و تغییر سابقه.');return
    if args.mode=='publish' and os.getenv('ENABLE_POSTING')!='true':
        print('انتشار اصلی خاموش است.');return
    if not channel:raise ValueError('شناسه کانال تنظیم نشده است.')
    if args.mode=='test' and channel==os.getenv('TELEGRAM_CHANNEL_ID','').strip():
        raise ValueError('کانال تست باید جدا باشد.')
    token=os.getenv('TELEGRAM_BOT_TOKEN','').strip()
    if not token:raise ValueError('توکن تنظیم نشده است.')
    if channel_state.get('pending'):raise ValueError('یک ارسال تعیین‌تکلیف‌نشده وجود دارد؛ ابتدا کانال و pending را بررسی کنید.')
    if any(x['id']==p['id'] for x in channel_state['sent']):
        print('این نوبت قبلاً ارسال شده است.');return
    pending={'id':p['id'],'day':day,'slot':slot,'attempted_at':now.isoformat()}
    state[channel]=channel_state
    channel_state['pending']=pending
    save(state)  # Fail closed if persistence fails before sending.
    try:
        message_id=send(p,channel,token)
    except TelegramRejected:
        channel_state['pending']=None
        save(state)
        raise
    channel_state['sent'].append({**pending,'message_id':message_id})
    channel_state['pending']=None
    save(state)
    print('ارسال موفق؛ شناسه پیام:',message_id)

if __name__=='__main__':
    try:main()
    except Exception as e:
        print('خطا:',str(e) if isinstance(e,(RuntimeError,ValueError)) else type(e).__name__)
        sys.exit(1)

