# ============================================================
# ARGUS - SOCIAL STATS COLLECTOR (v1.0.2)
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
from telethon.tl import functions

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

BOT_TOKEN = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
CHAT_ID = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()

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
    if not VK_TOKEN or not VK_GROUP_ID: 
        log.warning("VK: No token or group ID")
        return None
    
    gid = VK_GROUP_ID.replace("-", "")
    subs = 0
    posts = []

    # 1. ТЕСТ: Подписчики
    try:
        url = f"https://api.vk.com/method/groups.getById?group_id={gid}&fields=members_count&access_token={VK_TOKEN}&v=5.199"
        r = requests.get(url, timeout=15).json()
        
        if "error" in r:
            log.error(f"VK API Error (getById): {r['error']}")
        else:
            # В новых версиях VK возвращает {"response": {"groups": [{"members_count": X}]}}
            resp = r.get("response", {})
            groups = resp.get("groups", []) if isinstance(resp, dict) else resp
            
            if groups and isinstance(groups, list):
                subs = groups[0].get("members_count", 0)
                log.info(f"VK TEST: Found {subs} subscribers.")
            else:
                log.warning(f"VK TEST: Unexpected response structure: {r}")
    except Exception as e:
        log.error(f"VK Connection Error (getById): {e}")

    # 2. ТЕСТ: Посты
    try:
        url = f"https://api.vk.com/method/wall.get?owner_id=-{gid}&count=20&access_token={VK_TOKEN}&v=5.199"
        r = requests.get(url, timeout=15).json()
        
        if "error" in r:
            log.error(f"VK API Error (wall.get): {r['error']}")
        else:
            items = r.get("response", {}).get("items", [])
            log.info(f"VK TEST: Found {len(items)} recent posts.")
            
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
            log.info(f"VK TEST: {len(posts)} posts found for today ({today_str}).")
    except Exception as e:
        log.error(f"VK Connection Error (wall.get): {e}")

    return {"date": today_str, "subscribers": subs, "posts": posts}

async def get_tg_stats():
    log.info("Collecting TG stats...")
    if not TG_API_ID or not TG_API_HASH or not TG_SESSION:
        log.warning("TG: No API ID, hash or session")
        return None

    posts = []
    subs = 0
    
    try:
        async with TelegramClient(StringSession(TG_SESSION), TG_API_ID, TG_API_HASH) as client:
            channel = await client.get_entity(TG_CHANNEL_ID)
            
            full_channel = await client(functions.channels.GetFullChannelRequest(channel=channel))
            subs = full_channel.full_chat.participants_count
            log.info(f"TG TEST: Found {subs} subscribers.")
            
            messages = await client.get_messages(channel, limit=20)
            log.info(f"TG TEST: Found {len(messages)} recent messages.")
            
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
            log.info(f"TG TEST: {len(posts)} messages found for today ({today_str}).")
            
    except Exception as e:
        log.error(f"Telethon error: {e}")
        return None

    return {"date": today_str, "subscribers": subs, "posts": posts}

def send_daily_summary(vk_data, tg_data):
    if not BOT_TOKEN or not CHAT_ID: return
    lines = [f"📊 <b>Статистика за {now_local.strftime('%d.%m.%Y')}</b>", ""]
    
    if vk_data:
        vk_hist = load_json(VK_FILE).get("history", [])
        prev_vk = vk_hist[-1] if vk_hist else None
        delta_subs = vk_data["subscribers"] - prev_vk["subscribers"] if prev_vk and prev_vk["date"] != today_str else 0
        
        lines.append(f"🔵 <b>ВКонтакте</b>")
        lines.append(f"  Подписчики: {vk_data['subscribers']} (<b>+{delta_subs}</b> за день)")
        if vk_data["posts"]:
            best = max(vk_data["posts"], key=lambda x: x["views"])
            lines.append(f"  Постов: {len(vk_data['posts'])}. Лучший: {best['type']} ({best['views']} просм.)")
        else:
            lines.append("  Сегодня постов не было.")
        lines.append("")
    else:
        lines.append("🔵 <b>ВКонтакте</b>: ❌ Ошибка сбора\n")
        
    if tg_data:
        tg_hist = load_json(TG_FILE).get("history", [])
        prev_tg = tg_hist[-1] if tg_hist else None
        delta_subs = tg_data["subscribers"] - prev_tg["subscribers"] if prev_tg and prev_tg["date"] != today_str else 0
        
        lines.append(f"✈️ <b>Telegram</b>")
        lines.append(f"  Подписчики: {tg_data['subscribers']} (<b>+{delta_subs}</b> за день)")
        if tg_data["posts"]:
            best = max(tg_data["posts"], key=lambda x: x["views"])
            lines.append(f"  Постов: {len(tg_data['posts'])}. Лучший: {best['type']} ({best['views']} просм.)")
        else:
            lines.append("  Сегодня постов не было.")
    else:
        lines.append("✈️ <b>Telegram</b>: ❌ Ошибка сбора")
            
    text = "\n".join(lines)
    try:
        requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"}, timeout=20)
    except Exception as e:
        log.error(f"Failed to send summary: {e}")

def main():
    log.info(f"STARTING SOCIAL STATS COLLECTOR | Date: {today_str}")
    
    # Тестируем и собираем ВК
    vk_data = get_vk_stats()
    if vk_data:
        data = load_json(VK_FILE)
        data["history"] = [h for h in data["history"] if h.get("date") != today_str]
        data["history"].append(vk_data)
        save_json(VK_FILE, data)
        log.info("VK stats saved.")
    
    # Тестируем и собираем ТГ
    tg_data = asyncio.run(get_tg_stats())
    if tg_data:
        data = load_json(TG_FILE)
        data["history"] = [h for h in data["history"] if h.get("date") != today_str]
        data["history"].append(tg_data)
        save_json(TG_FILE, data)
        log.info("TG stats saved.")

    # Отчет
    send_daily_summary(vk_data, tg_data)
    log.info("DONE.")

if __name__ == "__main__":
    main()