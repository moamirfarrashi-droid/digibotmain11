"""Collect dated news candidates, never translate or publish unreviewed news."""
import json
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
import xml.etree.ElementTree as ET
import requests
ROOT=Path(__file__).resolve().parent

def clean_url(url):
 p=urlsplit(url)
 if p.scheme not in ('https','http') or not p.netloc:return None
 return urlunsplit((p.scheme,p.netloc,p.path,p.query,''))

def parse_feed(blob,source,now,max_age):
 root=ET.fromstring(blob);result=[]
 entries=root.findall('./channel/item') or root.findall('{http://www.w3.org/2005/Atom}entry')
 for item in entries:
  fields={x.tag.split('}')[-1]:x for x in item}
  def val(key):
   e=fields.get(key)
   return ''.join(e.itertext()).strip() if e is not None else ''
  title=re.sub('<[^>]+>','',val('title')).strip()
  link=val('link')
  if not link and fields.get('link') is not None:link=fields['link'].get('href','')
  url=clean_url(link)
  raw=val('pubDate') or val('published') or val('updated')
  try:
   try:dt=parsedate_to_datetime(raw)
   except (ValueError,TypeError):dt=datetime.fromisoformat(raw.replace('Z','+00:00'))
   if dt.tzinfo is None:continue
   age=(now-dt).total_seconds()/3600
  except (ValueError,TypeError,OverflowError):continue
  if not title or not url or not -1<=age<=max_age:continue
  result.append(dict(source=source,title=title[:500],url=url,published_at=dt.isoformat(),review_status='unreviewed'))
 return result

def main():
 config=json.loads((ROOT/'ai-sources.json').read_text());now=datetime.now(timezone.utc);found=[];errors=[]
 for source in config['feeds']:
  try:
   r=requests.get(source['url'],timeout=(8,20),headers={'User-Agent':'DigiHinet-News-Reader/1.0'})
   r.raise_for_status()
   if len(r.content)>6000000:raise ValueError('Feed exceeds size limit')
   found+=parse_feed(r.content,source['name'],now,config['policy']['max_age_hours'])
  except (requests.RequestException,ValueError,ET.ParseError) as e:errors.append({'source':source['name'],'error':type(e).__name__})
 unique={}
 for p in found:unique.setdefault(p['url'],p)
 found=sorted(unique.values(),key=lambda x:datetime.fromisoformat(x['published_at']),reverse=True)
 out=ROOT/'out';out.mkdir(exist_ok=True)
 (out/'ai-news-candidates.json').write_text(json.dumps(dict(collected_at=now.isoformat(),published=False,candidates=found,errors=errors),ensure_ascii=False,indent=2))
 print('خبرهای پیشنهادی:',len(found),'| فقط برای بررسی؛ هیچ خبری منتشر نشد.','| منابع ناموفق:',len(errors))
 return found
if __name__=='__main__':main()
