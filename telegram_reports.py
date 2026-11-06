"""Private admin reports for the existing Telegram bot. No AI or new bot."""
import argparse
from datetime import date, datetime, timedelta
import hashlib
import html
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from zoneinfo import ZoneInfo
import requests

ROOT = Path(__file__).resolve().parent
TZ = ZoneInfo('Asia/Tehran')
STATE = 'telegram-report-state.json'
LABELS = {'normal': 'محتوای عادی', 'ai': 'آموزش AI', 'news': 'خبر تازه'}
TYPES = {'text': 'متن', 'photo': 'تصویر', 'video': 'ویدیو', 'animation': 'GIF', 'poll': 'نظرسنجی'}
ESC = html.escape


def read(name, default=None):
    p = ROOT / name
    if not p.exists():
        if default is not None:
            return default
        raise RuntimeError('فایل موردنیاز موجود نیست: ' + name)
    return json.loads(p.read_text(encoding='utf-8-sig'))


def persist(state):
    path = ROOT / STATE
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    tmp.replace(path)
    if os.getenv('PERSIST_STATE') != 'true':
        return
    subprocess.run(['git', 'add', STATE], cwd=ROOT, check=True, capture_output=True)
    diff = subprocess.run(['git', 'diff', '--cached', '--quiet'], cwd=ROOT)
    if diff.returncode == 1:
        subprocess.run(['git', 'commit', '-m', 'Save report delivery state [skip ci]'], cwd=ROOT, check=True, capture_output=True)
        subprocess.run(['git', 'push', 'origin', 'HEAD'], cwd=ROOT, check=True, capture_output=True)
    elif diff.returncode:
        raise RuntimeError('ذخیره سابقه گزارش ناموفق بود.')


class API:
    def __init__(self, token):
        self.token = token

    def call(self, method, **payload):
        try:
            r = requests.post('https://api.telegram.org/bot' + self.token + '/' + method,
                              json=payload, timeout=(8, 25))
            data = r.json()
        except (requests.RequestException, ValueError):
            raise RuntimeError('ارتباط تلگرام نامشخص بود؛ تکرار خودکار ارسال انجام نشد.') from None
        if data.get('ok') is not True:
            # Never log Telegram payloads, identifiers, URLs containing tokens, or raw exceptions.
            raise RuntimeError('تلگرام درخواست ' + method + ' را نپذیرفت؛ کد ' + str(data.get('error_code', r.status_code)))
        return data.get('result')

    def send(self, chat, text):
        return self.call('sendMessage', chat_id=chat, text=text, parse_mode='HTML',
                         link_preview_options={'is_disabled': True})


def day_of(stamp):
    return datetime.fromisoformat(stamp).astimezone(TZ).date()


def sources(channel):
    out = []
    for cat, state_name, posts_name, config_name in [
        ('normal', 'campaign-state.json', 'campaign.json', 'campaign-config.json'),
        ('ai', 'ai-state.json', 'ai-posts.json', 'ai-config.json'),
        ('news', 'ai-news-state.json', None, None),
    ]:
        state = read(state_name, {}).get(channel, {})
        posts = read(posts_name) if posts_name else []
        config = read(config_name) if config_name else {}
        out.append((cat, state, {p['id']: p for p in posts}, config))
    return out


def rows_for(channel, target):
    rows, pending = [], []
    for cat, state, posts, config in sources(channel):
        for sent in state.get('sent', []):
            if day_of(sent['attempted_at']) == target and sent.get('message_id'):
                p = posts.get(sent.get('id'), {})
                rows.append({'category': cat, 'title': p.get('title') or sent.get('title') or sent.get('id', 'پست'),
                             'type': p.get('type', 'text'), **sent})
        if state.get('pending'):
            pending.append(LABELS[cat])
    rows.sort(key=lambda p: datetime.fromisoformat(p['attempted_at']))
    return rows, pending


