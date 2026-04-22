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
import time
from aiofiles import open as aiopen
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from functools import lru_cache
from typing import Optional
from aiofiles.os import remove as aioremove
from pyrogram.errors import MessageNotModified, FloodWait
from collections import defaultdict
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

app = Client(
    "MediaInfo-Bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    workers=6,
    sleep_threshold=60,
)

stream_semaphore  = asyncio.Semaphore(6)
channel_semaphore = asyncio.Semaphore(3)
active_users: set = set()

_last_edit:      dict[int, float] = {}
channel_queues:  dict[int, list]  = defaultdict(list)
channel_locks:   dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
last_edit_time:  dict[int, float] = {}
EDIT_DELAY = 3.5

scheduler = AsyncIOScheduler()


_LANGUAGE_MAP: dict[str, str] = {
    'en': 'English',  'eng': 'English',
    'hi': 'Hindi',    'hin': 'Hindi',
    'ta': 'Tamil',    'tam': 'Tamil',
    'te': 'Telugu',   'tel': 'Telugu',
    'ml': 'Malayalam','mal': 'Malayalam',
    'kn': 'Kannada',  'kan': 'Kannada',
    'bn': 'Bengali',  'ben': 'Bengali',
    'mr': 'Marathi',  'mar': 'Marathi',
    'gu': 'Gujarati', 'guj': 'Gujarati',
    'pa': 'Punjabi',  'pun': 'Punjabi',
    'bho':'Bhojpuri',
    'zh': 'Chinese',  'chi': 'Chinese',  'cmn': 'Chinese',
    'ko': 'Korean',   'kor': 'Korean',
    'pt': 'Portuguese','por': 'Portuguese',
    'th': 'Thai',     'tha': 'Thai',
    'tl': 'Tagalog',  'tgl': 'Tagalog',  'fil': 'Tagalog',
    'ja': 'Japanese', 'jpn': 'Japanese',
    'es': 'Spanish',  'spa': 'Spanish',
    'sv': 'Swedish',  'swe': 'Swedish',
    'fr': 'French',   'fra': 'French',   'fre': 'French',
    'de': 'German',   'deu': 'German',   'ger': 'German',
    'it': 'Italian',  'ita': 'Italian',
    'ru': 'Russian',  'rus': 'Russian',
    'ar': 'Arabic',   'ara': 'Arabic',
    'tr': 'Turkish',  'tur': 'Turkish',
    'nl': 'Dutch',    'nld': 'Dutch',    'dut': 'Dutch',
    'pl': 'Polish',   'pol': 'Polish',
    'vi': 'Vietnamese','vie': 'Vietnamese',
    'id': 'Indonesian','ind': 'Indonesian',
    'ms': 'Malay',    'msa': 'Malay',    'may': 'Malay',
    'fa': 'Persian',  'fas': 'Persian',  'per': 'Persian',
    'ur': 'Urdu',     'urd': 'Urdu',
    'he': 'Hebrew',   'heb': 'Hebrew',
    'el': 'Greek',    'ell': 'Greek',    'gre': 'Greek',
    'hu': 'Hungarian','hun': 'Hungarian',
    'cs': 'Czech',    'ces': 'Czech',    'cze': 'Czech',
    'ro': 'Romanian', 'ron': 'Romanian', 'rum': 'Romanian',
    'da': 'Danish',   'dan': 'Danish',
    'fi': 'Finnish',  'fin': 'Finnish',
    'no': 'Norwegian','nor': 'Norwegian',
    'uk': 'Ukrainian','ukr': 'Ukrainian',
    'ca': 'Catalan',  'cat': 'Catalan',
    'hr': 'Croatian', 'hrv': 'Croatian',
    'sk': 'Slovak',   'slk': 'Slovak',   'slo': 'Slovak',
    'sr': 'Serbian',  'srp': 'Serbian',
    'bg': 'Bulgarian','bul': 'Bulgarian',
    'unknown': 'Original Audio',
}


@lru_cache(maxsize=256)
def get_full_language_name(code: str) -> str:
    if not code:
        return 'Unknown'
    cleaned = code.split('(')[0].strip()
    return _LANGUAGE_MAP.get(cleaned.lower(), 'Original Audio')


