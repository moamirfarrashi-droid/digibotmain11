"""Short Persian AI/technology news from public feeds; no translation service."""
import argparse,hashlib,html,json,os,re,sys,unicodedata
from datetime import datetime,timezone
from pathlib import Path
from difflib import SequenceMatcher
from html.parser import HTMLParser
from zoneinfo import ZoneInfo
import xml.etree.ElementTree as ET
import requests
import ai_daily as transport
ROOT=Path(__file__).resolve().parent
CRONS={'47 8 * * *':1,'47 15 * * *':2}
class PlainText(HTMLParser):
 def __init__(self):super().__init__();self.parts=[];self.hidden=0
 def handle_starttag(self,tag,attrs):
  if tag in ('script','style'):self.hidden+=1
 def handle_endtag(self,tag):
  if tag in ('script','style'):self.hidden=max(0,self.hidden-1)
 def handle_data(self,data):
  if not self.hidden:self.parts.append(data)
def plain(value):
 p=PlainText();p.feed(value or '');return re.sub(r'\s+',' ',html.unescape(' '.join(p.parts))).strip()
def normal(t):
 t=unicodedata.normalize('NFKC',t).replace('ي','ی').replace('ك','ک').replace('\u200c',' ')
 return re.sub(r'[^\w\s]','',t).lower().strip()
def similar(a,b):return SequenceMatcher(None,normal(a),normal(b)).ratio()>=.72

def collect(now=None):
 from collect_ai_news import parse_feed
 cfg=json.loads((ROOT/'ai-sources.json').read_text());now=now or datetime.now(timezone.utc);found=[];errors=[]
 for source in cfg['feeds']:
  try:
   r=requests.get(source['url'],timeout=(8,20),headers={'User-Agent':'DigiHinet-News-Reader/1.0'});r.raise_for_status()
   if len(r.content)>12000000:raise ValueError('Feed size')
   candidates=parse_feed(r.content,source['name'],now,cfg['policy']['max_age_hours'])
   root=ET.fromstring(r.content);items=root.findall('./channel/item');details={}
   for item in items:
    url=(item.findtext('link') or '').strip()
    desc=plain(item.findtext('description') or '')
    details[url]=desc
   for p in candidates:
    title=plain(p['title']);n=normal(title)
    if not re.search('[\u0600-\u06ff]',title):continue
    if any(normal(w) in n for w in cfg['exclude_keywords']):continue
    ai=any(normal(w) in n for w in cfg['ai_keywords'])
    tech=any(normal(w) in n for w in cfg['tech_keywords'])
    if not ai and not tech:continue
    summary=details.get(p['url'],'')
    # Feed excerpts only. Remove website promo boilerplate and embedded source URLs.
    summary=re.split(r'نوشته .*اولین بار|این نوشته .*اولین بار|The post',summary)[0]
    summary=re.sub(r'https?://\S+','',summary)
    if title in summary:summary=summary.replace(title,'',1).strip(' .؛:')
    for name in cfg['display_source_names']:summary=summary.replace(name,'')
    summary=re.sub(r'\s+',' ',summary).strip(' .؛:')
    if summary:
     words=summary.split();summary=' '.join(words[:38])
     if len(words)>38:summary+='…'
    p.update(title=title[:230],summary=summary,score=2 if ai else 1)
    found.append(p)
  except (requests.RequestException,ET.ParseError,ValueError) as e:errors.append({'source':source['name'],'error':type(e).__name__})
 ordered=sorted(found,key=lambda p:(p['score'],datetime.fromisoformat(p['published_at'])),reverse=True);unique=[]
 for p in ordered:
  if not any(p['url']==q['url'] or similar(p['title'],q['title']) for q in unique):unique.append(p)
 return unique,errors