def message_link(channel, mid):
    if re.fullmatch(r'@[A-Za-z0-9_]+', channel):
        return 'https://t.me/' + channel[1:] + '/' + str(int(mid))
    if re.fullmatch(r'-100\d+', channel):
        return 'https://t.me/c/' + channel[4:] + '/' + str(int(mid))
    return ''


def split_blocks(blocks, limit=3500):
    chunks, current = [], ''
    for block in blocks:
        if len(block) > limit:
            raise RuntimeError('بخش گزارش بیش از حد بلند است.')
        if current and len(current) + len(block) + 2 > limit:
            chunks.append(current)
            current = ''
        current += ('\n\n' if current else '') + block
    if current:
        chunks.append(current)
    return chunks


def report(channel, target, now=None):
    now = now or datetime.now(TZ)
    rows, pending = rows_for(channel, target)
    counts = {cat: sum(r['category'] == cat for r in rows) for cat in LABELS}
    header = ('📊 <b>گزارش انتشار ' + target.isoformat() + '</b>\nکانال: ' + ESC(channel) +
              '\n✅ ارسال تأییدشده: ' + str(len(rows)) + '\n' +
              ' | '.join(LABELS[c] + ': ' + str(counts[c]) for c in LABELS))
    blocks = [header]
    for i, row in enumerate(rows, 1):
        stamp = datetime.fromisoformat(row['attempted_at']).astimezone(TZ).strftime('%H:%M')
        link = message_link(channel, row['message_id'])
        title = ESC(str(row['title'])[:240])
        blocks.append(str(i) + '. ' + title + '\n' + stamp + ' · ' + LABELS[row['category']] +
                      ' · ' + TYPES.get(row['type'], ESC(row['type'])) +
                      ('\n<a href="' + ESC(link, quote=True) + '">مشاهده پیام</a>' if link else ''))
    if not rows:
        blocks.append('برای این تاریخ هیچ ارسال تأییدشده‌ای در سابقه بات ثبت نشده است.')
    blocks.append('⚠️ ارسال نامشخص فعلی: ' + ('، '.join(pending) if pending else 'ندارد'))
    blocks.append('زمان تهیه گزارش: ' + now.astimezone(TZ).strftime('%Y-%m-%d %H:%M') +
                  ' تهران\nزمان‌های بالا زمان آغاز درخواست بات هستند. حذف بعدی پیام در تلگرام از روی سابقه تشخیص داده نمی‌شود.')
    return split_blocks(blocks)


def plan(channel, target, now=None):
    now = now or datetime.now(TZ)
    blocks = ['🗓 <b>برنامه ' + target.isoformat() + '</b>']
    for cat, state, posts, config in sources(channel):
        blocks.append('<b>' + LABELS[cat] + '</b>')
        if cat == 'news':
            for slot, time in [(1, '12:17'), (2, '19:17')]:
                key = target.isoformat() + '-s' + str(slot)
                done = any(s.get('key') == key for s in state.get('sent', []))
                blocks.append(('✅ ' if done else '⏳ ') + time + ' · خبر تازه، مشروط به وجود خبر مناسب')
            continue
        start = state.get('start_date') or config.get('start_date')
        if not start:
            blocks.append('تاریخ شروع هنوز ثبت نشده است؛ ساعت‌ها: ' + '، '.join(config.get('times', [])))
            continue
        day = (target - date.fromisoformat(start)).days + 1
        if not 1 <= day <= config.get('days', 20):
            blocks.append('این تاریخ خارج از بازه کمپین است.')
            continue
        for p in sorted((p for p in posts.values() if p['day'] == day), key=lambda p: p['slot']):
            time = p.get('time') or config['times'][p['slot'] - 1]
            sent = any(s.get('id') == p['id'] for s in state.get('sent', []))
            due = datetime.fromisoformat(target.isoformat() + 'T' + time + ':00+03:30') < now
            status = '✅ ارسال‌شده' if sent else ('⚠️ بدون تأیید ارسال' if due else '⏳ در برنامه')
            blocks.append(time + ' · ' + ESC(p['title'][:240]) + '\n' + status)
    blocks.append('بدون تأیید به معنی خطای قطعی نیست؛ اجرای گیت‌هاب ممکن است تأخیر داشته باشد.')
    return split_blocks(blocks)