@lru_cache(maxsize=64)
def get_standard_resolution(height: int) -> Optional[str]:
    if not height:
        return None
    if height <= 240:  return "240p"
    if height <= 360:  return "360p"
    if height <= 480:  return "480p"
    if height <= 720:  return "720p"
    if height <= 1080: return "1080p"
    if height <= 1440: return "1440p"
    if height <= 2160: return "2160p"
    return "2160p+"


@lru_cache(maxsize=128)
def get_video_format(codec: str, transfer: str = '', hdr: str = '', bit_depth: str = '') -> Optional[str]:
    if not codec:
        return None
    codec = codec.lower()
    parts: list[str] = []

    if   any(x in codec for x in ('hevc', 'h.265', 'h265')):  parts.append('HEVC')
    elif 'av1' in codec:                                        parts.append('AV1')
    elif any(x in codec for x in ('avc', 'avc1', 'h.264', 'h264')): parts.append('x264')
    elif 'vp9' in codec:                                        parts.append('VP9')
    elif any(x in codec for x in ('mpeg4', 'xvid')):            parts.append('MPEG4')
    else:
        return None

    try:
        if bit_depth and int(bit_depth) > 8:
            parts.append(f"{bit_depth}bit")
    except (ValueError, TypeError):
        pass

    t = transfer.lower();  h = hdr.lower()
    if any(x in t for x in ('pq', 'hlg', 'smpte', '2084', 'st 2084')) or 'hdr' in h or 'dolby' in h:
        parts.append('HDR')

    return ' '.join(parts)


def _is_video_track(track: dict) -> bool:
    t      = (track.get('@type',        '') or '').lower()
    fmt    = (track.get('Format',       '') or '').lower()
    cid    = (track.get('CodecID',      '') or '').lower()
    fp     = (track.get('Format_Profile','') or '').lower()
    title  = (track.get('Title',        '') or '').lower()
    menu   = str(track.get('MenuID',    '') or '').lower()

    return any([
        t == 'video',
        any(x in fmt for x in ('avc','hevc','h.264','h264','h.265','h265','av1','vp9','mpeg-4','mpeg4','xvid')),
        any(x in cid for x in ('avc','h264','hevc','h265','av1','vp9','mpeg4','xvid','27')),
        'video' in menu,
        'video' in title,
        any(x in fp  for x in ('main','high','baseline')),
    ])


def _has_subtitles(tracks: list) -> bool:
    for track in tracks:
        if not isinstance(track, dict):
            continue
        t   = (track.get('@type',       '') or '').lower()
        fmt = (track.get('Format',      '') or '').lower()
        cid = (track.get('CodecID',     '') or '').lower()
        enc = (track.get('Encoding',    '') or '').lower()
        fi  = (track.get('Format_Info', '') or '').lower()
        ttl = (track.get('Title',       '') or '').lower()
        if any([
            t == 'text',
            any(x in fmt for x in ('pgs','subrip','ass','ssa','srt','dvb_subtitle','dvd_subtitle')),
            any(x in cid for x in ('s_text','subp','pgs','subtitle','dvb','dvd')),
            any(x in enc for x in ('utf-8','utf8','unicode','text')),
            any(x in fi  for x in ('subtitle','caption','text')),
            'subtitle' in ttl,
        ]):
            return True
    return False


def _parse_int(value) -> int:
    try:
        return int(re.findall(r"\d+", str(value))[0])
    except Exception:
        return 0


def _parse_duration(value) -> float:
    try:
        if not value:
            return 0
        v = str(value).strip()
        if v.replace('.', '', 1).lstrip('-').isdigit():
            f = float(v)
            if f > 86_400_000:
                return f / 1_000_000
            if f > 86_400:
                return f / 1_000
            return f
        if ':' in v:
            parts = [float(p) for p in v.split(':')]
            if len(parts) == 3:
                return parts[0]*3600 + parts[1]*60 + parts[2]
            if len(parts) == 2:
                return parts[0]*60 + parts[1]
    except Exception:
        pass
    return 0


def _fmt_duration(s: float) -> str:
    s = int(s)
    return f"{s//3600:02}:{(s%3600)//60:02}:{s%60:02}"


