# ============================================================
# ARGUS - SOCIAL STATS COLLECTOR (v1.0.4) - ONLY COLLECT
# Path: crypto/collect/collect_social_stats.py
# ============================================================

import os
import sys
import json
import logging
import requests
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.functions.channels import GetFullChannelRequest

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("social_stats")

TZ = ZoneInfo("Europe/Kaliningrad")
now_local = datetime.now(timezone.utc).astimezone(TZ)
today_str = now_local.strftime("%Y-%m-%d")

DATA_DIR = Path("crypto/data/social_stats")
DATA_DIR.mkdir(parents=True, exist_ok=True)
VK_FILE = DATA_DIR / "vk.json"
TG_FILE = DATA_DIR / "tg.json"

VK_TOKEN = (os.getenv("VK_TOKEN") or "").strip()
VK_GROUP_ID = (os.getenv("VK_GROUP_ID") or "").strip()
TG_API_ID = int(os.getenv("TG_API_ID") or 0)
TG_API_HASH = (os.getenv("TG_API_HASH") or "").strip()
TG_SESSION = (os.getenv("TG_SESSION") or "").strip()
TG_CHANNEL_ID = (os.getenv("TG_CHANNEL_ID") or "").strip()

def load_json(path):
    if not path.exists(): return {"platform": path.stem, "history": []}
    try:
        with open(path, "r", encoding="utf-8") as f: return json.load(f)
    except Exception: return {"platform": path.stem, "history": []}

def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def detect_post_type(text):
    text = (text or "").lower()
    if "новости" in text: return "news"
    if "утро" in text or "утренний" in text: return "morning"
    if "вечер" in text or "вечерний" in text: return "evening"
    return "other"

def get_vk_stats():
    log.info("Collecting VK stats...")
    if not VK_TOKEN or not VK_GROUP_ID: return None
    
    gid = VK_GROUP_ID.replace("-", "")
    subs = 0
    posts = []

    try:
        url = f"https://api.vk.com/method/groups.getById?group_id={gid}&fields=members_count&access_token={VK_TOKEN}&v=5.199"
        r = requests.get(url, timeout=15).json()
        if "error" not in r:
            resp = r.get("response", {})
            groups = resp.get("groups", []) if isinstance(resp, dict) else resp
            if groups and isinstance(groups, list):
                subs = groups[0].get("members_count", 0)
                log.info(f"VK: Found {subs} subscribers.")
    except Exception as e:
        log.error(f"VK Error (subs): {e}")

    try:
        url = f"https://api.vk.com/method/wall.get?owner_id=-{gid}&count=20&access_token={VK_TOKEN}&v=5.199"
        r = requests.get(url, timeout=15).json()
        if "error" not in r:
            items = r.get("response", {}).get("items", [])
            for item in items:
                dt = datetime.fromtimestamp(item["date"], tz=timezone.utc).astimezone(TZ)
                if dt.strftime("%Y-%m-%d") == today_str:
                    posts.append({
                        "type": detect_post_type(item.get("text", "")),
                        "time": dt.strftime("%H:%M"),
                        "views": item.get("views", {}).get("count", 0),
                        "likes": item.get("likes", {}).get("count", 0),
                        "reposts": item.get("reposts", {}).get("count", 0),
                        "comments": item.get("comments", {}).get("count", 0)
                    })
            log.info(f"VK: Found {len(posts)} posts for today.")
    except Exception as e:
        log.error(f"VK Error (posts): {e}")

    return {"date": today_str, "subscribers": subs, "posts": posts}

async def get_tg_stats():
    log.info("Collecting TG stats...")
    if not TG_API_ID or not TG_API_HASH or not TG_SESSION: return None

    posts = []
    subs = 0
    
    try:
        async with TelegramClient(StringSession(TG_SESSION), TG_API_ID, TG_API_HASH) as client:
            channel = None
            if TG_CHANNEL_ID.lstrip('-').isdigit():
                try:
                    channel = await client.get_entity(int(TG_CHANNEL_ID))
                except Exception:
                    pass
            
            if not channel:
                fallback_name = TG_CHANNEL_ID if not TG_CHANNEL_ID.lstrip('-').isdigit() else "@Crypto_and_m0ney"
                if not fallback_name.startswith("@"): fallback_name = "@" + fallback_name
                channel = await client.get_entity(fallback_name)
            
            full_channel = await client(GetFullChannelRequest(channel=channel))
            subs = full_channel.full_chat.participants_count
            
            messages = await client.get_messages(channel, limit=20)
            for msg in messages:
                if not msg.text: continue
                dt = msg.date.astimezone(TZ)
                if dt.strftime("%Y-%m-%d") == today_str:
                    posts.append({
                        "type": detect_post_type(msg.text),
                        "time": dt.strftime("%H:%M"),
                        "views": msg.views if msg.views else 0,
                        "forwards": msg.forwards if msg.forwards else 0,
                        "likes": sum(r.count for r in msg.reactions.results) if msg.reactions else 0
                    })
            log.info(f"TG: Found {len(posts)} posts for today.")
    except Exception as e:
        log.error(f"Telethon error: {e}")
        return None

    return {"date": today_str, "subscribers": subs, "posts": posts}

def main():
    log.info(f"STARTING COLLECTOR | Date: {today_str}")
    
    vk_data = get_vk_stats()
    if vk_data:
        data = load_json(VK_FILE)
        data["history"] = [h for h in data["history"] if h.get("date") != today_str]
        data["history"].append(vk_data)
        save_json(VK_FILE, data)
        log.info("VK stats saved.")
    
    tg_data = asyncio.run(get_tg_stats())
    if tg_data:
        data = load_json(TG_FILE)
        data["history"] = [h for h in data["history"] if h.get("date") != today_str]
        data["history"].append(tg_data)
        save_json(TG_FILE, data)
        log.info("TG stats saved.")

    log.info("DONE COLLECTOR.")

if __name__ == "__main__":
    main()