def save(state):
 path=ROOT/'ai-news-state.json';temp=path.with_suffix('.tmp');temp.write_text(json.dumps(state,ensure_ascii=False,indent=2)+'\n');temp.replace(path)
 if os.getenv('PERSIST_STATE')=='true':
  import subprocess
  subprocess.run(['git','add',path.name],cwd=ROOT,check=True)
  diff=subprocess.run(['git','diff','--cached','--quiet'],cwd=ROOT)
  if diff.returncode==1:
   subprocess.run(['git','commit','-m','Save Persian news state [skip ci]'],cwd=ROOT,check=True,stdout=subprocess.DEVNULL)
   subprocess.run(['git','push','origin','HEAD'],cwd=ROOT,check=True,stdout=subprocess.DEVNULL)
  elif diff.returncode:raise RuntimeError('ثبت سابقه ناموفق بود.')
def make_post(p):
 return {'type':'text','title':'📰 '+p['title'],'body':p['summary'] or 'خبر تازه هوش مصنوعی و فناوری','id':hashlib.sha256(p['url'].encode()).hexdigest()[:20]}

def run(mode,slot,now=None):
 if slot not in (1,2):raise ValueError('نوبت باید ۱ یا ۲ باشد.')
 if mode=='publish' and os.getenv('ENABLE_AI_NEWS')!='true':print('خبر خودکار خاموش است.');return
 now=now or datetime.now(ZoneInfo('Asia/Tehran'));datekey=now.astimezone(ZoneInfo('Asia/Tehran')).date().isoformat();key=datekey+'-s'+str(slot)
 channel=os.getenv('TELEGRAM_TEST_CHANNEL_ID' if mode=='test' else 'TELEGRAM_CHANNEL_ID','').strip()
 if mode!='preview':
  if not channel:raise ValueError('کانال تنظیم نشده است.')
  if mode=='test' and channel==os.getenv('TELEGRAM_CHANNEL_ID','').strip():raise ValueError('کانال تست باید جدا باشد.')
 state=json.loads((ROOT/'ai-news-state.json').read_text()) if (ROOT/'ai-news-state.json').exists() else {}
 cs=state.get(channel,{'sent':[],'pending':None})
 if mode!='preview':
  if cs.get('pending'):raise ValueError('ارسال نامشخص قبلی را در کانال بررسی کنید؛ تکرار متوقف است.')
  if any(x['key']==key for x in cs['sent']):print('خبر این نوبت امروز قبلاً ارسال شده است.');return
 candidates,errors=collect(now.astimezone(timezone.utc));chosen=next((p for p in candidates if not any(p['url']==x['url'] or similar(p['title'],x['title']) for x in cs['sent'])),None)
 out=ROOT/'out';out.mkdir(exist_ok=True)
 (out/'ai-news-candidates.json').write_text(json.dumps({'collected_at':now.isoformat(),'candidates':candidates,'errors':errors},ensure_ascii=False,indent=2))
 if not chosen:
  if not candidates and len(errors)==len(json.loads((ROOT/'ai-sources.json').read_text())['feeds']):raise RuntimeError('دریافت همه منابع ناموفق بود؛ خبری ارسال نشد.')
  print('خبر تازه و غیرتکراری موجود نیست؛ خبری ارسال نشد.');return
 post=make_post(chosen)
 (out/'news-fa-preview.txt').write_text(post['title']+'\n\n'+post['body'])
 if mode=='preview':print('پیش‌نمایش فارسی آماده شد؛ بدون ارسال.');return
 token=os.getenv('TELEGRAM_BOT_TOKEN','').strip()
 if not token:raise ValueError('توکن تنظیم نشده است.')
 pending={'key':key,'url':chosen['url'],'title':chosen['title'],'source':chosen['source'],'attempted_at':now.isoformat()}
 cs['pending']=pending;state[channel]=cs;save(state)
 try:mid=transport.send(post,channel,token)
 except transport.TelegramRejected:
  cs['pending']=None;save(state);raise
 cs['sent'].append({**pending,'message_id':mid});cs['pending']=None;save(state)
 print('خبر فارسی ارسال شد؛ شناسه پیام:',mid)
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--mode',choices=['preview','test','publish'],default='preview');ap.add_argument('--slot',type=int,default=0);a=ap.parse_args()
 run(a.mode,a.slot or CRONS.get(os.getenv('EVENT_SCHEDULE',''),1))
if __name__=='__main__':
 try:main()
 except Exception as e:
  print('خطا:',str(e) if isinstance(e,(ValueError,RuntimeError)) else type(e).__name__);sys.exit(1)