async def _run_mediainfo(path: str) -> dict:
    try:
        proc = await asyncio.create_subprocess_shell(
            f'mediainfo --ParseSpeed=0 --Language=raw --Output=JSON "{path}"',
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=8)
        except asyncio.TimeoutError:
            proc.kill();  await proc.wait()
            return {}
        return json.loads(stdout.decode() or '{}')
    except Exception as e:
        logger.warning(f"mediainfo error: {e}")
        return {}


async def _run_ffprobe_full(path: str) -> dict:
    try:
        proc = await asyncio.create_subprocess_shell(
            f'ffprobe -v error -show_streams -show_format -of json "{path}"',
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=8)
        return json.loads(out.decode() or '{}')
    except Exception as e:
        logger.warning(f"ffprobe error: {e}")
        return {}


def _parse_ffprobe(data: dict) -> tuple:
    streams  = data.get('streams', [])
    fmt      = data.get('format',  {})

    duration  = 0.0
    width = height = None
    codec = bit_depth = hdr = transfer = ''
    audio_langs: set[str] = set()
    sub_langs:   set[str] = set()
    has_sub = False

    dur_raw = fmt.get('duration') or ''
    if dur_raw:
        duration = _parse_duration(dur_raw)

    for s in streams:
        ctype = (s.get('codec_type') or '').lower()
        tags  = s.get('tags') or {}

        if ctype == 'video':
            if not width:
                width  = s.get('width')  or s.get('coded_width')
            if not height:
                height = s.get('height') or s.get('coded_height')

            codec_raw = (s.get('codec_name') or '').lower()
            if   'hevc' in codec_raw or 'h265' in codec_raw: codec = 'hevc'
            elif 'h264' in codec_raw or 'avc'  in codec_raw: codec = 'avc'
            elif 'av1'  in codec_raw:                          codec = 'av1'
            elif 'vp9'  in codec_raw:                          codec = 'vp9'
            elif 'mpeg4' in codec_raw or 'xvid' in codec_raw: codec = 'mpeg4'
            else: codec = codec_raw

            bps = str(s.get('bits_per_raw_sample') or s.get('bits_per_coded_sample') or '')
            if bps.isdigit() and bps != '0':
                bit_depth = bps

            ct = (s.get('color_transfer') or '').lower()
            cs = (s.get('color_space')    or '').lower()
            if any(x in ct for x in ('smpte2084', 'arib-std-b67', 'smpte428')):
                hdr = 'HDR'
            elif 'bt2020' in cs and not hdr:
                hdr = 'HDR'

            if not duration:
                d = tags.get('DURATION') or tags.get('duration') or ''
                if d:
                    duration = _parse_duration(d)

        elif ctype == 'audio':
            lang = tags.get('language') or tags.get('LANGUAGE') or ''
            audio_langs.add(get_full_language_name(lang or 'unknown'))

        elif ctype == 'subtitle':
            has_sub = True
            lang = tags.get('language') or tags.get('LANGUAGE') or ''
            if lang:
                sub_langs.add(get_full_language_name(lang))

    audio_str = ', '.join(sorted(audio_langs)) if audio_langs else 'Original Audio'
    if sub_langs:
        sub_str = ', '.join(sorted(sub_langs))
    elif has_sub:
        sub_str = 'ESUB'
    else:
        sub_str = 'No Esubs'

    return duration, width, height, codec, bit_depth, hdr, transfer, audio_str, sub_str


