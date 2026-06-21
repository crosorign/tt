#!/usr/bin/env python3
"""
Tech Meets Travel — CAR NEWS AUTOMATION BOT v2.0
Fully automated YouTube channel for global + Indian car news.
Hybrid videos: ~2 min default, 5–8 min long-form for high-score stories.

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

def _topic_key(t):
    """Normalize topic for fuzzy comparison — strips punctuation, lowercase, 45 chars."""
    import re as _r
    return _r.sub(r'[^\w\s]', '', str(t).lower().strip())[:45]

def _is_duplicate_topic(new_topic, recent_topics, threshold=0.75):
    """Return True if new_topic is too similar to any recent topic."""
    new_key = _topic_key(new_topic)
    for old in recent_topics:
        old_key = _topic_key(old)
        # Exact match
        if new_key == old_key:
            return True
        # Word overlap check (>75% shared words = duplicate)
        new_words = set(new_key.split())
        old_words = set(old_key.split())
        if new_words and old_words:
            overlap = len(new_words & old_words) / max(len(new_words), len(old_words))
            if overlap >= threshold:
                return True
    return False


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
TARGET_MIN_WORDS_LONG = 750
TARGET_MAX_WORDS_LONG = 1200
LONG_FORM_SCORE_THRESHOLD = 7.0
VIDEO_FPS = 30
AI_DISCLOSURE = (
    "\n\n🤖 Content created with AI assistance for informational purposes."
)
BGM_DIR = "assets/bgm"

VOICE_FEMALE = "ta-IN-PallaviNeural"   # Tamil female voice
VOICE_MALE   = "ta-IN-ValluvarNeural"  # Tamil male voice

EQ_FEMALE = (
    "highpass=f=90,"
    "equalizer=f=220:t=q:w=0.9:g=2,"    # body warmth
    "equalizer=f=900:t=q:w=0.9:g=2,"    # presence
    "equalizer=f=2500:t=q:w=1:g=1.5,"   # clarity
    "equalizer=f=5500:t=q:w=1:g=-2,"    # de-ess
    "equalizer=f=8500:t=q:w=1:g=-3,"    # cut digital edge
    "aecho=0.75:0.62:26:0.06,"          # small room — natural Indian studio feel
    "acompressor=threshold=-20dB:ratio=1.8:attack=8:release=200:makeup=2,"
    "atempo=0.98,"                        # slight slow — removes TTS rush
    "loudnorm=I=-14:TP=-1.5:LRA=11"
)

EQ_MALE = (
    "highpass=f=70,"
    "equalizer=f=130:t=q:w=0.8:g=2,"    # chest resonance
    "equalizer=f=450:t=q:w=0.9:g=1.5,"  # warmth
    "equalizer=f=2200:t=q:w=1:g=2,"     # intelligibility
    "equalizer=f=6500:t=q:w=1:g=-2.5,"  # cut harshness
    "aecho=0.72:0.55:22|38:0.07|0.04,"  # dual-tap natural room
    "acompressor=threshold=-18dB:ratio=1.7:attack=7:release=250:makeup=2.5,"
    "atempo=0.97,"                        # PrabhatNeural slightly faster than ideal
    "loudnorm=I=-14:TP=-1.5:LRA=11"
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


RATE_BY_FORMAT_TT = {
    "news":       "-5%",    # fast, urgent breaking news
    "launch":     "-6%",    # energetic reveal
    "comparison": "-10%",   # measured, analytical
    "explainer":  "-11%",   # clear for technical info
    "ev":         "-7%",    # modern, confident
    "suv":        "-8%",    # bold, confident
    "default":    "-8%",
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

MASTER_SYSTEM_PROMPT = """You are an expert automotive journalist, EV analyst, and viral YouTube creator for "Tech Meets Travel" — India's go-to channel for car facts, upcoming launches, and EV reality checks.

PRIMARY AUDIENCE: Indian car buyers aged 22–45 — first-time buyers, upgrade seekers, EV curious, car enthusiasts. They think in ₹, buy from showrooms, compare EMIs, and watch before they visit the dealership.

CONTENT PILLARS (in priority order — proven viral):
1. MIND-BLOWING CAR FACTS — engineering secrets, design stories, hidden features most owners don't know
2. UPCOMING CAR LAUNCHES — India launch timeline, expected price, who should buy, competition
3. EV REALITY — real-world range, charging cost vs petrol savings, which EV actually makes sense in India
4. COMPARISON VERDICT — pick a clear winner with Indian on-road price, EMI, ownership cost
5. BUYER INTELLIGENCE — right variant, right time to buy, negotiation facts, hidden costs

TONE: The knowledgeable friend who just returned from a press drive. Confident opinions, not a spec sheet reader. Use "you" directly. Have a point of view — don't sit on the fence.

INDIA CONTEXT — always include where relevant:
- Prices in ₹ ex-showroom + on-road estimate
- EMI at 8.5% / 60 months
- Petrol/diesel/electricity running cost per km
- Waiting period and delivery reality
- Which Indian city/use case this car fits

GLOBAL CONTEXT — only when it directly affects Indian buyers:
- Technology arriving in India (BYD, Tesla timeline)
- Safety ratings that apply to India-sold cars
- Global recalls that affect India models

VIRAL CONTENT TRIGGERS (weave into every script):
- A fact so surprising viewers screenshot it and share
- A number comparison: "₹8 per km diesel vs ₹1.2 per km EV"
- A counterintuitive truth: "The cheaper variant is actually better because..."
- A specific insider detail nobody mentions in reviews

Rules: factually accurate, specific numbers always, zero vague "affordable" language, monetization-safe, never misleading clickbait.
"""

DAILY_TOPIC_PROMPT = """{master_prompt}

You are selecting today's highest-potential story for "Tech Meets Travel".

TODAY: {date} | {day}
FRESH CAR NEWS: {car_news}
TRENDING SEARCHES: {trends}
RECENTLY USED TOPICS — DO NOT repeat similar themes: {recent_topics}

STORY PRIORITY (pick what will generate most views today):
1. VIRAL CAR FACT — an engineering detail, design secret, or hidden feature most Indian owners don't know
2. UPCOMING LAUNCH — India-bound car with confirmed/expected price, launch date, who should buy
3. EV REALITY CHECK — actual ownership cost, range truth, charging reality in India
4. COMPARISON WITH VERDICT — pick a clear winner, explain why for Indian roads/budget
5. BUYER INTELLIGENCE — right variant, right time, hidden cost warning

CATEGORY ROTATION — pick most underrepresented from recent topics:
A) VIRAL FACT  B) UPCOMING LAUNCH  C) EV REALITY  D) COMPARISON  E) BUYER ADVICE  F) MARKET TREND

GREAT TOPIC = Specific Car/Model + Surprising Number/Fact + India Relevance
Examples of great topics:
- "Tata Punch EV real-world range tested: 180km or 315km — the truth"
- "Why the Maruti Swift base variant outsells the top model 4:1 in India"
- "Mahindra BE 6 hidden feature every owner should know"
- "₹1.2 per km vs ₹8 per km — Nexon EV vs Nexon Diesel 5-year cost breakdown"
- "Hyundai Creta next-gen: 3 things they removed that buyers are angry about"

Suggest video_mode "long" only for Tier 1–2 stories where depth adds value.

Return ONLY valid JSON:
{{
  "topic": "<specific India-relevant topic with a number or surprising angle>",
  "format": "<news|launch|comparison|explainer|ev|suv>",
  "category": "<A|B|C|D|E|F>",
  "priority_tier": <1|2|3>,
  "video_mode": "<short|long>",
  "pexels_keyword": "<car|suv|electric car|highway india|concept car>",
  "hook_angle": "<the single most surprising/useful fact a viewer will share>",
  "reason": "<why this story today>",
  "viral_fact": "<one concrete number or fact that will make viewers screenshot this>",
  "india_angle": "<specific relevance to Indian buyers — price, EMI, availability>",
  "news_summary": {{
    "what": "<what happened>",
    "why_matters": "<why it matters to Indian buyers>",
    "specs": "<key specs if known>",
    "price": "<₹ ex-showroom if known, else expected range>",
    "emi_estimate": "<approx EMI at 8.5% 60 months if price known>",
    "timeline": "<India launch timeline if known>",
    "source": "<Autocar India|CarDekho|Manufacturer|Reuters|etc>"
  }},
  "monetization_score": {{
    "viral": <1-10>,
    "search": <1-10>,
    "cpm": <1-10>,
    "sponsor": <1-10>,
    "total": <average 1-10>
  }},
  "title_candidates": ["<20 title options under 70 chars>"],
  "image_search_queries": ["<5-10 specific image search queries>"],
  "broll_queries": ["<3-5 b-roll search queries>"]
}}
"""


SCRIPT_PROMPT_SHORT = """{master_prompt}

You are writing a punchy, opinionated car video script for "Tech Meets Travel".
Think: the friend who actually read the brochure, drove the car, and has a strong opinion.

Topic: {topic}
Format: {format_type}
Hook: {hook_angle}
Voice: {voice_gender}

VIDEO STRUCTURE (~2 minutes, 4 beats):

BEAT 1 — THE VIRAL OPENER (15 seconds)
Lead with the most surprising fact, number, or counterintuitive truth. NOT "Today we look at..."
The opener should be something a viewer screenshots and forwards on WhatsApp.
Example opener style: "Most people buying the Creta don't know that the ₹2 lakh cheaper variant has better resale."

BEAT 2 — THE FULL STORY WITH INDIA NUMBERS (60 seconds)
- Ex-showroom price in ₹, on-road estimate, approx EMI at 8.5% / 60 months
- Real-world specs that matter: actual range or mileage, boot space, ground clearance
- Competition comparison with price difference clearly stated
- One specific fact nobody mentions in mainstream reviews

BEAT 3 — THE VERDICT (25 seconds)
Be direct. Pick a winner or say exactly who should buy/wait/skip and WHY.
"If you drive under 60km a day in a city — buy the EV. If you travel on highways monthly — wait."
Never leave viewers without a clear action: buy now / wait for X / pick variant Y.

BEAT 4 — COMMENT TRIGGER (10 seconds)
End with a two-choice question that forces a comment:
"Petrol or Electric — what are you picking? Drop it below 👇"
"Worth the price or overrated? Tell me in comments"

FORMAT-SPECIFIC ANGLE:
news: what changed and what it means for buyers | launch: price prediction + who should pre-book
comparison: pick a clear winner for 3 buyer types | explainer: one surprising technical fact simply explained
ev: real running cost vs petrol with actual ₹/km numbers | suv: ground clearance, 7-seater truth, highway comfort

