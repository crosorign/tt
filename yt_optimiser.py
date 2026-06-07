#!/usr/bin/env python3
"""
YouTube Settings Optimiser — 2026 Algorithm
Runs on all 3 channels and updates video metadata based on latest algorithm research.

What it fixes per video:
1. defaultLanguage — set correctly (ta for Tamil, en for English)
2. selfDeclaredMadeForKids — false (required for monetisation)
3. embeddable — true (more distribution surfaces)
4. publicStatsViewable — true (social proof)
5. license — youtube (standard, best for recommendations)
6. categoryId — correct per channel
7. Description — adds AI disclosure label (required 2026, prevents demotion)
8. Description — adds chapter timestamps if missing
9. Tags — ensures 500-char limit enforced, adds channel-specific SEO tags

Quota cost: 50 units per video update (list is 1 unit)
Daily quota: 10,000 units → safe to update ~150 videos per run
Run with: python3 yt_optimiser.py --channel am|nn|tt --max 50
"""

import os, sys, json, time, pickle, re, argparse
import datetime
from pathlib import Path
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.errors import HttpError

# ── Channel configs ─────────────────────────────────────────────────
CHANNELS = {
    "am": {
        "name":          "ஆலய மணி",
        "language":      "ta",          # Tamil
        "category_id":   "22",          # People & Blogs
        "base_tags":     ["ஆலய மணி","aalaya mani","tamil devotional","தமிழ் பக்தி",
                          "murugan","sivan","vinayagar","ayyappan","temple","prayer",
                          "pooja","devotional songs tamil","bhakti tamil"],
        "ai_disclosure": "\n\n🤖 This video was created with AI assistance. | இந்த video AI உதவியுடன் உருவாக்கப்பட்டது.",
        "chapters_hint": "0:00 Introduction\n0:15 திருவிழா சிறப்பு\n1:00 வழிபாடு முறை\n3:00 மந்திரம் & ஸ்தோத்திரம்\n4:30 Subscribe & Share",
    },
    "nn": {
        "name":          "நிதி நீதி தமிழ்",
        "language":      "ta",
        "category_id":   "22",
        "base_tags":     ["நிதி நீதி தமிழ்","nidhi neethi tamil","tamil finance",
                          "personal finance tamil","cibil score tamil","loan tips tamil",
                          "consumer rights tamil","rbi tamil","investment tamil",
                          "money management tamil","legal rights tamil"],
        "ai_disclosure": "\n\n🤖 Educational content created with AI assistance. | AI உதவியுடன் உருவாக்கப்பட்ட கல்வி உள்ளடக்கம்.\n⚠️ For information only — consult a certified financial advisor for personal advice.",
        "chapters_hint": "0:00 Introduction\n0:15 முக்கிய தகவல்\n0:45 விவரமான விளக்கம்\n1:20 நடவடிக்கை எடுங்கள்\n1:50 Subscribe & Share",
    },
    "tt": {
        "name":          "Tech Meets Travel",
        "language":      "en",          # English
        "category_id":   "2",           # Autos & Vehicles
        "base_tags":     ["tech meets travel","indian cars 2026","car news india",
                          "tata cars","mahindra cars","hyundai india","ev cars india",
                          "car launch india","suv india","electric vehicle india",
                          "car review india","upcoming cars india"],
        "ai_disclosure": "\n\n🤖 Content created with AI assistance for informational purposes.",
        "chapters_hint": "0:00 Introduction\n0:10 Key Highlights\n0:45 Specs & Features\n1:20 Price & Launch Date\n1:50 Our Take | Subscribe",
    },
}

SCOPES = ["https://www.googleapis.com/auth/youtube.force-ssl"]
TOKEN_FILE = "youtube_token.pickle"
CLIENT_SECRETS = "client_secrets.json"

def get_youtube():
    creds = None
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, "rb") as f:
            creds = pickle.load(f)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRETS, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, "wb") as f:
            pickle.dump(creds, f)
    return build("youtube", "v3", credentials=creds)