def _parse_tracks(tracks: list) -> tuple:
    duration  = 0.0
    width = height = None
    codec = bit_depth = hdr = transfer = ''
    audio_langs: set[str] = set()
    sub_langs:   set[str] = set()

    for track in tracks:
        if not isinstance(track, dict):
            continue
        t = (track.get('@type', '') or '').lower()

        if t == 'general':
            if not duration:
                duration = _parse_duration(track.get('Duration'))

        elif _is_video_track(track):
            for field in ('Height', 'Sampled_Height', 'Encoded_Height'):
                raw = str(track.get(field, '') or '').split()[0]
                if raw.isdigit():
                    height = int(raw)
                    break

            for field in ('Width', 'Sampled_Width', 'Encoded_Width'):
                raw = str(track.get(field, '') or '').split()[0]
                if raw.isdigit():
                    width = int(raw)
                    break

            codec     = (track.get('Format', '') or '').lower()
            bit_depth = track.get('BitDepth', '') or ''
            transfer  = (track.get('transfer_characteristics', '') or
                         track.get('TransferCharacteristics', '') or '').lower()
            hdr       = (track.get('HDR_Format', '') or
                         track.get('HDR_Format_Compatibility', '') or '')

            if not duration:
                duration = _parse_duration(track.get('Duration'))

            track_str = str(track).lower()
            if 'dolby vision' in track_str:
                hdr = 'Dolby Vision'
            elif 'hdr' in track_str and not hdr:
                hdr = 'HDR'

        elif t == 'audio':
            lang = None
            for field in ('Language', 'Language_String', 'Title'):
                v = track.get(field)
                if v:
                    lang = v
                    break
            audio_langs.add(get_full_language_name(lang or 'unknown'))

        elif t in ('text', 'menu', 'subtitle'):
            lang = track.get('Language') or track.get('Language_String') or 'unknown'
            sub_langs.add(get_full_language_name(lang))

    audio_str = ', '.join(sorted(audio_langs)) if audio_langs else 'Original Audio'
    sub_str   = ', '.join(sorted(sub_langs))   if sub_langs   else (
                    'ESUB' if _has_subtitles(tracks) else 'No Esubs')

    return duration, width, height, codec, bit_depth, hdr, transfer, audio_str, sub_str


async def _probe(path: str) -> tuple:
    mi_data = await _run_mediainfo(path)
    tracks  = mi_data.get('media', {}).get('track', [])
    mi      = _parse_tracks(tracks)
    mi_dur, mi_w, mi_h = mi[0], mi[1], mi[2]

    fp_data = await _run_ffprobe_full(path)
    fp      = _parse_ffprobe(fp_data) if fp_data else None

    if fp is None:
        return mi

    fp_dur, fp_w, fp_h = fp[0], fp[1], fp[2]

    duration  = mi_dur or fp_dur
    width     = mi_w   or fp_w
    height    = mi_h   or fp_h
    codec     = mi[3]  or fp[3]
    bit_depth = mi[4]  or fp[4]
    hdr       = mi[5]  or fp[5]
    transfer  = mi[6]  or fp[6]
    audio     = mi[7] if mi[7] != 'Unknown' else fp[7]
    subtitle  = mi[8] if mi[8] != 'No Sub'  else fp[8]

    return duration, width, height, codec, bit_depth, hdr, transfer, audio, subtitle


def _build_caption(message, media, result: tuple) -> str:
    duration, width, height, codec, bit_depth, hdr, transfer, audio, sub = result

    quality     = get_standard_resolution(min(w for w in (width, height) if w) if width and height else (height or width or 0))
    fmt         = get_video_format(codec, transfer, hdr, bit_depth)
    video_line  = ' '.join(filter(None, [quality, fmt])) or 'Unknown'

    return CAPTION_TEMPLATE.format(
        title     = message.caption or getattr(media, 'file_name', None) or 'Video',
        video_line= video_line,
        duration  = _fmt_duration(duration) if duration else 'Unknown',
        audio     = audio,
        subtitle  = sub,
    )


def caption_has_media_info(caption: str) -> bool:
    if not caption:
        return False
    hits = (
        bool(re.search(r'🎬', caption)),
        bool(re.search(r'⏳\s*\d{2}:\d{2}:\d{2}', caption)),
        bool(re.search(r'🔊', caption)),
        bool(re.search(r'💬', caption)),
    )
    return sum(hits) >= 2


_STREAM_STEPS = [
    ("16KB",  16  * 1024),
    ("1MB",   1   * 1024 * 1024),
    ("3MB",   3   * 1024 * 1024),
    ("8MB",   8   * 1024 * 1024),
]


async def _stream_chunk(media, size: int, path: str) -> bool:
    try:
        written = 0
        async with stream_semaphore:
            async with aiopen(path, 'wb') as f:
                async for chunk in app.stream_media(media):
                    if not chunk:
                        break
                    remaining = size - written
                    if remaining <= 0:
                        break
                    piece = chunk[:remaining]
                    await f.write(piece)
                    written += len(piece)
                    if written >= size:
                        break
        return os.path.exists(path) and os.path.getsize(path) > 0
    except Exception as e:
        logger.warning(f"stream_chunk failed ({size}): {e}")
        return False


