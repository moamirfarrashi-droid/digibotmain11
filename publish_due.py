"""Reconcile overdue campaign slots against confirmed Telegram delivery history."""
import argparse
from datetime import date, datetime, timedelta
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
TZ = ZoneInfo('Asia/Tehran')
SPECS = [('normal', 'campaign20', 'campaign', 'ENABLE_POSTING'),
         ('ai', 'ai_daily', 'ai', 'ENABLE_AI_POSTING')]


def read(name, default=None):
    path = ROOT / name
    if not path.exists() and default is not None:
        return default
    return json.loads(path.read_text(encoding='utf-8-sig'))


def campaign_posts(prefix):
    return read('campaign.json' if prefix == 'campaign' else 'ai-posts.json')


def queue(channel, now):
    tasks, blocked = [], []
    for kind, module, prefix, flag in SPECS:
        if os.getenv(flag) != 'true':
            continue
        cfg = read(prefix + '-config.json')
        state = read(prefix + '-state.json', {}).get(channel, {})
        if state.get('pending'):
            blocked.append(kind)
            continue
        start = state.get('start_date') or cfg.get('start_date') or now.date().isoformat()
        start_date = date.fromisoformat(start)
        sent = {x['id'] for x in state.get('sent', [])}
        for p in campaign_posts(prefix):
            if not 1 <= p['day'] <= cfg['days'] or p['id'] in sent:
                continue
            clock = p.get('time') or cfg['times'][p['slot'] - 1]
            scheduled = datetime.fromisoformat(
                (start_date + timedelta(days=p['day'] - 1)).isoformat() + 'T' + clock + ':00+03:30')
            if scheduled <= now:
                tasks.append({'kind': kind, 'module': module, 'prefix': prefix,
                              'post': p, 'scheduled_at': scheduled.isoformat(), 'start_date': start})
    tasks.sort(key=lambda t: (t['scheduled_at'], t['kind'], t['post']['slot']))
    return tasks, blocked


def deliver(task, channel, token, now):
    mod = importlib.import_module(task['module'])
    prefix, p = task['prefix'], task['post']
    state = read(prefix + '-state.json', {})
    cs = state.setdefault(channel, {'start_date': task['start_date'], 'sent': [], 'pending': None})
    if cs.get('pending'):
        raise RuntimeError('ارسال نامشخص قبلی وجود دارد؛ بدون بررسی دوباره ارسال نمی‌شود.')
    if any(x['id'] == p['id'] for x in cs['sent']):
        return False
    # Missing local media is a definite pre-send failure, not an ambiguous delivery.
    if p['type'] in ('photo', 'animation'):
        path = ROOT / p['media']
        if not path.is_file() or path.stat().st_size == 0:
            raise RuntimeError('فایل تصویر یا GIF معتبر موجود نیست: ' + p['media'])
    pending = {'id': p['id'], 'day': p['day'], 'slot': p['slot'],
               'scheduled_at': task['scheduled_at'], 'attempted_at': now.isoformat()}
    cs['pending'] = pending
    mod.save(state)  # Persist reservation before sending; never send if Git fails.
    try:
        mid = mod.send(p, channel, token)
    except mod.TelegramRejected:
        cs['pending'] = None
        mod.save(state)
        raise
    cs['sent'].append({**pending, 'message_id': mid})
    cs['pending'] = None
    mod.save(state)
    print('ارسال جبرانی موفق:', task['kind'], p['id'], '| شناسه پیام:', mid)
    return True


def save_retry(state):
    path = ROOT / 'catchup-retry-state.json'
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    tmp.replace(path)
    if os.getenv('PERSIST_STATE') != 'true':
        return
    subprocess.run(['git', 'add', path.name], cwd=ROOT, check=True, capture_output=True)
    diff = subprocess.run(['git', 'diff', '--cached', '--quiet'], cwd=ROOT)
    if diff.returncode == 1:
        subprocess.run(['git', 'commit', '-m', 'Update catch-up retry state [skip ci]'], cwd=ROOT, check=True, capture_output=True)
        subprocess.run(['git', 'push', 'origin', 'HEAD'], cwd=ROOT, check=True, capture_output=True)
    elif diff.returncode:
        raise RuntimeError('ثبت زمان بررسی خبر ناموفق بود.')


def news_due(channel, now):
    if os.getenv('ENABLE_AI_NEWS') != 'true':
        return []
    state = read('ai-news-state.json', {}).get(channel, {})
    if state.get('pending'):
        raise RuntimeError('خبر ارسال نامشخص دارد؛ نیاز به بررسی کانال است.')
    sent = {x['key'] for x in state.get('sent', [])}
    return [slot for slot, clock in [(1, '12:17'), (2, '19:17')]
            if now.strftime('%H:%M') >= clock and now.date().isoformat() + '-s' + str(slot) not in sent]


def reconcile(channel, now, max_posts=4, gap=10, preview=False):
    tasks, blocked = queue(channel, now)
    if blocked:
        print('ارسال نامشخص قبلی؛ دسته‌های متوقف:', ', '.join(blocked))
    print('تعداد پست‌های آماده موعدرسیده و ارسال‌نشده:', len(tasks))
    errors = bool(blocked)
    failed = set()
    attempts = 0
    for task in tasks:
        if attempts >= max_posts:
            break
        if task['kind'] in failed:
            continue
        print('نوبت:', task['scheduled_at'], task['post']['id'])
        if preview:
            continue
        attempts += 1
        try:
            deliver(task, channel, os.environ['TELEGRAM_BOT_TOKEN'].strip(), datetime.now(TZ))
        except Exception as exc:
            failed.add(task['kind'])
            errors = True
            print('ارسال انجام یا تأیید نشد:', task['kind'],
                  str(exc) if isinstance(exc, (RuntimeError, ValueError)) else type(exc).__name__)
        if attempts < max_posts:
            time.sleep(gap)
    try:
        slots = news_due(channel, now)
        if preview:
            print('نوبت‌های خبر امروز در انتظار بررسی:', slots)
        elif slots:
            retry = read('catchup-retry-state.json', {})
            stamp = retry.get(channel, {}).get('news_retry_after')
            if not stamp or datetime.fromisoformat(stamp) <= now:
                # Backoff source fetching when no suitable fresh news exists.
                retry[channel] = {'news_retry_after': (now + timedelta(minutes=30)).isoformat()}
                save_retry(retry)
                news = importlib.import_module('news_fa')
                for slot in slots:
                    news.run('publish', slot, now=datetime.now(TZ))
    except Exception as exc:
        errors = True
        print('بررسی خبر کامل نشد:', str(exc) if isinstance(exc, (RuntimeError, ValueError)) else type(exc).__name__)
    return not errors


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--preview', action='store_true')
    args = parser.parse_args()
    channel = os.getenv('TELEGRAM_CHANNEL_ID', '').strip()
    if not channel:
        raise ValueError('شناسه کانال اصلی تنظیم نشده است.')
    if not args.preview:
        if not os.getenv('TELEGRAM_BOT_TOKEN', '').strip():
            raise ValueError('توکن تنظیم نشده است.')
        if os.getenv('PERSIST_STATE') != 'true':
            raise ValueError('برای جبران خودکار، ثبت دائمی سابقه باید فعال باشد.')
    ok = reconcile(channel, datetime.now(TZ), preview=args.preview)
    if not ok:
        sys.exit(1)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('خطا:', str(exc) if isinstance(exc, (RuntimeError, ValueError)) else type(exc).__name__)
        sys.exit(1)
