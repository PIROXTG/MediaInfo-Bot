import asyncio
import json
import subprocess
import os
import logging
import sys
import psutil
import re
from aiofiles import open as aiopen
from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from functools import lru_cache
from typing import Optional

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

app = Client("MediaInfo-Bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, workers=10)

queue = asyncio.Queue()
processing_lock = asyncio.Lock()

_LANGUAGE_MAP = {
    'en': 'English','eng': 'English',
    'hi': 'Hindi','hin': 'Hindi',
    'ta': 'Tamil','tam': 'Tamil',
    'te': 'Telugu','tel': 'Telugu',
    'ml': 'Malayalam','mal': 'Malayalam',
    'kn': 'Kannada','kan': 'Kannada',
    'bn': 'Bengali','ben': 'Bengali',
    'mr': 'Marathi','mar': 'Marathi',
    'gu': 'Gujarati','guj': 'Gujarati',
    'pa': 'Punjabi','pun': 'Punjabi',
    'bho': 'Bhojpuri',
    'zh': 'Chinese','chi': 'Chinese','cmn': 'Chinese',
    'ko': 'Korean','kor': 'Korean',
    'pt': 'Portuguese','por': 'Portuguese',
    'th': 'Thai','tha': 'Thai',
    'tl': 'Tagalog','tgl': 'Tagalog','fil': 'Tagalog',
    'ja': 'Japanese','jpn': 'Japanese',
    'es': 'Spanish','spa': 'Spanish',
    'fr': 'French','fra': 'French','fre': 'French',
    'de': 'German','deu': 'German','ger': 'German',
    'it': 'Italian','ita': 'Italian',
    'ru': 'Russian','rus': 'Russian',
    'ar': 'Arabic','ara': 'Arabic',
    'tr': 'Turkish','tur': 'Turkish',
    'nl': 'Dutch','nld': 'Dutch',
    'pl': 'Polish','pol': 'Polish',
    'vi': 'Vietnamese','vie': 'Vietnamese',
    'id': 'Indonesian','ind': 'Indonesian',
    'unknown': 'Original', 'und': 'Original'
}

@lru_cache(maxsize=256)
def get_full_language_name(code: str) -> str:
    if not code:
        return 'Original'
    return _LANGUAGE_MAP.get(code.lower(), code)

def get_video_format(codec: str, transfer: str = '', hdr: str = '', bit_depth: str = '') -> Optional[str]:
    if not codec:
        return None

    codec = codec.lower()
    format_info = []

    if any(x in codec for x in ['hevc', 'h.265', 'h265']):
        format_info.append('HEVC')
    elif 'av1' in codec:
        format_info.append('AV1')
    elif any(x in codec for x in ['avc', 'avc1', 'h.264', 'h264']):
        format_info.append('x264')
    elif 'vp9' in codec:
        format_info.append('VP9')
    elif any(x in codec for x in ['mpeg4', 'xvid']):
        format_info.append('MPEG4')
    else:
        return None

    try:
        if bit_depth and int(bit_depth) > 8:
            format_info.append(f"{bit_depth}bit")
    except:
        pass

    transfer = transfer.lower()
    hdr = hdr.lower()

    if any(x in transfer for x in ['pq', 'hlg', 'smpte', '2084']) or 'hdr' in hdr:
        format_info.append('HDR')

    return ' '.join(format_info)

def get_standard_resolution(height: int) -> Optional[str]:
    if not height:
        return None
    if height <= 240: return "240p"
    elif height <= 360: return "360p"
    elif height <= 480: return "480p"
    elif height <= 720: return "720p"
    elif height <= 1080: return "1080p"
    elif height <= 1440: return "1440p"
    elif height <= 2160: return "2160p"
    else: return "2160p+"

def get_quality(width, height):
    if not width or not height:
        return None
    return get_standard_resolution(min(width, height))

def ffprobe_to_tracks(streams: list) -> list:
    tracks = []

    for s in streams:
        tracks.append({
            "@type": (s.get("codec_type") or "").capitalize(),
            "Format": s.get("codec_name"),
            "CodecID": s.get("codec_tag_string"),
            "Title": (s.get("tags", {}) or {}).get("title", ""),
            "MenuID": "",
            "Format_Info": "",
            "Encoding": (s.get("tags", {}) or {}).get("encoding", "")
        })

    return tracks

def has_subtitles(tracks: list) -> bool:
    if not tracks or not isinstance(tracks, list):
        return False
    
    for track in tracks:
        if not isinstance(track, dict):
            continue
            
        track_type = (track.get('@type', '') or '').lower()
        format_str = (track.get('Format', '') or '').lower()
        codec_id = (track.get('CodecID', '') or '').lower()
        encoding = (track.get('Encoding', '') or '').lower()
        format_info = (track.get('Format_Info', '') or '').lower()
        
        if any([
            track_type == 'text',
            any(x in format_str for x in ['pgs', 'subrip', 'ass', 'ssa', 'srt']),
            any(x in codec_id for x in ['s_text', 'subp', 'pgs', 'subtitle']),
            any(x in encoding for x in ['utf-8', 'utf8', 'text']),
            any(x in format_info for x in ['subtitle', 'caption', 'text']),
            'subtitle' in str(track.get('Title', '')).lower()
        ]):
            return True
    
    return False

def install_ffmpeg():
    try:
        subprocess.run(["ffprobe", "-version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except:
        subprocess.run(["apt", "update", "-y"])
        subprocess.run(["apt", "install", "-y", "ffmpeg"])


async def get_media_info(file_path):
    cmd = [
        "ffprobe",
        "-loglevel", "quiet",
        "-print_format", "json",
        "-show_streams",
        "-show_format",
        file_path
    ]

    process = await asyncio.to_thread(
        subprocess.run,
        cmd,
        stdout=subprocess.PIPE,
        timeout=15,
        check=False
    )

    data = json.loads(process.stdout.decode("utf-8", errors="ignore"))

    duration = float(data.get("format", {}).get("duration", 0))
    width = height = None
    codec = None
    bit_depth = ""
    hdr = ""
    transfer = ""

    audio_languages = set()
    subtitle_languages = set()

    streams = data.get("streams", [])

    tracks = []
    for s in streams:
        tracks.append({
            "@type": (s.get("codec_type") or "").lower(),
            "Format": s.get("codec_name"),
            "CodecID": s.get("codec_tag_string"),
            "Title": (s.get("tags", {}) or {}).get("title", ""),
            "MenuID": "",
            "Format_Info": "",
            "Encoding": (s.get("tags", {}) or {}).get("encoding", "")
        })

    has_sub = has_subtitles(tracks)

    for stream in streams:

        if stream.get("codec_type") == "video" and height is None:
            width = stream.get("width") or stream.get("coded_width")
            height = stream.get("height") or stream.get("coded_height")

            codec = (stream.get("codec_name") or "").lower()

            pix_fmt = (stream.get("pix_fmt") or "").lower()
            profile = (stream.get("profile") or "").lower()
            bits_per_raw = stream.get("bits_per_raw_sample")

            if bits_per_raw:
                bit_depth = str(bits_per_raw)
            elif any(x in pix_fmt for x in ["p10", "10", "yuv420p10", "yuv422p10", "yuv444p10"]):
                bit_depth = "10"
            elif any(x in pix_fmt for x in ["p12", "12"]):
                bit_depth = "12"
            elif any(x in pix_fmt for x in ["yuv420p", "yuv422p", "yuv444p"]):
                bit_depth = "8"
            else:
                bit_depth = ""

            transfer = (stream.get("color_transfer") or "").lower()
            color_space = (stream.get("color_space") or "").lower()
            color_primaries = (stream.get("color_primaries") or "").lower()

            if any(x in transfer for x in ["smpte2084", "arib-std-b67", "pq", "hlg"]) \
               or "bt2020" in color_space \
               or "bt2020" in color_primaries:
                hdr = "HDR"

            if "dolby" in profile:
                hdr = "Dolby Vision"

        elif stream.get("codec_type") == "audio":
            lang = stream.get("tags", {}).get("language", "unknown")
            audio_languages.add(get_full_language_name(lang))

        elif stream.get("codec_type") == "subtitle":
            lang = stream.get("tags", {}).get("language", "unknown")
            subtitle_languages.add(get_full_language_name(lang))

    subtitle_text = (
        ", ".join(sorted(subtitle_languages))
        if subtitle_languages
        else ("Unknown Subtitles" if has_sub else "No Sub")
    )

    return (
        duration,
        width,
        height,
        codec,
        bit_depth,
        hdr,
        transfer,
        ", ".join(sorted(audio_languages)) if audio_languages else "Unknown",
        subtitle_text
    )

def format_duration(s):
    s = int(s)
    return f"{s//3600:02}:{(s%3600)//60:02}:{s%60:02}"

async def process_message(message):
    file_path = None
    media = message.video or message.document
    temp = f"tmp_{message.id}.bin"

    async with aiopen(temp, "wb") as f:
        async for chunk in app.stream_media(media, limit=STREAM_LIMIT):
            await f.write(chunk)

    try:
        duration, width, height, codec, bit_depth, hdr, transfer, audio, sub = await get_media_info(temp)
        if duration == 0 or not width or not height:
            raise Exception()
        file_path = temp
    except:
        if os.path.exists(temp):
            os.remove(temp)

        file_path = await message.download()
        duration, width, height, codec, bit_depth, hdr, transfer, audio, sub = await get_media_info(file_path)

    quality = get_quality(width, height) or "Unknown"
    format_info = get_video_format(codec, transfer, hdr, bit_depth)

    video_line = " ".join(filter(None, [quality, format_info])) or "Unknown"

    caption = CAPTION_TEMPLATE.format(
        title=message.caption or media.file_name or "Video",
        video_line=video_line,
        duration=format_duration(duration) if duration else "Unknown",
        audio=audio,
        subtitle=sub
    )

    return caption, file_path

async def worker():
    while True:
        message, mode = await queue.get()
        file_path = None

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

def caption_has_media_info(caption: str) -> bool:
    if not caption:
        return False

    matches = [
        bool(re.search(r"🎬", caption)),
        bool(re.search(r"⏳\s*\d{2}:\d{2}:\d{2}", caption)),
        bool(re.search(r"🔊", caption)),
        bool(re.search(r"💬", caption)),
    ]

    return sum(matches) >= 2

@app.on_message(filters.chat(ALLOWED_CHATS) & filters.channel & (filters.video | filters.document))
async def channel_handler(_, message):

    if caption_has_media_info(message.caption or ""):
        return

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
    os.execv(sys.executable, [sys.executable] + sys.argv)


@app.on_message(filters.command("shutdown") & filters.user(ADMIN_ID))
async def shutdown(_, m):
    await m.reply_text("Shutting down...")
    scheduler.shutdown(wait=False)
    await app.stop()
    os._exit(0)

@app.on_message(filters.command("update") & filters.user(ADMIN_ID))
async def update(_, m):
    await m.reply_text("Updating...")

    try:
        os.system("git pull")
        os.system("pip install -r requirements.txt --no-cache-dir --upgrade")
        await m.reply_text("✅ Updated. Restarting...")
        os.execv(sys.executable, [sys.executable] + sys.argv)

    except Exception as e:
        await m.reply_text(f"Update failed: {e}")

@app.on_message(filters.command("info") & filters.reply)
async def info_command(_, message):
    reply = message.reply_to_message

    if not (reply.video or reply.document):
        return await message.reply_text("⚠️ Reply to a video or file.")

    temp_path = f"info_{reply.id}.bin"

    try:
        media = reply.video or reply.document

        async with aiopen(temp_path, "wb") as f:
            async for chunk in app.stream_media(media, limit=STREAM_LIMIT):
                await f.write(chunk)

        duration, width, height, codec, bit_depth, hdr, transfer, audio, sub = await get_media_info(temp_path)

        quality = get_quality(width, height) if width and height else "Unknown"

        format_info = get_video_format(codec, transfer, hdr, bit_depth)
        video_line = " ".join(filter(None, [quality, format_info])) or "Unknown"

        text = (
            f"<b>📊 Media Info</b>\n\n"
            f"🎬 <b>Video:</b> {video_line}\n"
            f"⏳ <b>Duration:</b> {format_duration(duration) if duration else 'Unknown'}\n"
            f"🔊 <b>Audio:</b> {audio}\n"
            f"💬 <b>Subtitles:</b> {sub}"
        )

        await message.reply_text(text, parse_mode=ParseMode.HTML)

    except Exception as e:
        await message.reply_text(f"❌ Failed to extract info\n\n<code>{e}</code>")

    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


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