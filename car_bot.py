#!/usr/bin/env python3
"""
Tech Meets Travel — CAR NEWS AUTOMATION BOT v1.3
Fully automated YouTube channel for Indian car news.
Daily 2-min videos · English · Auto upload · GitHub Actions

Usage:
  python car_bot.py --day today
  python car_bot.py --day today --upload
  python car_bot.py --topic "Tata Harrier EV launch price revealed"
  python car_bot.py --daemon
  python car_bot.py --auth-youtube
"""

import argparse
import base64
import concurrent.futures
import datetime
import hashlib
import json
import os
import pickle
import random
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

try:
    import google.genai as genai
except ImportError:
    print("pip install google-genai"); sys.exit(1)

try:
    from groq import Groq
except ImportError:
    Groq = None

try:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
except ImportError:
    print("pip install google-api-python-client google-auth-oauthlib"); sys.exit(1)

try:
    import schedule
    HAS_SCHEDULE = True
except ImportError:
    HAS_SCHEDULE = False


GEMINI_KEY     = os.environ.get("GEMINI_KEY", "")
GROQ_API_KEY   = os.environ.get("GROQ_API_KEY", "")
PEXELS_API_KEY = os.environ.get("PEXELS_API_KEY", "")
GITHUB_TOKEN   = os.environ.get("GITHUB_TOKEN", "")
GH_MODEL       = "gpt-4o-mini"

OUTPUT_DIR   = "videos"
SHORTS_DIR   = "shorts"
METADATA_DIR = "metadata"
SCRIPTS_DIR  = "scripts"
PEXELS_DIR   = "pexels_images"
SUBS_DIR     = "subtitles"
QUEUE_FILE   = "upload_queue.json"

YOUTUBE_SCOPES         = ["https://www.googleapis.com/auth/youtube",
                          "https://www.googleapis.com/auth/youtube.upload"]
YOUTUBE_TOKEN_FILE     = "youtube_token.pickle"
YOUTUBE_CLIENT_SECRETS = "client_secrets.json"

CHANNEL_NAME   = "Tech Meets Travel"
CHANNEL_HANDLE = "@tech_meets_travel"

TARGET_MIN_WORDS = 280
TARGET_MAX_WORDS = 340

VOICE_FEMALE = "en-IN-NeerjaNeural"   # Indian English female
VOICE_MALE   = "en-IN-PrabhatNeural"  # Indian English male

EQ_FEMALE = (
    "highpass=f=80,"
    "equalizer=f=300:t=q:w=0.7:g=1.5,"
    "equalizer=f=3000:t=q:w=0.8:g=1,"
    "acompressor=threshold=-18dB:ratio=2:attack=8:release=80:makeup=1,"
    "loudnorm=I=-14:TP=-1.5:LRA=11"
)

EQ_MALE = (
    "highpass=f=60,"
    "equalizer=f=200:t=q:w=0.7:g=2,"
    "equalizer=f=2500:t=q:w=0.8:g=1.5,"
    "acompressor=threshold=-20dB:ratio=1.8:attack=8:release=80:makeup=2,"
    "loudnorm=I=-14:TP=-1.5:LRA=10"
)

VOICE_ASSIGNMENT = {
    "news":       ("male",   VOICE_MALE,   EQ_MALE),
    "launch":     ("male",   VOICE_MALE,   EQ_MALE),
    "comparison": ("female", VOICE_FEMALE, EQ_FEMALE),
    "explainer":  ("male",   VOICE_MALE,   EQ_MALE),
    "ev":         ("female", VOICE_FEMALE, EQ_FEMALE),
    "suv":        ("male",   VOICE_MALE,   EQ_MALE),
    "default":    ("male",   VOICE_MALE,   EQ_MALE),
}

BGM_PROFILES = {
    "news":       {"freq": "528", "freq2": "396", "mood": "energetic modern"},
    "launch":     {"freq": "440", "freq2": "880", "mood": "exciting reveal"},
    "comparison": {"freq": "396", "freq2": "528", "mood": "analytical neutral"},
    "explainer":  {"freq": "396", "freq2": "528", "mood": "calm informative"},
    "ev":         {"freq": "528", "freq2": "660", "mood": "futuristic tech"},
    "suv":        {"freq": "220", "freq2": "440", "mood": "powerful bold"},
    "default":    {"freq": "440", "freq2": "528", "mood": "professional"},
}

PEXELS_QUERIES = {
    "car": [
        "modern car driving india highway",
        "luxury car showroom india",
        "car headlights night driving",
        "suv mountain road india",
        "car interior dashboard modern",
        "automotive design studio",
        "car engine mechanical",
        "busy indian city traffic cars",
    ],
    "suv": [
        "suv off road adventure",
        "large suv india highway",
        "suv mountain terrain",
        "suv interior premium",
        "4x4 vehicle muddy terrain",
        "suv family travel",
    ],
    "electric car": [
        "electric car charging station",
        "ev charging india",
        "electric vehicle futuristic",
        "ev battery technology",
        "electric car interior minimalist",
        "sustainable transport city",
        "electric car highway driving",
    ],
    "highway india": [
        "highway india expressway",
        "national highway india cars",
        "expressway cars speed",
        "india road trip cars",
        "golden quadrilateral highway",
    ],
    "concept car": [
        "futuristic concept car design",
        "auto expo india cars",
        "car design sketch studio",
        "prototype vehicle reveal",
        "automotive future design",
        "concept vehicle showroom",
    ],
    "default": [
        "modern car driving",
        "automotive technology",
        "car showroom premium",
        "road trip vehicle india",
        "car headlights dark road",
    ],
}

EVERGREEN_TOPICS = [
    "Why the Nexon EV base variant is the smartest buy — not the top spec",
    "Maruti Jimny vs Mahindra Thar: Which actually goes off-road in India?",
    "The real cost of owning an EV in India for 5 years vs petrol — exact numbers",
    "Why dealers push this model over that one — the truth nobody talks about",
    "India's most returned car: what buyers regret after 6 months",
    "Hyundai Creta vs Tata Nexon: I tested both, here's my honest pick",
    "The one variant to avoid in every popular Indian car — buyer trap explained",
    "Hidden costs of buying a car in India nobody tells you before purchase",
    "Why the waiting period for this SUV means you should buy the competitor",
    "CNG vs Electric vs Petrol: Which actually saves money after 3 years?",
    "The car that outsells everything else — but should you actually buy it?",
    "Mahindra BE 6 real owner review after 3 months: honest problems",
    "Why your car insurance is probably costing ₹20,000 extra every year",
    "The safest cars in India under ₹15 lakh — crash test results explained",
    "Upcoming launches that will destroy resale value of current bestsellers",
]

CONTENT_FORMAT_TYPES = [
    "news",
    "launch",
    "comparison",
    "explainer",
    "ev",
    "suv",
]

DAILY_TOPIC_PROMPT = """You are a content strategist for "Tech Meets Travel" — India's sharpest car news YouTube channel.

TODAY: {date} | {day}
FRESH CAR NEWS: {car_news}
TRENDING SEARCHES: {trends}
RECENTLY USED TOPICS — DO NOT repeat similar themes: {recent_topics}

MANDATORY CATEGORY ROTATION — pick the category most underrepresented in recent topics:
A) LAUNCH NEWS — brand new model reveal, price announcement, booking open
B) REAL BUYER ADVICE — which variant to buy, should you wait, honest pick
C) COMPARISON — two specific cars head to head with a clear winner
D) SURPRISING FACT — something most people get wrong about a popular car
E) MARKET TREND — sales data with an interesting story behind the numbers
F) EV REALITY — real-world range, charging, cost vs petrol, honest verdict

CURIOSITY HOOK RULES (pick one that makes someone stop scrolling):
- A shocking number: "₹4 lakh cheaper", "40% more range", "outsells Creta by 2x"  
- A contrarian take: "Why I'd AVOID the top variant", "This car is NOT what the ads show"
- A hidden fact: "The one thing Maruti doesn't tell you about the Swift", "Why dealers prefer this model"
- An urgent decision: "Buy before June 30 or lose ₹50,000 — here's why"

TOPIC MUST be hyper-specific. NOT "Tata EV review" but "Tata Curvv EV: Why the ₹17.49L base is the only smart choice"

Return ONLY valid JSON:
{{
  "topic": "<specific, curiosity-driving topic with a number or surprising angle>",
  "format": "<news|launch|comparison|explainer|ev|suv>",
  "category": "<A|B|C|D|E|F>",
  "pexels_keyword": "<car|suv|electric car|highway india|concept car>",
  "hook_angle": "<the single most surprising fact that will make someone click>",
  "reason": "<one sentence: what makes this different from yesterday's content>"
}}
"""


SCRIPT_PROMPT = """You are a sharp, witty Indian car journalist who runs "Tech Meets Travel" on YouTube.
Think: the guy your friends call before buying a car. Knowledgeable, opinionated, fun.
NOT a robot reading specs. A real person who loves cars and talks like it.

Topic: {topic}
Format: {format_type}
Hook: {hook_angle}
Voice: {voice_gender}

━━━━━━━━━━━━━━━━━━━━━━━━━
PERSONALITY (this is what makes people subscribe):
- Talk like you're WhatsApp-voicing your car-obsessed friend
- Use natural Indian English: "yaar", "basically", "trust me", "here's the thing"
- Have opinions: "This is actually good value" or "Honestly? I'd wait for the next variant"
- React to the news: "And wait, it gets better..." or "But here's the catch..."
- Use comparisons Indians understand: "cheaper than a base Creta", "same range as Nexon EV"

VIDEO STRUCTURE (2 minutes, 4 beats):

BEAT 1 — OPEN WITH THE MONEY SHOT (15 seconds)
Start with the single most interesting fact. No intro, no "today we", no "hi guys".
Hit them immediately with what matters most.
Examples:
  "Tata just dropped the Harrier EV price — and it's ₹3 lakhs less than everyone predicted."
  "Mahindra Scorpio-N gets a mid-life update and honestly? They fixed the one thing everyone complained about."
  "Real world range of the Nexon EV: we drove it 247 km on one charge. Here's what happened."

BEAT 2 — THE FULL STORY (60 seconds)
Give everything they came for. Specific, real numbers. No vague "powerful engine" — say "170 bhp and 380 Nm".
Cover: price (variant-wise if possible), key specs, what's new, launch date, how to book.
Slip in one comparison: "For context, the base XUV700 starts at ₹13.99 lakh — this undercuts it by ₹80k."
End Beat 2 with a teaser: "But there's one more thing Tata announced — and I think it changes everything."

BEAT 3 — YOUR HONEST TAKE (25 seconds)  
This is why people watch YOU instead of reading CarDekho.
Should they buy it? Should they wait? Which variant? Why?
Be specific: "The mid variant at ₹18.5 lakh is the sweet spot — avoid the top spec unless you really need the panoramic roof."
Or: "Honestly I'd wait 6 months for the first batch quality issues to get sorted."

BEAT 4 — CLOSE NATURALLY (10 seconds)
Don't beg. Ask a real question that makes them comment.
"Which would you pick — Harrier EV or MG ZS EV? Drop it in the comments."
OR: "If you want the full spec breakdown video — comment 'specs' below and I'll make it."

━━━━━━━━━━━━━━━━━━━━━━━━━
FORMAT-SPECIFIC TONE:

news:       Urgent, direct. Lead with the breaking detail. "This just happened — here's what it means for you."
launch:     Excited but grounded. Cover specs AND real-world usability.
comparison: Pick a winner. Don't sit on the fence — "Look, if it were my money, I'm going Mahindra."
explainer:  Patient but never boring. Use analogies. "Think of regenerative braking like a bicycle dynamo but for your battery."
ev:         Enthusiastic about tech, realistic about range anxiety. Address India-specific EV concerns.
suv:        Bold. Talk about road presence, ground clearance, off-road capability for Indian roads.

━━━━━━━━━━━━━━━━━━━━━━━━━
HARD RULES:
1. {target_min_words}-{target_max_words} words (2-min video at natural pace)
2. ZERO markdown — no asterisks, no headers, no bullets — pure speech
3. ZERO filler: "In this video", "As I mentioned", "Without further ado" = instantly cut
4. Real numbers every time — never "affordable" without the actual price
5. Never say "I" more than 8 times — talk about THEM (the viewer) and the CAR
6. End every Beat with a micro-hook that pulls into the next beat
7. NO [BEAT 1] labels — just write the speech straight through
8. NEVER start with the car brand name. Start with the IMPACT or SURPRISE.
   BAD: "Tata has launched..." GOOD: "₹3 lakhs less than everyone expected — Tata just made a big move."
9. ONE surprising/counterintuitive fact per video. Something viewer cannot get from just reading a headline.
10. The LAST sentence must be a question that triggers comments: "Tata or Mahindra — drop your pick below."
11. TONE EXAMPLES (write exactly like this, not formal):
    GOOD: "Look yaar, Maruti has been playing it safe for too long. The new Swift? Nice car. But here's the thing — at ₹9 lakh you're one variant away from a Nexon. And I know which one I'd pick."
    GOOD: "Honest take? The base variant. Skip the top spec. That panoramic roof costs you ₹1.5 lakh and adds zero to the driving experience on Indian roads."
    BAD: "The vehicle offers impressive specifications including advanced safety features."
    BAD: "This automobile presents exceptional value proposition for Indian consumers."
"""

SUBTITLE_PROMPT = """You are a professional subtitle editor.

Below is a voiceover script for a car news video. Break it into short subtitle lines.

Rules:
1. Max 8 words per line
2. Natural line breaks at phrase boundaries
3. Return ONLY the subtitle lines, one per line
4. No timestamps — just the lines in order

Script:
{script}
"""

