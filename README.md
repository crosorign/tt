# Tech Meets Travel — Car News Automation Bot

Fully automated YouTube channel for Indian car news. Daily 2-minute videos.

## Channel: [@tech_meets_travel](https://www.youtube.com/@tech_meets_travel)

## What this bot does

Twice daily (7 AM & 7 PM IST):
1. Scrapes Indian car news (Autocar, CarDekho, Zigwheels)
2. LLM picks best topic for today's audience
3. Generates 2-min English script (220-280 words)
4. Fetches relevant Pexels car images
5. Generates voiceover (male/female based on format)
6. Creates professional video with Ken Burns, transitions, text overlays
7. Burns English subtitles into video
8. Uploads to YouTube with SEO metadata + pinned comment
9. Generates YouTube Shorts version (40s vertical)

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

### 3. YouTube OAuth (one time)
```bash
# Place client_secrets.json from Google Cloud Console
python3 setup_youtube_secrets.py

# Encode token for GitHub Actions
base64 -w 0 youtube_token.pickle
# → Copy output → GitHub Secrets → YOUTUBE_TOKEN_BASE64
```

### 4. GitHub Secrets to set
| Secret | Value |
|--------|-------|
| `GEMINI_KEY` | Gemini API key |
| `GROQ_API_KEY` | Groq API key |
| `PEXELS_API_KEY` | Pexels API key |
| `YOUTUBE_TOKEN_BASE64` | base64 of youtube_token.pickle |
| `CLIENT_SECRETS_BASE64` | base64 of client_secrets.json |

## Usage

```bash
# Auto topic (LLM decides)
python3 car_bot.py --day today

# Auto topic + upload
python3 car_bot.py --day today --upload

# Custom topic
python3 car_bot.py --topic "Tata Harrier EV launch price revealed"

# Custom topic + format
python3 car_bot.py --topic "Thar vs Scorpio" --format comparison

# 24/7 daemon
python3 car_bot.py --daemon
```

## Content formats

| Format | Voice | BGM mood | Use case |
|--------|-------|----------|----------|
| `news` | Male | Energetic modern | Breaking car news, price announcements |
| `launch` | Male | Exciting reveal | New car launch details |
| `comparison` | Female | Analytical neutral | Side-by-side car comparison |
| `explainer` | Male | Calm informative | How ADAS, hybrid tech works |
| `ev` | Female | Futuristic tech | Electric vehicle deep dives |
| `suv` | Male | Powerful bold | SUV news and offroad content |

## Output per video

```
videos/          → full 2-min video (1920×1080)
shorts/          → 40s vertical clip (1080×1920)
subtitles/       → English subtitle file
scripts/         → script text
metadata/        → title, description, tags JSON
thumbnails/      → branded thumbnail PNG
```

## Automation schedule

| Time IST | Content |
|----------|---------|
| 7:00 AM | Morning news video (auto upload) |
| 7:00 PM | Evening format video (comparison/explainer/suv) |

Both run via GitHub Actions scheduled triggers.
