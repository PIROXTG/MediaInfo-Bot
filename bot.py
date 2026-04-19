import asyncio
import json
import subprocess
import os
import logging
import sys
import psutil
import gc
import re
import uuid
from aiofiles import open as aiopen
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from functools import lru_cache
from typing import Optional
from aiofiles.os import remove as aioremove
from pyrogram.errors import MessageNotModified
from collections import defaultdict
from pyrogram.errors import FloodWait
from config import (
    API_ID, API_HASH, BOT_TOKEN,
    ADMIN_ID, ALLOWED_CHATS,
    LOG_FORMAT, LOG_LEVEL,
    GC_THRESHOLD,
    CAPTION_TEMPLATE,
)

logging.basicConfig(level=LOG_LEVEL, format=LOG_FORMAT, force=True)
logging.getLogger("pyrogram").setLevel(logging.ERROR)

logger = logging.getLogger(__name__)

app = Client("MyMediaInfoRoBot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, workers=4)

scheduler = AsyncIOScheduler()
stream_semaphore = asyncio.Semaphore(2)
active_users = set()
_last_edit = {}
channel_queues = defaultdict(list)
channel_locks = defaultdict(asyncio.Lock)
last_edit_time = {}

EDIT_DELAY = 2.5

async def safe_edit(msg, text, delay=1.5, parse_mode=None):
    if not msg:
        return

    key = msg.id
    now = asyncio.get_event_loop().time()

    if key in _last_edit and now - _last_edit[key] < delay:
        return

    try:
        await msg.edit_text(text, parse_mode=parse_mode)
        _last_edit[key] = now
    except MessageNotModified:
        pass
    except Exception:
        pass

async def api_delay():
    await asyncio.sleep(0.4)

_LANGUAGE_MAP = {
    'en': 'English',  'eng': 'English',
    'hi': 'Hindi',   'hin': 'Hindi',
    'ta': 'Tamil',   'tam': 'Tamil',
    'te': 'Telugu',  'tel': 'Telugu',
    'ml': 'Malayalam', 'mal': 'Malayalam',
    'kn': 'Kannada', 'kan': 'Kannada',
    'bn': 'Bengali', 'ben': 'Bengali',
    'mr': 'Marathi', 'mar': 'Marathi',
    'gu': 'Gujarati', 'guj': 'Gujarati',
    'pa': 'Punjabi', 'pun': 'Punjabi',
    'bho': 'Bhojpuri',
    'zh': 'Chinese', 'chi': 'Chinese', 'cmn': 'Chinese',
    'ko': 'Korean',  'kor': 'Korean',
    'pt': 'Portuguese', 'por': 'Portuguese',
    'th': 'Thai',    'tha': 'Thai',
    'tl': 'Tagalog', 'tgl': 'Tagalog', 'fil': 'Tagalog',
    'ja': 'Japanese', 'jpn': 'Japanese',
    'es': 'Spanish', 'spa': 'Spanish',
    'sv': 'Swedish', 'swe': 'Swedish',
    'fr': 'French', 'fra': 'French', 'fre': 'French',
    'de': 'German', 'deu': 'German', 'ger': 'German',
    'it': 'Italian', 'ita': 'Italian',
    'ru': 'Russian', 'rus': 'Russian',
    'ar': 'Arabic', 'ara': 'Arabic',
    'tr': 'Turkish', 'tur': 'Turkish',
    'nl': 'Dutch', 'nld': 'Dutch', 'dut': 'Dutch',
    'pl': 'Polish', 'pol': 'Polish',
    'vi': 'Vietnamese', 'vie': 'Vietnamese',
    'id': 'Indonesian', 'ind': 'Indonesian',
    'ms': 'Malay', 'msa': 'Malay', 'may': 'Malay',
    'fa': 'Persian', 'fas': 'Persian', 'per': 'Persian',
    'ur': 'Urdu', 'urd': 'Urdu',
    'he': 'Hebrew', 'heb': 'Hebrew',
    'el': 'Greek', 'ell': 'Greek', 'gre': 'Greek',
    'hu': 'Hungarian', 'hun': 'Hungarian',
    'cs': 'Czech', 'ces': 'Czech', 'cze': 'Czech',
    'ro': 'Romanian', 'ron': 'Romanian', 'rum': 'Romanian',
    'da': 'Danish', 'dan': 'Danish',
    'fi': 'Finnish', 'fin': 'Finnish',
    'no': 'Norwegian', 'nor': 'Norwegian',
    'uk': 'Ukrainian', 'ukr': 'Ukrainian',
    'ca': 'Catalan', 'cat': 'Catalan',
    'hr': 'Croatian', 'hrv': 'Croatian',
    'sk': 'Slovak', 'slk': 'Slovak', 'slo': 'Slovak',
    'sr': 'Serbian', 'srp': 'Serbian',
    'bg': 'Bulgarian', 'bul': 'Bulgarian',
    'unknown': 'Unknown'
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

def install_mediainfo():
    try:
        subprocess.run(["mediainfo", "--version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except:
        subprocess.run(["apt", "update", "-y"])
        subprocess.run(["apt", "install", "-y", "mediainfo"])

def run_gc():
    gc.collect()

def parse_int(value):
    try:
        return int(re.findall(r"\d+", str(value))[0])
    except:
        return 0

def parse_duration(value):
    try:
        if not value:
            return 0

        value = str(value).strip()

        if value.replace('.', '').isdigit():
            val = float(value)
            return val / 1000 if val > 10000 else val

        if ":" in value:
            parts = value.split(":")
            parts = [float(p) for p in parts]

            if len(parts) == 3:
                return parts[0]*3600 + parts[1]*60 + parts[2]
            elif len(parts) == 2:
                return parts[0]*60 + parts[1]

        return 0

    except:
        return 0

async def get_media_info(file_path):
    cmd = f'mediainfo --ParseSpeed=0 --Language=raw --Output=JSON "{file_path}"'

    try:
        process = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=5)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            return (0, None, None, None, "", "", "", "Unknown", "Unknown")

        try:
            data = json.loads(stdout.decode() or "{}")
        except:
            data = {}

    except Exception as e:
        logger.error(f"MediaInfo error: {e}")
        return (0, None, None, None, "", "", "", "Unknown", "Unknown")

    tracks = data.get("media", {}).get("track", [])

    duration = 0
    width = height = None
    codec = None
    bit_depth = ""
    hdr = ""
    transfer = ""

    audio_languages = set()
    subtitle_languages = set()

    for track in tracks:
        t = track.get("@type", "").lower()

        if t == "video":
            width = parse_int(track.get("Width"))
            height = parse_int(track.get("Height"))
            codec = (track.get("Format") or "").lower()

            bit_depth = track.get("BitDepth", "")
            transfer = (track.get("transfer_characteristics") or "").lower()

            if duration == 0:
                duration = parse_duration(track.get("Duration"))

            if "hdr" in str(track).lower():
                hdr = "HDR"
            if "dolby" in str(track).lower():
                hdr = "Dolby Vision"

        elif t == "audio":
            lang = track.get("Language", "unknown")
            audio_languages.add(get_full_language_name(lang))

        elif t in ["text", "menu", "subtitle"]:
            lang = track.get("Language", "unknown")
            subtitle_languages.add(get_full_language_name(lang))

    if duration == 0:
        for track in tracks:
            if track.get("@type", "").lower() == "general":
                duration = parse_duration(track.get("Duration"))
                if duration > 0:
                    break

    subtitle_text = (
        ", ".join(sorted(subtitle_languages))
        if subtitle_languages else "No Sub"
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

async def process_message(message, progress_msg=None):
    media = message.video or message.document

    MAX_RETRIES = 1
    retry_count = 0

    if progress_msg:
        await api_delay()
        await safe_edit(progress_msg, "⚡ Fast scan...")

    while retry_count <= MAX_RETRIES:
        temp_file = f"probe_16KB_{message.id}_{uuid.uuid4().hex}.bin"

        try:
            target_size = 65536

            async with stream_semaphore:
                async with aiopen(temp_file, "wb") as f:
                    async for chunk in app.stream_media(media, limit=1):
                        if not chunk:
                            break
                        await f.write(chunk[:target_size])
                        break

            await asyncio.sleep(1)

            if not os.path.exists(temp_file) or os.path.getsize(temp_file) == 0:
                retry_count += 1
                await asyncio.sleep(1)
                continue

            result = await get_media_info(temp_file)
            duration, width, height = result[:3]

            if width and height and duration > 0:
                return build_caption(message, media, result), None

        except Exception as e:
            logger.warning(f"16KB attempt failed: {e}")
            retry_count += 1
            await asyncio.sleep(1)

        finally:
            if os.path.exists(temp_file):
                await aioremove(temp_file)

    steps = [
        ("256KB", 262144),
    ]

    for label, target_size in steps:
        if progress_msg:
            await api_delay()
            await safe_edit(progress_msg, f"📦 Scanning {label}...")

        temp_file = f"probe_{label}_{message.id}_{uuid.uuid4().hex}.bin"

        try:
            written = 0

            async with stream_semaphore:
                async with aiopen(temp_file, "wb") as f:
                    async for chunk in app.stream_media(media, limit=1):
                        if not chunk:
                            break

                        remaining = target_size - written
                        if remaining <= 0:
                            break

                        to_write = chunk[:remaining]
                        await f.write(to_write)
                        written += len(to_write)

                        if written >= target_size:
                            break

            await asyncio.sleep(1)

            if not os.path.exists(temp_file) or os.path.getsize(temp_file) == 0:
                continue

            result = await get_media_info(temp_file)
            duration, width, height = result[:3]

            if width and height and duration > 0:
                return build_caption(message, media, result), None

        except Exception as e:
            logger.warning(f"{label} fallback failed: {e}")

        finally:
            if os.path.exists(temp_file):
                await aioremove(temp_file)

    try:
        if progress_msg:
            await api_delay()
            await safe_edit(progress_msg, "⬇️ Downloading (final fallback)...")

        file_path = await asyncio.wait_for(message.download(), timeout=30)
        result = await get_media_info(file_path)

        return build_caption(message, media, result), file_path

    except asyncio.TimeoutError:
        logger.error("Full download timeout")
        return "❌ Could not extract media info (file too large)", None
        
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

async def handle_private(message):
    file_path = None
    progress_msg = None
    user_id = message.from_user.id

    try:
        await api_delay()
        progress_msg = await message.reply_text("⏳ Processing...")

        caption, file_path = await process_message(message, progress_msg)

        try:
            await safe_edit(progress_msg, caption, parse_mode=ParseMode.HTML)
        except MessageNotModified:
            pass

    except Exception as e:
        logger.error(e)

    finally:
        active_users.discard(user_id)

        if file_path and os.path.exists(file_path):
            await aioremove(file_path)

async def process_channel_queue(channel_id):
    async with channel_locks[channel_id]:

        while channel_queues[channel_id]:
            message, caption = channel_queues[channel_id].pop(0)

            try:
                now = asyncio.get_event_loop().time()
                last = last_edit_time.get(channel_id, 0)

                if now - last < EDIT_DELAY:
                    await asyncio.sleep(EDIT_DELAY - (now - last))

                await message.edit_caption(caption, parse_mode=ParseMode.HTML)

                last_edit_time[channel_id] = asyncio.get_event_loop().time()

            except FloodWait as e:
                logger.warning(f"FloodWait: sleeping {e.value}s")
                await asyncio.sleep(e.value)

                try:
                    await message.edit_caption(caption, parse_mode=ParseMode.HTML)
                    last_edit_time[channel_id] = asyncio.get_event_loop().time()
                except Exception as err:
                    logger.error(f"Retry failed: {err}")

            except Exception as e:
                logger.error(f"Edit failed: {e}")

@app.on_message(filters.chat(ALLOWED_CHATS) & filters.channel & (filters.video | filters.document))
async def channel_handler(_, message):

    if caption_has_media_info(message.caption or ""):
        return

    original_caption = message.caption or ""

    caption, file_path = await process_message(message)

    if not caption or "❌ Could not extract media info" in caption:
        if original_caption:
            caption = original_caption
        else:
            return

    else:
        if original_caption:
            caption = f"{original_caption}\n\n{caption}"

    channel_id = message.chat.id

    channel_queues[channel_id].append((message, caption))

    asyncio.create_task(process_channel_queue(channel_id))

    if file_path and os.path.exists(file_path):
        await aioremove(file_path)
        
@app.on_message(filters.private & (filters.video | filters.document))
async def private_handler(_, message):

    user_id = message.from_user.id

    if user_id in active_users:
        await message.reply_text("⚠️ Please wait until your previous file is processed.")
        return

    active_users.add(user_id)
    asyncio.create_task(handle_private(message))

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

def build_caption(message, media, result):
    duration, width, height, codec, bit_depth, hdr, transfer, audio, sub = result

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

    return caption

@app.on_message(filters.command("info") & filters.reply)
async def info_command(_, message):
    reply = message.reply_to_message

    if not (reply.video or reply.document):
        return await message.reply_text("⚠️ Reply to a video or file.")

    media = reply.video or reply.document
    temp = f"info_{reply.id}.bin"

    try:
        async with stream_semaphore:
            async with aiopen(temp, "wb") as f:
                async for chunk in app.stream_media(media, limit=8):
                    await f.write(chunk)

        await asyncio.sleep(1)

        result = await get_media_info(temp)
        duration, width, height = result[:3]

        if not (duration > 0 and width and height):
            if os.path.exists(temp):
                os.remove(temp)

            temp = await reply.download()
            result = await get_media_info(temp)

        caption = build_caption(reply, media, result)

        await message.reply_text(caption, parse_mode=ParseMode.HTML)

    except Exception as e:
        await message.reply_text(f"❌ Failed to extract info\n\n<code>{e}</code>")

    finally:
        if temp and os.path.exists(temp):
            os.remove(temp)

async def main():
    gc.set_threshold(*GC_THRESHOLD)

    install_ffmpeg()
    install_mediainfo()
    await app.start()

    me = await app.get_me()
    logger.info(f"@{me.username} started")

    await app.send_message(ADMIN_ID, "🚀 Bot Started")

    scheduler.add_job(run_gc, "interval", minutes=10)
    scheduler.start()

    await asyncio.Event().wait()

if __name__ == "__main__":
    app.run(main())
