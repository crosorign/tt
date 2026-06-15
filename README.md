# Tech Meets Travel — Car News Automation Bot

Fully automated YouTube channel for global + Indian car and EV news. Hybrid video lengths: ~2 min daily shorts by default, 5–8 min long-form for high-score stories.

## Channel: [@tech_meets_travel](https://www.youtube.com/@tech_meets_travel)

## What this bot does

Twice daily (7 AM & 7 PM IST):
1. Scrapes car news and trending searches
2. LLM picks the highest-potential story (Tesla/BYD/global EV + Indian launches)
3. Scores monetization potential and selects short vs long video mode
4. Generates script-aware content package (titles, Shorts script, tags, keywords, chapters)
5. Fetches topic-relevant stock images via search queries
6. Creates main video (Ken Burns, subtitles, overlays) + dedicated vertical Short
7. Uploads to YouTube with SEO metadata, AI disclosure, pinned comment, custom thumbnail

## Setup

### 1. Install dependencies
```bash
pip install google-genai groq edge-tts google-api-python-client \
            google-auth-oauthlib requests beautifulsoup4 Pillow schedule
sudo apt install ffmpeg
```

### 2. Set environment variables
```bash
export GEMINI_KEY="your_key"          # aistudio.google.com/apikey
export GROQ_API_KEY="your_key"        # console.groq.com
export PEXELS_API_KEY="your_key"      # pexels.com/api
```

Optional: add royalty-free MP3 files to `assets/bgm/{format}.mp3` (news, launch, ev, etc.) for better background music.

### 3. YouTube OAuth (one time)
```bash
python3 setup_youtube_secrets.py
base64 -w 0 youtube_token.pickle
# → GitHub Secrets → YOUTUBE_TOKEN_BASE64
```

## Usage

```bash
# Default: ~2 min short video
python3 car_bot.py --day today --upload

# Force 5–8 min long-form
python3 car_bot.py --day today --upload --long-form

# Auto long-form when monetization score >= 7 (evening CI slot)
python3 car_bot.py --day today --upload --auto-long-form

# Custom topic
python3 car_bot.py --topic "BYD Seal vs Tesla Model 3" --format comparison --upload
```

## Video modes

| Mode | Length | Words | Trigger |
|------|--------|-------|---------|
| `short` | ~2 min | 280–340 | Default (morning slot) |
| `long` | 5–8 min | 750–1200 | `--long-form` or score ≥ 7 + tier 1–2 |

## Content formats

| Format | Voice | Use case |
|--------|-------|----------|
| `news` | Male | Breaking car news |
| `launch` | Male | New model reveals |
| `comparison` | Female | Head-to-head picks |
| `explainer` | Male | Tech explained simply |
| `ev` | Female | EV deep dives |
| `suv` | Male | SUV news |

## Output per video

```
videos/          → main video (1920×1080)
shorts/          → dedicated 45–60s vertical Short
subtitles/       → English subtitle file
scripts/         → full script text
metadata/        → expanded JSON (titles, keywords, chapters, short_script, etc.)
thumbnails/      → CTR-optimized thumbnail PNG
```

## Automation schedule

| Time IST | Slot | Mode |
|----------|------|------|
| 7:00 AM | Morning | Short (fast daily news) |
| 7:00 PM | Evening | `--auto-long-form` when story scores high |

Both run via GitHub Actions (`.github/workflows/daily.yml`).