METADATA_PROMPT = """Generate YouTube metadata for "Tech Meets Travel" — Indian car news channel.

Topic: {topic}
Format: {format_type}
Hook: {hook_angle}

Return ONLY valid JSON:
{{
  "title": "<title>",
  "description": "<description>",
  "tags": "<tags>",
  "pinned_comment": "<comment>",
  "thumbnail_concept": "<concept>"
}}

TITLE (most important — decides if people click):
- Under 60 characters
- Include specific number, price, or comparison if possible
- Pattern: [Surprising fact or question] — NOT "[Brand] [Model] Review"
- Good: "Tata Harrier EV: ₹3 Lakh Cheaper Than Expected"
- Good: "Why I'd Skip the Nexon EV in 2026"  
- Good: "Mahindra XUV700 vs Tata Harrier: Honest Pick"
- Bad: "Tata Harrier EV Full Review | Tech Meets Travel"
- DO NOT add "| Tech Meets Travel" — wastes characters

DESCRIPTION (first 2 lines = Google search snippet = MOST IMPORTANT):
Line 1: The hook — same energy as Beat 1 of the script
Line 2: "Tech Meets Travel covers daily Indian car news, EVs, SUVs, and launches."
Then: timestamps, hashtags

PINNED COMMENT:
A question that forces a 2-option reply. 
"Which would you pick — comment below 👇"
Or: "What's your biggest concern about EVs in India? Comment below 👇"

TAGS: Mix of specific (car name, model year) + broad (indian cars, car news india, ev india)
"""

THUMBNAIL_PROMPT = """Create a detailed AI image generation prompt for a YouTube thumbnail.
Channel: Tech Meets Travel (Indian Car News)
Topic: {topic}
Format: {format_type}
Thumbnail concept: {thumbnail_concept}

Return a detailed prompt:
- Professional automotive visual
- Bold text overlay space
- High contrast for small size
- Indian context when relevant
"""

KB_PRESETS = [
    ("min(1.0+0.0006*on,1.15)", "iw/2-(iw/zoom/2)+on*0.2", "ih/2-(ih/zoom/2)",       "zoom-in pan-right"),
    ("min(1.0+0.0006*on,1.15)", "iw/2-(iw/zoom/2)-on*0.2", "ih/2-(ih/zoom/2)",       "zoom-in pan-left"),
    ("max(1.15-0.0006*on,1.0)", "iw/2-(iw/zoom/2)",         "ih/2-(ih/zoom/2)",       "zoom-out center"),
    ("min(1.0+0.0005*on,1.10)", "iw/2-(iw/zoom/2)",         "ih/2-(ih/zoom/2)+on*0.15","zoom-in pan-up"),
    ("max(1.12-0.0005*on,1.0)", "iw/2-(iw/zoom/2)+on*0.15", "ih/2-(ih/zoom/2)",       "zoom-out pan-right"),
    ("min(1.0+0.0003*on,1.08)", "iw/2-(iw/zoom/2)",         "ih/2-(ih/zoom/2)",       "slow-zoom"),
]

XFADE_TRANSITIONS = ["fade", "dissolve", "wipeleft", "wiperight", "fadeblack", "fade"]


def run(cmd, timeout=300):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def log(msg):
    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {msg}")


def get_dur(f):
    r = run(["ffprobe", "-v", "error", "-show_entries",
             "format=duration", "-of", "csv=p=0", f])
    try:
        return float(r.stdout.strip())
    except:
        return 0.0


def ensure_dirs():
    for d in [OUTPUT_DIR, SHORTS_DIR, METADATA_DIR, SCRIPTS_DIR,
              PEXELS_DIR, SUBS_DIR, THUMBNAIL_DIR]:
        os.makedirs(d, exist_ok=True)


def load_queue():
    if os.path.exists(QUEUE_FILE):
        with open(QUEUE_FILE) as f:
            return json.load(f)
    return []


def save_queue(q):
    with open(QUEUE_FILE, "w") as f:
        json.dump(q, f, indent=2)


USED_TOPICS_FILE = "used_topics.txt"


def load_recent_topics(n=20):
    topics = []
    if os.path.exists(USED_TOPICS_FILE):
        with open(USED_TOPICS_FILE, encoding="utf-8") as f:
            lines = [l.strip() for l in f.readlines() if l.strip()]
        topics = lines[-n:]
    if not topics and os.path.isdir(METADATA_DIR):
        files = sorted(Path(METADATA_DIR).glob("*.json"), reverse=True)[:n]
        for fp in files:
            try:
                d = json.loads(fp.read_text())
                t = d.get("topic", "")
                if t:
                    topics.append(t)
            except:
                pass
    return topics


def deduplicate_topic(topic):
    """Hard check: if topic was already used, append date to differentiate."""
    used = load_recent_topics(60)
    if topic in used:
        date_str = datetime.datetime.now().strftime("%d-%b-%Y")
        deduped = f"{topic} — {date_str}"
        log(f"  🚫 Topic already used → adjusted to: {deduped}")
        return deduped
    return topic


def save_used_topic(topic):
    try:
        existing = []
        if os.path.exists(USED_TOPICS_FILE):
            with open(USED_TOPICS_FILE, encoding="utf-8") as f:
                existing = [l.strip() for l in f.readlines() if l.strip()]
        if topic not in existing:
            existing.append(topic)
        existing = existing[-60:]
        with open(USED_TOPICS_FILE, "w", encoding="utf-8") as f:
            f.write("\n".join(existing) + "\n")
        run(["git", "config", "user.email", "bot@techmeetstravel.com"])
        run(["git", "config", "user.name", "Tech Meets Travel Bot"])
        run(["git", "add", USED_TOPICS_FILE])
        r = run(["git", "commit", "-m", f"chore: log topic [{topic[:40]}]"])
        if r.returncode == 0:
            run(["git", "push"])
    except Exception as e:
        log(f"  ⚠️ Could not save topic history: {e}")


def parse_json_response(raw):
    clean = raw.strip()
    if clean.startswith("```"):
        parts = clean.split("```")
        clean = parts[1] if len(parts) > 1 else clean
        if clean.startswith("json"):
            clean = clean[4:]
    return json.loads(clean.strip())


def fetch_car_news():
    """Fetch real India car news from top automotive sites."""
    import urllib.request, urllib.error
    news_items = []

    sources = [
        # RSS feeds from India-specific automotive sites
        "https://www.rushlane.com/feed",
        "https://www.autocarindia.com/rss.xml",
        "https://www.carwale.com/rss/news.xml",
    ]
    for url in sources:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=8) as r:
                content = r.read().decode("utf-8", errors="ignore")
            # Extract titles from RSS
            titles = re.findall(r"<title><!\[CDATA\[(.*?)\]\]></title>|<title>(.*?)</title>", content)
            for t in titles[1:8]:  # skip channel title
                title = (t[0] or t[1]).strip()
                if title and len(title) > 20 and any(
                    kw in title.lower() for kw in
                    ["car","suv","ev","electric","launch","price","maruti","tata",
                     "mahindra","hyundai","kia","honda","toyota","renault"]
                ):
                    news_items.append(title)
        except Exception as e:
            log(f"  ⚠️ News fetch {url}: {e}")

    if not news_items:
        # Fallback evergreen topics
        news_items = [
            "Mahindra XUV 7XO sales accelerating in 2026",
            "Tata Punch + Nexon dominate 66% of Tata volumes",
            "Maruti Suzuki e-Vitara EV launch imminent",
            "Hyundai Creta EV real-world range tested",
            "Tata Sierra EV launch expected this year",
        ]

    log(f"  📰 {len(news_items)} news items fetched")
    return "\n".join(news_items[:8])


def fetch_trends():
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.get("https://trends.google.com/trends/trendingsearches/daily?geo=IN",
                         headers=headers, timeout=10)
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, "html.parser")
            items = [d.get_text(strip=True) for d in soup.find_all("div", class_="title")]
            return "\n".join(f"- {i}" for i in items[:10])
    except:
        pass
    return "- upcoming cars india\n- ev cars 2026\n- new suv launches"


def fetch_pexels_images(keyword, output_dir, count=5):
    if not PEXELS_API_KEY:
        log("⚠️ PEXELS_API_KEY not set")
        return []

    os.makedirs(output_dir, exist_ok=True)
    headers = {"Authorization": PEXELS_API_KEY}
    downloaded = []

    queries = PEXELS_QUERIES.get(keyword, PEXELS_QUERIES["default"])
    queries = list(queries)
    week_seed = int(datetime.datetime.now().strftime("%Y%W"))
    _rng = random.Random(week_seed)
    _rng.shuffle(queries)

    for query in queries:
        if len(downloaded) >= count:
            break
        try:
            resp = requests.get(
                "https://api.pexels.com/v1/search",
                headers=headers,
                params={"query": query, "per_page": 3, "orientation": "landscape"},
                timeout=15
            )
            if resp.status_code != 200:
                continue
            for photo in resp.json().get("photos", []):
                if len(downloaded) >= count:
                    break
                img_url = photo["src"]["large2x"]
                fname = os.path.join(output_dir, f"{photo['id']}.jpg")
                if os.path.exists(fname):
                    downloaded.append(fname)
                    continue
                ir = requests.get(img_url, timeout=30, stream=True)
                if ir.status_code == 200:
                    with open(fname, "wb") as f:
                        for chunk in ir.iter_content(8192):
                            f.write(chunk)
                    downloaded.append(fname)
                    log(f"  📸 {os.path.basename(fname)} ({query})")
        except Exception as e:
            log(f"  ⚠️ Pexels error: {e}")

    log(f"  ✅ {len(downloaded)} images fetched")
    return downloaded


def ensure_fallback_image():
    if not os.path.exists("image.png"):
        try:
            from PIL import Image, ImageDraw
            img = Image.new("RGB", (1920, 1080), (10, 30, 60))
            d = ImageDraw.Draw(img)
            d.rectangle([80, 400, 600, 680], fill=(20, 50, 100))
            d.rectangle([640, 200, 1840, 880], fill=(15, 40, 80))
            img.save("image.png")
        except:
            pass


def ensure_bgm(format_type="default"):
    profile = BGM_PROFILES.get(format_type, BGM_PROFILES["default"])
    bgm_path = f"bgm_{format_type}.mp3"
    if os.path.exists(bgm_path):
        return bgm_path

    log(f"🎵 Generating BGM: {profile['mood']} ({profile['freq']}Hz)...")
    f1, f2 = profile["freq"], profile["freq2"]
    r = run([
        "ffmpeg", "-y", "-f", "lavfi", "-i", f"sine=frequency={f1}:duration=360",
        "-f", "lavfi", "-i", f"sine=frequency={f2}:duration=360",
        "-f", "lavfi", "-i", "anoisesrc=d=360:c=pink:r=44100:a=0.005",
        "-filter_complex",
        "[0:a]volume=0.12,afade=t=in:st=0:d=3,afade=t=out:st=197:d=3[s1];"
        "[1:a]volume=0.07,afade=t=in:st=0:d=5[s2];"
        "[2:a]lowpass=f=600,volume=0.08[n];"
        "[s1][s2][n]amix=inputs=3:duration=first[out]",
        "-map", "[out]", "-ar", "44100", "-ac", "2", bgm_path
    ], timeout=60)

    if r.returncode == 0:
        log(f"  ✅ BGM: {bgm_path}")
        return bgm_path
    else:
        log("  ⚠️ BGM generation failed")
        return BGM_FILE if os.path.exists(BGM_FILE) else None


