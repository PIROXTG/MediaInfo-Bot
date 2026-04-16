import asyncio
import json
import subprocess
import os
import logging
import sys
import psutil
from aiofiles import open as aiopen
from pyrogram import Client, filters
from pyrogram.enums import ParseMode

from config import (
    API_ID, API_HASH, BOT_TOKEN,
    ADMIN_ID, ALLOWED_CHATS,
    LOG_FORMAT, LOG_LEVEL,
    STREAM_LIMIT,
    CAPTION_TEMPLATE,
)

logging.basicConfig(level=LOG_LEVEL, format=LOG_FORMAT, force=True)
logging.getLogger("pyrogram").setLevel(logging.ERROR)

logger = logging.getLogger(__name__)

app = Client("bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, workers=3)

queue = asyncio.Queue()
processing_lock = asyncio.Lock()

LANG_MAP = {
    "hin": "Hindi",
    "mar": "Marathi",
    "tam": "Tamil",
    "tel": "Telugu",
    "mal": "Malayalam",
    "kan": "Kannada",
    "eng": "English",
    "hun": "Hungarian",
    "ind": "Indonesian",
    "ita": "Italian",
    "jpn": "Japanese",
    "kor": "Korean",
    "chi": "Chinese",
    "zho": "Chinese",
    "spa": "Spanish",
    "por": "Portuguese",
    "tha": "Thai",
    "pol": "Polish",
    "fre": "French",
    "fra": "French",
    "ger": "German",
    "deu": "German",
    "ara": "Arabic",
    "rus": "Russian",
    "tur": "Turkish",
    "vie": "Vietnamese",
    "nld": "Dutch",
    "und": "Unknown",
    "multi": "Multi",
    "dual": "Dual Audio"
}


def install_ffmpeg():
    try:
        subprocess.run(["ffprobe", "-version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except:
        subprocess.run(["apt", "update", "-y"])
        subprocess.run(["apt", "install", "-y", "ffmpeg"])


async def get_media_info(file_path):
    cmd = [
        "ffprobe", "-v", "error",
        "-print_format", "json",
        "-show_streams",
        "-show_format",
        file_path
    ]

    process = subprocess.run(cmd, stdout=subprocess.PIPE)
    data = json.loads(process.stdout)

    duration = float(data.get("format", {}).get("duration", 0))
    height = None
    codec = None
    bit_depth = ""
    audio_languages = set()
    subtitle = False

    for stream in data.get("streams", []):
        if stream["codec_type"] == "video":
            height = stream.get("height")

            raw = (stream.get("codec_name") or "").lower()

            if raw in ["hevc", "h265"]:
                codec = "x265"
            elif raw in ["h264", "avc"]:
                codec = "x264"
            elif raw == "av1":
                codec = "AV1"

            bits = stream.get("bits_per_raw_sample") or stream.get("bits_per_sample")
            if str(bits) == "10":
                bit_depth = "10Bit"

        elif stream["codec_type"] == "audio":
            lang = stream.get("tags", {}).get("language", "und").lower()
            audio_languages.add(LANG_MAP.get(lang, lang.capitalize()))

        elif stream["codec_type"] == "subtitle":
            subtitle = True

    return (
        duration,
        height,
        codec,
        bit_depth,
        ", ".join(sorted(audio_languages)) if audio_languages else "Unknown",
        subtitle
    )


def format_duration(s):
    s = int(s)
    return f"{s//3600:02}:{(s%3600)//60:02}:{s%60:02}"


def get_quality(h):
    if not h:
        return None
    if h >= 1080:
        return "1080p"
    if h >= 720:
        return "720p"
    if h >= 480:
        return "480p"
    return f"{h}p"


async def process_message(message):
    file_path = None

    media = message.video or message.document
    temp = f"tmp_{message.id}.bin"

    async with aiopen(temp, "wb") as f:
        async for chunk in app.stream_media(media, limit=STREAM_LIMIT):
            await f.write(chunk)

    try:
        duration, height, codec, bit_depth, audio, sub = await get_media_info(temp)
        if duration == 0:
            raise Exception()
        file_path = temp
    except:
        if os.path.exists(temp):
            os.remove(temp)

        file_path = await message.download()
        duration, height, codec, bit_depth, audio, sub = await get_media_info(file_path)

    quality = get_quality(height)
    video_line = " ".join(filter(None, [quality, codec, bit_depth])) or "Unknown"

    caption = CAPTION_TEMPLATE.format(
        title=message.caption or media.file_name or "Video",
        video_line=video_line,
        duration=format_duration(duration) if duration else "Unknown",
        audio=audio or "Unknown",
        subtitle="ESUB" if sub else "No Sub"
    )

    return caption, file_path


async def worker():
    while True:
        message, mode = await queue.get()

        async with processing_lock:
            try:
                caption, file_path = await process_message(message)

                if mode == "channel":
                    await message.edit_caption(caption, parse_mode=ParseMode.HTML)
                else:
                    await message.reply_text(caption, parse_mode=ParseMode.HTML)

                await asyncio.sleep(1)

            except Exception as e:
                logger.error(e)

            finally:
                if file_path and os.path.exists(file_path):
                    os.remove(file_path)

        queue.task_done()


@app.on_message(filters.chat(ALLOWED_CHATS) & filters.channel & (filters.video | filters.document))
async def channel_handler(_, message):
    await queue.put((message, "channel"))


@app.on_message(filters.private & (filters.video | filters.document))
async def private_handler(_, message):
    if not queue.empty():
        await message.reply_text("⚠️ Please send one file at a time.")
        return

    await queue.put((message, "private"))


@app.on_message(filters.command("start") & filters.private)
async def start(_, m):
    await m.reply_text(
        "<b>🎬 Media Info Bot</b>\n\n"
        "Send me any video or file and I’ll extract detailed media information for you instantly.\n\n"
        "I provide:\n"
        "• 🎞 Video quality, codec & bit depth\n"
        "• ⏳ Duration\n"
        "• 🔊 Audio languages\n"
        "• 💬 Subtitle info\n\n"
        "<b>⚡ Fast • Clean • Accurate</b>\n\n"
        "📌 <i>Note:</i> Please send only one file at a time.\n\n"
        "🤖 Bot by @piroxbots",
        parse_mode=ParseMode.HTML
    )


@app.on_message(filters.command("server") & filters.user(ADMIN_ID))
async def server(_, m):
    await m.reply_text(
        f"CPU: {psutil.cpu_percent()}%\nRAM: {psutil.virtual_memory().percent}%\nDisk: {psutil.disk_usage('/').percent}%"
    )


@app.on_message(filters.command("restart") & filters.user(ADMIN_ID))
async def restart(_, m):
    await m.reply_text("Restarting...")
    os.execv(sys.executable, ["python"] + sys.argv)


@app.on_message(filters.command("shutdown") & filters.user(ADMIN_ID))
async def shutdown(_, m):
    await m.reply_text("Shutting down...")
    scheduler.shutdown(wait=False)
    await app.stop()
    os._exit(0)


async def main():

    install_ffmpeg()
    await app.start()

    me = await app.get_me()
    logger.info(f"@{me.username} started")

    await app.send_message(ADMIN_ID, "🚀 Bot Started")

    asyncio.create_task(worker())

    scheduler.start()

    await asyncio.Event().wait()


if __name__ == "__main__":
    app.run(main())