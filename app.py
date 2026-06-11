import html
import html.parser
import os
import re
import threading
import time

import telebot
from flask import Flask, abort, request
from telebot.types import InputMediaPhoto, InputMediaVideo, Update
from telebot.types import MessageEntity


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
    "\U0001F700-\U0001FAFF"
    "\U00002700-\U000027BF"
    "\U000024C2-\U0001F251"
    "\U0000FE0F"
    "]+",
    flags=re.UNICODE,
)

EMOJI_REPLACEMENTS = {
    "🔴": "- ",
    "🟠": "- ",
    "🟢": "- ",
    "🟡": "- ",
    "🔵": "- ",
    "🟣": "- ",
    "⚫": "- ",
    "⚫️": "- ",
    "⚪": "- ",
    "⚪️": "- ",
    "🟤": "- ",
}

FORBIDDEN_PHRASES = (
    "Game InQisitor",
    "Game InQsitor",
)


def utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def remove_emojis(text: str) -> str:
    return EMOJI_RE.sub("", text).strip()


def html_to_text_and_entities(text_html: str):
    parser = _TelegramHtmlParser()
    parser.feed(text_html)
    parser.close()
    return parser.text, parser.entities


class _TelegramHtmlParser(html.parser.HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.text = ""
        self.entities = []
        self.stack = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        entity_type = None
        extra = {}

        if tag in ("b", "strong"):
            entity_type = "bold"
        elif tag in ("i", "em"):
            entity_type = "italic"
        elif tag == "u":
            entity_type = "underline"
        elif tag in ("s", "strike", "del"):
            entity_type = "strikethrough"
        elif tag == "code":
            entity_type = "code"
        elif tag == "pre":
            entity_type = "pre"
        elif tag == "blockquote":
            entity_type = "expandable_blockquote" if "expandable" in attrs else "blockquote"
        elif tag == "a" and attrs.get("href"):
            entity_type = "text_link"
            extra["url"] = attrs["href"]
        elif tag == "tg-spoiler":
            entity_type = "spoiler"
        elif tag == "span" and attrs.get("class") == "tg-spoiler":
            entity_type = "spoiler"
        elif tag == "tg-emoji" and attrs.get("emoji-id"):
            entity_type = "custom_emoji"
            extra["custom_emoji_id"] = attrs["emoji-id"]

        if entity_type:
            self.stack.append((tag, entity_type, utf16_len(self.text), extra))

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            start_tag, entity_type, offset, extra = self.stack[index]
            if start_tag != tag and not (tag == "strong" and start_tag == "b"):
                continue

            self.stack.pop(index)
            length = utf16_len(self.text) - offset
            if length > 0:
                self.entities.append(MessageEntity(entity_type, offset, length, **extra))
            break

    def handle_data(self, data):
        self.text += data

    def close(self):
        super().close()
        text_len = utf16_len(self.text)
        while self.stack:
            _, entity_type, offset, extra = self.stack.pop()
            length = text_len - offset
            if length > 0:
                self.entities.append(MessageEntity(entity_type, offset, length, **extra))


def transform_line(line: str, is_first: bool):
    transformed = []
    offset_map = [0] * (utf16_len(line) + 1)
    old_offset = 0
    new_offset = 0
    index = 0

    while index < len(line):
        old_text = line[index]
        replacement = None

        for emoji, repl in sorted(EMOJI_REPLACEMENTS.items(), key=lambda item: len(item[0]), reverse=True):
            if line.startswith(emoji, index):
                old_text = emoji
                replacement = repl
                break

        char_len = utf16_len(old_text)

        if replacement is not None:
            new_text = replacement
            next_index = index + len(old_text)
            while next_index < len(line) and line[next_index].isspace() and line[next_index] != "\n":
                char_len += utf16_len(line[next_index])
                next_index += 1
        elif EMOJI_RE.fullmatch(old_text):
            new_text = ""
            next_index = index + len(old_text)
            while next_index < len(line) and line[next_index].isspace() and line[next_index] != "\n":
                char_len += utf16_len(line[next_index])
                next_index += 1
        else:
            new_text = old_text
            next_index = index + len(old_text)

        transformed.append(new_text)
        next_old_offset = old_offset + char_len
        new_offset += utf16_len(new_text)

        for offset in range(old_offset + 1, next_old_offset + 1):
            offset_map[offset] = new_offset

        old_offset = next_old_offset
        index = next_index

    new_line = "".join(transformed).strip() if is_first else "".join(transformed)
    stripped_left = len("".join(transformed)) - len("".join(transformed).lstrip())
    if is_first and stripped_left:
        trim_len = utf16_len("".join(transformed)[:stripped_left])
        offset_map = [max(0, offset - trim_len) for offset in offset_map]

    return new_line, offset_map


def copy_entity(entity, offset, length):
    kwargs = {}
    for name in ("url", "user", "language", "custom_emoji_id"):
        value = getattr(entity, name, None)
        if value is not None:
            kwargs[name] = value

    return MessageEntity(entity.type, offset, length, **kwargs)


def sort_entities(entities):
    seen = set()
    unique = []

    for entity in entities:
        key = (
            entity.type,
            entity.offset,
            entity.length,
            getattr(entity, "url", None),
            getattr(entity, "language", None),
            getattr(entity, "custom_emoji_id", None),
        )
        if key in seen:
            continue

        seen.add(key)
        unique.append(entity)

    return sorted(unique, key=lambda entity: (entity.offset, -entity.length))


def utf16_to_py_index_map(text: str):
    mapping = {}
    utf_offset = 0

    for index, char in enumerate(text):
        mapping[utf_offset] = index
        utf_offset += utf16_len(char)

    mapping[utf_offset] = len(text)
    return mapping


def entity_to_html(entity, inner_html: str) -> str:
    if entity.type == "bold":
        return f"<b>{inner_html}</b>"
    if entity.type == "italic":
        return f"<i>{inner_html}</i>"
    if entity.type == "underline":
        return f"<u>{inner_html}</u>"
    if entity.type == "strikethrough":
        return f"<s>{inner_html}</s>"
    if entity.type == "spoiler":
        return f"<tg-spoiler>{inner_html}</tg-spoiler>"
    if entity.type == "code":
        return f"<code>{inner_html}</code>"
    if entity.type == "pre":
        language = getattr(entity, "language", None)
        if language:
            return f'<pre><code class="language-{html.escape(language, quote=True)}">{inner_html}</code></pre>'
        return f"<pre>{inner_html}</pre>"
    if entity.type == "blockquote":
        return f"<blockquote>{inner_html}</blockquote>"
    if entity.type == "expandable_blockquote":
        return f"<blockquote expandable>{inner_html}</blockquote>"
    if entity.type == "text_link":
        url = html.escape(getattr(entity, "url", "") or "", quote=True)
        return f'<a href="{url}">{inner_html}</a>'
    if entity.type == "text_mention":
        user = getattr(entity, "user", None)
        if user and getattr(user, "id", None):
            return f'<a href="tg://user?id={user.id}">{inner_html}</a>'
    if entity.type == "custom_emoji":
        emoji_id = html.escape(getattr(entity, "custom_emoji_id", "") or "", quote=True)
        return f'<tg-emoji emoji-id="{emoji_id}">{inner_html}</tg-emoji>'

    return inner_html


def render_html(text: str, entities=None) -> str:
    if not entities:
        return html.escape(text)

    index_map = utf16_to_py_index_map(text)
    nodes = []

    for entity in sort_entities(entities):
        start = index_map.get(entity.offset)
        end = index_map.get(entity.offset + entity.length)

        if start is None or end is None or start >= end:
            continue

        nodes.append(
            {
                "entity": entity,
                "start": start,
                "end": end,
                "children": [],
            }
        )

    roots = []
    stack = []

    for node in sorted(nodes, key=lambda item: (item["start"], -item["end"])):
        while stack and node["start"] >= stack[-1]["end"]:
            stack.pop()

        if stack and node["end"] <= stack[-1]["end"]:
            stack[-1]["children"].append(node)
        else:
            roots.append(node)

        stack.append(node)

    def render_range(start, end, children):
        output = []
        cursor = start

        for child in sorted(children, key=lambda item: (item["start"], item["end"])):
            if child["start"] < cursor:
                continue

            output.append(html.escape(text[cursor:child["start"]]))
            inner_html = render_range(child["start"], child["end"], child["children"])
            output.append(entity_to_html(child["entity"], inner_html))
            cursor = child["end"]

        output.append(html.escape(text[cursor:end]))
        return "".join(output)

    return render_range(0, len(text), roots)


def edit_caption(caption: str, entities=None):
    if not caption:
        final_text, final_entities = html_to_text_and_entities(FINAL_LINE)
        return final_text, final_entities

    line_infos = []
    position = 0
    utf_position = 0

    for line in caption.split("\n"):
        line_utf_len = utf16_len(line)
        line_infos.append(
            {
                "text": line,
                "start": utf_position,
                "end": utf_position + line_utf_len,
                "py_start": position,
            }
        )
        position += len(line) + 1
        utf_position += line_utf_len + 1

    kept_lines = [
        line_info
        for line_info in line_infos
        if not any(bad in line_info["text"] for bad in FORBIDDEN_PHRASES)
    ]

    if not kept_lines:
        final_text, final_entities = html_to_text_and_entities(FINAL_LINE)
        return final_text, final_entities

    new_lines = []
    new_entities = []
    line_mappings = []
    current_offset = 0

    for index, line_info in enumerate(kept_lines):
        new_line, offset_map = transform_line(line_info["text"], index == 0)

        if index == 0 and new_line and not new_line.rstrip().endswith("."):
            new_line = f"{new_line}."

        line_mappings.append((line_info, offset_map, current_offset, utf16_len(new_line)))
        new_lines.append(new_line)
        current_offset += utf16_len(new_line) + 1

    for entity in entities or []:
        entity_start = entity.offset
        entity_end = entity.offset + entity.length

        for line_index, (line_info, offset_map, new_line_start, new_line_len) in enumerate(line_mappings):
            overlap_start = max(entity_start, line_info["start"])
            overlap_end = min(entity_end, line_info["end"])

            if overlap_start >= overlap_end:
                continue

            if line_index == 0 and entity.type == "bold":
                continue

            relative_start = overlap_start - line_info["start"]
            relative_end = overlap_end - line_info["start"]
            new_start = new_line_start + offset_map[relative_start]
            new_end = new_line_start + offset_map[relative_end]

            if new_end > new_start:
                new_entities.append(copy_entity(entity, new_start, new_end - new_start))

    first_line_len = utf16_len(new_lines[0])
    if first_line_len > 0:
        new_entities.append(MessageEntity("bold", 0, first_line_len))

    final_text, final_entities = html_to_text_and_entities(FINAL_LINE)
    final_start = current_offset
    new_lines.append(final_text)

    for entity in final_entities:
        new_entities.append(copy_entity(entity, final_start + entity.offset, entity.length))

    return "\n".join(new_lines), sort_entities(new_entities)


def edit_caption_html(original_caption_html: str) -> str:
    text, entities = html_to_text_and_entities(original_caption_html)
    return render_html(*edit_caption(text, entities))


def edit_message_caption_html(message) -> str:
    entities = getattr(message, "caption_entities", None)
    if entities:
        text, new_entities = edit_caption(message.caption, entities)
        return render_html(text, new_entities)

    html_caption = getattr(message, "html_caption", None)
    if html_caption:
        return edit_caption_html(html_caption)

    text, new_entities = edit_caption(message.caption, None)
    return render_html(text, new_entities)


def process_single_media(message):
    if not message.caption:
        return

    new_caption = edit_message_caption_html(message)

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

    new_caption = edit_message_caption_html(first)
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