async def process_message(message, progress_msg=None) -> tuple[str, Optional[str]]:
    media = message.video or message.document

    async def _update(text: str):
        if progress_msg:
            await _safe_edit(progress_msg, text)
            await asyncio.sleep(0.3)

    await _update("⚡ Fast scan (16 KB)…")

    for label, size in _STREAM_STEPS:
        tmp = f"probe_{label}_{message.id}_{uuid.uuid4().hex[:8]}.bin"
        try:
            await _update(f"📦 Scanning {label}…")
            ok = await _stream_chunk(media, size, tmp)
            if not ok:
                continue

            result = await _probe(tmp)
            _, w, h = result[0], result[1], result[2]
            if w or h:
                return _build_caption(message, media, result), None

        except Exception as e:
            logger.warning(f"{label} probe error: {e}")
        finally:
            if os.path.exists(tmp):
                await aioremove(tmp)

    await _update("⬇️ Full download (fallback)…")
    try:
        file_size = getattr(media, 'file_size', 0) or 0
        if file_size > 2 * 1024 ** 3:
            return message.caption or getattr(media, 'file_name', None) or 'Video', None

        try:
            file_path = await asyncio.wait_for(message.download(), timeout=60)
        except FloodWait as e:
            await asyncio.sleep(e.value)
            file_path = await message.download()

        result = await _probe(file_path)
        return _build_caption(message, media, result), file_path

    except asyncio.TimeoutError:
        logger.error("Full download timed out")
    except Exception as e:
        logger.error(f"Full download failed: {e}")

    return message.caption or getattr(media, 'file_name', None) or 'Video', None


async def _safe_edit(msg, text: str, parse_mode=None):
    if not msg:
        return
    key  = msg.id
    now  = asyncio.get_event_loop().time()
    if key in _last_edit and now - _last_edit[key] < 1.7:
        return
    try:
        await msg.edit_text(text, parse_mode=parse_mode)
        _last_edit[key] = now
    except (MessageNotModified, Exception):
        pass


async def _process_channel_queue(channel_id: int):
    global EDIT_DELAY
    async with channel_locks[channel_id]:
        while channel_queues[channel_id]:
            message, caption = channel_queues[channel_id].pop(0)
            now  = asyncio.get_event_loop().time()
            last = last_edit_time.get(channel_id, 0)
            if now - last < EDIT_DELAY:
                await asyncio.sleep(EDIT_DELAY - (now - last))
            try:
                await message.edit_caption(caption, parse_mode=ParseMode.HTML)
                last_edit_time[channel_id] = asyncio.get_event_loop().time()
            except FloodWait as e:
                EDIT_DELAY = max(EDIT_DELAY, e.value / 10 + 1)
                await asyncio.sleep(e.value)
                try:
                    await message.edit_caption(caption, parse_mode=ParseMode.HTML)
                    last_edit_time[channel_id] = asyncio.get_event_loop().time()
                except Exception as err:
                    logger.error(f"Retry edit failed: {err}")
            except Exception as e:
                logger.error(f"Edit failed: {e}")


@app.on_message(
    filters.chat(ALLOWED_CHATS) & filters.channel &
    (filters.video | filters.document)
)
async def channel_handler(_, message):
    if caption_has_media_info(message.caption or ''):
        return

    caption, file_path = await process_message(message)

    channel_id = message.chat.id
    channel_queues[channel_id].append((message, caption))
    asyncio.create_task(_process_channel_queue(channel_id))

    if file_path and os.path.exists(file_path):
        await aioremove(file_path)


@app.on_message(filters.private & (filters.video | filters.document))
async def private_handler(_, message):
    user_id = message.from_user.id
    if user_id in active_users:
        await message.reply_text("⚠️ Please wait until your current file is processed.")
        return
    active_users.add(user_id)
    asyncio.create_task(_handle_private(message))


