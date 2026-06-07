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

GROQ_MODEL   = "llama-3.3-70b-versatile"
GEMINI_MODEL_ECONOMY  = "gemini-1.5-flash"
GEMINI_MODEL_STANDARD = "gemini-2.0-flash"
GEMINI_MODEL_PREMIUM  = "gemini-2.5-flash"
_QUOTA_EXHAUSTED = False
BGM_FILE     = "bgm.mp3"
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
    "car":        ["luxury car front view", "sports car driving", "car on road india"],
    "suv":        ["suv offroad driving", "large suv india", "suv mountain road"],
    "ev":         ["electric car charging", "ev car future", "tesla electric vehicle"],
    "launch":     ["car showroom india", "new car display", "car launch event"],
    "concept":    ["concept car design", "futuristic car prototype", "car show geneva"],
    "comparison": ["two cars side by side", "car comparison", "different car models"],
    "interior":   ["car interior dashboard", "luxury car seats", "car infotainment"],
    "engine":     ["car engine bay", "engine performance", "hybrid engine"],
    "default":    ["car driving highway", "modern car front", "car sunsets"],
}

EVERGREEN_TOPICS = [
    "Top 5 SUVs launching in India this year",
    "Tata Harrier vs Mahindra XUV700 — which is better?",
    "Best electric cars under 20 lakhs in India",
    "Upcoming Maruti Suzuki cars in 2026",
    "Mahindra Thar Roxx — complete details",
    "Hyundai Creta 2026 facelift — what's new?",
    "SUV vs Sedan — which is right for you?",
    "Top 10 concept cars that became reality",
    "Best budget cars for first-time buyers in India",
    "EV vs Petrol — real cost comparison over 5 years",
    "Upcoming Tata cars in 2026",
    "Kia EV9 launched in India — full review",
    "Best 7-seater SUVs in India under 25 lakhs",
    "CNG vs Petrol vs EV — which fuel makes sense?",
    "Upcoming Hyundai cars lineup 2026",
]

CONTENT_FORMAT_TYPES = [
    "news",
    "launch",
    "comparison",
    "explainer",
    "ev",
    "suv",
]

DAILY_TOPIC_PROMPT = """You are a content strategist for "Tech Meets Travel" — an Indian car news YouTube channel.

YOUR AUDIENCE: Indian car enthusiasts aged 22-45, interested in upcoming launches, EV news, SUV reviews, concept cars, and automotive technology. They want quick, accurate, engaging car news.

TODAY: {date} | {day}
CAR NEWS (raw RSS feeds): {car_news}
TRENDING SEARCHES: {trends}
RECENTLY USED TOPICS (DO NOT repeat): {recent_topics}

STEP 1 — Topic quality check:
Ask: "Is this the most interesting car story for an Indian viewer today?"
Pick the most newsworthy or highest-interest topic.

STEP 2 — Format selection:
- news: breaking car news, launch dates, price announcements
- launch: new car launch details, variants, features
- comparison: compare two cars side by side
- explainer: how something works (ADAS, hybrid tech, etc.)
- ev: electric vehicle specific content
- suv: SUV specific content

STEP 3 — Uniqueness check:
The recently used topics above must NOT be repeated.

Return ONLY valid JSON, nothing else:
{{
  "topic": "<Clickable English topic — specific, exciting, news-driven>",
  "format": "<news/launch/comparison/explainer/ev/suv>",
  "pexels_keyword": "<car/suv/ev/launch/concept/comparison/interior/engine>",
  "hook_angle": "<First 5 seconds — the hook that grabs attention>",
  "reason": "<Why this topic today>"
}}"""