def get_channel_videos(yt, max_results=50):
    """Get recent videos from authenticated channel."""
    videos = []
    page_token = None
    
    # Get channel ID first
    ch_resp = yt.channels().list(part="id,snippet", mine=True).execute()
    channel_id = ch_resp["items"][0]["id"]
    channel_name = ch_resp["items"][0]["snippet"]["title"]
    print(f"Channel: {channel_name} ({channel_id})")
    
    # Get uploads playlist
    pl_resp = yt.channels().list(
        part="contentDetails", mine=True).execute()
    uploads_pl = pl_resp["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
    
    while len(videos) < max_results:
        req = yt.playlistItems().list(
            part="snippet,contentDetails",
            playlistId=uploads_pl,
            maxResults=min(50, max_results - len(videos)),
            pageToken=page_token
        )
        resp = req.execute()
        for item in resp["items"]:
            vid_id = item["contentDetails"]["videoId"]
            title  = item["snippet"]["title"]
            published = item["snippet"]["publishedAt"][:10]
            videos.append({"id": vid_id, "title": title, "published": published})
        
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    
    print(f"Found {len(videos)} videos")
    return videos


def get_video_details(yt, video_ids):
    """Get full details for a batch of videos."""
    results = {}
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i:i+50]
        resp = yt.videos().list(
            part="snippet,status,contentDetails",
            id=",".join(batch)
        ).execute()
        for item in resp["items"]:
            results[item["id"]] = item
    return results


def needs_ai_disclosure(description, disclosure):
    """Check if AI disclosure is already present."""
    return "AI" not in description and "ai assist" not in description.lower()


def has_chapters(description):
    """Check if description already has chapter timestamps."""
    return bool(re.search(r'\d+:\d+\s+\w', description))


def optimise_tags(existing_tags, base_tags, title, max_chars=490):
    """Merge existing + base tags, enforce 500 char limit."""
    # Combine existing + base, deduplicate
    all_tags = list(dict.fromkeys(existing_tags + base_tags))
    
    # Add title words as tags (high-value for search)
    title_words = [w.strip('.,!?') for w in title.split() if len(w) > 3]
    for w in title_words[:5]:
        if w not in all_tags:
            all_tags.append(w)
    
    # Enforce 500 char limit
    result, total = [], 0
    for tag in all_tags:
        if total + len(tag) + 1 <= max_chars:
            result.append(tag)
            total += len(tag) + 1
    return result


def update_video(yt, video_id, current, cfg, dry_run=False):
    """Apply all optimisations to a single video."""
    snippet = current["snippet"]
    status  = current["status"]
    changes = []
    
    title       = snippet.get("title", "")
    description = snippet.get("description", "")
    tags        = snippet.get("tags", [])
    category    = snippet.get("categoryId", "")
    language    = snippet.get("defaultLanguage", "")
    made_kids   = status.get("selfDeclaredMadeForKids", True)
    embeddable  = status.get("embeddable", False)
    pub_stats   = status.get("publicStatsViewable", False)
    license_type= status.get("license", "")
    
    # Build new values
    new_desc = description
    
    # 1. AI disclosure (mandatory 2026 — prevents demotion)
    if needs_ai_disclosure(description, cfg["ai_disclosure"]):
        new_desc = new_desc + cfg["ai_disclosure"]
        changes.append("✅ AI disclosure added")
    
    # 2. Chapter timestamps
    if not has_chapters(new_desc):
        new_desc = new_desc + "\n\n" + cfg["chapters_hint"]
        changes.append("✅ Chapter timestamps added")
    
    # 3. Truncate description to 5000 chars
    new_desc = new_desc[:5000]
    
    # 4. Tags optimisation
    new_tags = optimise_tags(tags, cfg["base_tags"], title)
    if len(new_tags) != len(tags) or set(new_tags) != set(tags):
        changes.append(f"✅ Tags: {len(tags)} → {len(new_tags)} ({sum(len(t) for t in new_tags)} chars)")
    
    # 5. Status fixes
    status_changes = {}
    if made_kids:
        status_changes["selfDeclaredMadeForKids"] = False
        changes.append("✅ madeForKids: true → false")
    if not embeddable:
        status_changes["embeddable"] = True
        changes.append("✅ embeddable enabled")
    if not pub_stats:
        status_changes["publicStatsViewable"] = True
        changes.append("✅ publicStatsViewable enabled")
    if license_type != "youtube":
        status_changes["license"] = "youtube"
        changes.append(f"✅ license: {license_type} → youtube")
    
    # 6. Category fix
    if category != cfg["category_id"]:
        changes.append(f"✅ categoryId: {category} → {cfg['category_id']}")
    
    # 7. Default language
    if language != cfg["language"]:
        changes.append(f"✅ defaultLanguage: {language or 'none'} → {cfg['language']}")
    
    if not changes:
        return False, []
    
    if dry_run:
        return True, changes
    
    try:
        # Build update body
        update_body = {
            "id": video_id,
            "snippet": {
                "title":           title,
                "description":     new_desc,
                "tags":            new_tags,
                "categoryId":      cfg["category_id"],
                "defaultLanguage": cfg["language"],
            },
        }
        
        yt.videos().update(
            part="snippet",
            body=update_body
        ).execute()
        
        # Status update separately (different part)
        if status_changes or made_kids:
            status_body = {
                "id": video_id,
                "status": {
                    "privacyStatus":           status.get("privacyStatus", "public"),
                    "selfDeclaredMadeForKids": False,
                    "embeddable":              True,
                    "publicStatsViewable":     True,
                    "license":                 "youtube",
                }
            }
            yt.videos().update(
                part="status",
                body=status_body
            ).execute()
        
        return True, changes
    
    except HttpError as e:
        if "quotaExceeded" in str(e):
            print(f"  ⚠️  Quota exceeded — stopping")
            raise
        print(f"  ⚠️  Update failed: {e}")
        return False, []