async def _handle_private(message):
    file_path = None
    progress_msg = None
    user_id = message.from_user.id
    try:
        await asyncio.sleep(0.5)
        progress_msg = await message.reply_text("⏳ Processing…")
        caption, file_path = await process_message(message, progress_msg)
        try:
            await _safe_edit(progress_msg, caption, parse_mode=ParseMode.HTML)
        except MessageNotModified:
            pass
    except Exception as e:
        logger.error(f"Private handler error: {e}")
    finally:
        active_users.discard(user_id)
        if file_path and os.path.exists(file_path):
            await aioremove(file_path)


@app.on_message(filters.command("info") & filters.reply)
async def info_command(_, message):
    reply = message.reply_to_message
    if not (reply and (reply.video or reply.document)):
        return await message.reply_text("⚠️ Reply to a video or document.")

    media = reply.video or reply.document
    tmp   = f"info_{reply.id}_{uuid.uuid4().hex[:6]}.bin"
    try:
        ok = await _stream_chunk(media, 8 * 1024 * 1024, tmp)
        if not ok:
            tmp2 = await reply.download()
            result = await _probe(tmp2)
            os.remove(tmp2)
        else:
            result = await _probe(tmp)

        caption = _build_caption(reply, media, result)
        await message.reply_text(caption, parse_mode=ParseMode.HTML)
    except Exception as e:
        await message.reply_text(f"❌ Failed\n\n<code>{e}</code>", parse_mode=ParseMode.HTML)
    finally:
        if os.path.exists(tmp):
            await aioremove(tmp)


@app.on_message(filters.command("start") & filters.private)
async def start(_, m):
    await m.reply_text(
        "<b>🎬 Media Info Bot</b>\n\n"
        "Send me any video or file and I'll extract detailed media information.\n\n"
        "I provide:\n"
        "• 🎞 Video quality, codec &amp; bit depth\n"
        "• ⏳ Duration\n"
        "• 🔊 Audio languages\n"
        "• 💬 Subtitle info\n\n"
        "<b>⚡ Fast • Clean • Accurate</b>\n\n"
        "📌 <i>Note:</i> Send one file at a time.\n\n"
        "🤖 Bot by @piroxbots",
        parse_mode=ParseMode.HTML,
    )


@app.on_message(filters.command("server") & filters.user(ADMIN_ID))
async def server_cmd(_, m):
    await m.reply_text(
        f"CPU: {psutil.cpu_percent()}%\n"
        f"RAM: {psutil.virtual_memory().percent}%\n"
        f"Disk: {psutil.disk_usage('/').percent}%"
    )


@app.on_message(filters.command("restart") & filters.user(ADMIN_ID))
async def restart_cmd(_, m):
    await m.reply_text("Restarting…")
    os.execv(sys.executable, [sys.executable] + sys.argv)


@app.on_message(filters.command("shutdown") & filters.user(ADMIN_ID))
async def shutdown_cmd(_, m):
    await m.reply_text("Shutting down…")
    scheduler.shutdown(wait=False)
    await app.stop()
    os._exit(0)


@app.on_message(filters.command("update") & filters.user(ADMIN_ID))
async def update_cmd(_, m):
    await m.reply_text("Updating…")
    try:
        os.system("git pull")
        os.system("pip install -r requirements.txt --no-cache-dir -q")
        await m.reply_text("✅ Updated. Restarting…")
        os.execv(sys.executable, [sys.executable] + sys.argv)
    except Exception as e:
        await m.reply_text(f"Update failed: {e}")


def _install_deps():
    for binary, pkg in (("ffprobe", "ffmpeg"), ("mediainfo", "mediainfo")):
        r = subprocess.run(["which", binary], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if r.returncode != 0:
            logger.info(f"Installing {pkg}…")
            subprocess.run(["apt", "update", "-y"], stdout=subprocess.DEVNULL)
            subprocess.run(["apt", "install", "-y", pkg], stdout=subprocess.DEVNULL)


async def main():
    gc.set_threshold(*GC_THRESHOLD)
    _install_deps()

    await app.start()
    me = await app.get_me()
    logger.info(f"@{me.username} started")
    await app.send_message(ADMIN_ID, "🚀 Bot Started")

    scheduler.add_job(gc.collect, "interval", minutes=20)
    scheduler.start()

    await asyncio.Event().wait()


if __name__ == "__main__":
    app.run(main())