SCRIPT_PROMPT = """You are a professional YouTube scriptwriter for "Tech Meets Travel" — an Indian car news channel.

Topic: {topic}
Format: {format_type}
Hook: {hook_angle}
Voice: {voice_gender}

━━━━━━━━━━━━━━━━━━━━━━━━━
SCRIPT STRUCTURE (follow exactly — 4 beats for a 2-minute video):

BEAT 1 — HOOK (15 seconds)
Jump straight into the most exciting fact or question.
Example good hook: "Tata is about to launch the car that could change the Indian EV market forever."
DO NOT introduce yourself. Start with the news.

BEAT 2 — CORE INFORMATION (60 seconds)
Deliver complete, accurate information with specific details:
- Actual numbers: prices, range, horsepower, torque, battery size
- Launch dates, variant details, key features
- Comparisons where relevant
- No filler, no fluff

BEAT 3 — PRACTICAL ANALYSIS (25 seconds)
What does this mean for the Indian buyer?
Should they wait for this launch? Is it worth the price?
Give a clear, opinionated take.

BEAT 4 — CTA (10 seconds)
Natural close. Ask viewers to subscribe for more car news.
DO NOT sound desperate. "If you found this useful, consider subscribing."

━━━━━━━━━━━━━━━━━━━━━━━━━

FORMAT TONE:
- news:     confident, fast-paced — "Here's what just happened"
- launch:   excited, detailed — "Here's everything you need to know"
- comparison: balanced, data-driven — "Here's how they stack up"
- explainer: clear, educational — "Here's how it works"
- ev:        forward-looking, tech-focused — "The future is electric"
- suv:       bold, adventurous — "Built for the wild"

CRITICAL RULES:
1. {target_min_words}-{target_max_words} words exactly (for ~2 min at natural pace)
2. Conversational English — how you'd explain to a friend
3. Use real numbers — actual prices, specs, dates
4. No markdown, no headers, no bullets — pure flowing speech
5. Every sentence must earn its place — no filler
6. Information must be complete — viewer shouldn't need to search elsewhere

YOUTUBE RETENTION RULES:
1. HOOK (0-10s): Lead with the most exciting spec or controversy.
   Bad: "Today we're talking about the new Tata car..."
   Good: "Tata just confirmed the price — and it's ₹3 lakhs cheaper than expected."

2. PATTERN INTERRUPT every 25s: "But here's what nobody is telling you..."

3. COMPARISON ANCHOR: Always compare to what viewers know.
   "That's the same price as a fully loaded Swift" — makes numbers real.

4. BUILD SUSPENSE: Don't give the conclusion in Beat 1.
   "I'll tell you the exact launch date at the end — but first..."

5. CALL TO ACTION: "Drop a comment — Tata or Mahindra?" 
   Forces 2-option comment = massive engagement signal to algorithm.
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

Return ONLY valid JSON, no markdown:
{{
  "title": "<SEO-optimized title — see rules below>",
  "description": "<Full description — see rules below>",
  "tags": "<25 comma-separated tags — see rules below>",
  "pinned_comment": "<Engaging pinned comment>",
  "thumbnail_concept": "<Thumbnail description>"
}}

TITLE RULES:
- Under 60 characters
- Format: [Exciting hook or question] | Tech Meets Travel
- Include the most-searched keyword naturally
- Use numbers when possible

DESCRIPTION RULES:
Line 1: Hook that matches the video's first 5 seconds
Line 2: "In this video, we cover [topic summary] | Indian Car News"
Then:
- 3-5 key points viewers will learn
- "Subscribe to Tech Meets Travel for daily car news updates"
- Hashtags: #TechMeetsTravel #IndianCars #[brand] #[topic keyword]

TAGS RULES:
- indian cars, car news india, upcoming cars, new car launches, [brand] india, ev cars india
- Mix of high-volume and long-tail tags

PINNED COMMENT:
- Ask viewers a specific question about the topic
- End with: Subscribe to Tech Meets Travel for more car news 🔔

THUMBNAIL CONCEPT:
- Background: bold color (red, orange, dark blue) based on format
- Bold white/yellow text (main hook) — left 60% of image
- Right 40%: car visual
- High contrast, readable at 120px

MONETISATION-FOCUSED SEO RULES:

TITLE (car enthusiast CTR):
- Car name + price/launch year = highest CTR for car content
- "Tata Harrier EV 2026 — Price Revealed | Launch Date Confirmed"
- Controversy works: "Why Mahindra XEV 9e is BETTER than Nexon EV"

DESCRIPTION LINE 1: The exact news hook
DESCRIPTION LINE 2: "Full details on [car name] launch, price, specs | Tech Meets Travel"

TAGS: Car name + variants + year + India + launch
"tata harrier ev" + "harrier ev price india" + "harrier ev 2026" + "tata ev launch"
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


def _call_gemini(prompt_text, model_name=GEMINI_MODEL_ECONOMY):
    global _QUOTA_EXHAUSTED
    if _QUOTA_EXHAUSTED:
        return ""
    import time
    import random
    if not GEMINI_KEY:
        raise Exception("GEMINI_KEY not set")
    client = genai.Client(api_key=GEMINI_KEY)
    max_attempts = 2
    for attempt in range(max_attempts):
        try:
            resp = client.models.generate_content(
                model=model_name, contents=prompt_text)
            return resp.text
        except Exception as e:
            err_str = str(e)
            if "429" in err_str or "quota" in err_str.lower() or "RESOURCE_EXHAUSTED" in err_str:
                _QUOTA_EXHAUSTED = True
                log(f"Quota exhausted on {model_name}. Attempt {attempt+1}/{max_attempts}")
                if attempt < max_attempts - 1:
                    sleep_time = random.uniform(15, 25) * (2 ** attempt)
                    log(f"Backing off {sleep_time:.0f}s before retry...")
                    time.sleep(sleep_time)
                    continue
            elif "404" in err_str or "NOT_FOUND" in err_str:
                log(f"Model {model_name} not found, skipping")
                return ""
            else:
                log(f"Gemini call failed: {e}")
                if attempt < max_attempts - 1:
                    time.sleep(5)
                    continue
            return ""
    return ""

def _call_groq(prompt, max_retries=3):
    if not (GROQ_API_KEY and Groq):
        return None
    for attempt in range(max_retries):
        try:
            client = Groq(api_key=GROQ_API_KEY)
            resp = client.chat.completions.create(
                messages=[{"role": "user", "content": prompt}],
                model=GROQ_MODEL, temperature=0.85, max_tokens=4000,
            )
            return resp.choices[0].message.content
        except Exception as e:
            err = str(e)
            if "tokens per day" in err or "TPD" in err:
                log("⚠️ Groq daily limit — falling back to Gemini")
                return None
            if "429" in err or "rate_limit" in err.lower():
                wait = 10 * (attempt + 1)
                log(f"⏳ Groq 429 retry {attempt+1}/{max_retries} in {wait}s...")
                time.sleep(wait)
            else:
                return None
    return None


def _call_github(prompt_text):
    if not GITHUB_TOKEN:
        return None
    import requests
    try:
        resp = requests.post(
            "https://models.inference.ai.azure.com/chat/completions",
            headers={"Authorization": f"Bearer {GITHUB_TOKEN}", "Content-Type": "application/json"},
            json={"model": GH_MODEL, "messages": [{"role": "user", "content": prompt_text}],
                  "temperature": 0.7, "max_tokens": 4000},
            timeout=60,
        )
        if resp.status_code == 200:
            return resp.json()["choices"][0]["message"]["content"]
        if resp.status_code == 429:
            log("GitHub model rate limited")
        else:
            log(f"GitHub model returned {resp.status_code}")
        return None
    except Exception as e:
        log(f"GitHub model call failed: {e}")
        return None


def call_llm(prompt_text, task="economy"):
    global _QUOTA_EXHAUSTED
    if _QUOTA_EXHAUSTED and task not in ("script", "topic"):
        log("Quota exhausted, skipping non-critical LLM call")
        return ""

    if task in ("script", "topic"):
        log(f"call_llm task={task}: trying Groq (LLaMA) first")
        try:
            result = _call_groq(prompt_text)
            if result and result.strip():
                return result
        except Exception as e:
            log(f"Groq failed for {task}: {e}")

    if task != "premium":
        result = _call_github(prompt_text)
        if result and result.strip():
            return result

    tier_map = {
        "economy":  [GEMINI_MODEL_ECONOMY,  GEMINI_MODEL_STANDARD, GEMINI_MODEL_PREMIUM],
        "standard": [GEMINI_MODEL_STANDARD, GEMINI_MODEL_PREMIUM],
        "premium":  [GEMINI_MODEL_PREMIUM],
    }
    models = tier_map.get(task, tier_map["economy"])
    for model_name in models:
        if _QUOTA_EXHAUSTED:
            log(f"Quota exhausted, skipping remaining models in tier")
            break
        log(f"call_llm task={task} model={model_name}")
        result = _call_gemini(prompt_text, model_name=model_name)
        if result and result.strip():
            return result
    return ""


def parse_json_response(raw):
    clean = raw.strip()
    if clean.startswith("```"):
        parts = clean.split("```")
        clean = parts[1] if len(parts) > 1 else clean
        if clean.startswith("json"):
            clean = clean[4:]
    return json.loads(clean.strip())


def fetch_car_news():
    news = []
    sources = [
        ("https://www.autocarindia.com/car-news", "Autocar"),
        ("https://www.cardekho.com/india-car-news", "CarDekho"),
        ("https://www.zigwheels.com/upcoming-cars", "Zigwheels"),
        ("https://www.rushlane.com/car-news", "RushLane"),
    ]
    headers = {"User-Agent": "Mozilla/5.0"}
    for url, src in sources:
        try:
            r = requests.get(url, headers=headers, timeout=10)
            if r.status_code == 200:
                soup = BeautifulSoup(r.text, "html.parser")
                for a in soup.find_all("a", href=True):
                    t = a.get_text(strip=True)
                    keywords = ["launch", "price", "ev", "hybrid", "suv", "tata",
                                "mahindra", "hyundai", "maruti", "kia", "honda",
                                "reveal", "upcoming", "booking", "delivery",
                                "facelift", "concept", "horsepower", "range"]
                    if any(k.lower() in t.lower() for k in keywords) and len(t) > 15:
                        news.append(f"[{src}] {t[:120]}")
        except:
            pass
    return "\n".join(news[:20]) if news else "No fresh news. Use evergreen topics."


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
                 "--rate=-13%", "--pitch=+0Hz", "--write-media", voice_file],
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

    raw = call_llm(prompt, task="topic")
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
        resp = call_llm(build_prompt(attempt), task="script")
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
    raw = call_llm(prompt, task="script")
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

def generate_thumbnail(title, format_type, output_name):
    """Generate premium automotive thumbnail — format-specific color palette."""
    try:
        from PIL import Image, ImageDraw, ImageFont
        import math
        os.makedirs(THUMBNAIL_DIR, exist_ok=True)

        W, H = 1280, 720
        cfg = TT_THUMB_FORMATS.get(format_type, TT_THUMB_FORMATS["default"])
        img = Image.new("RGB", (W, H), cfg["c1"])
        d   = ImageDraw.Draw(img)

        def load_font(size, bold=True):
            try: return ImageFont.truetype(ENG_BOLD_FONT if bold else ENG_REG_FONT, size)
            except: return ImageFont.load_default()

        def bg_grad():
            for y in range(H):
                t = y/H
                col = tuple(int(cfg["c1"][j]+(cfg["c2"][j]-cfg["c1"][j])*t) for j in range(3))
                d.line([(0,y),(W,y)], fill=col)

        def shadow_text(x, y, text, font, fill):
            for ox,oy in [(3,3),(-2,-2),(2,-2),(-2,2)]:
                d.text((x+ox,y+oy), text, font=font, fill=(0,0,0))
            d.text((x,y), text, font=font, fill=fill)

        def wrap_title(text, n=18):
            words = text.split()
            lines, line = [], ""
            for w in words:
                if len(line+w)<=n: line+=w+" "
                else:
                    if line: lines.append(line.strip())
                    line=w+" "
            if line: lines.append(line.strip())
            return lines[:3]

        bg_grad()

        # Diagonal accent panel (right 35%)
        px = int(W*0.63)
        for x in range(px,W):
            t = (x-px)/(W-px)
            col = tuple(max(0,int(c*(1-t*0.4))) for c in cfg["c2"])
            d.line([(x,0),(x,H)],fill=col)
        d.polygon([(px-35,0),(px+35,0),(px-35,H),(px-90,H)], fill=cfg["c2"])

        # Grid lines (tech aesthetic)
        for x in range(0,W,90):
            d.line([(x,0),(x,H)], fill=(*cfg["c2"],), width=1)
        for y in range(0,H,90):
            d.line([(0,y),(W,y)], fill=(*cfg["c2"],), width=1)

        # Car silhouette (right panel)
        car_cx, car_cy = px+(W-px)//2, H//2+20
        s = 1.6
        body = [
            (car_cx-int(118*s),car_cy+int(22*s)), (car_cx-int(120*s),car_cy-int(7*s)),
            (car_cx-int(92*s), car_cy-int(32*s)), (car_cx-int(28*s), car_cy-int(58*s)),
            (car_cx+int(42*s), car_cy-int(58*s)), (car_cx+int(102*s),car_cy-int(28*s)),
            (car_cx+int(120*s),car_cy-int(7*s)),  (car_cx+int(122*s),car_cy+int(22*s)),
        ]
        d.polygon(body, fill=(32,35,50))
        d.polygon(body, outline=cfg["acc"], width=2)
        wind = [
            (car_cx-int(82*s),car_cy-int(30*s)), (car_cx-int(25*s),car_cy-int(54*s)),
            (car_cx+int(38*s),car_cy-int(54*s)), (car_cx+int(92*s),car_cy-int(26*s)),
            (car_cx+int(36*s),car_cy-int(12*s)), (car_cx-int(22*s),car_cy-int(12*s)),
        ]
        d.polygon(wind, fill=(22,52,98))
        for wx,wy in [(car_cx-int(72*s),car_cy+int(28*s)),(car_cx+int(72*s),car_cy+int(28*s))]:
            r = int(25*s)
            d.ellipse([wx-r,wy-r,wx+r,wy+r], fill=(12,12,18))
            d.ellipse([wx-r+3,wy-r+3,wx+r-3,wy+r-3], outline=cfg["acc"], width=2)
            d.ellipse([wx-7,wy-7,wx+7,wy+7], fill=(45,48,58))
        d.ellipse([car_cx+int(116*s)-5,car_cy-int(4*s)-3,
                   car_cx+int(116*s)+5,car_cy-int(4*s)+3], fill=(255,238,178))

        # Borders
        d.rectangle([0,0,W,10], fill=cfg["acc"])
        d.rectangle([0,H-10,W,H], fill=cfg["acc"])

        # Format badge
        badge = cfg["badge"]
        bw = len(badge)*16+42
        bfont = load_font(22)
        d.rounded_rectangle([W-bw-18,16,W-18,62], radius=8, fill=cfg["bb"])
        d.text((W-bw//2-18,39), badge, font=bfont, fill=(255,255,255), anchor="mm")

        # Channel handle
        hfont = load_font(20, bold=False)
        d.text((28,H-38), "@tech_meets_travel", font=hfont, fill=(158,162,178))

        # Title text
        lines = wrap_title(title, 17)
        ty = 105
        for i,line in enumerate(lines):
            font = load_font(82 if i==0 else 56)
            col  = (255,255,255) if i==0 else (198,202,218)
            shadow_text(28, ty, line, font, col)
            ty += (92 if i==0 else 64)

        d.rectangle([28,ty+5,min(28+420,px-15),ty+11], fill=cfg["acc"])

        out = f"{THUMBNAIL_DIR}/{output_name}_thumb.png"
        img.save(out)
        log(f"  ✅ Thumbnail: {out}")
        return out
    except Exception as e:
        log(f"  ⚠️ Thumbnail failed: {e}")
        return None

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
            "categoryId":  "27",
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
        log(f"❌ Upload failed: {e}"); return None


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
    log("📸 Fetching Pexels images...")
    images = fetch_pexels_images(pexels_kw, img_dir, count=5)
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
