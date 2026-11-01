"""DigiHinet channel publisher. No language-model API required."""

import argparse
import html
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from datetime import datetime
from html.parser import HTMLParser
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import requests


ROOT = Path(__file__).resolve().parent


def load(name):
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


class Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1

        if tag in ("p", "li", "h2", "h3", "br"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip = max(0, self.skip - 1)

        if tag in ("p", "li"):
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def clean(source):
    parser = Text()
    parser.feed(source)

    return "\n".join(
        re.sub(r"\s+", " ", part).strip()
        for part in "".join(parser.parts).splitlines()
        if part.strip()
    )


def get(url, **kwargs):
    response = requests.get(
        url,
        timeout=(10, 35),
        headers={"User-Agent": "DigiHinetChannelBot/1.0"},
        **kwargs,
    )
    response.raise_for_status()
    return response


def article_candidates(config):
    base = config["site"].rstrip("/")

    rows = get(
        base + "/wp-json/wp/v2/posts",
        params={
            "per_page": 30,
            "_embed": 1,
            "status": "publish",
        },
    ).json()

    for row in rows:
        if row.get("status") != "publish":
            continue

        title = clean(row["title"]["rendered"])

        paragraphs = [
            paragraph
            for paragraph in clean(row["content"]["rendered"]).splitlines()
            if len(paragraph) > 90
            and not paragraph.startswith(("http", "[", "{"))
        ]

        if not paragraphs:
            continue

        excerpt = paragraphs[0]

        if len(excerpt) > 470:
            excerpt = excerpt[:467].rsplit(" ", 1)[0] + "…"

        media = row.get("_embedded", {}).get(
            "wp:featuredmedia", [{}]
        )[0]

        image = media.get("source_url", "")

        yield {
            "id": "article:" + str(row["id"]),
            "type": "photo" if image else "text",
            "title": title,
            "body": (
                "از مقاله دیجی‌هاینت:\n\n"
                + excerpt
                + "\n\nمتن کامل و جزئیات را در سایت بخوانید."
            ),
            "media": image,
            "url": row["link"],
            "button": "مطالعه مقاله",
        }


class Videos(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls = []

    def handle_starttag(self, tag, attrs):
        if tag in ("video", "source"):
            attributes = dict(attrs)

            for key in ("src", "data-src"):
                if attributes.get(key):
                    self.urls.append(attributes[key])


def portfolio_candidates(config, video=False):
    for item in load("portfolio.json"):
        post = dict(item)

        if video:
            try:
                parser = Videos()
                parser.feed(get(item["url"]).text)

                urls = [
                    urljoin(item["url"], url)
                    for url in parser.urls
                    if ".mp4" in url.lower()
                ]

                if not urls:
                    continue

                post.update(
                    type="video",
                    media=urls[0],
                    id=item["id"] + ":video",
                )

            except requests.RequestException:
                print("ویدیوی این نمونه‌کار در دسترس نبود.")
                continue

        else:
            post["type"] = "photo" if post.get("media") else "card"
            post["id"] += ":photo"

        yield post


def used_ids(state, channel):
    return {
        post["id"]
        for post in state["sent"]
        if post["channel"] == channel
    }


def select(config, state, channel, kind):
    used = used_ids(state, channel)

    sources = {
        "article": lambda: article_candidates(config),
        "portfolio": lambda: portfolio_candidates(config),
        "video": lambda: portfolio_candidates(config, True),
        "text": lambda: (
            post
            for post in load("posts.json")
            if post["type"] == "text"
        ),
        "card": lambda: (
            post
            for post in load("posts.json")
            if post["type"] == "card"
        ),
    }

    for source in dict.fromkeys([kind, "text", "card"]):
        try:
            for post in sources[source]():
                if post["id"] not in used:
                    return post

        except (requests.RequestException, ValueError, KeyError):
            print(
                "دریافت منبع ناموفق بود؛ "
                "بررسی محتوای آموزشی جایگزین."
            )

    return None


def render_card(post):
    from PIL import Image, ImageDraw, ImageFont
    import arabic_reshaper
    from bidi.algorithm import get_display

    font_path = os.getenv(
        "FONT_PATH",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    )

    title_font = ImageFont.truetype(
        font_path,
        47,
        layout_engine=ImageFont.Layout.BASIC,
    )

    font = ImageFont.truetype(
        font_path,
        34,
        layout_engine=ImageFont.Layout.BASIC,
    )

    image = Image.new("RGB", (1080, 1080), "#343434")
    draw = ImageDraw.Draw(image)

    draw.rectangle((0, 0, 1080, 18), fill="#EFA800")

    def visual(text):
        return get_display(arabic_reshaper.reshape(text))

    def wrap(text, selected_font, width):
        lines = []
        line = ""

        for word in text.split():
            candidate = (line + " " + word).strip()

            if (
                draw.textlength(
                    visual(candidate),
                    font=selected_font,
                ) > width
                and line
            ):
                lines.append(line)
                line = word
            else:
                line = candidate

        if line:
            lines.append(line)

        return lines

    y = 95

    for line in wrap(post["title"], title_font, 900):
        draw.text(
            (990, y),
            visual(line),
            font=title_font,
            fill="#EFA800",
            anchor="rt",
        )
        y += 76

    y += 35

    for paragraph in post.get(
        "card_text", post["body"]
    ).splitlines():
        for line in wrap(paragraph, font, 900):
            if y > 890:
                raise ValueError(
                    "متن کارت بیش از ظرفیت قالب است؛ "
                    "card_text را کوتاه کنید."
                )

            draw.text(
                (990, y),
                visual(line),
                font=font,
                fill="white",
                anchor="rt",
            )
            y += 59

        y += 12

    draw.line(
        (90, 960, 990, 960),
        fill="#8A8A8A",
        width=2,
    )

    draw.text(
        (90, 985),
        "DigiHinet  |  @digihinet",
        font=font,
        fill="#EFA800",
    )

    path = ROOT / "out/card.png"
    path.parent.mkdir(exist_ok=True)
    image.save(path)

    return path


def caption(post):
    text = (
        "<b>"
        + html.escape(post["title"])
        + "</b>\n\n"
        + html.escape(post["body"])
        + "\n\n@digihinet"
    )

    limit = 1024 if post["type"] != "text" else 4096

    if len(text.encode("utf-16-le")) // 2 > limit:
        raise ValueError(
            "متن پست از ظرفیت تلگرام بلندتر است؛ "
            "آن را کوتاه کنید."
        )

    return text


def checkpoint(state):
    path = ROOT / "state.json"
    temporary_path = path.with_suffix(".tmp")

    temporary_path.write_text(
        json.dumps(
            state,
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )

    temporary_path.replace(path)

    if os.getenv("PERSIST_STATE") == "true":
        commands = [
            ["git", "add", "state.json"],
            [
                "git",
                "commit",
                "-m",
                "Update publishing state [skip ci]",
            ],
            ["git", "push", "origin", "HEAD"],
        ]

        for command in commands:
            subprocess.run(
                command,
                cwd=ROOT,
                check=True,
                stdout=subprocess.DEVNULL,
            )


def send(post, channel, token, card=None):
    url = "https://api.telegram.org/bot" + token + "/"

    method = {
        "text": "sendMessage",
        "photo": "sendPhoto",
        "card": "sendPhoto",
        "video": "sendVideo",
    }[post["type"]]

    text_field = (
        "text"
        if method == "sendMessage"
        else "caption"
    )

    payload = {
        "chat_id": channel,
        "parse_mode": "HTML",
        text_field: caption(post),
    }

    if post.get("url"):
        payload["reply_markup"] = json.dumps(
            {
                "inline_keyboard": [
                    [
                        {
                            "text": post.get(
                                "button",
                                "مشاهده در سایت",
                            ),
                            "url": post["url"],
                        }
                    ]
                ]
            }
        )

    try:
        if card:
            with card.open("rb") as image:
                response = requests.post(
                    url + method,
                    data=payload,
                    files={
                        "photo": (
                            "card.png",
                            image,
                            "image/png",
                        )
                    },
                    timeout=(10, 100),
                )

        else:
            if post["type"] != "text":
                payload[post["type"]] = post["media"]

            if post["type"] == "video":
                payload["supports_streaming"] = "true"

            response = requests.post(
                url + method,
                data=payload,
                timeout=(10, 100),
            )

    except requests.RequestException:
        raise RuntimeError(
            "وضعیت ارسال نامشخص است؛ "
            "کانال را بررسی و pending را طبق راهنما "
            "تعیین تکلیف کنید."
        ) from None

    try:
        data = response.json()

    except ValueError:
        raise RuntimeError(
            "پاسخ تلگرام نامشخص است؛ "
            "pending را بررسی کنید."
        ) from None

    if not data.get("ok"):
        description = str(
            data.get("description", "")
        ).replace(token, "[REDACTED]")[:300]

        code = data.get(
            "error_code",
            response.status_code,
        )

        raise RuntimeError(
            f"{code} | {description}"
        )

    return data["result"]["message_id"]


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--mode",
        choices=["preview", "test", "publish"],
        default="preview",
    )

    parser.add_argument(
        "--kind",
        choices=[
            "auto",
            "article",
            "portfolio",
            "video",
            "text",
            "card",
        ],
        default="auto",
    )

    args = parser.parse_args()

    config = load("config.json")
    state = load("state.json")

    today = datetime.now(
        ZoneInfo("Asia/Tehran")
    ).date().isoformat()

    channel = os.getenv(
        "TELEGRAM_CHANNEL_ID",
        "@digihinet",
    )

    if args.mode == "test":
        channel = os.getenv(
            "TELEGRAM_TEST_CHANNEL_ID",
            "",
        )

        if (
            not channel
            or channel == os.getenv(
                "TELEGRAM_CHANNEL_ID",
                "@digihinet",
            )
        ):
            raise ValueError(
                "برای تست، شناسه یک کانال "
                "آزمایشی جداگانه لازم است."
            )

    if args.mode != "preview":
        if (
            args.mode == "publish"
            and os.getenv("ENABLE_POSTING") != "true"
        ):
            print(
                "انتشار اصلی خاموش است؛ "
                "ENABLE_POSTING را true کنید."
            )
            return

        if not os.getenv("TELEGRAM_BOT_TOKEN"):
            raise ValueError(
                "توکن بات تنظیم نشده است."
            )

        if state.get("pending"):
            raise ValueError(
                "یک ارسال pending وجود دارد؛ "
                "قبل از ادامه آن را بررسی کنید."
            )

        already_posted_today = any(
            item["channel"] == channel
            and item["date"] == today
            for item in state["sent"]
        )

        if already_posted_today:
            print(
                "سهم امروز این کانال "
                "قبلاً منتشر شده است."
            )
            return

    if args.kind == "auto":
        weekday = str(
            datetime.now(
                ZoneInfo("Asia/Tehran")
            ).weekday()
        )
        kind = config["week"][weekday]
    else:
        kind = args.kind

    post = select(
        config,
        state,
        channel,
        kind,
    )

    if not post:
        raise ValueError(
            "محتوای منتشرنشده موجود نیست؛ "
            "بانک محتوا را تکمیل کنید."
        )

    formatted = caption(post)

    card = (
        render_card(post)
        if post["type"] == "card"
        else None
    )

    output = ROOT / "out"
    output.mkdir(exist_ok=True)

    (output / "preview.txt").write_text(
        clean(formatted)
        + "\n"
        + post.get("url", ""),
        encoding="utf-8",
    )

    (output / "preview.json").write_text(
        json.dumps(
            post,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(
        "نوع:",
        post["type"],
        "| عنوان:",
        post["title"],
    )

    if args.mode == "preview":
        print(
            "فقط پیش‌نمایش ساخته شد؛ "
            "هیچ پیامی ارسال نشد."
        )
        return

    pending = {
        "id": post["id"],
        "channel": channel,
        "date": today,
        "type": post["type"],
    }

    state["pending"] = pending

    # Save the reservation before sending.
    checkpoint(state)

    message_id = send(
        post,
        channel,
        os.environ["TELEGRAM_BOT_TOKEN"],
        card,
    )

    state["sent"].append(
        {
            **pending,
            "message_id": message_id,
        }
    )

    state["pending"] = None
    checkpoint(state)

    print(
        "ارسال موفق؛ شناسه پیام:",
        message_id,
    )


if __name__ == "__main__":
    try:
        main()

    except Exception as error:
        if isinstance(
            error,
            (ValueError, RuntimeError),
        ):
            print("خطا:", str(error))

        else:
            print(
                "خطا:",
                type(error).__name__,
                "؛ راهنما و وضعیت pending را بررسی کنید.",
            )

        sys.exit(1)