def run_optimiser(channel_key, max_videos=50, dry_run=False):
    cfg = CHANNELS[channel_key]
    print(f"\n{'='*60}")
    print(f"  YouTube Settings Optimiser — {cfg['name']}")
    print(f"  Max videos: {max_videos} | Dry run: {dry_run}")
    print(f"{'='*60}\n")
    
    yt = get_youtube()
    videos = get_channel_videos(yt, max_results=max_videos)
    
    if not videos:
        print("No videos found")
        return
    
    # Get full details
    video_ids = [v["id"] for v in videos]
    details   = get_video_details(yt, video_ids)
    
    updated  = 0
    skipped  = 0
    total_changes = []
    
    for v in videos:
        vid_id = v["id"]
        title  = v["title"]
        
        if vid_id not in details:
            continue
        
        current = details[vid_id]
        
        print(f"{'[DRY RUN] ' if dry_run else ''}📹 {title[:55]}...")
        
        try:
            changed, changes = update_video(yt, vid_id, current, cfg, dry_run)
            if changed:
                for c in changes:
                    print(f"  {c}")
                updated += 1
                total_changes.extend(changes)
                if not dry_run:
                    time.sleep(0.5)  # rate limit respect
            else:
                print("  ✅ Already optimised")
                skipped += 1
        except HttpError as e:
            if "quotaExceeded" in str(e):
                print(f"\n⚠️  Quota exceeded after {updated} updates. Run again tomorrow.")
                break
            print(f"  ❌ Error: {e}")
    
    print(f"\n{'='*60}")
    print(f"  {'[DRY RUN] ' if dry_run else ''}Summary:")
    print(f"  Updated: {updated} | Skipped (already OK): {skipped}")
    
    # Count change types
    change_counts = {}
    for c in total_changes:
        key = c.split(':')[0].strip('✅ ').split('→')[0].strip()
        change_counts[key] = change_counts.get(key, 0) + 1
    
    if change_counts:
        print(f"\n  Changes applied:")
        for k, v in sorted(change_counts.items(), key=lambda x: -x[1]):
            print(f"    {v:3d}x {k}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--channel", choices=["am","nn","tt"], required=True)
    parser.add_argument("--max",     type=int, default=50)
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview changes without applying")
    args = parser.parse_args()
    
    run_optimiser(args.channel, max_videos=args.max, dry_run=args.dry_run)