def generate_srt(english_lines, total_duration, output_path):
    lines = [l.strip() for l in english_lines if l.strip()]
    if not lines:
        return None

    usable_duration = total_duration * 0.95
    word_counts = [max(len(l.split()), 1) for l in lines]
    total_words = sum(word_counts)
    time_weights = [max(1.2, min(5.0, (wc / total_words) * usable_duration))
                    for wc in word_counts]
    scale = usable_duration / sum(time_weights)
    durations = [t * scale for t in time_weights]

    srt_content = ""
    cursor = 0.3

    def fmt(s):
        h = int(s // 3600)
        m = int((s % 3600) // 60)
        sec = int(s % 60)
        ms = int((s % 1) * 1000)
        return f"{h:02d}:{m:02d}:{sec:02d},{ms:03d}"

    for i, (line, dur) in enumerate(zip(lines, durations)):
        start = cursor
        end   = min(cursor + dur - 0.1, total_duration - 0.2)
        srt_content += f"{i+1}\n{fmt(start)} --> {fmt(end)}\n{line}\n\n"
        cursor += dur

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(srt_content)
    log(f"  ✅ SRT: {output_path} ({len(lines)} lines)")
    return output_path


def build_text_overlay(title_short, format_type):
    safe = lambda s: s.replace("'", "").replace(":", "-").replace('"', "")
    channel = safe(CHANNEL_NAME)
    title   = safe(title_short[:45]) if title_short else ""

    fmt_labels = {
        "news":       "CAR NEWS",
        "launch":     "NEW LAUNCH",
        "comparison": "VS",
        "explainer":  "EXPLAINED",
        "ev":         "EV",
        "suv":        "SUV",
    }
    fmt_label = fmt_labels.get(format_type, "CAR NEWS")

    overlays = [
        f"drawtext=fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf:text='{channel}':fontsize=24:fontcolor=white@0.85:"
        f"x=30:y=28:shadowcolor=black@0.9:shadowx=2:shadowy=2",
        f"drawtext=fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf:text='{fmt_label}':fontsize=20:fontcolor=yellow@0.9:"
        f"x=w-tw-30:y=28:shadowcolor=black@0.9:shadowx=2:shadowy=2",
    ]

    if title:
        overlays.append(
            f"drawtext=fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf:text='{title}':fontsize=36:fontcolor=white@1.0:"
            f"x=(w-tw)/2:y=h-100:"
            f"shadowcolor=black@0.95:shadowx=3:shadowy=3:"
            f"alpha='if(lt(t,0.5),0,if(lt(t,2),(t-0.5)/1.5,if(lt(t,7),1,if(lt(t,8),(8-t),0))))'"
        )

    return ",".join(overlays)


BRAND_DIR = "assets/brand"
LOGO_WATERMARK = f"{BRAND_DIR}/logo_watermark.png"
INTRO_FRAME    = f"{BRAND_DIR}/intro_frame.png"
OUTRO_FRAME    = f"{BRAND_DIR}/outro_frame.png"
INTRO_DURATION = 2.0
OUTRO_DURATION = 3.0


def make_intro_clip(output_path):
    if not os.path.exists(INTRO_FRAME):
        return None
    bell = f"/tmp/brand_bell_{os.getpid()}.mp3"
    run(["ffmpeg", "-y", "-f", "lavfi",
         "-i", f"sine=frequency=880:duration={INTRO_DURATION}",
         "-f", "lavfi", "-i", f"sine=frequency=1320:duration={INTRO_DURATION}",
         "-filter_complex",
         f"[0:a]volume=0.5,afade=t=in:st=0:d=0.2,afade=t=out:st={INTRO_DURATION-0.5}:d=0.5[b1];"
         f"[1:a]volume=0.3,afade=t=out:st={INTRO_DURATION-0.5}:d=0.5[b2];"
         "[b1][b2]amix=inputs=2:duration=longest,"
         f"apad=pad_dur={INTRO_DURATION}[bell]",
         "-map", "[bell]", "-t", str(INTRO_DURATION), bell], timeout=15)
    has_bell = os.path.exists(bell)
    cmd = ["ffmpeg", "-y",
           "-loop", "1", "-t", str(INTRO_DURATION + 0.1), "-i", INTRO_FRAME]
    if has_bell:
        cmd.extend(["-i", bell])
    vf = (f"fps=25,scale=1920:1080:force_original_aspect_ratio=decrease,"
          f"pad=1920:1080:(ow-iw)/2:(oh-ih)/2,"
          f"fade=t=in:st=0:d=0.4,fade=t=out:st={INTRO_DURATION-0.4}:d=0.4")
    cmd.extend(["-vf", vf])
    if has_bell:
        cmd.extend(["-map", "0:v", "-map", "1:a"])
    else:
        cmd.extend(["-map", "0:v",
                    "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
                    "-map", "2:a"])
    cmd.extend(["-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-ar", "44100", "-ac", "2",
                "-t", str(INTRO_DURATION), output_path])
    r = run(cmd, timeout=30)
    try:
        if os.path.exists(bell): os.remove(bell)
    except: pass
    return output_path if r.returncode == 0 else None


def make_outro_clip(output_path):
    if not os.path.exists(OUTRO_FRAME):
        return None
    text_filter = (
        "drawtext=fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf:text='Subscribe for daily car news 🔔':fontsize=48:"
        "fontcolor=white@0.95:x=(w-tw)/2:y=h-120:"
        "shadowcolor=black@0.9:shadowx=3:shadowy=3,"
        "drawtext=fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf:text='@tech_meets_travel':fontsize=32:"
        "fontcolor=gold@0.9:x=(w-tw)/2:y=h-65:"
        "shadowcolor=black@0.8:shadowx=2:shadowy=2"
    )
    r = run(["ffmpeg", "-y",
             "-loop", "1", "-t", str(OUTRO_DURATION + 0.1), "-i", OUTRO_FRAME,
             "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
             "-filter_complex",
             f"[0:v]fps=25,scale=1920:1080:force_original_aspect_ratio=decrease,"
             f"pad=1920:1080:(ow-iw)/2:(oh-ih)/2,"
             f"fade=t=in:st=0:d=0.4,"
             f"fade=t=out:st={OUTRO_DURATION-0.4}:d=0.4,"
             f"{text_filter}[v];"
             f"[1:a]apad=pad_dur={OUTRO_DURATION}[a]",
             "-map", "[v]", "-map", "[a]",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
             "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-ar", "44100", "-ac", "2",
             "-t", str(OUTRO_DURATION), output_path], timeout=30)
    return output_path if r.returncode == 0 else None


def concat_clips(clips, output_path):
    flist = f"/tmp/concat_{os.path.basename(output_path)}.txt"
    with open(flist, "w") as f:
        for c in clips:
            f.write(f"file '{os.path.abspath(c)}'\n")
    r = run(["ffmpeg", "-y", "-f", "concat", "-safe", "0",
             "-i", flist,
             "-vf", "fps=25,scale=1920:1080:force_original_aspect_ratio=decrease,"
                    "pad=1920:1080:(ow-iw)/2:(oh-ih)/2",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "24",
             "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-ar", "44100", "-ac", "2",
             "-movflags", "+faststart",
             output_path], timeout=180)
    try: os.remove(flist)
    except: pass
    return r.returncode == 0


def build_video_filter(images, total_frames, fps=25, seed=0):
    rng = random.Random(seed)
    num = len(images)
    seg_frames = total_frames // num
    filters = []

    for i in range(num):
        preset = KB_PRESETS[i % len(KB_PRESETS)]
        z_expr, x_expr, y_expr, label = preset
        adj = max(int(seg_frames * rng.uniform(0.9, 1.1)), fps * 3)
        log(f"    Image {i+1}: {label}")
        filters.append(
            f"[{i}:v]loop=loop=-1:size=1:start=0,"
            f"scale=1920:1080:force_original_aspect_ratio=increase,"
            f"crop=1920:1080,"
            f"zoompan=z='{z_expr}':x='{x_expr}':y='{y_expr}':d={adj}:fps={fps}:s=1920x1080,"
            f"trim=0:{adj/fps:.2f},setpts=PTS-STARTPTS[v{i}]"
        )

    prev = "v0"
    xfade_dur = 0.8
    for i in range(1, num):
        transition = XFADE_TRANSITIONS[i % len(XFADE_TRANSITIONS)]
        offset = max(0.5, i * seg_frames / fps - xfade_dur)
        label  = f"x{i}"
        filters.append(
            f"[{prev}][v{i}]xfade=transition={transition}"
            f":duration={xfade_dur}:offset={offset:.2f}[{label}]"
        )
        prev = label

    return num, ";".join(filters), prev


def create_video(script_text, english_subtitles, images_input, output_name,
                 format_type="default", title_short="", bgm_path=None,
                 source_citation="", topic_val=""):
    ensure_dirs()
    ensure_fallback_image()

    script_file = f"/tmp/{output_name}_script.txt"
    voice_file  = f"/tmp/{output_name}_voice.mp3"
    human_file  = f"/tmp/{output_name}_human.mp3"
    mixed_file  = f"/tmp/{output_name}_mixed.mp3"
    raw_file    = f"/tmp/{output_name}_raw.mp4"
    overlay_file= f"/tmp/{output_name}_overlay.mp4"
    srt_file    = f"{SUBS_DIR}/{output_name}.srt"
    video_file  = f"{OUTPUT_DIR}/{output_name}_video.mp4"
    short_file  = f"{SHORTS_DIR}/{output_name}_short.mp4"

    with open(script_file, "w", encoding="utf-8") as f:
        f.write(script_text)

    gender, voice_id, eq_filter = VOICE_ASSIGNMENT.get(
        format_type, VOICE_ASSIGNMENT["default"])
    log(f"🔊 Step 1/7 Voice ({gender} — {voice_id})...")
    t0 = time.time()
    try:
        r = run(["edge-tts", "--file", script_file, "--voice", voice_id,
                 "--rate=-8%", "--pitch=+0Hz", "--write-media", voice_file],
                timeout=300)
    except subprocess.TimeoutExpired:
        log("❌ TTS timeout"); return None
    if r.returncode != 0:
        log(f"❌ TTS error: {r.stderr[-200:]}"); return None
    dur = get_dur(voice_file)
    log(f"  Voice: {dur:.1f}s ({time.time()-t0:.0f}s)")

    log("🎧 Step 2/7 Voice EQ...")
    r = run(["ffmpeg", "-y", "-i", voice_file, "-af", eq_filter, human_file])
    if r.returncode != 0:
        shutil.copy(voice_file, human_file)
    dur = get_dur(human_file)

    log("🎵 Step 3/7 BGM mix...")
    if bgm_path and os.path.exists(bgm_path):
        fo  = max(0, dur - 2)
        bfo = max(0, dur - 3)
        fc = (
            "[0:a]volume=1.0,afade=t=in:st=0:d=1,afade=t=out:st={fo}:d=2[v];"
            "[1:a]volume=0.07,afade=t=in:st=0:d=3,afade=t=out:st={bfo}:d=3[b];"
            "[v][b]amix=inputs=2:duration=first:dropout_transition=2[out]"
        ).format(fo=fo, bfo=bfo)
        run(["ffmpeg", "-y", "-i", human_file, "-i", bgm_path,
             "-filter_complex", fc, "-map", "[out]", "-ac", "2", mixed_file])
        audio = mixed_file if os.path.exists(mixed_file) else human_file
    else:
        audio = human_file
    total_dur = get_dur(audio)

    log("🎬 Step 4/7 Video (Ken Burns)...")
    if isinstance(images_input, list):
        images = [f for f in images_input if os.path.exists(f)]
    else:
        images = []

    if not images and os.path.exists(OUTRO_FRAME):
        images = [OUTRO_FRAME]
    elif not images and os.path.exists("image.png"):
        images = ["image.png"]
    if not images:
        log("❌ No images"); return None

    log(f"  Using {len(images)} images")
    fps = 25
    seed = int(hashlib.md5(output_name.encode()).hexdigest()[:8], 16)
    total_frames = max(int(total_dur * fps), fps * 5)
    num_inputs, vfilter, vlabel = build_video_filter(images, total_frames, fps, seed)

    cmd = ["ffmpeg", "-y"]
    for img in images:
        cmd.extend(["-loop", "1", "-t", str(total_dur + 2), "-i", img])
    cmd.extend(["-i", audio, "-filter_complex", vfilter,
                "-map", f"[{vlabel}]", "-map", f"{num_inputs}:a",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "24",
                "-pix_fmt", "yuv420p", "-c:a", "aac",
                "-ar", "44100", "-ac", "2",
                "-avoid_negative_ts", "make_zero", raw_file])
    r = run(cmd, timeout=400)
    if r.returncode != 0:
        r = run(["ffmpeg", "-y", "-loop", "1", "-i", images[0], "-i", audio,
                 "-vf", "scale=1920:1080:force_original_aspect_ratio=decrease,"
                        "pad=1920:1080:(ow-iw)/2:(oh-ih)/2",
                 "-c:v", "libx264", "-preset", "veryfast", "-crf", "24",
                 "-pix_fmt", "yuv420p", "-c:a", "aac",
                 "-ar", "44100", "-ac", "2", raw_file],
                timeout=300)
        if r.returncode != 0:
            log("❌ Video encoding failed"); return None

    log("✍️  Step 5/7 Text overlays...")
    overlay_filter = build_text_overlay(title_short, format_type)
    r = run(["ffmpeg", "-y", "-i", raw_file,
             "-vf", overlay_filter,
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "24",
             "-c:a", "copy", overlay_file], timeout=200)
    working = overlay_file if r.returncode == 0 else raw_file

    log("📝 Step 6/7 English subtitles...")
    srt_created = False
    if english_subtitles:
        srt_path = generate_srt(english_subtitles, total_dur, srt_file)
        if srt_path:
            r = run(["ffmpeg", "-y", "-i", working,
                     "-vf", f"subtitles={srt_path}:force_style='"
                            "FontName=Arial,FontSize=20,"
                            "PrimaryColour=&H00FFFFFF,"
                            "OutlineColour=&H00000000,"
                            "BackColour=&H60000000,"
                            "Bold=1,Outline=2,Shadow=1,"
                            "Alignment=2,MarginV=50'",
                     "-c:v", "libx264", "-preset", "veryfast", "-crf", "24",
                     "-c:a", "copy", video_file], timeout=200)
            if r.returncode == 0:
                srt_created = True
                log("  ✅ Subtitles burned in")
    if not srt_created:
        shutil.copy(working, video_file)

    log("🔤 Step 7/8 Source citation + hook overlay...")
    combined_file = f"/tmp/{output_name}_combined.mp4"

    hooks = {
        "news":       "Breaking car news — explained in 2 minutes",
        "launch":     "New car launch — full details in 2 minutes",
        "comparison": "Which car is better? Find out here",
        "explainer":  "Car tech explained simply",
        "ev":         "Electric vehicle news you need to know",
        "suv":        "SUV news and updates",
    }
    hook_phrase = hooks.get(format_type, "Car news you need to know")
    safe_hook   = hook_phrase.replace("'", "").replace(":", " -")
    safe_src    = source_citation.replace("'", "").replace(":", " -")

    show_end = min(75, total_dur - 5)

    combined_vf = (
        f"drawtext=fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf:text='{safe_hook}':fontsize=28:"
        f"fontcolor=yellow@0.95:x=(w-tw)/2:y=40:"
        f"shadowcolor=black@0.9:shadowx=2:shadowy=2:"
        f"enable='between(t,0,5)',"
        f"drawtext=fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf:text='{safe_src}':fontsize=16:"
        f"fontcolor=white@0.70:x=20:y=h-45:"
        f"shadowcolor=black@0.8:shadowx=1:shadowy=1:"
        f"enable='between(t,15,{show_end:.0f})'"
    )

    r_combined = run([
        "ffmpeg", "-y", "-i", video_file,
        "-vf", combined_vf,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "24",
        "-c:a", "copy",
        combined_file
    ], timeout=200)

    if r_combined.returncode == 0 and os.path.exists(combined_file):
        shutil.move(combined_file, video_file)
        log(f"  ✅ Citations + hook overlay")
    else:
        for f in [combined_file]:
            try:
                if os.path.exists(f): os.remove(f)
            except: pass

    log("🎨 Brand overlays...")
    if os.path.exists(LOGO_WATERMARK):
        wm_file = f"/tmp/{output_name}_wm.mp4"
        r_wm = run(["ffmpeg", "-y",
                    "-i", video_file, "-i", LOGO_WATERMARK,
                    "-filter_complex",
                    "[1:v]scale=200:200[wm];[0:v][wm]overlay=W-220:H-220:format=auto",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "24",
                    "-c:a", "copy", wm_file], timeout=300)
        if r_wm.returncode == 0:
            shutil.move(wm_file, video_file)
            log("  ✅ Logo watermark added")

    intro_clip = f"/tmp/{output_name}_intro.mp4"
    outro_clip = f"/tmp/{output_name}_outro.mp4"
    final_clip = f"/tmp/{output_name}_final.mp4"

    has_intro = make_intro_clip(intro_clip)
    has_outro = make_outro_clip(outro_clip)

    clips = []
    if has_intro and os.path.exists(intro_clip):
        clips.append(intro_clip)
    clips.append(video_file)
    if has_outro and os.path.exists(outro_clip):
        clips.append(outro_clip)

    if len(clips) > 1:
        ok = concat_clips(clips, final_clip)
        if ok and os.path.exists(final_clip):
            shutil.move(final_clip, video_file)
            log(f"  ✅ Intro + content + Outro combined")

    for f in [intro_clip, outro_clip, final_clip]:
        try:
            if os.path.exists(f): os.remove(f)
        except: pass

    log("📱 Step 8/8 Shorts...")
    run(["ffmpeg", "-y", "-i", video_file, "-ss", "0", "-t", "40",
         "-vf", "scale=1920:1080,"
                "crop=ih*9/16:ih:(iw-ih*9/16)/2:0,"
                "scale=1080:1920",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "27",
         "-c:a", "aac", short_file], timeout=120)

    mb = os.path.getsize(video_file) / (1024*1024)
    log(f"  ✅ {video_file} ({mb:.1f}MB)")

    for f in [script_file, voice_file, human_file, mixed_file, raw_file, overlay_file]:
        try:
            if os.path.exists(f): os.remove(f)
        except: pass

    return video_file


def discover_daily_config():
    log("🧠 LLM deciding today's topic...")
    now = datetime.datetime.now()

    car_news  = fetch_car_news()
    trends    = fetch_trends()
    recent_topics = load_recent_topics(10)

    slot = os.environ.get("SLOT_HINT", "")
    pref_fmt = os.environ.get("PREFERRED_FORMATS", "")
    slot_note = ""
    if slot == "morning":
        slot_note = "TIME SLOT: Morning. Prefer news or launch format."
    elif slot == "evening":
        slot_note = "TIME SLOT: Evening. Prefer comparison, explainer or suv format."

    prompt = DAILY_TOPIC_PROMPT.format(
        date=now.strftime("%Y-%m-%d"),
        day=now.strftime("%A"),
        car_news=car_news[:800],
        trends=trends[:300],
        recent_topics=", ".join(recent_topics[:5]) or "None yet",
    )
    if slot_note:
        prompt += f"\n\n{slot_note}"

    raw = call_llm(prompt, prefer="gemini", max_tokens=1000)
    try:
        data = parse_json_response(raw)
        data["topic"] = deduplicate_topic(data["topic"])
        log(f"  📌 Topic: {data['topic']}")
        log(f"  🎭 Format: {data['format']}")
        log(f"  💡 Reason: {data.get('reason','')}")
        return data
    except Exception as e:
        log(f"  ⚠️ JSON parse failed ({e}) — using random evergreen")
        return {
            "topic":           deduplicate_topic(random.choice(EVERGREEN_TOPICS)),
            "format":          random.choice(CONTENT_FORMAT_TYPES),
            "pexels_keyword":  "car",
            "hook_angle":      "Here's the biggest car story you need to know today.",
            "reason":          "Fallback",
        }


def generate_script(topic, format_type, hook_angle, voice_gender):
    log(f"  📝 Script ({format_type}, {voice_gender} voice)...")
    t0 = time.time()

    def build_prompt(attempt=0):
        note = ""
        if attempt > 0:
            note = (
                f"\n\nCRITICAL — ATTEMPT {attempt+1}: Previous response was too short. "
                f"You MUST write {TARGET_MIN_WORDS}-{TARGET_MAX_WORDS} words. "
                "Beat 2 alone needs 150+ words. Write FULL complete sentences."
            )
        return SCRIPT_PROMPT.format(
            topic=topic,
            format_type=format_type,
            hook_angle=hook_angle,
            voice_gender=voice_gender,
            target_min_words=TARGET_MIN_WORDS,
            target_max_words=TARGET_MAX_WORDS,
        ) + note

    text = ""
    for attempt in range(3):
        resp = call_llm(build_prompt(attempt))
        words = len(resp.strip().split())
        log(f"  Attempt {attempt+1}: {words} words")
        if words >= TARGET_MIN_WORDS:
            text = resp.strip()
            break
        text = resp.strip()
        if attempt < 2:
            log(f"  Too short ({words} < {TARGET_MIN_WORDS}) — retrying...")
            time.sleep(3)

    if len(text.split()) > TARGET_MAX_WORDS:
        words = text.split()
        text = " ".join(words[:TARGET_MAX_WORDS])

    if not text.strip():
        log("  ❌ Script generation failed — all attempts returned empty")
        return ""
    log(f"  ✅ Script: {len(text.split())} words in {time.time()-t0:.0f}s")
    return text


def generate_subtitles(script):
    import textwrap
    words = script.strip().split()
    lines = textwrap.wrap(' '.join(words), width=40)
    subtitles = []
    for i, line in enumerate(lines, 1):
        subtitles.append(f"{i}\\n{line}")
    return subtitles


def generate_metadata(topic, format_type, hook_angle):
    log("  📋 Generating metadata...")
    prompt = METADATA_PROMPT.format(
        topic=topic,
        format_type=format_type,
        hook_angle=hook_angle,
    )
    raw = call_llm_groq(prompt, max_retries=3)
    try:
        return parse_json_response(raw)
    except Exception as e:
        log(f"  ⚠️ Metadata JSON parse failed ({e}) — using fallback")
        return {
            "title": f"{topic[:55]} | Tech Meets Travel",
            "description": (
                f"{hook_angle}\n"
                f"In this video, we cover {topic} | Indian Car News\n\n"
                f"0:00 Introduction\n"
                f"0:15 The Story\n"
                f"0:45 Key Details\n"
                f"1:30 What It Means\n"
                f"1:50 Subscribe & Share\n\n"
                f"Subscribe to Tech Meets Travel for daily car news updates!\n\n"
                f"#TechMeetsTravel #IndianCars #CarNewsIndia"
            ),
            "tags": (
                "indian cars, car news india, upcoming cars india, "
                "tech meets travel, new car launch, ev india, "
                f"{topic[:30]}, car news 2026"
            ),
            "pinned_comment": (
                f"What do you think about this? Comment below 👇\n"
                f"Subscribe to Tech Meets Travel for daily car news 🔔"
            ),
            "thumbnail_concept": (
                f"Bold background. White/yellow text: '{topic[:30]}'. "
                "Car visual on right side. High contrast."
            ),
        }


SOURCE_PROMPT = """Given this Indian car news video topic, identify the authoritative source.

Topic: {topic}
Format: {format_type}

Return ONLY a short source attribution (max 40 chars):
- For news/launches: "Source: Autocar India" or "Source: CarDekho" or "Source: Zigwheels"
- For official info: "Source: Manufacturer Press Release"
- For specs: "Source: Official Spec Sheet"
- Default: "Source: Autocar India"

Return ONLY the source string, nothing else."""


def get_source_citation(topic):
    citations = {
        "car": "https://www.cardekho.com",
        "bike": "https://www.bikedekho.com",
        "toyota": "https://www.toyota.com",
        "honda": "https://www.honda.com",
        "hyundai": "https://www.hyundai.com",
        "tata": "https://www.tatamotors.com",
        "mahindra": "https://www.mahindra.com",
        "maruti": "https://www.marutisuzuki.com",
        "ev": "https://www.ev.com",
        "electric": "https://www.ev.com",
    }
    topic_lower = topic.lower()
    for keyword, url in citations.items():
        if keyword in topic_lower:
            return url
    return "https://www.wikipedia.org"


SERIES_FILE = "video_series.json"

def generate_mcq(topic):
    return [
        {"question": f"{topic} பற்றி மேலும் அறிய விரும்புகிறீர்களா?", "options": ["ஆம்", "இல்லை"], "answer": 0},
        {"question": "இந்த தகவல் உங்களுக்கு பயனுள்ளதாக இருந்ததா?", "options": ["மிகவும் பயனுள்ளது", "சரி", "பயனற்றது"], "answer": 0},
    ]


SERIES_TOPIC_GROUPS = {
    "tata":       ["tata", "harrier", "safari", "nexon", "curvv", "altroz", "tiago"],
    "mahindra":   ["mahindra", "xuv", "thar", "scorpio", "bolero", "xev"],
    "hyundai":    ["hyundai", "creta", "venue", "i20", "tucson", "ioniq"],
    "maruti":     ["maruti", "suzuki", "swift", "baleno", "fronx", "grand vitara", "dzire", "ertiga"],
    "kia":        ["kia", "seltos", "sonet", "ev6", "ev9", "carens"],
    "ev":         ["ev", "electric", "ev9", "nexon ev", "ioniq", "ev6", "xev"],
    "suv":        ["suv", "offroad", "thar", "scorpio", "xuv700", "harrier"],
    "comparison": ["vs", "versus", "which one", "better"],
}


def detect_series_group(topic):
    topic_lower = topic.lower()
    for group, keywords in SERIES_TOPIC_GROUPS.items():
        if any(kw in topic_lower for kw in keywords):
            return group
    return None


def load_series_data():
    if os.path.exists(SERIES_FILE):
        with open(SERIES_FILE) as f:
            return json.load(f)
    return {}


def save_series_data(data):
    with open(SERIES_FILE, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    try:
        run(["git", "add", SERIES_FILE])
        run(["git", "commit", "-m", "chore: update series data"])
        run(["git", "push"])
    except:
        pass


def get_series_info(topic, video_id=None):
    group = detect_series_group(topic)
    if not group:
        return None, None, None, None

    data = load_series_data()
    if group not in data:
        data[group] = []

    series = data[group]
    part_num = len(series) + 1

    series_titles = {
        "tata":       "Tata Cars Complete Coverage",
        "mahindra":   "Mahindra Cars Deep Dive",
        "hyundai":    "Hyundai Cars Updates",
        "maruti":     "Maruti Suzuki Guide",
        "kia":        "Kia India Coverage",
        "ev":         "Electric Vehicle Series",
        "suv":        "SUV Series",
        "comparison": "Comparison Series",
    }
    series_title = series_titles.get(group, f"{group.capitalize()} Series")

    prev_video_id = series[-1]["video_id"] if series else None

    if video_id:
        series.append({
            "part":     part_num,
            "topic":    topic,
            "video_id": video_id,
            "date":     datetime.datetime.now().isoformat(),
        })
        data[group] = series
        save_series_data(data)

    return part_num, len(series), series_title, prev_video_id


def build_series_end_card(part_num, series_title, prev_video_id):
    if part_num == 1:
        return f"\n\n📚 This is Part 1 of our '{series_title}' series."
    else:
        prev_url = f"https://youtu.be/{prev_video_id}" if prev_video_id else ""
        return (
            f"\n\n📚 {series_title} — Part {part_num}\n"
            f"Previous part: {prev_url}"
        )


def get_authenticated_service():
    creds = None
    b64 = os.environ.get("YOUTUBE_TOKEN_BASE64")
    if b64:
        try:
            creds = pickle.loads(base64.b64decode(b64))
        except: pass

    if not creds and os.path.exists(YOUTUBE_TOKEN_FILE):
        with open(YOUTUBE_TOKEN_FILE, "rb") as f:
            creds = pickle.load(f)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as e:
                log(f"⚠️ Token refresh failed: {e}")
                return None
        else:
            if not os.path.exists(YOUTUBE_CLIENT_SECRETS):
                log(f"⚠️ {YOUTUBE_CLIENT_SECRETS} not found — skipping upload")
                return None
            try:
                flow = InstalledAppFlow.from_client_secrets_file(
                    YOUTUBE_CLIENT_SECRETS, YOUTUBE_SCOPES)
                creds = flow.run_local_server(port=8080)
            except Exception as e:
                log(f"⚠️ OAuth flow failed: {e}"); return None
        try:
            with open(YOUTUBE_TOKEN_FILE, "wb") as f:
                pickle.dump(creds, f)
        except: pass

    try:
        return build("youtube", "v3", credentials=creds)
    except Exception as e:
        log(f"⚠️ YouTube service error: {e}"); return None


def validate_script(text):
    if not text or len(text) < 200:
        return False, text, "too short"
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"\*{1,3}([^*]+)\*{1,3}", r"\1", text)
    text = re.sub(r"^[-*]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\d+\.\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"```[^`]*```", "", text, flags=re.DOTALL)
    text = text.strip()
    return True, text, "ok"


def validate_tags(tags_str):
    tags = [t.strip() for t in tags_str.split(",") if t.strip()][:25]
    result, total = [], 0
    for tag in tags:
        if total + len(tag) + 1 <= 490:
            result.append(tag)
            total += len(tag) + 1
        else:
            break
    return ", ".join(result)



def failure_alert(message):
    """GitHub Actions error annotation — visible in CI summary."""
    print(f"::error title=Tech Meets Travel Bot Error::{message}")
    log(f"❌ ALERT: {message}")


THUMBNAIL_DIR = "thumbnails"
TAMIL_BOLD_FONT = "/usr/share/fonts/truetype/noto/NotoSansTamil-Bold.ttf"
ENG_BOLD_FONT   = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
ENG_REG_FONT    = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

TT_THUMB_FORMATS = {
    "news":       {"c1":(8,12,25),   "c2":(20,8,40),  "acc":(232,0,28),   "bb":(185,0,0),    "badge":"BREAKING"},
    "launch":     {"c1":(5,25,8),    "c2":(2,10,3),   "acc":(0,215,95),   "bb":(0,165,65),   "badge":"LAUNCH"},
    "comparison": {"c1":(8,8,32),    "c2":(2,2,18),   "acc":(50,148,255), "bb":(25,98,215),  "badge":"VS"},
    "explainer":  {"c1":(10,20,38),  "c2":(3,8,22),   "acc":(255,178,0),  "bb":(198,128,0),  "badge":"EXPLAINED"},
    "ev":         {"c1":(0,22,26),   "c2":(0,8,12),   "acc":(0,228,198),  "bb":(0,175,155),  "badge":"EV"},
    "suv":        {"c1":(22,12,4),   "c2":(8,4,0),    "acc":(255,138,0),  "bb":(198,88,0),   "badge":"SUV"},
    "default":    {"c1":(8,12,25),   "c2":(3,5,18),   "acc":(255,198,0),  "bb":(178,138,0),  "badge":"NEWS"},
}

def _draw_animated_car(d, cx, cy, scale, accent, style="sedan"):
    """Draw a dynamic animated-style car silhouette with motion blur effect."""
    import math
    s = scale

    # Motion blur lines (speed effect)
    for i in range(6):
        oy = (i-3) * int(12*s)
        alpha = max(20, 80 - abs(i-3)*20)
        blur_len = int((80 + abs(i-3)*30)*s)
        d.line([(cx-int(145*s)-blur_len, cy+oy),
                (cx-int(145*s), cy+oy)],
               fill=(*accent[:3], alpha), width=max(1, 3-abs(i-3)))

    if style == "suv":
        # Boxy SUV profile
        body_pts = [
            (cx-int(130*s), cy+int(30*s)),
            (cx-int(132*s), cy-int(5*s)),
            (cx-int(108*s), cy-int(45*s)),
            (cx-int(40*s),  cy-int(72*s)),
            (cx+int(55*s),  cy-int(72*s)),
            (cx+int(108*s), cy-int(40*s)),
            (cx+int(130*s), cy-int(5*s)),
            (cx+int(132*s), cy+int(30*s)),
        ]
        roof_pts = [
            (cx-int(100*s), cy-int(43*s)),
            (cx-int(36*s),  cy-int(70*s)),
            (cx+int(52*s),  cy-int(70*s)),
            (cx+int(100*s), cy-int(38*s)),
            (cx+int(50*s),  cy-int(12*s)),
            (cx-int(32*s),  cy-int(12*s)),
        ]
    elif style == "ev":
        # Sleek EV with cab-forward design
        body_pts = [
            (cx-int(128*s), cy+int(28*s)),
            (cx-int(130*s), cy-int(8*s)),
            (cx-int(88*s),  cy-int(55*s)),
            (cx-int(15*s),  cy-int(68*s)),
            (cx+int(55*s),  cy-int(68*s)),
            (cx+int(115*s), cy-int(32*s)),
            (cx+int(130*s), cy-int(5*s)),
            (cx+int(130*s), cy+int(28*s)),
        ]
        roof_pts = [
            (cx-int(80*s),  cy-int(53*s)),
            (cx-int(12*s),  cy-int(66*s)),
            (cx+int(52*s),  cy-int(66*s)),
            (cx+int(108*s), cy-int(30*s)),
            (cx+int(48*s),  cy-int(10*s)),
            (cx-int(10*s),  cy-int(10*s)),
        ]
    else:
        # Standard sedan with dynamic roofline
        body_pts = [
            (cx-int(120*s), cy+int(25*s)),
            (cx-int(122*s), cy-int(8*s)),
            (cx-int(95*s),  cy-int(35*s)),
            (cx-int(30*s),  cy-int(62*s)),
            (cx+int(45*s),  cy-int(62*s)),
            (cx+int(105*s), cy-int(30*s)),
            (cx+int(122*s), cy-int(8*s)),
            (cx+int(124*s), cy+int(25*s)),
        ]
        roof_pts = [
            (cx-int(85*s),  cy-int(33*s)),
            (cx-int(28*s),  cy-int(58*s)),
            (cx+int(42*s),  cy-int(58*s)),
            (cx+int(95*s),  cy-int(28*s)),
            (cx+int(38*s),  cy-int(10*s)),
            (cx-int(24*s),  cy-int(10*s)),
        ]

    # Body
    d.polygon(body_pts, fill=(22,25,38))
    d.polygon(body_pts, outline=accent, width=2)

    # Windshield/roof glass with tint
    d.polygon(roof_pts, fill=(18,45,88))
    d.polygon(roof_pts, outline=(*accent[:3], 120), width=1)

    # Wheels with rim detail
    for wx, wy in [(cx-int(78*s), cy+int(28*s)), (cx+int(78*s), cy+int(28*s))]:
        r = int(28*s)
        # Tire
        d.ellipse([wx-r, wy-r, wx+r, wy+r], fill=(10,10,14))
        d.ellipse([wx-r, wy-r, wx+r, wy+r], outline=accent, width=2)
        # Rim spokes (5-spoke)
        for angle in range(0, 360, 72):
            rad = math.radians(angle)
            x1 = wx + int(math.cos(rad)*r*0.25)
            y1 = wy + int(math.sin(rad)*r*0.25)
            x2 = wx + int(math.cos(rad)*r*0.8)
            y2 = wy + int(math.sin(rad)*r*0.8)
            d.line([(x1,y1),(x2,y2)], fill=(55,60,75), width=2)
        d.ellipse([wx-int(r*0.25), wy-int(r*0.25),
                   wx+int(r*0.25), wy+int(r*0.25)], fill=(40,44,58))

    # Headlight glow
    hx, hy = cx+int(120*s), cy-int(5*s)
    for r, alpha in [(22,30),(15,60),(8,120),(4,200)]:
        d.ellipse([hx-r, hy-r, hx+r, hy+r], fill=(*accent[:3], alpha))

    # Tail light
    tx, ty = cx-int(118*s), cy-int(5*s)
    d.ellipse([tx-8, ty-4, tx+8, ty+4], fill=(200,30,30))




def generate_scenes(output_name, format_type, num_scenes=5):
    """Generate animated car scene images. Replaces Pexels — zero copyright, zero cost."""
    from PIL import Image, ImageDraw
    import os, math, random, hashlib

    seed = int(hashlib.md5(output_name.encode()).hexdigest()[:8], 16)
    W, H = 1920, 1080
    scene_dir = os.path.join(PEXELS_DIR, output_name)
    os.makedirs(scene_dir, exist_ok=True)

    pool = {
        "news":       ["night_highway","city_aerial","showroom","dashboard","mountain_road"],
        "launch":     ["showroom","night_highway","mountain_road","city_aerial","night_highway"],
        "comparison": ["night_highway","mountain_road","showroom","city_aerial","dashboard"],
        "ev":         ["ev_charging","night_highway","city_aerial","dashboard","mountain_road"],
        "suv":        ["mountain_road","night_highway","city_aerial","showroom","night_highway"],
        "explainer":  ["dashboard","showroom","city_aerial","night_highway","mountain_road"],
    }
    scenes = pool.get(format_type, pool["news"])[:num_scenes]
    acc_map = {
        "news":(232,0,28),"launch":(0,215,95),"comparison":(50,148,255),
        "ev":(0,228,198),"suv":(255,138,0),"explainer":(255,178,0),
    }
    acc = acc_map.get(format_type, (255,198,0))
    paths = []

    for idx, scene in enumerate(scenes):
        out = os.path.join(scene_dir, f"{idx:02d}_{scene}.png")
        if os.path.exists(out):
            paths.append(out); continue

        img = Image.new("RGB",(W,H),(5,8,18))
        d   = ImageDraw.Draw(img)
        rs  = seed + idx * 7919
        random.seed(rs)

        if scene == "night_highway":
            for y in range(H):
                t=y/H; d.line([(0,y),(W,y)],fill=(int(2+t*10),int(4+t*15),int(12+t*28)))
            for _ in range(180):
                x,y=random.randint(0,W),random.randint(0,H//3); b=random.randint(140,255)
                r2=random.choice([1,1,2]); d.ellipse([x-r2,y-r2,x+r2,y+r2],fill=(b,b,b))
            vx,vy=W//2,H//2-30
            d.polygon([(0,H),(W,H),(vx+60,vy),(vx-60,vy)],fill=(18,20,28))
            for j in range(8):
                t=j/8; y=int(vy+(H-vy)*t); xw=int(5+t*40)
                d.line([(W//2-xw//4,y),(W//2+xw//4,y)],fill=(220,200,80),width=max(1,int(t*4)))
            for cx2,sp in [(W//2-120,.15),(W//2+120,.15)]:
                for r3 in range(280,0,-10):
                    t=1-r3/280; a=int(t*20)
                    d.ellipse([cx2-r3*sp,vy-r3*.08,cx2+r3*sp,vy+r3*.5],fill=(min(255,a*3),min(255,a*3),min(255,a*2)))
            _draw_animated_car(d,W*3//4,H//2+30,1.3,acc)

        elif scene == "showroom":
            for y in range(H):
                t=y/H; d.line([(0,y),(W,y)],fill=(int(5+t*12),int(5+t*10),int(10+t*20)))
            cx2,cy2=W//2+100,H*3//5
            for r3 in range(500,0,-8):
                t=1-r3/500; c=(0,min(255,int(t*60)),min(255,int(t*40)))
                d.ellipse([cx2-r3,cy2-r3,cx2+r3,cy2+r3],outline=c,width=1)
            ped_y=H*2//3
            d.ellipse([W//2-260,ped_y-18,W//2+260,ped_y+38],fill=(18,20,32))
            d.ellipse([W//2-260,ped_y-20,W//2+260,ped_y],fill=(30,33,50))
            _draw_animated_car(d,W//2,ped_y-90,2.0,acc)

        elif scene == "city_aerial":
            for y in range(H//3):
                t=y/(H//3); d.line([(0,y),(W,y)],fill=(int(5+t*20),int(8+t*30),int(18+t*55)))
            for _ in range(55):
                bx=random.randint(0,W-100); by=random.randint(H//4,H-80)
                bw=random.randint(40,120); bh=random.randint(30,100); br=random.randint(18,45)
                d.rectangle([bx,by,bx+bw,by+bh],fill=(br,br+5,br+10))
                for wy2 in range(by+5,by+bh-5,8):
                    for wx2 in range(bx+5,bx+bw-5,10):
                        if random.random()>.5:
                            d.rectangle([wx2,wy2,wx2+5,wy2+4],
                                         fill=(random.randint(180,255),random.randint(160,240),random.randint(80,160)))
            d.line([(0,H*2//3),(W,H//3)],fill=(28,30,42),width=110)
            for j in range(10):
                t=j/10; cx3=int(t*W); cy3=int(H*2//3-t*(H*2//3-H//3))
                d.ellipse([cx3-5,cy3-3,cx3+5,cy3+3],fill=(255,240,180) if j%3!=0 else (220,30,30))

        elif scene == "dashboard":
            for y in range(H):
                t=y/H; d.line([(0,y),(W,y)],fill=(int(3+t*12),int(3+t*10),int(8+t*20)))
            vp=(W//2,H//3)
            d.polygon([(W//4,H*2//3),(W*3//4,H*2//3),(vp[0]+35,vp[1]),(vp[0]-35,vp[1])],fill=(15,16,22))
            d.polygon([(0,0),(W//5,0),(W//4+50,H*2//3),(0,H*2//3)],fill=(10,10,16))
            d.polygon([(W,0),(W*4//5,0),(W*3//4-50,H*2//3),(W,H*2//3)],fill=(10,10,16))
            for y in range(H*2//3,H):
                t=(y-H*2//3)/(H-H*2//3); d.line([(0,y),(W,y)],fill=(int(10+t*5),int(10+t*5),int(15+t*8)))
            sw_cx,sw_cy,sw_r=W//2,H-130,130
            d.ellipse([sw_cx-sw_r,sw_cy-sw_r,sw_cx+sw_r,sw_cy+sw_r],outline=(40,42,55),width=16)
            d.ellipse([sw_cx-sw_r,sw_cy-sw_r,sw_cx+sw_r,sw_cy+sw_r],outline=(60,65,80),width=4)
            for angle in [90,210,330]:
                rad=math.radians(angle)
                d.line([(sw_cx+int(math.cos(rad)*25),sw_cy+int(math.sin(rad)*25)),
                        (sw_cx+int(math.cos(rad)*sw_r*.87),sw_cy+int(math.sin(rad)*sw_r*.87))],fill=(45,48,62),width=18)
            d.ellipse([sw_cx-26,sw_cy-26,sw_cx+26,sw_cy+26],fill=(25,28,38))

        elif scene == "mountain_road":
            sky=[(8,10,25),(25,15,40),(60,20,50),(120,35,30),(200,80,20),(240,140,30),(255,200,60)]
            zh=H//2//len(sky)
            for j,col in enumerate(sky):
                y1=j*zh; nc=sky[min(j+1,len(sky)-1)]
                for y in range(y1,y1+zh+5):
                    t=max(0,min(1,(y-y1)/max(zh,1)))
                    d.line([(0,y),(W,y)],fill=tuple(int(col[k]+(nc[k]-col[k])*t) for k in range(3)))
            sx2,sy2=W*2//3,H//3
            for r3 in range(110,0,-4):
                t=1-r3/110; d.ellipse([sx2-r3,sy2-r3,sx2+r3,sy2+r3],fill=(255,int(160+t*95),int(t*80)))
            for layer,(y_base,dark) in enumerate([(H*2//3,8),(H*3//5,15),(H//2+30,25)]):
                pts2=[(0,H)]
                x2=0
                while x2<W:
                    pk=random.randint(60,180)*(layer+1)//2; pts2.append((x2,y_base-pk)); x2+=random.randint(60,150)
                pts2.append((W,H)); d.polygon(pts2,fill=(dark,dark+3,dark+8))
            d.polygon([(W//2-75,H//2+20),(W//2+75,H//2+20),(W*3//4,H),(W//4,H)],fill=(20,20,28))
            _draw_animated_car(d,W//2,H*3//4-20,.65,acc)

        else:  # ev_charging
            for y in range(H):
                t=y/H; d.line([(0,y),(W,y)],fill=(int(t*5),int(8+t*15),int(15+t*25)))
            for sx in [W//3,W*2//3]:
                d.rectangle([sx-25,H//4,sx+25,H*3//4],fill=(8,20,30))
                d.rectangle([sx-25,H//4,sx+25,H*3//4],outline=(0,150,140),width=2)
                d.rectangle([sx-18,H//4+20,sx+18,H//4+100],fill=(0,30,45))
                for r3 in [26,20,14,8]:
                    t=1-r3/28; col=(0,int(180+t*75),int(150+t*50))
                    x0,y0,x1,y1=sx-r3+5,H//4+35,sx+r3-5,H//4+95
                    if x1>x0 and y1>y0: d.arc([x0,y0,x1,y1],150,int(150+t*240),fill=col,width=2)
            cx3,cy3=W//2,H*3//4-40; s3=1.8
            ebody=[(cx3-int(128*s3),cy3+int(28*s3)),(cx3-int(130*s3),cy3-int(8*s3)),
                   (cx3-int(88*s3),cy3-int(55*s3)),(cx3-int(15*s3),cy3-int(68*s3)),
                   (cx3+int(55*s3),cy3-int(68*s3)),(cx3+int(115*s3),cy3-int(32*s3)),
                   (cx3+int(130*s3),cy3-int(5*s3)),(cx3+int(130*s3),cy3+int(28*s3))]
            d.polygon(ebody,fill=(5,20,35)); d.polygon(ebody,outline=(0,180,160),width=3)
            for r3 in range(180,0,-18):
                t=1-r3/180; a=int(t*6); bb=[cx3-r3,cy3-r3//2,cx3+r3,cy3+r3//2]
                if bb[2]>bb[0] and bb[3]>bb[1]: d.ellipse(bb,outline=(0,min(255,a*25),min(255,a*18)),width=1)

        img.save(out)
        paths.append(out)
        log(f"  🎨 Scene {idx+1}/{len(scenes)}: {scene}")

    return paths



def _tt_wrap(text, n):
    words = text.split()
    lines, line = [], ""
    for w in words:
        if len(line+w) <= n: line += w+" "
        else:
            if line: lines.append(line.strip())
            line = w+" "
    if line: lines.append(line.strip())
    return lines[:3]



def _tt_shadow(d, x, y, text, font, fill, shadow=(0,0,0,200)):
    for ox, oy in [(3,3),(-2,-2),(2,-2),(-2,2)]:
        d.text((x+ox,y+oy), text, font=font, fill=shadow)
    d.text((x,y), text, font=font, fill=fill)



def _tt_font(size, bold=True):
    from PIL import ImageFont
    try:
        path = ENG_BOLD_FONT if bold else ENG_REG_FONT
        return ImageFont.truetype(path, size)
    except:
        return ImageFont.load_default()



def generate_thumbnail(title, format_type, output_name):
    """6 distinct layouts — one per format type."""
    try:
        from PIL import Image, ImageDraw
        import os, math, random
        os.makedirs(THUMBNAIL_DIR, exist_ok=True)
        W,H=1280,720
        cfg=TT_THUMB_FORMATS.get(format_type, TT_THUMB_FORMATS["default"])
        img=Image.new("RGB",(W,H),cfg["c1"]); d=ImageDraw.Draw(img)
        def grad(c1=None,c2=None):
            c1=c1 or cfg["c1"]; c2=c2 or cfg["c2"]
            for y in range(H):
                t=y/H; d.line([(0,y),(W,y)],fill=tuple(int(c1[j]+(c2[j]-c1[j])*t) for j in range(3)))
        fmt=format_type or "default"
        acc=cfg["acc"]

        if fmt=="news":
            grad(); d.polygon([(0,0),(W*2//3,0),(W//2,H),(0,H)],fill=(185,0,20))
            d.polygon([(0,0),(W*2//3-8,0),(W//2-8,H),(0,H)],fill=(225,5,30))
            d.text((22,75),"B R E A K I N G",font=_tt_font(26),fill=(255,210,0))
            d.rectangle([14,70,18,H-70],fill=(255,210,0))
            _animated_car(d,W-200,H//2+20,1.2,acc,"sedan")
            lines=_tt_wrap(title,16); ty=125
            for i,ln in enumerate(lines):
                fs=92 if i==0 else 66; _tt_shadow(d,45,ty,ln,_tt_font(fs),(255,255,255) if i==0 else (220,220,220)); ty+=fs+14
            d.rectangle([45,ty+6,580,ty+14],fill=(225,5,30))
        elif fmt=="launch":
            grad(); cx2,cy2=int(W*.7),H//2
            for r in range(700,0,-5):
                t=1-r/700; d.ellipse([cx2-r,cy2-r,cx2+r,cy2+r],outline=(0,min(255,int(t*80)),min(255,int(t*50))),width=1)
            _animated_car(d,int(W*.68),H//2+15,1.4,acc,"sedan")
            d.polygon([(W-200,0),(W,0),(W,200)],fill=acc)
            d.text((W-108,28),"NEW",font=_tt_font(34),fill=(255,255,255))
            d.text((W-142,68),"LAUNCH",font=_tt_font(24),fill=(255,255,255))
            lines=_tt_wrap(title,17); ty=155
            for i,ln in enumerate(lines):
                fs=86 if i==0 else 62; _tt_shadow(d,48,ty,ln,_tt_font(fs),(255,255,255) if i==0 else (200,240,200)); ty+=fs+14
            d.rectangle([48,ty+6,480,ty+14],fill=acc)
        elif fmt=="comparison":
            for x in range(W//2):
                t=x/(W//2); d.line([(x,0),(x,H)],fill=tuple(int(cfg["c1"][j]+t*20) for j in range(3)))
            for x in range(W//2,W):
                t=(x-W//2)/(W//2); d.line([(x,0),(x,H)],fill=(int(8+t*30),int(3+t*10),int(3+t*6)))
            d.rectangle([W//2-5,0,W//2+5,H],fill=(255,255,255))
            d.ellipse([W//2-62,H//2-62,W//2+62,H//2+62],fill=(255,255,255))
            d.text((W//2,H//2),"VS",font=_tt_font(72),fill=(12,12,35),anchor="mm")
            _animated_car(d,W//4,H//2+10,1.1,acc,"sedan")
            _animated_car(d,W*3//4,H//2+10,1.1,(255,130,0),"suv")
            parts=title.lower().split(" vs ") if " vs " in title.lower() else ["",""]
            if parts[0]: d.text((W//4,H-90),parts[0][:20].upper(),font=_tt_font(32),fill=(200,220,255),anchor="mm")
            if len(parts)>1 and parts[1]: d.text((W*3//4,H-90),parts[1][:20].upper(),font=_tt_font(32),fill=(255,200,150),anchor="mm")
            d.rectangle([0,0,W,55],fill=(15,15,40))
            d.text((W//2,27),"HONEST COMPARISON",font=_tt_font(30),fill=(200,200,255),anchor="mm")
        elif fmt=="ev":
            grad(); random.seed(42)
            for _ in range(12):
                x=random.randint(50,W-50); y1,y2=random.randint(0,H//2),random.randint(H//2,H)
                d.line([(x,y1),(x,y2)],fill=(0,75,95),width=2)
                d.line([(x,y2),(x+random.choice([-100,100]),y2)],fill=(0,75,95),width=2)
                d.ellipse([x-5,y2-5,x+5,y2+5],fill=(0,175,155))
            _animated_car(d,int(W*.67),H//2+10,1.35,acc,"ev")
            d.rounded_rectangle([42,42,190,96],radius=12,fill=acc)
            d.text((116,69),"EV NEWS",font=_tt_font(28),fill=(255,255,255),anchor="mm")
            lines=_tt_wrap(title,17); ty=118
            for i,ln in enumerate(lines):
                fs=82 if i==0 else 60; _tt_shadow(d,42,ty,ln,_tt_font(fs),(255,255,255) if i==0 else (160,240,225)); ty+=fs+14
            d.rectangle([42,ty+5,480,ty+13],fill=acc)
        elif fmt=="explainer":
            for y in range(H):
                t=y/H; d.line([(0,y),(W,y)],fill=(int(12+t*20),int(10+t*14),int(5+t*8)))
            d.rectangle([0,0,W,88],fill=(28,22,5)); d.rectangle([0,80,W,92],fill=(215,162,0))
            d.text((W//2,44),"💡 EXPLAINED IN TAMIL",font=_tt_font(36),fill=(215,162,0),anchor="mm")
            d.rectangle([0,88,14,H],fill=(215,162,0))
            _animated_car(d,W-165,H//2+30,1.0,acc,"sedan")
            lines=_tt_wrap(title,19); ty=120
            for i,ln in enumerate(lines):
                fs=78 if i==0 else 58; _tt_shadow(d,38,ty,ln,_tt_font(fs),(255,248,220) if i==0 else (200,188,155)); ty+=fs+16
            d.rounded_rectangle([38,H-108,305,H-58],radius=8,fill=(38,30,8))
            d.text((172,H-83),"Must Know",font=_tt_font(26),fill=(215,162,0),anchor="mm")
        else:
            for y in range(H):
                t=y/H; d.line([(0,y),(W,y)],fill=(int(15+t*32),int(8+t*18),int(3+t*8)))
            for i,(y1,y2,col) in enumerate([(H-55,H,(38,20,8)),(H-95,H-58,(28,14,5)),(H-130,H-98,(20,10,3))]):
                d.rectangle([0,y1,W,y2],fill=col)
            pts=[]
            for x in range(W//2,W,6):
                peak=H-75-abs(math.sin((x-W//2)/110)*170); pts.append((x,int(peak)))
            pts+=[(W,H-55),(W//2,H-55)]
            if len(pts)>3: d.polygon(pts,fill=(22,12,4))
            _animated_car(d,int(W*.66),H//2,1.35,acc,"suv")
            d.rectangle([42,42,225,98],fill=acc); d.rectangle([42,42,225,98],outline=(255,160,50),width=3)
            d.text((133,70),"4×4 SUV",font=_tt_font(32),fill=(255,255,255),anchor="mm")
            lines=_tt_wrap(title,16); ty=125
            for i,ln in enumerate(lines):
                fs=84 if i==0 else 62; _tt_shadow(d,42,ty,ln,_tt_font(fs),(255,255,255) if i==0 else (240,180,110)); ty+=fs+14
            d.rectangle([42,ty+6,470,ty+14],fill=acc)

        d.text((38,H-44),"@tech_meets_travel",font=_tt_font(22,False),fill=(155,158,172))
        out=f"{THUMBNAIL_DIR}/{output_name}_thumb.png"
        img.save(out); log(f"  ✅ Thumbnail: {out}")
        return out
    except Exception as e:
        log(f"  ⚠️ Thumbnail: {e}"); return None



def upload_short_to_youtube(short_path, main_title, main_description, tags_str, youtube):
    """Upload Short to YouTube with #Shorts tag for Shorts feed discovery."""
    if not short_path or not os.path.exists(short_path):
        return None
    try:
        # Shorts title: keep under 100 chars, add #Shorts
        short_title = main_title[:90] + " #Shorts" if len(main_title) <= 90 else main_title[:88] + "… #Shorts"

        # Shorts description: first 2 lines + #Shorts tag
        short_desc_lines = (main_description or "").split("\n")[:3]
        short_desc = "\n".join(short_desc_lines) + "\n\n#Shorts"

        # Tags: add Shorts-specific tags
        tags = [t.strip() for t in tags_str.split(",") if t.strip()][:25]
        if "Shorts" not in tags: tags.insert(0, "Shorts")
        if "YouTubeShorts" not in tags: tags.insert(1, "YouTubeShorts")

        body = {
            "snippet": {
                "title":       short_title[:100],
                "description": short_desc[:5000],
                "tags":        tags[:30],
                "categoryId":  "22",   # People & Blogs — YouTube classifies Shorts here
            },
            "status": {
                "privacyStatus":           "public",
                "selfDeclaredMadeForKids": False,
            },
        }

        req = youtube.videos().insert(
            part="snippet,status", body=body,
            media_body=MediaFileUpload(short_path, chunksize=-1, resumable=True))
        resp = req.execute()
        vid = resp["id"]
        log(f"  ✅ Short uploaded: https://youtu.be/{vid}")
        return vid
    except Exception as e:
        log(f"  ⚠️ Short upload failed: {e}")
        return None



# ═══════════════════════════════════════════════════════════════════
# RESILIENT LLM ROUTER — 5-provider waterfall
# Priority: Groq (fast) → Gemini (reliable) → GitHub Models (free)
#           → Cerebras (fast free) → Groq fallback models
#
# All providers use OpenAI-compatible SDK for consistency.
# GitHub Models: uses GITHUB_TOKEN (auto-set in Actions — zero config)
# Cerebras: uses CEREBRAS_API_KEY secret (optional, add if available)
# ═══════════════════════════════════════════════════════════════════

GITHUB_TOKEN    = os.environ.get("GITHUB_TOKEN", "")
CEREBRAS_KEY    = os.environ.get("CEREBRAS_API_KEY", "")

# ── Provider configs ────────────────────────────────────────────────
PROVIDERS = [
    # name, base_url, api_key, model, use_for
    ("groq",     "https://api.groq.com/openai/v1",         GROQ_API_KEY,  "llama-3.3-70b-versatile",        "script"),
    ("gemini",   None,                                       GEMINI_KEY,    "gemini-2.5-flash",               "all"),
    ("github",   "https://models.inference.ai.azure.com",  GITHUB_TOKEN,  "gpt-4o-mini",                    "all"),
    ("cerebras", "https://api.cerebras.ai/v1",              CEREBRAS_KEY,  "llama-3.3-70b",                  "all"),
    ("groq_fb",  "https://api.groq.com/openai/v1",         GROQ_API_KEY,  "llama3-8b-8192",                 "fallback"),
]

def _call_provider(name, base_url, api_key, model, prompt, max_tokens=4000):
    """Call a single provider. Returns text or raises."""
    if not api_key:
        raise Exception(f"{name}: no API key")

    if name == "gemini":
        # Gemini uses its own SDK
        client = genai.Client(api_key=api_key)
        resp = client.models.generate_content(
            model=model, contents=prompt)
        return resp.text
    else:
        # All others: OpenAI-compatible
        from openai import OpenAI
        client = OpenAI(base_url=base_url, api_key=api_key)
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            temperature=0.85,
        )
        return resp.choices[0].message.content


def _is_retryable(err_str):
    """True if the error is transient (rate limit / server overload)."""
    return any(c in err_str for c in [
        "429", "503", "502", "RESOURCE_EXHAUSTED", "UNAVAILABLE",
        "high demand", "overloaded", "ServiceUnavailable",
        "rate_limit", "tokens per day", "TPD", "Internal",
        "timeout", "timed out",
    ])


def call_llm(prompt, max_retries=3, prefer="gemini", max_tokens=4000):
    """
    Resilient multi-provider router.
    Tries each provider in priority order.
    On transient errors → retry with backoff.
    On permanent errors → skip to next provider immediately.
    """
    # Build provider order based on preference
    if prefer == "groq":
        order = ["groq", "gemini", "github", "cerebras", "groq_fb"]
    else:
        order = ["gemini", "groq", "github", "cerebras", "groq_fb"]

    provider_map = {p[0]: p for p in PROVIDERS}
    last_error = ""

    for provider_name in order:
        if provider_name not in provider_map:
            continue
        name, base_url, api_key, model, _ = provider_map[provider_name]
        if not api_key:
            continue   # skip providers with no key configured

        for attempt in range(max_retries):
            try:
                result = _call_provider(name, base_url, api_key, model, prompt, max_tokens)
                if result and result.strip():
                    if attempt > 0 or provider_name != order[0]:
                        log(f"  ✅ LLM: {name}/{model.split('-')[0]}")
                    return result.strip()
            except Exception as e:
                err = str(e)
                last_error = err
                if _is_retryable(err):
                    # Daily limit hit — skip provider entirely
                    if "tokens per day" in err or "TPD" in err or "daily" in err.lower():
                        log(f"  ⚠️ {name}: daily limit — trying next provider")
                        break
                    wait = min(10 * (2 ** attempt), 60)
                    log(f"  ⏳ {name} retry {attempt+1}/{max_retries} in {wait}s ({err[:60]})")
                    time.sleep(wait)
                else:
                    # Non-retryable (auth, invalid model etc) — skip provider
                    log(f"  ⚠️ {name}: {err[:80]} — skipping")
                    break

    raise Exception(f"All LLM providers failed. Last: {last_error[:150]}")


def call_llm_groq(prompt, max_retries=3):
    """Script generation — prefers Groq for quality, all providers as fallback."""
    return call_llm(prompt, max_retries=max_retries, prefer="groq", max_tokens=4000)


def call_llm_gemini(prompt, max_retries=3):
    """Explicit Gemini — but falls back gracefully to other providers."""
    return call_llm(prompt, max_retries=max_retries, prefer="gemini", max_tokens=2000)


# Keep _call_gemini and _call_groq for backward compatibility
def _call_gemini(prompt, max_retries=5):
    return call_llm(prompt, max_retries=max_retries, prefer="gemini")

def _call_groq(prompt, max_retries=3):
    return call_llm(prompt, max_retries=max_retries, prefer="groq")



UPLOAD_QUEUE_FILE = "upload_queue.json"


def is_quota_exceeded(err_str):
    """Check if error is YouTube quota exceeded."""
    return any(x in str(err_str).lower() for x in
               ["quotaexceeded", "quota exceeded", "usageexceeded",
                "403", "dailylimitexceeded"])


def queue_for_retry(video_path, metadata, privacy="public"):
    """Save failed upload to queue for next run."""
    try:
        queue = []
        if os.path.exists(UPLOAD_QUEUE_FILE):
            with open(UPLOAD_QUEUE_FILE) as f:
                queue = json.load(f)
        queue.append({
            "video_path": video_path,
            "metadata":   metadata,
            "privacy":    privacy,
            "queued_at":  datetime.datetime.now().isoformat(),
        })
        with open(UPLOAD_QUEUE_FILE, "w") as f:
            json.dump(queue, f, indent=2, ensure_ascii=False)
        log(f"  📋 Queued for retry: {os.path.basename(video_path)}")
        # Commit queue to git so it persists
        try:
            run(["git", "config", "user.email", "bot@channel.com"])
            run(["git", "config", "user.name",  "Bot"])
            run(["git", "add", UPLOAD_QUEUE_FILE])
            run(["git", "commit", "-m", "chore: queue video for upload retry"])
            run(["git", "push"])
        except: pass
    except Exception as e:
        log(f"  ⚠️ Queue save failed: {e}")


def upload_pending_from_queue():
    """Upload any videos queued from previous failed runs."""
    if not os.path.exists(UPLOAD_QUEUE_FILE):
        return
    try:
        with open(UPLOAD_QUEUE_FILE) as f:
            queue = json.load(f)
        if not queue:
            return
        log(f"📤 Processing upload queue ({len(queue)} pending)...")
        youtube = get_authenticated_service()
        if not youtube:
            return
        remaining = []
        for item in queue:
            path = item.get("video_path", "")
            if not os.path.exists(path):
                log(f"  ⚠️ Queued file missing: {path} — skipping")
                continue
            try:
                vid = upload_to_youtube(path, item.get("metadata", {}),
                                        item.get("privacy", "public"))
                if vid:
                    log(f"  ✅ Queued upload succeeded: {vid}")
                else:
                    remaining.append(item)
            except Exception as e:
                if is_quota_exceeded(e):
                    log(f"  ⚠️ Still quota exceeded — keeping in queue")
                    remaining.append(item)
                else:
                    log(f"  ⚠️ Queue upload failed: {e}")
        with open(UPLOAD_QUEUE_FILE, "w") as f:
            json.dump(remaining, f, indent=2, ensure_ascii=False)
        if not remaining:
            try:
                run(["git", "add", UPLOAD_QUEUE_FILE])
                run(["git", "commit", "-m", "chore: clear upload queue"])
                run(["git", "push"])
            except: pass
    except Exception as e:
        log(f"  ⚠️ Queue processing failed: {e}")


def upload_to_youtube(video_path, metadata, privacy="public"):
    if not os.path.exists(video_path):
        log(f"❌ Video not found: {video_path}"); return None

    youtube = get_authenticated_service()
    if not youtube:
        log("⚠️ YouTube auth failed — skipping upload"); return None

    body = {
        "snippet": {
            "title":       metadata.get("title", "")[:100],
            "description": metadata.get("description", "")[:5000],
            "tags":        [t.strip() for t in
                           validate_tags(metadata.get("tags","")).split(",")][:25],
            "categoryId":  "2"   # Autos & Vehicles,
        },
        "status": {
            "privacyStatus":           privacy,
            "selfDeclaredMadeForKids": False,
        },
    }

    try:
        t0 = time.time()
        req = youtube.videos().insert(
            part="snippet,status", body=body,
            media_body=MediaFileUpload(video_path, chunksize=-1, resumable=True))
        resp = req.execute()
        vid = resp["id"]
        log(f"✅ Uploaded: https://youtu.be/{vid} ({time.time()-t0:.0f}s)")

        if metadata.get("pinned_comment"):
            try:
                time.sleep(30)   # avoid rapid-fire spam detection
                youtube.commentThreads().insert(
                    part="snippet",
                    body={"snippet": {"videoId": vid, "topLevelComment": {
                        "snippet": {"textOriginal": metadata["pinned_comment"]}
                    }}}).execute()
                log("  ✅ Pinned comment set")
            except: pass

        thumb = metadata.get("thumbnail_path", "")
        if thumb and os.path.exists(thumb):
            try:
                youtube.thumbnails().set(
                    videoId=vid,
                    media_body=MediaFileUpload(thumb, mimetype="image/png")
                ).execute()
                log("  ✅ Custom thumbnail uploaded")
            except Exception as e:
                log(f"  ⚠️ Thumbnail upload failed: {e}")

        return vid
    except Exception as e:
        err = str(e)
        if is_quota_exceeded(err):
            log(f"❌ YouTube quota exceeded — queuing for next run")
            queue_for_retry(video_path, metadata, privacy)
        else:
            log(f"❌ Upload failed: {err[:150]}")
        return None



# ═══════════════════════════════════════════════════════════════
# FREE MEDIA FETCHER — Zero cost, zero copyright
# Layer 1: Wikimedia Commons (CC-BY, real car photos)
# Layer 2: Pixabay (free HD stock, no attribution needed commercially)
# Layer 3: YouTube CC-BY clips via yt-dlp
# Layer 4: Animated Pillow scenes (existing fallback)
# ═══════════════════════════════════════════════════════════════

PIXABAY_API_KEY = os.environ.get("PIXABAY_API_KEY", "")

# Wikimedia Commons category map for Indian cars
WIKIMEDIA_CAR_CATEGORIES = {
    "maruti":      ["Maruti_Suzuki_Swift", "Maruti_Suzuki_Baleno", "Maruti_Fronx",
                    "Maruti_Suzuki_Brezza", "Maruti_Dzire", "Maruti_Suzuki_WagonR"],
    "tata":        ["Tata_Nexon", "Tata_Punch", "Tata_Harrier", "Tata_Safari",
                    "Tata_Sierra_EV", "Tata_Curvv"],
    "mahindra":    ["Mahindra_Scorpio-N", "Mahindra_Thar", "Mahindra_XUV700",
                    "Mahindra_BE_6", "Mahindra_XUV_3XO", "Mahindra_Bolero"],
    "hyundai":     ["Hyundai_Creta", "Hyundai_Venue", "Hyundai_i20",
                    "Hyundai_Alcazar"],
    "kia":         ["Kia_Seltos", "Kia_Sonet", "Kia_Carens"],
    "honda":       ["Honda_City", "Honda_Elevate", "Honda_Amaze"],
    "toyota":      ["Toyota_Innova_Crysta", "Toyota_Fortuner", "Toyota_Hilux"],
    "ev":          ["Tata_Nexon_EV", "Mahindra_BE_6", "BYD_Seal_U",
                    "Hyundai_Creta_Electric"],
    "suv":         ["Mahindra_Scorpio-N", "Mahindra_Thar", "Tata_Harrier",
                    "Hyundai_Creta", "Kia_Seltos"],
    "default":     ["India_car", "Indian_automobile", "Car_India"],
}


def fetch_wikimedia_car_images(topic, format_type, output_dir, count=4):
    """Fetch real car photos from Wikimedia Commons — CC-BY, free forever."""
    import urllib.request, urllib.parse, json, hashlib, re as _re

    os.makedirs(output_dir, exist_ok=True)
    paths = []

    # Determine search category from topic
    topic_lower = topic.lower()
    category_list = WIKIMEDIA_CAR_CATEGORIES.get("default", [])
    for brand, cats in WIKIMEDIA_CAR_CATEGORIES.items():
        if brand in topic_lower:
            category_list = cats
            break

    # Also do a direct search
    # Extract car model name from topic for search query
    search_query = _re.sub(r'[₹%#!?|–—]', ' ', topic)
    search_query = ' '.join(search_query.split()[:6])  # first 6 words

    headers = {"User-Agent": "TechMeetsTravel/1.0 (YouTube car news bot; contact@techmeetsTravel.com)"}

    # Method 1: Search by category
    for cat in category_list[:3]:
        if len(paths) >= count: break
        try:
            url = (
                "https://commons.wikimedia.org/w/api.php?"
                f"action=query&generator=categorymembers&gcmtitle=Category:{urllib.parse.quote(cat)}"
                "&gcmtype=file&gcmlimit=10&prop=imageinfo&iiprop=url|size|extmetadata"
                "&iiurlwidth=1280&format=json"
            )
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.loads(r.read())

            pages = data.get("query", {}).get("pages", {})
            for page in pages.values():
                if len(paths) >= count: break
                info = page.get("imageinfo", [{}])[0]
                img_url = info.get("thumburl") or info.get("url", "")
                if not img_url: continue
                # Only JPG/PNG
                if not any(img_url.lower().endswith(ext) for ext in ['.jpg','.jpeg','.png']): continue

                fname = os.path.join(output_dir, f"wiki_{hashlib.md5(img_url.encode()).hexdigest()[:8]}.jpg")
                if os.path.exists(fname):
                    paths.append(fname); continue
                try:
                    req2 = urllib.request.Request(img_url, headers=headers)
                    with urllib.request.urlopen(req2, timeout=15) as r2:
                        with open(fname, 'wb') as f: f.write(r2.read())
                    if os.path.getsize(fname) > 5000:
                        paths.append(fname)
                        log(f"  📸 Wikimedia: {os.path.basename(fname)}")
                except: pass
        except Exception as e:
            log(f"  ⚠️ Wikimedia category {cat}: {e}")

    # Method 2: Direct search if not enough images
    if len(paths) < 2:
        try:
            url = (
                "https://commons.wikimedia.org/w/api.php?"
                f"action=query&generator=search&gsrsearch=filetype:bitmap+{urllib.parse.quote(search_query)}"
                "&gsrnamespace=6&gsrlimit=10&prop=imageinfo&iiprop=url|size"
                "&iiurlwidth=1280&format=json"
            )
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.loads(r.read())

            pages = data.get("query", {}).get("pages", {})
            for page in pages.values():
                if len(paths) >= count: break
                info = page.get("imageinfo", [{}])[0]
                img_url = info.get("thumburl") or info.get("url", "")
                if not img_url: continue
                if not any(img_url.lower().endswith(ext) for ext in ['.jpg','.jpeg','.png']): continue

                fname = os.path.join(output_dir, f"wiki_s_{hashlib.md5(img_url.encode()).hexdigest()[:8]}.jpg")
                if os.path.exists(fname):
                    paths.append(fname); continue
                try:
                    req2 = urllib.request.Request(img_url, headers=headers)
                    with urllib.request.urlopen(req2, timeout=15) as r2:
                        with open(fname, 'wb') as f: f.write(r2.read())
                    if os.path.getsize(fname) > 5000:
                        paths.append(fname)
                        log(f"  📸 Wikimedia search: {os.path.basename(fname)}")
                except: pass
        except Exception as e:
            log(f"  ⚠️ Wikimedia search: {e}")

    log(f"  ✅ Wikimedia: {len(paths)} images")
    return paths


def fetch_pixabay_car_images(topic, format_type, output_dir, count=3):
    """Fetch free HD car images from Pixabay — no attribution needed."""
    import urllib.request, urllib.parse, json, hashlib

    os.makedirs(output_dir, exist_ok=True)
    paths = []

    # Use free Pixabay API (25 req/hour without key, 5000/hour with key)
    # Key is optional — works without it at lower rate
    query_map = {
        "ev":          "electric car india",
        "suv":         "suv car india road",
        "news":        "car highway india night",
        "launch":      "car showroom luxury",
        "comparison":  "cars road india",
        "explainer":   "car dashboard interior",
    }
    query = query_map.get(format_type, "car india road")

    # Also add model name if recognisable
    topic_lower = topic.lower()
    for brand in ["maruti","tata","mahindra","hyundai","kia","honda","toyota","bmw","audi"]:
        if brand in topic_lower:
            query = f"{brand} car india"
            break

    try:
        params = {
            "key":         PIXABAY_API_KEY or "44301183-96cd52a18c6d19f69aa2b3e38",
            "q":           query,
            "image_type":  "photo",
            "category":    "transportation",
            "min_width":   "1280",
            "safesearch":  "true",
            "per_page":    "10",
            "order":       "popular",
        }
        url = "https://pixabay.com/api/?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"User-Agent": "TechMeetsTravel/1.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())

        for hit in data.get("hits", [])[:count*2]:
            if len(paths) >= count: break
            img_url = hit.get("webformatURL") or hit.get("largeImageURL","")
            if not img_url: continue

            fname = os.path.join(output_dir, f"pixabay_{hit['id']}.jpg")
            if os.path.exists(fname):
                paths.append(fname); continue
            try:
                req2 = urllib.request.Request(img_url, headers={"User-Agent": "TechMeetsTravel/1.0"})
                with urllib.request.urlopen(req2, timeout=15) as r2:
                    with open(fname, 'wb') as f: f.write(r2.read())
                if os.path.getsize(fname) > 5000:
                    paths.append(fname)
                    log(f"  📸 Pixabay: {os.path.basename(fname)}")
            except: pass

    except Exception as e:
        log(f"  ⚠️ Pixabay: {e}")

    log(f"  ✅ Pixabay: {len(paths)} images")
    return paths


def fetch_youtube_cc_clip(topic, output_dir, max_duration=30):
    """Download a short CC-BY licensed YouTube clip relevant to topic.
    Uses yt-dlp which is free and installed on GitHub Actions runners."""
    try:
        import subprocess, hashlib, re as _re

        result = subprocess.run(["yt-dlp", "--version"],
                                capture_output=True, text=True, timeout=5)
        if result.returncode != 0:
            return None
    except:
        log("  ⚠️ yt-dlp not installed — skipping CC clip fetch")
        return None

    os.makedirs(output_dir, exist_ok=True)

    # Build search query for CC-licensed car content
    # YouTube --match-filter "license = 'creativeCommon'" filters CC-BY videos
    search_terms = [
        f"ytsearch3:{topic} car review india",
        f"ytsearch3:india car {topic.split()[0]} test drive",
    ]

    for search in search_terms:
        try:
            fname_base = os.path.join(output_dir,
                         f"cc_{hashlib.md5(search.encode()).hexdigest()[:8]}")
            cmd = [
                "yt-dlp",
                "--match-filter", "license = 'creativeCommon'",
                "--max-filesize",  "30M",
                "--format",        "mp4[height<=480]/best[height<=480]",
                "--output",        fname_base + ".%(ext)s",
                "--max-downloads", "1",
                "--postprocessor-args", f"ffmpeg:-t {max_duration}",
                "--quiet",
                "--no-playlist",
                search,
            ]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            # Find downloaded file
            for ext in ['mp4','webm','mkv']:
                path = fname_base + f".{ext}"
                if os.path.exists(path) and os.path.getsize(path) > 10000:
                    log(f"  🎬 CC clip: {os.path.basename(path)}")
                    return path
        except: pass

    return None


def fetch_free_media(topic, format_type, output_dir, count=5):
    """Master fetcher — tries all free sources, returns best available images."""
    os.makedirs(output_dir, exist_ok=True)
    all_images = []

    log(f"  🔍 Fetching free media for: {topic[:50]}...")

    # Layer 1: Wikimedia Commons (real car photos)
    wiki_images = fetch_wikimedia_car_images(topic, format_type, output_dir, count=3)
    all_images.extend(wiki_images)

    # Layer 2: Pixabay (if need more)
    if len(all_images) < count:
        pix_images = fetch_pixabay_car_images(topic, format_type, output_dir,
                                               count=count - len(all_images))
        all_images.extend(pix_images)

    # Layer 3: Pexels (existing, if still need more)
    if len(all_images) < 3:
        pexels_kw = {
            "ev": "electric car", "suv": "suv india",
            "news": "car india", "launch": "car showroom",
            "comparison": "cars road", "explainer": "car dashboard",
        }.get(format_type, "car india")
        pexels_images = fetch_pexels_images(pexels_kw, output_dir,
                                             count=count - len(all_images))
        all_images.extend(pexels_images)

    log(f"  ✅ Free media: {len(all_images)} images total "
        f"(wiki={len(wiki_images)}, rest={len(all_images)-len(wiki_images)})")
    return all_images[:count]


def safe_process_video(topic=None, format_type=None, upload=False, privacy="public"):
    ensure_dirs()
    t_start = time.time()

    if topic:
        config = {
            "topic":          topic,
            "format":         format_type or random.choice(CONTENT_FORMAT_TYPES),
            "pexels_keyword": "car",
            "hook_angle":     "Here's the biggest car story you need to know today.",
        }
    else:
        config = discover_daily_config()

    topic_val     = config["topic"]
    fmt           = config["format"]
    pexels_kw     = config.get("pexels_keyword", "car")
    hook_angle    = config.get("hook_angle", "")
    gender, _, _  = VOICE_ASSIGNMENT.get(fmt, VOICE_ASSIGNMENT["default"])

    log(f"{'='*55}")
    log(f"  {CHANNEL_NAME}")
    log(f"  Topic: {topic_val}")
    log(f"  Format: {fmt} | Voice: {gender}")
    log(f"{'='*55}")

    save_used_topic(topic_val)

    safe_name = hashlib.md5(topic_val.encode()).hexdigest()[:10]
    img_dir   = os.path.join(PEXELS_DIR, safe_name)
    # Try real free media first (Wikimedia + Pixabay + Pexels)
    images = fetch_free_media(topic_val, fmt, img_dir, count=5)
    if not images:
        # Fall back to animated scenes
        log("🎨 Generating animated scenes (no free media found)...")
        images = generate_scenes(safe_name, fmt, num_scenes=5)
    if not images:
        ensure_fallback_image()
        images = ["image.png"] if os.path.exists("image.png") else []

    bgm_path = ensure_bgm(fmt)

    log("🤖 Step 1: Generating script...")
    script = generate_script(topic_val, fmt, hook_angle, gender)
    if not script or not script.strip():
        log("  ❌ Script empty — aborting pipeline")
        return None

    log("🤖 Step 2: Generating subtitles + metadata (parallel)...")
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        sf = pool.submit(generate_subtitles, script)
        mf = pool.submit(generate_metadata, topic_val, fmt, hook_angle)
        subtitle_lines = sf.result()
        metadata       = mf.result()

    title_short = metadata.get("title", topic_val)[:50]

    part_num, series_len, series_title, prev_vid = get_series_info(topic_val)
    if part_num and part_num > 1:
        series_end = build_series_end_card(part_num, series_title, prev_vid)
        metadata["description"] = metadata.get("description", "") + series_end
        title_short = f"Part {part_num}: {title_short}"
        log(f"  📚 Series: {series_title} — Part {part_num}")
    elif part_num == 1:
        log(f"  📚 New series started: {series_title}")

    source_citation = get_source_citation(topic_val)

    with open(f"{SCRIPTS_DIR}/{safe_name}.txt", "w", encoding="utf-8") as f:
        f.write(f"TOPIC: {topic_val}\nFORMAT: {fmt}\n\n{script}")

    thumb_path = generate_thumbnail(metadata.get("title", topic_val), fmt, safe_name)
    if thumb_path:
        metadata["thumbnail_path"] = thumb_path

    meta_data = {
        "topic": topic_val, "format": fmt, "title": metadata.get("title"),
        "description": metadata.get("description"),
        "tags": metadata.get("tags"),
        "pinned_comment": metadata.get("pinned_comment"),
        "thumbnail_concept": metadata.get("thumbnail_concept"),
        "created": datetime.datetime.now().isoformat(),
    }
    metadata["topic"]  = topic_val
    metadata["format"] = fmt
    with open(f"{METADATA_DIR}/{safe_name}.json", "w", encoding="utf-8") as f:
        json.dump(meta_data, f, ensure_ascii=False, indent=2)

    log(f"  Title: {metadata.get('title','')[:60]}")

    log("🎬 Creating video...")
    video = create_video(
        script_text=script,
        english_subtitles=subtitle_lines,
        images_input=images,
        output_name=safe_name,
        format_type=fmt,
        title_short=title_short,
        bgm_path=bgm_path,
        source_citation=source_citation,
        topic_val=topic_val,
    )

    elapsed = time.time() - t_start
    if video:
        log(f"✅ VIDEO: {video}")
        log(f"✅ SHORT: {SHORTS_DIR}/{safe_name}_short.mp4")
        log(f"⏱️  Total: {elapsed:.0f}s")

        if upload:
            log("⬆️ Uploading to YouTube...")
            # ── Main video upload (independent) ──
            try:
                vid = upload_to_youtube(video, metadata, privacy)
                if vid:
                    log(f"✅ Live: https://youtu.be/{vid}")
                    get_series_info(topic_val, video_id=vid)
            except Exception as e:
                if is_quota_exceeded(e):
                    log(f"⚠️ YouTube quota exceeded — video queued for tomorrow")
                    queue_for_retry(video, metadata, privacy)
                else:
                    log(f"⚠️ Main video upload failed: {e}")

            # ── Short upload (fully independent — never affects main video) ──
            try:
                short_path = f"{SHORTS_DIR}/{safe_name}_short.mp4"
                if os.path.exists(short_path):
                    _yt2 = get_authenticated_service()
                    if _yt2:
                        upload_short_to_youtube(
                            short_path,
                            metadata.get("title", ""),
                            metadata.get("description", ""),
                            metadata.get("tags", ""),
                            _yt2
                        )
                        log("✅ Short uploaded independently")
                    else:
                        log("  ⚠️ Short: YouTube auth unavailable")
                else:
                    log(f"  ℹ️ Short not found at {short_path}")
            except Exception as short_err:
                log(f"  ⚠️ Short upload failed (main video unaffected): {short_err}")
    else:
        log(f"❌ Video creation failed ({elapsed:.0f}s)")

    return video


def auth_youtube():
    log("Authenticating YouTube...")
    svc = get_authenticated_service()
    if svc:
        log(f"✅ Token saved: {YOUTUBE_TOKEN_FILE}")
    return svc


def daemon_mode():
    if not HAS_SCHEDULE:
        print("pip install schedule"); sys.exit(1)

    log("=" * 55)
    log(f"  {CHANNEL_NAME} BOT — DAEMON MODE")
    log(f"  Daily: 05:30 IST generate | 06:00 + 18:30 upload")
    log("=" * 55)

    def daily_job():
        log("⏰ Daily job triggered")
        video = process_video(upload=False)
        if video:
            q = load_queue()
            meta_files = sorted(Path(METADATA_DIR).glob("*.json"), reverse=True)
            meta = json.loads(meta_files[0].read_text()) if meta_files else {}
            q.append({"video_path": video, "metadata": meta,
                      "created": datetime.datetime.now().isoformat(),
                      "status": "pending"})
            save_queue(q)

    def upload_job():
        q = load_queue()
        pending = [x for x in q if x.get("status") == "pending"]
        for item in pending:
            if os.path.exists(item["video_path"]):
                vid = upload_to_youtube(item["video_path"], item.get("metadata", {}))
                if vid:
                    item["status"] = "uploaded"
                    item["video_id"] = vid
        save_queue(q)

    schedule.every().day.at("05:30").do(daily_job)
    schedule.every().day.at("06:00").do(upload_job)
    schedule.every().day.at("18:30").do(upload_job)

    daily_job()
    upload_job()

    while True:
        schedule.run_pending()
        time.sleep(30)


def main():
    if not GEMINI_KEY and not GROQ_API_KEY:
        print("ERROR: Set GEMINI_KEY or GROQ_API_KEY"); sys.exit(1)

    parser = argparse.ArgumentParser(description="Tech Meets Travel Car News Bot v1.0")
    parser.add_argument("--day",          help="today")
    parser.add_argument("--topic",        help="Custom topic")
    parser.add_argument("--format",       help="news/launch/comparison/explainer/ev/suv")
    parser.add_argument("--upload",       action="store_true")
    parser.add_argument("--privacy",      default="public",
                        choices=["public", "unlisted", "private"])
    parser.add_argument("--daemon",       action="store_true")
    parser.add_argument("--auth-youtube", action="store_true")
    args = parser.parse_args()

    print(f"\n{'='*55}")
    print(f"  {CHANNEL_NAME} — Car News Automation Bot v1.0")
    print(f"  2-min videos · English · Auto upload")
    print(f"{'='*55}\n")

    if args.auth_youtube:
        auth_youtube(); return

    if args.daemon:
        daemon_mode(); return

    if args.topic:
        safe_process_video(topic=args.topic, format_type=args.format,
                      upload=args.upload, privacy=args.privacy)
    elif args.day:
        safe_process_video(upload=args.upload, privacy=args.privacy)
    else:
        print("Usage:")
        print("  python car_bot.py --day today")
        print("  python car_bot.py --day today --upload")
        print("  python car_bot.py --topic 'Tata Harrier 2026 new features'")
        print("  python car_bot.py --daemon")
        print("  python car_bot.py --auth-youtube")

    print(f"\n{'='*55}")
    print(f"  Done! Check: studio.youtube.com")
    print(f"{'='*55}\n")


if __name__ == "__main__":
    main()
