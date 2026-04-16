import os

API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

ADMIN_ID = int(os.environ.get("ADMIN_ID", 0))

_raw = os.environ.get("ALLOWED_CHATS", "")
ALLOWED_CHATS = [int(x.strip()) for x in _raw.split(",") if x.strip()]

LOG_FORMAT = os.environ.get(
    "LOG_FORMAT",
    "[%(asctime)s][%(name)s][%(module)s][%(lineno)d][%(levelname)s] -> %(message)s",
)
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")

STREAM_LIMIT = int(os.environ.get("STREAM_LIMIT", 5))

CAPTION_TEMPLATE = os.environ.get(
    "CAPTION_TEMPLATE",
    "<b>{title}</b>\n\n"
    "🎬 <b>{video_line}</b> | ⏳ <b>{duration}</b>\n"
    "🔊 <b>{audio}</b>\n"
    "💬 <b>{subtitle}</b>\n\n"
)