HARD RULES:
1. {target_min_words}-{target_max_words} words
2. ZERO markdown — pure speech
3. Every price in ₹ — never just "affordable" or "competitive"
4. Never open with the brand name or "Today" — open with the fact or the number
5. One fact so specific viewers will say "I didn't know that"
6. LAST sentence = two-choice comment question

PAUSE MARKERS — mandatory:
[PAUSE_LONG] after viral opener and between beats | [PAUSE_MED] before the verdict | [PAUSE_SHORT] after key specs
"""

SCRIPT_PROMPT_LONG = """{master_prompt}

You are writing a deep-dive car video script for "Tech Meets Travel".

Topic: {topic}
Format: {format_type}
Hook: {hook_angle}
Voice: {voice_gender}

VIDEO STRUCTURE (5–8 minutes):

HOOK (0–15 sec) — viral fact or number — no greeting, no "Today we look at"
THE STORY — full context, India launch timeline, global comparison
SPECS THAT MATTER — real-world numbers Indian buyers care about: mileage/range, boot, ground clearance, EMI
INDIA PRICING BREAKDOWN — ex-showroom, on-road, EMI, insurance estimate, running cost per km
COMPETITION COMPARISON — vs 2 direct rivals with clear price difference stated in ₹
HIDDEN DETAILS — one thing reviewers never mention, one variant-specific insight
VERDICT — exactly who should buy (city commuter / highway traveller / family / first-time buyer)
CTA — natural comment question with two choices

HARD RULES:
1. {target_min_words}-{target_max_words} words
2. ZERO markdown — pure speech
3. Every price in ₹ — ex-showroom, never vague
4. At least one fact that makes a viewer say "I had no idea"
5. Give a definitive verdict — no "it depends on your needs" cop-outs
6. End with two-choice comment question

PAUSE MARKERS — use [PAUSE_LONG], [PAUSE_MED], [PAUSE_SHORT] at section transitions and key reveals.
"""

SCRIPT_PROMPT = SCRIPT_PROMPT_SHORT

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

METADATA_PROMPT = """Generate YouTube metadata for "Tech Meets Travel" — India's car facts, EV reality, and upcoming launches channel.

Topic: {topic}
Format: {format_type}
Hook: {hook_angle}

Return ONLY valid JSON, no markdown:
{{
  "title": "<title — see rules>",
  "description": "<description — see rules>",
  "tags": "<tags — see rules>",
  "pinned_comment": "<pinned comment>",
  "thumbnail_concept": "<thumbnail concept>",
  "categoryId": "2"
}}

TITLE RULES (critical for CTR):
- Under 65 characters
- Lead with the car model name or the surprising number — not the channel name
- Use formats proven viral in Indian car content:
  "Tata Punch EV Real Range: 180km or 315km? The Truth"
  "Why I'd Buy the ₹8 Lakh Variant Over the ₹12 Lakh One"
  "Mahindra BE 6 Hidden Feature Nobody Told You"
- Numbers in title always outperform generic titles
- Never start with "Tech Meets Travel"

DESCRIPTION RULES:
Line 1: The viral hook fact from the video (same energy as opening 5 seconds)
Line 2: "Watch before buying the [car model] | Tech Meets Travel"
Then:
- Chapter timestamps matching actual beats
- Key facts covered (3-5 bullet points)
- India-specific links: cardekho.com, autoportal.com for price check
- Subscribe CTA: "🔔 Subscribe for daily Indian car facts: @TechMeetsTravel"
- Disclaimer if EV cost estimates used

TAGS (30 total — Indian car SEO priority):
Tier 1 (5 high-volume): "[car model] india", "[car model] price", "best car india 2026", "ev india", "upcoming cars india 2026"
Tier 2 (10 model-specific): exact search terms Indian buyers use — "ex showroom price", "on road price [city]", "emi [model]", "mileage [model]", "[model] vs [competitor]"
Tier 3 (10 long-tail): "should i buy [model]", "[model] real world range", "[model] ownership cost", "best [segment] car india"
Tier 4 (5 channel): "tech meets travel", "indian car review", "car facts india", "ev reality india", "car buying guide india"

PINNED COMMENT:
- Ask a two-choice question: "Petrol or EV — what are you going with? 👇"
- Or: "Worth the price or overrated? Drop your pick below"
- Keep under 200 characters

THUMBNAIL CONCEPT:
- Show the car prominently (3/4 front angle)
- Large bold text overlay with the hook number or verdict word
- High contrast — readable at 120px
- Avoid generic stock photos — suggest car-specific image query
"""

CONTENT_PACKAGE_PROMPT = """{master_prompt}

Generate the full YouTube content package for this video.

Topic: {topic}
Format: {format_type}
Hook: {hook_angle}
Video mode: {video_mode}
News summary: {news_summary}

SCRIPT (for alignment — description and chapters must match this):
{script}

Return ONLY valid JSON:
{{
  "title_candidates": ["<20 titles under 70 chars, curiosity + SEO>"],
  "title": "<best single title under 60 chars>",
  "thumbnail_headline": "<max 4 words, high CTR, ALL CAPS ok>",
  "thumbnail_concept": "<visual concept for thumbnail background>",
  "short_title": "<Shorts title under 70 chars>",
  "short_script": "<45-60 second Shorts script, fast hook + one key fact + CTA>",
  "description": "<300-word SEO description with summary, keywords, CTA>",
  "tags": "<30 comma-separated YouTube tags>",
  "keywords": "<50 comma-separated SEO keywords>",
  "pinned_comment": "<2-option engagement question>",
  "community_post": "<YouTube community post asking viewer opinion>",
  "future_video_ideas": ["<20 related video ideas>"]
}}

Include AI disclosure is NOT needed in description — added automatically at upload.
First 2 description lines must hook search snippets. Tags: high-volume + model + buying-intent + trending.
"""

SHORT_SCRIPT_PROMPT = """{master_prompt}

Write a 45–60 second YouTube Shorts script for this car news topic.

Topic: {topic}
Hook: {hook_angle}
Main script excerpt: {script_excerpt}