def status(channel):
    blocks = ['⚙️ <b>وضعیت ثبت‌شده بات</b>\nکانال: ' + ESC(channel)]
    flags = {'normal': 'ENABLE_POSTING', 'ai': 'ENABLE_AI_POSTING', 'news': 'ENABLE_AI_NEWS'}
    for cat, state, posts, config in sources(channel):
        blocks.append(LABELS[cat] + ': ' + ('روشن' if os.getenv(flags[cat]) == 'true' else 'خاموش') +
                      '\nشروع: ' + ESC(state.get('start_date') or config.get('start_date') or 'ثبت نشده') +
                      '\nکل ارسال‌ها: ' + str(len(state.get('sent', []))) +
                      '\nارسال نامشخص: ' + ('دارد؛ پیش از تکرار کانال بررسی شود' if state.get('pending') else 'ندارد'))
    blocks.append('این گزارش سلامت همه اجراهای GitHub را اثبات نمی‌کند؛ خطاهای اجرا در Actions بررسی شوند.')
    return split_blocks(blocks)


HELP = ('🤖 <b>دستورهای گزارش مدیریتی</b>\n'
        '/today — گزارش امروز\n/yesterday — گزارش دیروز\n'
        '/plan — برنامه امروز\n/status — وضعیت ثبت‌شده\n'
        '/report 2026-11-07 — گزارش تاریخ میلادی\n/id — شناسه عددی شما\n/help — راهنما\n\n'
        'دستورها فقط در خصوصی بات پذیرفته می‌شوند. پاسخ در اجرای بعدی GitHub می‌رسد و ممکن است تأخیر داشته باشد.\n'
        'برای دریافت خودکار شبانه، شناسه خود را در Secret با نام TELEGRAM_REPORT_ADMIN_IDS قرار دهید؛ '
        'چند شناسه را با کاما جدا کنید. هر مدیر باید قبلاً Start زده باشد.')


def admin(api, channel, uid):
    return api.call('getChatMember', chat_id=channel, user_id=uid).get('status') in ('creator', 'administrator')


def command(text, channel, now):
    parts = text.strip().split()
    if not parts:
        return [HELP]
    cmd = parts[0].split('@')[0].lower()
    today = now.astimezone(TZ).date()
    if text.strip() in ('گزارش امروز', 'گزارش الان') or cmd in ('/today', '/report') and len(parts) == 1:
        return report(channel, today, now)
    if cmd == '/yesterday':
        return report(channel, today - timedelta(days=1), now)
    if cmd == '/plan':
        return plan(channel, today, now)
    if cmd == '/status':
        return status(channel)
    if cmd == '/report':
        try:
            if len(parts) != 2:
                raise ValueError()
            target = date.fromisoformat(parts[1])
        except ValueError:
            return ['تاریخ را به شکل /report 2026-11-07 وارد کنید.']
        return report(channel, target, now)
    return [HELP]


