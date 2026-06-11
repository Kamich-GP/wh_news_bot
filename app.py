import html
import os
import re
import threading
import time

import telebot
from flask import Flask, abort, request
from telebot.types import InputMediaPhoto, InputMediaVideo, Update


TOKEN = os.environ["BOT_TOKEN"]
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "").rstrip("/")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")
FINAL_LINE = os.environ.get(
    "FINAL_LINE",
    '<a href="https://t.me/gameplusbackstage">GP Backstage</a> | #новости',
)
MEDIA_TIMEOUT = float(os.environ.get("MEDIA_TIMEOUT", "0.9"))

bot = telebot.TeleBot(TOKEN, parse_mode="HTML", threaded=False)
app = Flask(__name__)

media_groups = {}
lock = threading.Lock()

EMOJI_RE = re.compile(
    "["
    "\U0001F600-\U0001F64F"
    "\U0001F300-\U0001F5FF"
    "\U0001F680-\U0001F6FF"
    "\U0001F1E0-\U0001F1FF"
    "\U00002700-\U000027BF"
    "\U000024C2-\U0001F251"
    "]+",
    flags=re.UNICODE,
)

EMOJI_REPLACEMENTS = {
    "🟢": "- ",
    "🟡": "- ",
    "🔴": "- ",
    "🔵": "- ",
}

FORBIDDEN_PHRASES = (
    "Game InQisitor",
    "Game InQsitor",
)


def remove_emojis(text: str) -> str:
    return EMOJI_RE.sub("", text).strip()


def replace_list_emojis(text: str) -> str:
    for emoji, repl in EMOJI_REPLACEMENTS.items():
        text = re.sub(rf"{re.escape(emoji)}\s*", repl, text)
    return text


def remove_forbidden_lines(lines):
    cleaned = []

    for line in lines:
        plain_line = re.sub(r"<[^>]+>", "", html.unescape(line))
        if not any(bad in plain_line for bad in FORBIDDEN_PHRASES):
            cleaned.append(line)

    return cleaned


def normalize_first_line(line: str) -> str:
    first_line = remove_emojis(line)
    first_line = replace_list_emojis(first_line).strip()

    if not first_line:
        return ""

    plain_first_line = re.sub(r"<[^>]+>", "", html.unescape(first_line)).rstrip()
    if plain_first_line.endswith("."):
        return f"<b>{first_line}</b>"

    return f"<b>{first_line}.</b>"


def get_caption_html(message) -> str:
    html_caption = getattr(message, "html_caption", None)
    if html_caption:
        return html_caption

    if not message.caption:
        return ""

    return html.escape(message.caption)


def edit_caption_html(original_caption_html: str) -> str:
    if not original_caption_html:
        return FINAL_LINE

    lines = original_caption_html.split("\n")
    lines = remove_forbidden_lines(lines)

    if not lines:
        return FINAL_LINE

    first_line = normalize_first_line(lines[0])
    middle_lines = lines[1:]
    new_text = "\n".join([first_line] + middle_lines + [FINAL_LINE])

    return replace_list_emojis(new_text)


def process_single_media(message):
    if not message.caption:
        return

    new_caption = edit_caption_html(get_caption_html(message))

    if message.content_type == "photo":
        bot.send_photo(
            message.chat.id,
            message.photo[-1].file_id,
            caption=new_caption,
            parse_mode="HTML",
        )
    elif message.content_type == "video":
        bot.send_video(
            message.chat.id,
            message.video.file_id,
            caption=new_caption,
            parse_mode="HTML",
        )


def process_media_group(group_id):
    with lock:
        group = media_groups.pop(group_id, None)

    if not group:
        return

    messages = sorted(group["messages"], key=lambda m: m.message_id)
    first = next((message for message in messages if message.caption), None)

    if not first:
        return

    new_caption = edit_caption_html(get_caption_html(first))
    media = []

    for message in messages:
        if message.content_type == "photo":
            item = InputMediaPhoto(message.photo[-1].file_id)
        elif message.content_type == "video":
            item = InputMediaVideo(message.video.file_id)
        else:
            continue

        if message.message_id == first.message_id:
            item.caption = new_caption
            item.parse_mode = "HTML"

        media.append(item)

    if media:
        bot.send_media_group(group["chat_id"], media)


def schedule_media_group_processing(group_id):
    def process_if_idle():
        with lock:
            group = media_groups.get(group_id)
            if not group or time.time() - group["last_update"] < MEDIA_TIMEOUT:
                return

        process_media_group(group_id)

    timer = threading.Timer(MEDIA_TIMEOUT, process_if_idle)
    timer.daemon = True
    timer.start()


@bot.message_handler(content_types=["photo", "video"])
def handle_media(message):
    if not message.media_group_id:
        process_single_media(message)
        return

    group_id = message.media_group_id

    with lock:
        if group_id not in media_groups:
            media_groups[group_id] = {
                "messages": [],
                "last_update": time.time(),
                "chat_id": message.chat.id,
            }

        media_groups[group_id]["messages"].append(message)
        media_groups[group_id]["last_update"] = time.time()

    schedule_media_group_processing(group_id)


@app.get("/")
def health():
    return "Bot is running"


@app.post(f"/webhook/{TOKEN}")
def webhook():
    if WEBHOOK_SECRET:
        header_secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if header_secret != WEBHOOK_SECRET:
            abort(403)

    update = Update.de_json(request.get_data().decode("utf-8"))
    bot.process_new_updates([update])
    return "", 200


def setup_webhook():
    if not WEBHOOK_URL:
        return

    webhook_url = f"{WEBHOOK_URL}/webhook/{TOKEN}"
    bot.remove_webhook()
    bot.set_webhook(webhook_url, secret_token=WEBHOOK_SECRET or None)


setup_webhook()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