Rules: instant hook in first 3 seconds, one killer fact, why it matters, comment CTA.
80-120 words. Pure speech. Include [PAUSE_SHORT] and [PAUSE_MED] markers. No markdown.
"""

KB_PRESETS = [
    # Strong zoom-in with aggressive pan right
    ("min(1.0+0.0012*on,1.25)", "iw/2-(iw/zoom/2)+on*0.5",  "ih/2-(ih/zoom/2)",          "zoom-in pan-right"),
    # Strong zoom-in with aggressive pan left
    ("min(1.0+0.0012*on,1.25)", "iw/2-(iw/zoom/2)-on*0.5",  "ih/2-(ih/zoom/2)",          "zoom-in pan-left"),
    # Dramatic zoom-out from tight crop
    ("max(1.30-0.0012*on,1.0)", "iw/2-(iw/zoom/2)",          "ih/2-(ih/zoom/2)",          "zoom-out center"),
    # Zoom-in + pan up (car reveal feel)
    ("min(1.0+0.0010*on,1.20)", "iw/2-(iw/zoom/2)",          "ih/2-(ih/zoom/2)+on*0.4",   "zoom-in pan-up"),
    # Zoom-out + pan right (sweeping shot)
    ("max(1.25-0.0010*on,1.0)", "iw/2-(iw/zoom/2)+on*0.4",  "ih/2-(ih/zoom/2)",          "zoom-out pan-right"),
    # Zoom-in + pan down (descend onto subject)
    ("min(1.0+0.0009*on,1.18)", "iw/2-(iw/zoom/2)",          "ih/2-(ih/zoom/2)-on*0.35",  "zoom-in pan-down"),
    # Diagonal drift — cinematic feel
    ("min(1.0+0.0008*on,1.15)", "iw/2-(iw/zoom/2)+on*0.3",  "ih/2-(ih/zoom/2)+on*0.25",  "diagonal drift"),
    # Zoom-out + pan left
    ("max(1.22-0.0009*on,1.0)", "iw/2-(iw/zoom/2)-on*0.35", "ih/2-(ih/zoom/2)",          "zoom-out pan-left"),
]

XFADE_TRANSITIONS = [
    "fade", "dissolve", "wipeleft", "wiperight",
    "slideright", "slideleft", "slideup",
    "circlecrop", "rectcrop", "fadeblack",
    "smoothleft", "smoothright",
]


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
    """Extract JSON from LLM response robustly — handles fences, control chars, truncation."""
    import re as _re
    text = raw.strip() if raw else ""

    # Strip markdown fences
    for fence in ["```json", "```JSON", "```"]:
        if text.startswith(fence):
            text = text[len(fence):]
            if text.endswith("```"):
                text = text[:-3]
            break

    text = text.strip()

    # Remove control characters that break JSON (except \n \t)
    text = _re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)

    # Try direct parse first
    try:
        return json.loads(text)
    except Exception:
        pass

    # Try extracting first {...} block
    match = _re.search(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", text, _re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except Exception:
            pass

    # Try fixing common issues: trailing commas, unquoted keys
    fixed = _re.sub(r",\s*([}\]])", r"\1", text)   # trailing commas
    fixed = _re.sub(r"(\w+):", r'"\1":', fixed)    # unquoted keys (best-effort)
    try:
        return json.loads(fixed)
    except Exception as e:
        raise ValueError(f"Cannot parse JSON after all attempts: {e}\nRaw: {text[:200]}")
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


# ═══════════════════════════════════════════════════════════════
# FREE MEDIA: Pollinations AI (TT) + End Screen
# ═══════════════════════════════════════════════════════════════

def fetch_pollinations_image_tt(car_name, format_type, output_path):
    """Free AI-generated unique car image — no API key, unique per video."""
    import urllib.parse, random
    prompts = {
        "ev":         f"{car_name} electric vehicle futuristic cinematic photography dramatic studio lighting Indian highway photorealistic 8K no text",
        "launch":     f"{car_name} brand new reveal dramatic studio lighting automotive photography India photorealistic 8K no text",
        "comparison": f"two modern Indian SUVs side by side dramatic comparison automotive photography no text",
        "suv":        f"{car_name} SUV mountain terrain cinematic automotive photography India photorealistic 8K no text",
        "default":    f"{car_name} cinematic automotive photography dramatic lighting Indian highway professional car shoot 8K no text",
    }
    prompt = prompts.get(format_type, prompts["default"])
    url = (f"https://image.pollinations.ai/prompt/{urllib.parse.quote(prompt)}"
           f"?width=1920&height=1080&nologo=true&enhance=true&seed={random.randint(1,99999)}")
    try:
        r = requests.get(url, timeout=20, stream=True)
        if r.status_code == 200:
            with open(output_path, "wb") as f:
                for chunk in r.iter_content(8192): f.write(chunk)
            log(f"  🎨 AI car image generated")
            return output_path
    except Exception as e:
        log(f"  ⚠️ Pollinations TT: {e}")
    return None


def add_end_screen_tt(youtube_service, video_id, duration_seconds):
    """Add subscribe + recent video end screen in last 20 seconds."""
    end_ms = max(0, int(duration_seconds) - 20) * 1000
    try:
        youtube_service.videos().update(
            part="endScreenContent",
            body={
                "id": video_id,
                "endScreenContent": {
                    "elements": [
                        {
                            "type": "SUBSCRIBE",
                            "position": {"cornerPosition": "TOP_RIGHT", "type": "CORNER"},
                            "startOffsetMs": str(end_ms),
                            "durationMs": "20000",
                        },
                        {
                            "type": "RECENT_UPLOAD",
                            "position": {"cornerPosition": "BOTTOM_LEFT", "type": "CORNER"},
                            "startOffsetMs": str(end_ms),
                            "durationMs": "20000",
                        },
                    ]
                }
            }
        ).execute()
        log("  ✅ End screen added")
    except Exception as e:
        log(f"  ⚠️ End screen: {e}")


TT_PLAYLIST_MAP_CONFIG = {
    "ev":         "TT_PLAYLIST_EV",
    "launch":     "TT_PLAYLIST_LAUNCHES",
    "comparison": "TT_PLAYLIST_COMPARE",
    "explainer":  "TT_PLAYLIST_EXPLAINER",
    "suv":        "TT_PLAYLIST_SUV",
    "news":       "TT_PLAYLIST_NEWS",
    "default":    "TT_PLAYLIST_DEFAULT",
}

def add_to_playlist_tt(youtube_service, video_id, format_type):
    """Auto-add video to correct format playlist."""
    env_key = TT_PLAYLIST_MAP_CONFIG.get(format_type, TT_PLAYLIST_MAP_CONFIG["default"])
    playlist_id = os.environ.get(env_key, "")
    if not playlist_id:
        return
    try:
        youtube_service.playlistItems().insert(
            part="snippet",
            body={"snippet": {
                "playlistId": playlist_id,
                "resourceId": {"kind": "youtube#video", "videoId": video_id}
            }}
        ).execute()
        log(f"  ✅ Added to {format_type} playlist")
    except Exception as e:
        log(f"  ⚠️ Playlist add: {e}")


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
                params={"query": query, "per_page": 5, "orientation": "landscape",
                "page": __import__("random").randint(1, 4)},
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
    os.makedirs(BGM_DIR, exist_ok=True)
    cached_bgm = os.path.join(BGM_DIR, f"{format_type}.mp3")
    if os.path.exists(cached_bgm):
        return cached_bgm

    bgm_path = f"bgm_{format_type}.mp3"
    if os.path.exists(bgm_path):
        return bgm_path

    profile = BGM_PROFILES.get(format_type, BGM_PROFILES["default"])
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
    log("  ⚠️ BGM generation failed")
    return None


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
    vf = (f"fps=30,scale=1920:1080:force_original_aspect_ratio=decrease,"
          f"pad=1920:1080:(ow-iw)/2:(oh-ih)/2,"
          f"fade=t=in:st=0:d=0.4,fade=t=out:st={INTRO_DURATION-0.4}:d=0.4")
    cmd.extend(["-vf", vf])
    if has_bell:
        cmd.extend(["-map", "0:v", "-map", "1:a"])
    else:
        cmd.extend(["-map", "0:v",
                    "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
                    "-map", "2:a"])
    cmd.extend(["-c:v", "libx264", "-preset", "medium", "-crf", "23",
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
             f"[0:v]fps=30,scale=1920:1080:force_original_aspect_ratio=decrease,"
             f"pad=1920:1080:(ow-iw)/2:(oh-ih)/2,"
             f"fade=t=in:st=0:d=0.4,"
             f"fade=t=out:st={OUTRO_DURATION-0.4}:d=0.4,"
             f"{text_filter}[v];"
             f"[1:a]apad=pad_dur={OUTRO_DURATION}[a]",
             "-map", "[v]", "-map", "[a]",
             "-c:v", "libx264", "-preset", "medium", "-crf", "23",
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
             "-vf", "fps=30,scale=1920:1080:force_original_aspect_ratio=decrease,"
                    "pad=1920:1080:(ow-iw)/2:(oh-ih)/2",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
             "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-ar", "44100", "-ac", "2",
             "-movflags", "+faststart",
             output_path], timeout=480)
    try: os.remove(flist)
    except: pass
    return r.returncode == 0


def build_video_filter(images, total_frames, fps=VIDEO_FPS, seed=0):
    rng = random.Random(seed)
    num = len(images)
    seg_frames = total_frames // num
    filters = []

    # Shuffle preset order per video for variety
    shuffled_presets = KB_PRESETS[:]
    rng.shuffle(shuffled_presets)

    for i in range(num):
        preset = shuffled_presets[i % len(shuffled_presets)]
        z_expr, x_expr, y_expr, label = preset
        # Tighter per-image duration — images change faster = less slideshow feel
        adj = max(int(seg_frames * rng.uniform(0.85, 1.05)), fps * 2)
        log(f"    Image {i+1}: {label}")
        filters.append(
            f"[{i}:v]loop=loop=-1:size=1:start=0,"
            f"scale=1920:1080:force_original_aspect_ratio=increase,"
            f"crop=1920:1080,"
            f"zoompan=z='{z_expr}':x='{x_expr}':y='{y_expr}':d={adj}:fps={fps}:s=1920x1080,"
            f"trim=0:{adj/fps:.2f},setpts=PTS-STARTPTS[v{i}]"
        )

    prev = "v0"
    xfade_dur = 1.2  # longer crossfade = cinematic, not choppy
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



def inject_pauses(text):
    """Convert [PAUSE_X] markers to natural ellipsis pauses for edge-tts."""
    text = text.replace("[PAUSE_LONG]",  "  ...  ")
    text = text.replace("[PAUSE_MED]",   " ... ")
    text = text.replace("[PAUSE_SHORT]", " .. ")
    return text


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

    script_text = inject_pauses(script_text)  # add natural breath pauses
    with open(script_file, "w", encoding="utf-8") as f:
        f.write(script_text)

    gender, voice_id, eq_filter = VOICE_ASSIGNMENT.get(
        format_type, VOICE_ASSIGNMENT["default"])
    log(f"🔊 Step 1/7 Voice SSML ({gender} — {voice_id})...")
    t0 = time.time()
    try:
        from ssml_processor import generate_ssml_audio, VOICE_EN_MALE, VOICE_EN_FEMALE
        ssml_voice = VOICE_EN_FEMALE if gender == "female" else VOICE_EN_MALE
        ok = generate_ssml_audio(
            script=script_text,
            output_path=human_file,
            voice=ssml_voice,
            language="en",
            call_llm_fn=lambda p, **kw: call_llm_gemini(p, max_retries=2),
            run_fn=run,
        )
        if not ok:
            raise RuntimeError("SSML pipeline returned False")
        dur = get_dur(human_file)
        log(f"  Voice SSML: {dur:.1f}s ({time.time()-t0:.0f}s)")
    except Exception as ssml_err:
        log(f"  ⚠️ SSML fallback ({ssml_err})")
        try:
            r = run(["edge-tts", "--file", script_file, "--voice", voice_id,
                     "--rate=-10%", "--pitch=+1Hz", "--write-media", voice_file],
                    timeout=300)
            if r.returncode != 0:
                log(f"❌ TTS error: {r.stderr[-200:]}"); return None
            r2 = run(["ffmpeg", "-y", "-i", voice_file, "-af", eq_filter, human_file])
            if r2.returncode != 0:
                shutil.copy(voice_file, human_file)
        except subprocess.TimeoutExpired:
            log("❌ TTS timeout"); return None
        dur = get_dur(human_file)
    log("🎧 Step 2/7 Voice EQ: handled by SSML pipeline")

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

    # Cap images: too many × long duration = zoompan CPU explosion
    # Long-form (~600s): max 6 images = ~100s each — manageable
    # Short-form (~120s): max 9 images = ~13s each — fine
    is_long = total_dur > 300
    max_imgs = 6 if is_long else 9
    if len(images) > max_imgs:
        images = images[:max_imgs]
        log(f"  Capped to {max_imgs} images (video={total_dur:.0f}s)")

    log(f"  Using {len(images)} images")
    fps = VIDEO_FPS
    seed = int(hashlib.md5(output_name.encode()).hexdigest()[:8], 16)
    total_frames = max(int(total_dur * fps), fps * 5)
    num_inputs, vfilter, vlabel = build_video_filter(images, total_frames, fps, seed)

    # Cinematic color grade: slight warmth + contrast boost + subtle vignette
    COLOR_GRADE = (
        f"[{vlabel}]"
        "eq=contrast=1.08:brightness=0.02:saturation=1.15,"
        "colorbalance=rs=0.04:gs=0.00:bs=-0.04:rm=0.03:gm=0.00:bm=-0.02,"
        "vignette=PI/5"
        "[graded]"
    )
    full_filter = vfilter + ";" + COLOR_GRADE
    out_label = "graded"

    # Dynamic timeout: 2.5× video duration, floor 300s, ceil 3000s
    encode_timeout = max(300, min(int(total_dur * 2.5), 3000))
    # Use veryfast for long videos — same quality visible on YouTube, 4× faster encode
    encode_preset = "veryfast" if is_long else "medium"
    encode_crf    = "22" if is_long else "20"
    log(f"  Encode: preset={encode_preset} crf={encode_crf} timeout={encode_timeout}s")

    cmd = ["ffmpeg", "-y"]
    for img in images:
        cmd.extend(["-loop", "1", "-t", str(total_dur + 2), "-i", img])
    cmd.extend(["-i", audio, "-filter_complex", full_filter,
                "-map", f"[{out_label}]", "-map", f"{num_inputs}:a",
                "-c:v", "libx264", "-preset", encode_preset, "-crf", encode_crf,
                "-pix_fmt", "yuv420p", "-c:a", "aac",
                "-ar", "44100", "-ac", "2",
                "-avoid_negative_ts", "make_zero", raw_file])
    r = run(cmd, timeout=encode_timeout)
    if r.returncode != 0:
        log("  ⚠️ Ken Burns failed — falling back to static slideshow")
        r = run(["ffmpeg", "-y", "-loop", "1", "-i", images[0], "-i", audio,
                 "-vf", "scale=1920:1080:force_original_aspect_ratio=decrease,"
                        "pad=1920:1080:(ow-iw)/2:(oh-ih)/2",
                 "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                 "-pix_fmt", "yuv420p", "-c:a", "aac",
                 "-ar", "44100", "-ac", "2", raw_file],
                timeout=max(300, int(total_dur * 1.5)))
        if r.returncode != 0:
            log("❌ Video encoding failed"); return None

    log("✍️  Step 5/7 Text overlays + subtitles...")
    overlay_filter = build_text_overlay(title_short, format_type)
    combined_vf = overlay_filter
    srt_created = False
    if english_subtitles:
        srt_path = generate_srt(english_subtitles, total_dur, srt_file)
        if srt_path:
            combined_vf = (
                f"{overlay_filter},subtitles={srt_path}:force_style='"
                "FontName=Arial,FontSize=28,"
                "PrimaryColour=&H00FFFFFF,"
                "OutlineColour=&H00000000,"
                "BackColour=&H80000000,"
                "Bold=1,Outline=3,Shadow=1,"
                "Alignment=2,MarginV=60'"
            )
            r = run(["ffmpeg", "-y", "-i", raw_file,
                     "-vf", combined_vf,
                     "-c:v", "libx264", "-preset", "medium", "-crf", "20",
                     "-c:a", "copy", "-movflags", "+faststart", overlay_file], timeout=max(240, int(total_dur * 1.5)))
            if r.returncode == 0:
                srt_created = True
                working = overlay_file
                log("  ✅ Overlays + subtitles burned in (single pass)")
            else:
                working = raw_file
        else:
            working = raw_file
    else:
        working = raw_file

    if not srt_created:
        r = run(["ffmpeg", "-y", "-i", raw_file,
                 "-vf", overlay_filter,
                 "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                 "-c:a", "copy", "-movflags", "+faststart", overlay_file], timeout=max(200, int(total_dur * 1.2)))
        working = overlay_file if r.returncode == 0 else raw_file

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
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
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
                    "-c:v", "libx264", "-preset", "medium", "-crf", "20",
                    "-c:a", "copy", "-movflags", "+faststart", wm_file], timeout=max(300, int(total_dur * 1.5)))
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

    log("📱 Shorts clip deferred to create_short_video()")
    total_dur = get_dur(video_file)

    mb = os.path.getsize(video_file) / (1024*1024)
    log(f"  ✅ {video_file} ({mb:.1f}MB, {total_dur:.1f}s)")

    for f in [script_file, voice_file, human_file, mixed_file, raw_file, overlay_file]:
        try:
            if os.path.exists(f): os.remove(f)
        except: pass

    return video_file, total_dur


def create_short_video(short_script, images_input, output_name, format_type="default",
                       hook_headline="", bgm_path=None):
    """Vertical-native Short from dedicated short script."""
    ensure_dirs()
    if not short_script or not short_script.strip():
        return None

    script_file = f"/tmp/{output_name}_short_script.txt"
    voice_file = f"/tmp/{output_name}_short_voice.mp3"
    human_file = f"/tmp/{output_name}_short_human.mp3"
    raw_file = f"/tmp/{output_name}_short_raw.mp4"
    short_file = f"{SHORTS_DIR}/{output_name}_short.mp4"

    script_text = inject_pauses(short_script)
    with open(script_file, "w", encoding="utf-8") as script_handle:
        script_handle.write(script_text)

    gender, voice_id, eq_filter = VOICE_ASSIGNMENT.get(
        format_type, VOICE_ASSIGNMENT["default"])
    log(f"📱 Creating Short ({gender} voice)...")
    run(["edge-tts", "--file", script_file, "--voice", voice_id,
         "--rate=" + RATE_BY_FORMAT_TT.get(format_type, "-8%"),
         "--pitch=+0Hz", "--write-media", voice_file], timeout=180)
    if not os.path.exists(voice_file):
        return None

    run(["ffmpeg", "-y", "-i", voice_file, "-af", eq_filter, human_file], timeout=120)
    audio = human_file if os.path.exists(human_file) else voice_file
    total_dur = min(get_dur(audio), 60.0)

    images = [img for img in (images_input or []) if os.path.exists(img)][:4]
    if not images and os.path.exists("image.png"):
        images = ["image.png"]
    if not images:
        return None

    fps = VIDEO_FPS
    total_frames = max(int(total_dur * fps), fps * 5)
    num_inputs, vfilter, vlabel = build_video_filter(images, total_frames, fps, seed=42)

    cmd = ["ffmpeg", "-y"]
    for img in images:
        cmd.extend(["-loop", "1", "-t", str(total_dur + 1), "-i", img])
    cmd.extend(["-i", audio, "-filter_complex", vfilter,
                "-map", f"[{vlabel}]", "-map", f"{num_inputs}:a",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
                "-pix_fmt", "yuv420p", "-c:a", "aac", "-t", str(total_dur),
                raw_file])
    run(cmd, timeout=300)

    safe_hook = (hook_headline or "CAR NEWS").replace("'", "").replace(":", " -")[:40]
    vertical_vf = (
        "scale=1080:1920:force_original_aspect_ratio=increase,"
        "crop=1080:1920,"
        f"drawtext=fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf:"
        f"text='{safe_hook}':fontsize=42:fontcolor=yellow@0.95:"
        f"x=(w-tw)/2:y=120:shadowcolor=black@0.9:shadowx=2:shadowy=2:"
        f"enable='between(t,0,4)'"
    )
    run(["ffmpeg", "-y", "-i", raw_file, "-vf", vertical_vf,
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "24",
         "-c:a", "copy", "-movflags", "+faststart", short_file], timeout=180)

    for temp_file in [script_file, voice_file, human_file, raw_file]:
        try:
            if os.path.exists(temp_file):
                os.remove(temp_file)
        except OSError:
            pass

    if os.path.exists(short_file):
        log(f"  ✅ Short: {short_file} ({total_dur:.1f}s)")
        return short_file
    return None


def resolve_video_mode(config, long_form=False, auto_long_form=False):
    """Pick short vs long video mode from config and CLI flags."""
    if long_form:
        return "long"
    score_data = config.get("monetization_score") or {}
    total_score = score_data.get("total") if isinstance(score_data, dict) else None
    priority_tier = config.get("priority_tier", 3)
    if auto_long_form and total_score is not None:
        try:
            if float(total_score) >= LONG_FORM_SCORE_THRESHOLD and priority_tier <= 2:
                return "long"
        except (TypeError, ValueError):
            pass
    mode = config.get("video_mode", "short")
    return mode if mode in ("short", "long") else "short"


def get_word_targets(video_mode):
    if video_mode == "long":
        return TARGET_MIN_WORDS_LONG, TARGET_MAX_WORDS_LONG
    return TARGET_MIN_WORDS, TARGET_MAX_WORDS


def score_title_candidate(title):
    """Heuristic CTR/SEO score for auto-picking best title."""
    if not title:
        return 0
    score = 0
    length = len(title)
    if length <= 60:
        score += 3
    elif length <= 70:
        score += 1
    if re.search(r"\d|₹|\$|%|km|bhp|kWh", title):
        score += 2
    if re.search(r"\b(why|how|vs|just|new|secret|shocking|game)\b", title, re.I):
        score += 2
    if "| Tech Meets Travel" in title:
        score -= 3
    return score


def select_best_title(candidates, fallback_topic):
    titles = [t.strip() for t in (candidates or []) if t and t.strip()]
    if not titles:
        return fallback_topic[:60]
    return max(titles, key=score_title_candidate)[:60]


def generate_chapters_from_script(duration_seconds, video_mode="short"):
    """Build chapter timestamps from duration and video mode."""
    duration_seconds = max(float(duration_seconds or 120), 30)
    if video_mode == "long":
        sections = [
            (0.00, "Hook"),
            (0.08, "Introduction"),
            (0.20, "Main Story"),
            (0.55, "Industry Impact"),
            (0.80, "Final Verdict"),
            (0.92, "Subscribe"),
        ]
    else:
        sections = [
            (0.00, "The Main Story"),
            (0.15, "Full Details"),
            (0.50, "Comparison and Context"),
            (0.75, "Honest Take"),
            (0.88, "Subscribe"),
        ]
    lines = []
    for fraction, label in sections:
        total_secs = int(duration_seconds * fraction)
        minutes, seconds = divmod(total_secs, 60)
        lines.append(f"{minutes}:{seconds:02d} {label}")
    return "\n".join(lines)


def append_ai_disclosure(description):
    desc = (description or "").strip()
    if "AI assistance" in desc or "AI assist" in desc:
        return desc
    return desc + AI_DISCLOSURE


def normalize_config_defaults(config):
    """Ensure extended config fields exist with sane defaults."""
    config.setdefault("news_summary", {})
    config.setdefault("monetization_score", {"total": 5})
    config.setdefault("title_candidates", [])
    config.setdefault("image_search_queries", [])
    config.setdefault("broll_queries", [])
    config.setdefault("video_mode", "short")
    config.setdefault("priority_tier", 3)
    return config


def discover_daily_config(long_form=False, auto_long_form=False):
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
        master_prompt=MASTER_SYSTEM_PROMPT,
        date=now.strftime("%Y-%m-%d"),
        day=now.strftime("%A"),
        car_news=car_news[:800],
        trends=trends[:300],
        recent_topics=", ".join(recent_topics[:5]) or "None yet",
    )
    if slot_note:
        prompt += f"\n\n{slot_note}"

    raw = call_llm(prompt, prefer="gemini", max_tokens=2000)
    try:
        data = parse_json_response(raw)
        data = normalize_config_defaults(data)
        data["topic"] = deduplicate_topic(data["topic"])
        data["video_mode"] = resolve_video_mode(data, long_form, auto_long_form)
        log(f"  📌 Topic: {data['topic']}")
        log(f"  🎭 Format: {data['format']}")
        log(f"  ⏱️  Mode: {data['video_mode']}")
        score = data.get("monetization_score", {})
        if isinstance(score, dict) and score.get("total"):
            log(f"  📊 Monetization score: {score.get('total')}")
        log(f"  💡 Reason: {data.get('reason','')}")
        return data
    except Exception as e:
        log(f"  ⚠️ JSON parse failed ({e}) — using random evergreen")
        fallback = {
            "topic":           deduplicate_topic(random.choice(EVERGREEN_TOPICS)),
            "format":          random.choice(CONTENT_FORMAT_TYPES),
            "pexels_keyword":  "car",
            "hook_angle":      "Here's the biggest car story you need to know today.",
            "reason":          "Fallback",
            "video_mode":      "long" if long_form else "short",
            "priority_tier":   3,
            "news_summary":    {},
            "monetization_score": {"total": 5},
            "title_candidates": [],
            "image_search_queries": ["electric car india", "car showroom"],
            "broll_queries": ["highway driving car"],
        }
        return normalize_config_defaults(fallback)


def generate_script(topic, format_type, hook_angle, voice_gender, video_mode="short"):
    min_words, max_words = get_word_targets(video_mode)
    prompt_template = SCRIPT_PROMPT_LONG if video_mode == "long" else SCRIPT_PROMPT_SHORT
    log(f"  📝 Script ({format_type}, {voice_gender}, {video_mode}, {min_words}-{max_words} words)...")
    t0 = time.time()

    def build_prompt(attempt=0):
        note = ""
        if attempt > 0:
            note = (
                f"\n\nCRITICAL — ATTEMPT {attempt+1}: Previous response was too short. "
                f"You MUST write {min_words}-{max_words} words. Write FULL complete sentences."
            )
        return prompt_template.format(
            master_prompt=MASTER_SYSTEM_PROMPT,
            topic=topic,
            format_type=format_type,
            hook_angle=hook_angle,
            voice_gender=voice_gender,
            target_min_words=min_words,
            target_max_words=max_words,
        ) + note

    text = ""
    for attempt in range(3):
        resp = call_llm(build_prompt(attempt))
        words = len(resp.strip().split())
        log(f"  Attempt {attempt+1}: {words} words")
        if words >= min_words:
            text = resp.strip()
            break
        text = resp.strip()
        if attempt < 2:
            log(f"  Too short ({words} < {min_words}) — retrying...")
            time.sleep(3)

    if len(text.split()) > max_words:
        text = " ".join(text.split()[:max_words])

    if not text.strip():
        log("  ❌ Script generation failed — all attempts returned empty")
        return ""

    ok, cleaned, reason = validate_script(text, min_words=min_words)
    if not ok:
        log(f"  ⚠️ Script validation failed ({reason}) — retrying once...")
        resp = call_llm(build_prompt(1))
        ok2, cleaned2, reason2 = validate_script(resp.strip(), min_words=min_words)
        if ok2:
            text = cleaned2
        else:
            log(f"  ⚠️ Script still invalid ({reason2}) — using best effort")
            text = cleaned if cleaned else text.strip()
    else:
        text = cleaned

    log(f"  ✅ Script: {len(text.split())} words in {time.time()-t0:.0f}s")
    return text


def generate_subtitles(script):
    clean_script = re.sub(r"\[PAUSE_\w+\]", " ", script or "")
    try:
        prompt = SUBTITLE_PROMPT.format(script=clean_script[:3500])
        raw = call_llm_gemini(prompt, max_retries=3)  # Gemini — avoids Groq race in Phase 2
        lines = [line.strip() for line in raw.strip().split("\n") if line.strip()]
        if lines:
            return [f"{index}\\n{line}" for index, line in enumerate(lines, 1)]
    except Exception as subtitle_error:
        log(f"  ⚠️ LLM subtitles failed ({subtitle_error}) — using wrap fallback")

    import textwrap
    wrapped = textwrap.wrap(" ".join(clean_script.split()), width=40)
    return [f"{index}\\n{line}" for index, line in enumerate(wrapped, 1)]


def generate_content_package(topic, format_type, hook_angle, script, config, video_mode="short"):
    """Script-aware metadata and full content package."""
    log("  📦 Generating content package (script-aligned)...")
    news_summary = config.get("news_summary", {})
    prompt = CONTENT_PACKAGE_PROMPT.format(
        master_prompt=MASTER_SYSTEM_PROMPT,
        topic=topic,
        format_type=format_type,
        hook_angle=hook_angle,
        video_mode=video_mode,
        news_summary=json.dumps(news_summary, ensure_ascii=False),
        script=script[:6000],
    )
    raw = call_llm_groq(prompt, max_retries=3)
    try:
        package = parse_json_response(raw)
    except Exception as package_error:
        log(f"  ⚠️ Content package parse failed ({package_error}) — fallback metadata")
        package = generate_metadata(topic, format_type, hook_angle)

    candidates = package.get("title_candidates") or config.get("title_candidates") or []
    if not package.get("title"):
        package["title"] = select_best_title(candidates, topic)
    elif candidates:
        best = select_best_title(candidates + [package["title"]], topic)
        package["title"] = best

    if not package.get("thumbnail_headline"):
        package["thumbnail_headline"] = " ".join(package["title"].split()[:4]).upper()

    if not package.get("short_script"):
        package["short_script"] = generate_short_script(topic, hook_angle, script)

    package.setdefault("keywords", "")
    package.setdefault("community_post", f"What do you think about {topic}? Comment below.")
    package.setdefault("future_video_ideas", [])
    package.setdefault("short_title", package["title"][:70])
    package["title_candidates"] = candidates
    package["news_summary"] = news_summary
    package["monetization_score"] = config.get("monetization_score", {})
    package["video_mode"] = video_mode
    package["image_search_queries"] = config.get("image_search_queries", [])
    package["broll_queries"] = config.get("broll_queries", [])
    return package


def generate_short_script(topic, hook_angle, main_script):
    """Dedicated 45–60s Shorts script."""
    excerpt = re.sub(r"\[PAUSE_\w+\]", " ", main_script or "")[:1200]
    prompt = SHORT_SCRIPT_PROMPT.format(
        master_prompt=MASTER_SYSTEM_PROMPT,
        topic=topic,
        hook_angle=hook_angle,
        script_excerpt=excerpt,
    )
    try:
        text = call_llm_groq(prompt, max_retries=2).strip()
        if len(text.split()) >= 60:
            return text
    except Exception:
        pass
    words = excerpt.split()[:100]
    return (
        f"{hook_angle} [PAUSE_SHORT] "
        f"{' '.join(words)} [PAUSE_MED] "
        "Follow for daily car and EV news. What would you pick — comment below."
    )


def finalize_metadata(metadata, duration_seconds, source_citation=""):
    """Add chapters, AI disclosure, and source to description."""
    video_mode = metadata.get("video_mode", "short")
    chapters = generate_chapters_from_script(duration_seconds, video_mode)
    metadata["chapters"] = chapters

    description = metadata.get("description", "")
    if chapters and chapters not in description:
        description = description.rstrip() + "\n\n📑 Chapters:\n" + chapters
    if source_citation and source_citation not in description:
        description = description.rstrip() + f"\n\n📊 {source_citation}"
    metadata["description"] = append_ai_disclosure(description)
    metadata["duration_seconds"] = duration_seconds
    return metadata


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


def get_source_citation(topic, news_summary=None):
    if isinstance(news_summary, dict):
        source_name = news_summary.get("source", "").strip()
        if source_name and not source_name.startswith("http"):
            return f"Source: {source_name[:45]}"

    readable_sources = {
        "tesla": "Source: Tesla / Reuters",
        "byd": "Source: BYD / Autocar",
        "tata": "Source: Tata Motors",
        "mahindra": "Source: Mahindra Auto",
        "hyundai": "Source: Hyundai India",
        "maruti": "Source: Maruti Suzuki",
        "kia": "Source: Kia India",
        "mg": "Source: MG Motor India",
        "ev": "Source: EV Industry Reports",
        "electric": "Source: EV Industry Reports",
    }
    topic_lower = topic.lower()
    for keyword, label in readable_sources.items():
        if keyword in topic_lower:
            return label
    return "Source: Autocar India"


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
    """Build YouTube API service with auto scope-refresh."""
    import pickle, base64, os
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    REQUIRED_SCOPES = {
        "https://www.googleapis.com/auth/youtube",
        "https://www.googleapis.com/auth/youtube.upload",
        "https://www.googleapis.com/auth/youtube.force-ssl",
    }

    creds = None
    b64 = os.environ.get("YOUTUBE_TOKEN_BASE64", "")

    if b64:
        try:
            creds = pickle.loads(base64.b64decode(b64))
        except Exception as e:
            log(f"  ⚠️ Token decode failed: {e}")
            return None

    if not creds:
        token_file = "youtube_token.pickle"
        if os.path.exists(token_file):
            with open(token_file, "rb") as f:
                creds = pickle.load(f)

    if not creds:
        log("  ⚠️ No YouTube credentials found")
        return None

    # Refresh if expired
    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            log("  ✅ Token refreshed")
        except Exception as e:
            log(f"  ⚠️ Token refresh failed: {e}")
            return None

    # Check if force-ssl scope is present (needed for comments)
    token_scopes = set(getattr(creds, "scopes", []) or [])
    missing = REQUIRED_SCOPES - token_scopes
    if "https://www.googleapis.com/auth/youtube.force-ssl" in missing:
        log("  ℹ️ Token missing youtube.force-ssl — run setup_youtube_secrets.py locally to re-auth")
        # Still usable for upload, just not comments

    if not creds.valid:
        log("  ⚠️ Token invalid and cannot be refreshed — re-run auth setup")
        return None

    try:
        return build("youtube", "v3", credentials=creds)
    except Exception as e:
        log(f"  ⚠️ YouTube API build failed: {e}")
        return None


def validate_script(text, min_words=200):
    if not text or len(text.split()) < min_words * 0.7:
        return False, text, "too short"
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"\*{1,3}([^*]+)\*{1,3}", r"\1", text)
    text = re.sub(r"^[-*]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\d+\.\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"```[^`]*```", "", text, flags=re.DOTALL)
    filler = ("in this video", "without further ado", "smash that like")
    if any(phrase in text.lower() for phrase in filler):
        return False, text.strip(), "filler detected"
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



def generate_thumbnail(title, format_type, output_name, bg_image_path=None,
                       thumbnail_headline="", thumbnail_concept=""):
    """Format-specific thumbnail with CTR-optimized headline."""
    display_title = thumbnail_headline or title
    try:
        from PIL import Image, ImageDraw, ImageFilter, ImageEnhance
        import os, math, random
        os.makedirs(THUMBNAIL_DIR, exist_ok=True)
        W,H=1280,720
        cfg=TT_THUMB_FORMATS.get(format_type, TT_THUMB_FORMATS["default"])
        # Photo background if available
        if bg_image_path and os.path.exists(str(bg_image_path)):
            try:
                bg  = Image.open(bg_image_path).convert("RGB").resize((W,H),Image.LANCZOS)
                bg  = bg.filter(ImageFilter.GaussianBlur(radius=12))
                bg  = ImageEnhance.Brightness(bg).enhance(0.22)
                tint= Image.new("RGB",(W,H),cfg["c1"])
                img = Image.blend(bg, tint, alpha=0.45)
            except Exception:
                img=Image.new("RGB",(W,H),cfg["c1"])
        else:
            img=Image.new("RGB",(W,H),cfg["c1"])
        d=ImageDraw.Draw(img)
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
            _draw_animated_car(d,W-200,H//2+20,1.2,acc,"sedan")
            lines=_tt_wrap(display_title,16); ty=125
            for i,ln in enumerate(lines):
                fs=92 if i==0 else 66; _tt_shadow(d,45,ty,ln,_tt_font(fs),(255,255,255) if i==0 else (220,220,220)); ty+=fs+14
            d.rectangle([45,ty+6,580,ty+14],fill=(225,5,30))
        elif fmt=="launch":
            grad(); cx2,cy2=int(W*.7),H//2
            for r in range(700,0,-5):
                t=1-r/700; d.ellipse([cx2-r,cy2-r,cx2+r,cy2+r],outline=(0,min(255,int(t*80)),min(255,int(t*50))),width=1)
            _draw_animated_car(d,int(W*.68),H//2+15,1.4,acc,"sedan")
            d.polygon([(W-200,0),(W,0),(W,200)],fill=acc)
            d.text((W-108,28),"NEW",font=_tt_font(34),fill=(255,255,255))
            d.text((W-142,68),"LAUNCH",font=_tt_font(24),fill=(255,255,255))
            lines=_tt_wrap(display_title,17); ty=155
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
            _draw_animated_car(d,W//4,H//2+10,1.1,acc,"sedan")
            _draw_animated_car(d,W*3//4,H//2+10,1.1,(255,130,0),"suv")
            parts=display_title.lower().split(" vs ") if " vs " in display_title.lower() else ["",""]
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
            _draw_animated_car(d,int(W*.67),H//2+10,1.35,acc,"ev")
            d.rounded_rectangle([42,42,190,96],radius=12,fill=acc)
            d.text((116,69),"EV NEWS",font=_tt_font(28),fill=(255,255,255),anchor="mm")
            lines=_tt_wrap(display_title,17); ty=118
            for i,ln in enumerate(lines):
                fs=82 if i==0 else 60; _tt_shadow(d,42,ty,ln,_tt_font(fs),(255,255,255) if i==0 else (160,240,225)); ty+=fs+14
            d.rectangle([42,ty+5,480,ty+13],fill=acc)
        elif fmt=="explainer":
            for y in range(H):
                t=y/H; d.line([(0,y),(W,y)],fill=(int(12+t*20),int(10+t*14),int(5+t*8)))
            d.rectangle([0,0,W,88],fill=(28,22,5)); d.rectangle([0,80,W,92],fill=(215,162,0))
            d.text((W//2,44),"💡 EXPLAINED",font=_tt_font(36),fill=(215,162,0),anchor="mm")
            d.rectangle([0,88,14,H],fill=(215,162,0))
            _draw_animated_car(d,W-165,H//2+30,1.0,acc,"sedan")
            lines=_tt_wrap(display_title,19); ty=120
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
            _draw_animated_car(d,int(W*.66),H//2,1.35,acc,"suv")
            d.rectangle([42,42,225,98],fill=acc); d.rectangle([42,42,225,98],outline=(255,160,50),width=3)
            d.text((133,70),"4×4 SUV",font=_tt_font(32),fill=(255,255,255),anchor="mm")
            lines=_tt_wrap(display_title,16); ty=125
            for i,ln in enumerate(lines):
                fs=84 if i==0 else 62; _tt_shadow(d,42,ty,ln,_tt_font(fs),(255,255,255) if i==0 else (240,180,110)); ty+=fs+14
            d.rectangle([42,ty+6,470,ty+14],fill=acc)

        d.text((38,H-44),"@tech_meets_travel",font=_tt_font(22,False),fill=(155,158,172))
        out=f"{THUMBNAIL_DIR}/{output_name}_thumb.png"
        img.save(out); log(f"  ✅ Thumbnail: {out}")
        return out
    except Exception as e:
        log(f"  ⚠️ Thumbnail: {e}"); return None



def upload_short_to_youtube(short_path, short_metadata, youtube):
    """Upload Short to YouTube with dedicated Shorts metadata."""
    if not short_path or not os.path.exists(short_path):
        return None
    try:
        short_title = short_metadata.get("short_title") or short_metadata.get("title", "Car News")
        if "#Shorts" not in short_title:
            short_title = (short_title[:88] + " #Shorts") if len(short_title) <= 88 else short_title[:86] + "… #Shorts"

        short_desc = short_metadata.get("short_description") or short_metadata.get("description", "")
        short_desc_lines = short_desc.split("\n")[:4]
        short_desc = "\n".join(short_desc_lines) + "\n\n#Shorts #CarNews #EV"
        short_desc = append_ai_disclosure(short_desc)

        tags = [t.strip() for t in short_metadata.get("tags", "").split(",") if t.strip()][:25]
        if "Shorts" not in tags:
            tags.insert(0, "Shorts")
        if "YouTubeShorts" not in tags:
            tags.insert(1, "YouTubeShorts")

        body = {
            "snippet": {
                "title":       short_title[:100],
                "description": short_desc[:5000],
                "tags":        tags[:30],
                "categoryId":  "2",
                "defaultLanguage": "ta",
                "defaultAudioLanguage": "ta",
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
            "categoryId":  "2",   # Autos & Vehicles
            "defaultLanguage": "ta",
            "defaultAudioLanguage": "ta",
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

        # End screen + playlist
        video_dur = metadata.get("duration_seconds", 120)
        add_end_screen_tt(youtube, vid, video_dur)
        add_to_playlist_tt(youtube, vid, metadata.get("format", "default"))

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


def fetch_free_media(topic, format_type, output_dir, count=5, image_search_queries=None):
    """Master fetcher — tries all free sources, returns best available images."""
    os.makedirs(output_dir, exist_ok=True)
    all_images = []
    queries = [q.strip() for q in (image_search_queries or []) if q and q.strip()]
    if not queries:
        queries = [topic]

    log(f"  🔍 Fetching free media for: {topic[:50]}...")

    for query in queries[:5]:
        if len(all_images) >= count:
            break
        wiki_images = fetch_wikimedia_car_images(query, format_type, output_dir,
                                                  count=max(1, count - len(all_images)))
        for img in wiki_images:
            if img not in all_images:
                all_images.append(img)

    if len(all_images) < count:
        for query in queries[:3]:
            if len(all_images) >= count:
                break
            pix_images = fetch_pixabay_car_images(query, format_type, output_dir,
                                                   count=count - len(all_images))
            for img in pix_images:
                if img not in all_images:
                    all_images.append(img)

    if len(all_images) < count:
        pexels_kw = {
            "ev": "electric car", "suv": "suv india",
            "news": "car india", "launch": "car showroom",
            "comparison": "cars road", "explainer": "car dashboard",
        }.get(format_type, "car india")
        import re as _re_tt
        _car_match = _re_tt.search(
            r"\b(Tata|Maruti|Hyundai|Kia|Mahindra|Toyota|Honda|Skoda|MG|Nexon|Creta|Seltos|Punch|Brezza|Safari|Harrier|XUV|Scorpio|Innova|Fortuner|Tesla|BYD)\b",
            topic, _re_tt.IGNORECASE)
        if _car_match:
            pexels_kw = _car_match.group(1).lower() + " car"

        search_terms = queries + [pexels_kw]
        for term in search_terms[:4]:
            if len(all_images) >= count:
                break
            pexels_images = fetch_pexels_images(term, output_dir,
                                                 count=count - len(all_images))
            for img in pexels_images:
                if img not in all_images:
                    all_images.append(img)

    log(f"  ✅ Free media: {len(all_images)} images total")
    return all_images[:count]




# ═══════════════════════════════════════════════════════════════════════
# UNIVERSAL SCENE GENERATOR — pure Pillow, zero network, always works
# Generates 6-8 images per video using topic + deity/format as seed
# ═══════════════════════════════════════════════════════════════════════

def generate_video_scenes(output_name, topic="", scene_type="default",
                          num_scenes=6, channel="generic"):
    """Generate rich animated scene images. Pure Pillow — no network needed.

    channel: "am" = devotional, "nn" = finance, "tt" = cars, "generic"
    scene_type: format or deity or topic category
    Returns list of image paths.
    """
    from PIL import Image, ImageDraw, ImageFont
    import os, math, random, hashlib

    seed = int(hashlib.md5((output_name + topic).encode()).hexdigest()[:8], 16)
    random.seed(seed)
    W, H = 1920, 1080

    scene_dir = os.path.join(PEXELS_DIR, output_name)
    os.makedirs(scene_dir, exist_ok=True)

    def sf(size, bold=True):
        try:
            p = ENG_BOLD_FONT if bold else ENG_REG_FONT
            return ImageFont.truetype(p, size)
        except: return ImageFont.load_default()

    def tf(size):
        try: return ImageFont.truetype(TAMIL_BOLD_FONT, size)
        except: return ImageFont.load_default()

    def grad(d, c1, c2, w=W, h=H, axis='v'):
        for i in range(h if axis=='v' else w):
            t = i / (h if axis=='v' else w)
            col = tuple(int(c1[j]+(c2[j]-c1[j])*t) for j in range(3))
            if axis=='v': d.line([(0,i),(w,i)], fill=col)
            else: d.line([(i,0),(i,h)], fill=col)

    def glow(d, cx, cy, r_max, color, steps=15):
        for r in range(r_max, 0, -r_max//steps):
            t = 1-r/r_max
            a = int(t*28)
            d.ellipse([cx-r,cy-r,cx+r,cy+r], fill=(*color[:3],a))

    paths = []

    # ── Select scene palette based on channel ────────────────────────
    if channel == "am":
        palettes = [
            {"c1":(45,8,0),  "c2":(10,2,0),  "acc":(255,125,0),  "name":"dawn"},
            {"c1":(5,0,30),  "c2":(1,0,8),   "acc":(140,85,255), "name":"dusk"},
            {"c1":(0,20,5),  "c2":(0,5,1),   "acc":(0,190,70),   "name":"forest"},
            {"c1":(40,0,22), "c2":(12,0,6),  "acc":(255,50,160), "name":"temple"},
            {"c1":(42,30,0), "c2":(12,8,0),  "acc":(255,200,0),  "name":"golden"},
            {"c1":(0,22,40), "c2":(0,6,12),  "acc":(0,170,210),  "name":"ocean"},
        ]
    elif channel == "nn":
        palettes = [
            {"c1":(28,3,3),  "c2":(50,6,6),  "acc":(225,35,35),  "name":"alert"},
            {"c1":(3,14,30), "c2":(5,24,52), "acc":(50,142,255), "name":"trust"},
            {"c1":(2,20,5),  "c2":(4,38,8),  "acc":(0,190,75),   "name":"growth"},
            {"c1":(22,14,3), "c2":(38,25,5), "acc":(255,160,0),  "name":"warm"},
            {"c1":(18,3,24), "c2":(30,5,40), "acc":(175,75,255), "name":"premium"},
            {"c1":(8,7,4),   "c2":(18,14,8), "acc":(215,162,0),  "name":"gold"},
        ]
    else:  # tt / generic
        palettes = [
            {"c1":(5,10,22), "c2":(18,8,38), "acc":(232,0,28),   "name":"speed"},
            {"c1":(4,22,5),  "c2":(2,8,2),   "acc":(0,215,95),   "name":"launch"},
            {"c1":(6,6,28),  "c2":(2,2,16),  "acc":(50,148,255), "name":"tech"},
            {"c1":(0,18,22), "c2":(0,6,10),  "acc":(0,228,198),  "name":"ev"},
            {"c1":(20,10,3), "c2":(7,3,0),   "acc":(255,138,0),  "name":"offroad"},
            {"c1":(8,6,4),   "c2":(20,14,8), "acc":(255,198,0),  "name":"classic"},
        ]

    scene_list = ["hero", "ambient", "detail", "wide", "close", "atmosphere",
                  "texture", "perspective"][:num_scenes]

    for i, scene_name in enumerate(scene_list):
        out = os.path.join(scene_dir, f"{i:02d}_{scene_name}.png")
        if os.path.exists(out) and os.path.getsize(out) > 5000:
            paths.append(out); continue

        pal = palettes[i % len(palettes)]
        c1, c2, acc = pal["c1"], pal["c2"], pal["acc"]
        rs = seed + i * 6547  # different seed per scene
        random.seed(rs)

        img = Image.new("RGB", (W,H), c1)
        d   = ImageDraw.Draw(img)
        grad(d, c1, c2)

        # ── Scene-specific elements ──────────────────────────────────

        if scene_name == "hero":
            # Central glow with radiating lines
            cx, cy = W//2, H//2
            glow(d, cx, cy, 500, acc, 20)
            for angle in range(0, 360, 12):
                rad = math.radians(angle + rs%30)
                length = random.randint(300, 700)
                x2 = cx + int(math.cos(rad)*length)
                y2 = cy + int(math.sin(rad)*length)
                d.line([(cx,cy),(x2,y2)], fill=(*acc,6+random.randint(0,8)), width=1)
            glow(d, cx, cy, 200, acc, 12)
            # Channel-specific symbol
            if channel == "am":
                try: d.text((cx,cy-40), "ॐ", font=sf(220), fill=(*acc,60), anchor="mm")
                except: pass
            elif channel == "nn":
                try: d.text((cx,cy-30), "₹", font=sf(260), fill=(*acc,50), anchor="mm")
                except: pass
            else:
                # Car silhouette
                s = 1.8
                body = [(cx-int(120*s),cy+int(25*s)),(cx-int(122*s),cy-int(8*s)),
                        (cx-int(95*s),cy-int(35*s)),(cx-int(30*s),cy-int(62*s)),
                        (cx+int(45*s),cy-int(62*s)),(cx+int(105*s),cy-int(30*s)),
                        (cx+int(122*s),cy-int(8*s)),(cx+int(124*s),cy+int(25*s))]
                d.polygon(body, fill=(22,25,38))
                d.polygon(body, outline=acc, width=2)

        elif scene_name == "ambient":
            # Particle field
            for _ in range(120):
                px = random.randint(0,W); py = random.randint(0,H)
                r = random.choice([1,1,1,2,2,3])
                a = random.randint(40,160)
                d.ellipse([px-r,py-r,px+r,py+r], fill=(*acc,a))
            # Horizontal streaks
            for _ in range(30):
                y2 = random.randint(0,H)
                ln = random.randint(50,400)
                x2 = random.randint(0,W)
                a = random.randint(15,50)
                d.line([(x2,y2),(x2+ln,y2)], fill=(*acc,a), width=1)
            # Central glow subtle
            glow(d, W//2+random.randint(-200,200), H//2+random.randint(-100,100), 300, acc, 8)

        elif scene_name == "detail":
            # Grid pattern with focal point
            for x in range(0,W,90):
                a = max(8, 30 - abs(x-W//2)//30)
                d.line([(x,0),(x,H)], fill=(*acc,a), width=1)
            for y in range(0,H,90):
                a = max(8, 30 - abs(y-H//2)//20)
                d.line([(0,y),(W,y)], fill=(*acc,a), width=1)
            # Focal circle
            cx2 = W//2 + random.randint(-200,200)
            cy2 = H//2 + random.randint(-80,80)
            glow(d, cx2, cy2, 280, acc, 15)
            for r in [200,160,120,80]:
                d.ellipse([cx2-r,cy2-r,cx2+r,cy2+r], outline=(*acc,40+r//10), width=1)

        elif scene_name == "wide":
            # Panoramic horizontal layers
            num_layers = random.randint(4,7)
            for layer in range(num_layers):
                t = layer/num_layers
                y1 = int(H*t); y2 = int(H*(t+1/num_layers))+2
                darkness = 0.6 + t*0.4
                col = tuple(int(c1[j]*darkness + acc[j]*(1-darkness)*0.15) for j in range(3))
                d.rectangle([0,y1,W,y2], fill=col)
            # Horizon glow
            hy = H//2 + random.randint(-50,50)
            for r in range(H//3, 0, -H//60):
                t = 1-r/(H//3)
                a = int(t*12)
                d.ellipse([W//2-r*2,hy-r//2,W//2+r*2,hy+r//2], fill=(*acc,a))

        elif scene_name == "close":
            # Abstract close-up texture
            # Diagonal bands
            for i in range(-H, W+H, 80):
                a = random.randint(5,18)
                d.polygon([(i,0),(i+60,0),(i+60+H,H),(i+H,H)], fill=(*acc,a))
            # Dense particles in zone
            zx, zy = random.randint(W//4,W*3//4), random.randint(H//4,H*3//4)
            for _ in range(80):
                px = zx + random.randint(-250,250)
                py = zy + random.randint(-150,150)
                r = random.randint(2,6)
                a = random.randint(60,200)
                d.ellipse([px-r,py-r,px+r,py+r], fill=(*acc,a))

        elif scene_name == "atmosphere":
            # Misty layers from bottom
            for layer in range(8):
                t = layer/8
                y_base = H - int(layer * H//10)
                for y in range(max(0,y_base-80), min(H,y_base+80)):
                    tt = 1-abs(y-y_base)/80
                    a = int(tt * (20+layer*5))
                    col = tuple(min(255,c+a) for c in c1)
                    d.line([(0,y),(W,y)], fill=col)
            # Top vignette
            for y in range(H//4):
                t = 1-y/(H//4)
                col = tuple(int(c*t*0.8) for c in c1)
                d.line([(0,y),(W,y)], fill=col)
            # Floating orbs
            for _ in range(5):
                ox = random.randint(100,W-100)
                oy = random.randint(H//4,H*3//4)
                r = random.randint(30,80)
                glow(d, ox, oy, r*3, acc, 6)

        elif scene_name == "texture":
            # Geometric pattern
            size = random.choice([60,80,100])
            for row in range(H//size+2):
                for col2 in range(W//size+2):
                    x = col2*size + (row%2)*size//2
                    y = row*size
                    a = random.randint(5,22)
                    shape = (row+col2+rs) % 3
                    if shape == 0:
                        d.ellipse([x,y,x+size-4,y+size-4], outline=(*acc,a), width=1)
                    elif shape == 1:
                        d.rectangle([x+4,y+4,x+size-8,y+size-8], outline=(*acc,a), width=1)
                    else:
                        d.polygon([(x+size//2,y),(x+size,y+size),(x,y+size)],
                                  outline=(*acc,a), width=1)
            glow(d, W//2, H//2, 400, acc, 10)

        else:  # perspective
            # Tunnel / vanishing point
            cx3, cy3 = W//2+random.randint(-100,100), H//2+random.randint(-50,50)
            for r in range(600, 0, -20):
                t = 1-r/600; a = int(t*15)
                ratio = 0.6 + t*0.4
                d.ellipse([cx3-int(r*ratio),cy3-int(r*0.6),
                           cx3+int(r*ratio),cy3+int(r*0.6)],
                          outline=(*acc,a), width=1)
            glow(d, cx3, cy3, 120, acc, 10)
            # Radiating perspective lines
            for angle2 in range(0, 360, 20):
                rad2 = math.radians(angle2)
                length2 = 800
                x2 = cx3+int(math.cos(rad2)*length2)
                y2 = cy3+int(math.sin(rad2)*length2)
                d.line([(cx3,cy3),(x2,y2)], fill=(*acc,6), width=1)

        img.save(out)
        paths.append(out)

    log(f"  🎨 {len(paths)} scenes generated ({channel}/{scene_type})")
    return paths


def cleanup_old_artifacts(max_age_hours=24):
    """Delete generated artifacts older than max_age_hours to keep runner disk clean."""
    import time as _t
    now = _t.time()
    cutoff = now - (max_age_hours * 3600)
    removed = 0
    dirs_to_clean = [OUTPUT_DIR, SHORTS_DIR, METADATA_DIR, SCRIPTS_DIR,
                     PEXELS_DIR, SUBS_DIR, "/tmp"]
    extensions_to_clean = {".mp4", ".mp3", ".jpg", ".jpeg", ".png",
                            ".srt", ".txt", ".json"}
    for d in dirs_to_clean:
        if not os.path.exists(d):
            continue
        for root, dirs, files in os.walk(d):
            for fname in files:
                fpath = os.path.join(root, fname)
                try:
                    if (os.path.splitext(fname)[1].lower() in extensions_to_clean
                            and os.path.getmtime(fpath) < cutoff):
                        os.remove(fpath)
                        removed += 1
                except Exception:
                    pass
    log(f"🧹 Cleanup: removed {removed} artifacts older than {max_age_hours}h")


def safe_process_video(topic=None, format_type=None, upload=False, privacy="public",
                       long_form=False, auto_long_form=False):
    ensure_dirs()
    cleanup_old_artifacts(max_age_hours=24)
    t_start = time.time()

    if topic:
        config = normalize_config_defaults({
            "topic":          topic,
            "format":         format_type or random.choice(CONTENT_FORMAT_TYPES),
            "pexels_keyword": "car",
            "hook_angle":     "Here's the biggest car story you need to know today.",
            "video_mode":     "long" if long_form else "short",
        })
    else:
        config = discover_daily_config(long_form=long_form, auto_long_form=auto_long_form)

    topic_val = config["topic"]
    fmt = config["format"]
    hook_angle = config.get("hook_angle", "")
    video_mode = resolve_video_mode(config, long_form, auto_long_form)
    config["video_mode"] = video_mode
    gender, _, _ = VOICE_ASSIGNMENT.get(fmt, VOICE_ASSIGNMENT["default"])
    image_queries = config.get("image_search_queries") or []
    scene_count = 14 if video_mode == "long" else 8
    stock_count = 8 if video_mode == "long" else 6

    log(f"{'='*55}")
    log(f"  {CHANNEL_NAME}")
    log(f"  Topic: {topic_val}")
    log(f"  Format: {fmt} | Voice: {gender} | Mode: {video_mode}")
    log(f"{'='*55}")

    save_used_topic(topic_val)
    safe_name = hashlib.md5(topic_val.encode()).hexdigest()[:10]
    img_dir = os.path.join(PEXELS_DIR, safe_name)

    # ── PARALLEL PHASE 1: Script + Images + BGM all at once ──────────
    log("🚀 Phase 1: Script + Images + BGM in parallel...")

    def fetch_all_images():
        imgs = list(fetch_free_media(
            topic_val, fmt, img_dir, count=stock_count,
            image_search_queries=image_queries))
        poll_path = os.path.join(img_dir, "ai_car.jpg")
        car_name = topic_val.split()[0] if topic_val else "Electric SUV"
        try:
            poll = fetch_pollinations_image_tt(car_name, fmt, poll_path)
            if poll:
                imgs = [poll] + imgs
                log("  🎨 AI car image generated")
        except Exception as e:
            log(f"  ⚠️ Pollinations skipped: {e}")
        return imgs

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        script_future = pool.submit(
            generate_script, topic_val, fmt, hook_angle, gender, video_mode)
        images_future = pool.submit(fetch_all_images)
        bgm_future    = pool.submit(ensure_bgm, fmt)

        script = script_future.result()
        images = images_future.result()
        bgm_path = bgm_future.result()

    if not script or not script.strip():
        log("  ❌ Script empty — aborting pipeline")
        return None

    # Fallback scenes if images thin
    if len(images) < 3:
        log("  🎨 Stock thin — adding format-specific scenes...")
        if len(images) < 2:
            scene_paths = generate_scenes(safe_name, fmt, num_scenes=min(scene_count, 5))
            images.extend(scene_paths)
        else:
            fallback_scenes = generate_video_scenes(
                safe_name, topic=topic_val, scene_type=fmt,
                num_scenes=max(3, scene_count - len(images)), channel="tt")
            images.extend(fallback_scenes)
    if not images:
        ensure_fallback_image()
        images = ["image.png"] if os.path.exists("image.png") else []

    log(f"  📦 Total images: {len(images)}")

    # ── PARALLEL PHASE 2: Subtitles + Metadata + Source citation ─────
    log("🚀 Phase 2: Subtitles + Metadata + Source citation in parallel...")
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        subtitle_future = pool.submit(generate_subtitles, script)
        package_future  = pool.submit(
            generate_content_package, topic_val, fmt, hook_angle, script, config, video_mode)
        citation_future = pool.submit(
            get_source_citation, topic_val, config.get("news_summary"))

        subtitle_lines  = subtitle_future.result()
        metadata        = package_future.result()
        source_citation = citation_future.result()

    title_short = metadata.get("title", topic_val)[:50]
    part_num, series_len, series_title, prev_vid = get_series_info(topic_val)
    if part_num and part_num > 1:
        series_end = build_series_end_card(part_num, series_title, prev_vid)
        metadata["description"] = metadata.get("description", "") + series_end
        title_short = f"Part {part_num}: {title_short}"
        log(f"  📚 Series: {series_title} — Part {part_num}")
    elif part_num == 1:
        log(f"  📚 New series started: {series_title}")

    with open(f"{SCRIPTS_DIR}/{safe_name}.txt", "w", encoding="utf-8") as script_handle:
        script_handle.write(
            f"TOPIC: {topic_val}\nFORMAT: {fmt}\nMODE: {video_mode}\n\n{script}")

    # ── SEQUENTIAL: Main video (cannot parallelise — needs all inputs) ─
    log("🎬 Creating main video...")
    video_result = create_video(
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
    if not video_result:
        log("❌ Video creation failed")
        return None

    video, duration_seconds = video_result
    metadata = finalize_metadata(metadata, duration_seconds, source_citation)
    metadata["format"] = fmt
    metadata["topic"] = topic_val
    thumb_bg = images[0] if images else None

    # ── PARALLEL PHASE 3: Thumbnail + Short video simultaneously ──────
    log("🚀 Phase 3: Thumbnail + Short video in parallel...")
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        thumb_future = pool.submit(
            generate_thumbnail,
            metadata.get("title", topic_val), fmt, safe_name,
            thumb_bg, metadata.get("thumbnail_headline", ""),
            metadata.get("thumbnail_concept", ""),
        )
        short_future = pool.submit(
            create_short_video,
            metadata.get("short_script", ""), images, safe_name,
            fmt, metadata.get("thumbnail_headline", title_short), bgm_path,
        )
        thumb_path = thumb_future.result()
        short_path = short_future.result()

    if thumb_path:
        metadata["thumbnail_path"] = thumb_path
        log("  ✅ Thumbnail generated")

    short_metadata = {
        "title": metadata.get("title", ""),
        "short_title": metadata.get("short_title", metadata.get("title", "")),
        "description": metadata.get("description", ""),
        "short_description": metadata.get("short_script", "")[:500],
        "tags": metadata.get("tags", ""),
    }

    meta_data = {
        "topic": topic_val,
        "format": fmt,
        "video_mode": video_mode,
        "title": metadata.get("title"),
        "title_candidates": metadata.get("title_candidates", []),
        "description": metadata.get("description"),
        "tags": metadata.get("tags"),
        "keywords": metadata.get("keywords", ""),
        "chapters": metadata.get("chapters", ""),
        "pinned_comment": metadata.get("pinned_comment"),
        "community_post": metadata.get("community_post", ""),
        "thumbnail_headline": metadata.get("thumbnail_headline", ""),
        "thumbnail_concept": metadata.get("thumbnail_concept", ""),
        "short_script": metadata.get("short_script", ""),
        "short_title": metadata.get("short_title", ""),
        "future_video_ideas": metadata.get("future_video_ideas", []),
        "news_summary": metadata.get("news_summary", {}),
        "monetization_score": metadata.get("monetization_score", {}),
        "image_search_queries": metadata.get("image_search_queries", []),
        "broll_queries": metadata.get("broll_queries", []),
        "duration_seconds": duration_seconds,
        "created": datetime.datetime.now().isoformat(),
    }
    with open(f"{METADATA_DIR}/{safe_name}.json", "w", encoding="utf-8") as meta_handle:
        json.dump(meta_data, meta_handle, ensure_ascii=False, indent=2)

    log(f"  Title: {metadata.get('title','')[:60]}")

    elapsed = time.time() - t_start
    log(f"✅ VIDEO: {video}")
    if short_path:
        log(f"✅ SHORT: {short_path}")
    log(f"⏱️  Total: {elapsed:.0f}s")

    if upload:
        log("⬆️ Uploading to YouTube...")
        try:
            vid = upload_to_youtube(video, metadata, privacy)
            if vid:
                log(f"✅ Live: https://youtu.be/{vid}")
                get_series_info(topic_val, video_id=vid)
        except Exception as upload_error:
            if is_quota_exceeded(upload_error):
                log("⚠️ YouTube quota exceeded — video queued for tomorrow")
                queue_for_retry(video, metadata, privacy)
            else:
                log(f"⚠️ Main video upload failed: {upload_error}")

        try:
            if short_path and os.path.exists(short_path):
                youtube_service = get_authenticated_service()
                if youtube_service:
                    upload_short_to_youtube(short_path, short_metadata, youtube_service)
                    log("✅ Short uploaded independently")
                else:
                    log("  ⚠️ Short: YouTube auth unavailable")
        except Exception as short_upload_error:
            log(f"  ⚠️ Short upload failed (main video unaffected): {short_upload_error}")

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

    parser = argparse.ArgumentParser(description="Tech Meets Travel Car News Bot v2.0")
    parser.add_argument("--day",          help="today")
    parser.add_argument("--topic",        help="Custom topic")
    parser.add_argument("--format",       help="news/launch/comparison/explainer/ev/suv")
    parser.add_argument("--upload",       action="store_true")
    parser.add_argument("--long-form",    action="store_true",
                        help="Force 5-8 min long-form video")
    parser.add_argument("--auto-long-form", action="store_true",
                        help="Long-form when monetization score >= 7 and tier 1-2")
    parser.add_argument("--privacy",      default="public",
                        choices=["public", "unlisted", "private"])
    parser.add_argument("--daemon",       action="store_true")
    parser.add_argument("--auth-youtube", action="store_true")
    args = parser.parse_args()

    print(f"\n{'='*55}")
    print(f"  {CHANNEL_NAME} — Car News Automation Bot v2.0")
    print(f"  Hybrid videos · Global + India · Auto upload")
    print(f"{'='*55}\n")

    if args.auth_youtube:
        auth_youtube(); return

    if args.daemon:
        daemon_mode(); return

    video_kwargs = {
        "upload": args.upload,
        "privacy": args.privacy,
        "long_form": args.long_form,
        "auto_long_form": args.auto_long_form,
    }

    if args.topic:
        safe_process_video(topic=args.topic, format_type=args.format, **video_kwargs)
    elif args.day:
        safe_process_video(**video_kwargs)
    else:
        print("Usage:")
        print("  python car_bot.py --day today")
        print("  python car_bot.py --day today --upload")
        print("  python car_bot.py --day today --upload --auto-long-form")
        print("  python car_bot.py --day today --upload --long-form")
        print("  python car_bot.py --topic 'Tata Harrier 2026 new features'")
        print("  python car_bot.py --daemon")
        print("  python car_bot.py --auth-youtube")

    print(f"\n{'='*55}")
    print(f"  Done! Check: studio.youtube.com")
    print(f"{'='*55}\n")


if __name__ == "__main__":
    main()

