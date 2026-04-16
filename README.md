# MediaInfo Bot

A Telegram bot that automatically updates video captions with media information (quality, codec, duration, audio languages, subtitles) for posts in specified channels. Also works in private chats for on-demand media analysis.

**Made by [@piroxbots](https://t.me/piroxbots) · [Report bugs here](https://t.me/notyourpiro)**

---

## Features

- Detects resolution (1080p / 720p / 480p), codec (x264 / x265 / AV1), and bit depth (10Bit)
- Shows duration, audio languages, and subtitle availability
- Smart partial-download first — falls back to full download only if needed
- Parallel worker queue for handling multiple posts simultaneously
- Works in **channels** (auto-edits captions) and **private chats** (replies with media info)
- Scheduled garbage collection to keep memory usage low
- Admin commands: `/server`, `/restart`, `/update`, `/shutdown`

## Supported Audio Languages

The bot recognizes and maps the following languages from stream metadata:

Hindi, Marathi, Tamil, Telugu, Malayalam, Kannada, English, Hungarian, Indonesian, Italian, Japanese, Korean, Chinese, Spanish, Portuguese, Thai, Polish, French, German, Arabic, Russian, Turkish, Vietnamese, Dutch, and more. Unknown streams are labelled `Unknown`.

## Requirements

- Python 3.10+
- `ffprobe` (part of [FFmpeg](https://ffmpeg.org/)) — auto-installed if missing
- Telegram API credentials from [my.telegram.org](https://my.telegram.org)
- A bot token from [@BotFather](https://t.me/BotFather)
- The bot must be an **admin** in the target channels (to edit captions)

## Setup

### 1. Clone the repo

```bash
git clone https://github.com/youruser/mediainfo-bot.git
cd mediainfo-bot
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure environment variables

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
```

| Variable | Description | Default |
|----------|-------------|---------|
| `API_ID` | Telegram API ID from my.telegram.org | — |
| `API_HASH` | Telegram API hash from my.telegram.org | — |
| `BOT_TOKEN` | Bot token from @BotFather | — |
| `ADMIN_ID` | Your Telegram user ID (for admin commands) | — |
| `ALLOWED_CHATS` | Comma-separated channel IDs (e.g. `-1001234567890`) | — |
| `WORKERS` | Number of parallel workers | `3` |
| `STREAM_LIMIT` | Chunks to stream before trying full download | `5` |
| `LOG_LEVEL` | Logging level (`INFO`, `DEBUG`, etc.) | `INFO` |
| `LOG_FORMAT` | Log line format string | timestamp/module/level |
| `GC_THRESHOLD_0/1/2` | Python GC generation thresholds | `500, 5, 5` |
| `CAPTION_TEMPLATE` | Custom HTML caption template (see below) | built-in |

### 4. Run

```bash
python bot.py
```

## Docker

```bash
docker build -t mediainfo-bot .
docker run -d --env-file .env mediainfo-bot
```

## Deploy to Railway / Heroku

Set the environment variables in the platform dashboard and deploy. The `Procfile` is already included.

## Caption Format

```
<filename or original caption>

🎬 1080p x265 10Bit | ⏳ 01:45:32
🔊 English, Hindi
💬 ESUB
```

The caption template can be fully customized via the `CAPTION_TEMPLATE` environment variable. It uses Python's `str.format()` with these placeholders:

| Placeholder | Description |
|-------------|-------------|
| `{title}` | Original caption or filename |
| `{video_line}` | Quality + codec + bit depth (e.g. `1080p x265 10Bit`) |
| `{duration}` | Duration in `HH:MM:SS` format |
| `{audio}` | Comma-separated audio language list |
| `{subtitle}` | `ESUB` if subtitles found, otherwise `No Sub` |

## Admin Commands

| Command | Description |
|---------|-------------|
| `/server` | Show CPU, RAM, and disk usage |
| `/restart` | Restart the bot process |
| `/update` | Pull latest changes from git and restart |
| `/shutdown` | Stop the bot completely |

## User Commands

| Command | Description |
|---------|-------------|
| `/start` | Introduction and usage guide |

## How It Works

1. A video or document is posted in an allowed channel (or sent in a private chat).
2. The bot streams the first few chunks of the file and runs `ffprobe` on the partial download.
3. If the partial download yields valid info (duration > 0), the temp file is used as-is.
4. If not, the full file is downloaded and re-analysed.
5. The formatted caption is either edited onto the channel post or sent as a reply in private chat.
6. Temp files are cleaned up automatically after each job.

## Bug Reports & Support

Found a bug or need help? Reach out at **[@notyourpiro](https://t.me/notyourpiro)**

**Bot by [@piroxbots](https://t.me/piroxbots)**