def poll(api, channel, state, now):
    webhook = api.call('getWebhookInfo')
    if webhook.get('url'):
        raise RuntimeError('این بات Webhook فعال دارد؛ برای جلوگیری از اختلال هیچ تغییری داده نشد. دریافت دستور باید با آن هماهنگ شود.')
    updates = api.call('getUpdates', offset=state.get('offset', 0), limit=40,
                       timeout=0, allowed_updates=['message'])
    # Reserve the batch before replies: failures cannot automatically duplicate reports.
    if updates:
        state['offset'] = max(u['update_id'] for u in updates) + 1
        persist(state)
    handled = 0
    for update in updates:
        m = update.get('message', {})
        chat, sender = m.get('chat', {}), m.get('from', {})
        if chat.get('type') != 'private' or sender.get('is_bot') or not m.get('text'):
            continue
        if abs(now.timestamp() - m.get('date', 0)) > 86400:
            continue
        uid = sender.get('id')
        if not isinstance(uid, int) or chat.get('id') != uid:
            continue
        if m['text'].strip().split('@')[0] == '/id':
            api.send(uid, 'شناسه عددی شما: <code>' + str(uid) + '</code>')
            continue
        try:
            if not admin(api, channel, uid):
                api.send(uid, 'گزارش فقط برای مدیرهای فعلی کانال در دسترس است.')
                continue
            for chunk in command(m['text'], channel, now):
                api.send(uid, chunk)
            handled += 1
        except RuntimeError:
            print('پاسخ یک دستور کامل نشد؛ مدیر می‌تواند دستور را مجدد بفرستد.')
    print('تعداد دستورهای مدیریتی پاسخ‌داده‌شده:', handled)


def daily_target(now):
    now = now.astimezone(TZ)
    if (now.hour, now.minute) >= (22, 30):
        return now.date()
    if now.hour < 6:
        return now.date() - timedelta(days=1)
    return None


def daily(api, channel, state, now):
    if os.getenv('ENABLE_DAILY_REPORT') != 'true':
        return
    raw = os.getenv('TELEGRAM_REPORT_ADMIN_IDS', '').strip()
    if not raw:
        print('گزارش شبانه: فهرست مدیران دریافت‌کننده هنوز در Secret تنظیم نشده است.')
        return
    ids = list(dict.fromkeys(x for x in re.split(r'[,\s]+', raw) if x))
    if any(not re.fullmatch(r'[1-9]\d*', x) for x in ids) or len(ids) > 20:
        raise RuntimeError('فهرست شناسه مدیران باید شامل حداکثر ۲۰ عدد مثبت، جداشده با کاما باشد.')
    target = daily_target(now)
    if target is None:
        return
    ledger = state.setdefault('daily', {})
    for value in ids:
        uid = int(value)
        digest = hashlib.sha256((channel + ':' + value).encode()).hexdigest()[:24]
        key = target.isoformat() + ':' + digest
        if key in ledger:
            continue
        if not admin(api, channel, uid):
            continue
        ledger[key] = 'pending'
        persist(state)
        try:
            for chunk in report(channel, target, now):
                api.send(uid, chunk)
        except RuntimeError:
            print('گزارش روزانه نامشخص ماند؛ برای جلوگیری از تکرار، ارسال مجدد خودکار نمی‌شود.')
            continue
        ledger[key] = 'sent'
        persist(state)
    cutoff = (now.date() - timedelta(days=35)).isoformat()
    state['daily'] = {k: v for k, v in ledger.items() if k[:10] >= cutoff}
    persist(state)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--preview', action='store_true')
    args = parser.parse_args()
    channel = os.getenv('TELEGRAM_CHANNEL_ID', '').strip()
    if not channel:
        raise RuntimeError('TELEGRAM_CHANNEL_ID تنظیم نشده است.')
    now = datetime.now(TZ)
    if args.preview:
        print('\n\n'.join(report(channel, now.date(), now)))
        return
    if os.getenv('ENABLE_REPORTS', 'true') != 'true':
        print('گزارش‌گیری خاموش است.')
        return
    token = os.getenv('TELEGRAM_BOT_TOKEN', '').strip()
    if not token:
        raise RuntimeError('توکن تنظیم نشده است.')
    api = API(token)
    state = read(STATE, {'offset': 0, 'daily': {}})
    poll(api, channel, state, now)
    daily(api, channel, state, now)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('خطای گزارش:', str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__)
        sys.exit(1)
