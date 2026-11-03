# ============================================================
# ARGUS - SOCIAL REPORT (v1.0.0)
# Path: crypto/report/social_report.py
# ============================================================

import os
import sys
import json
import logging
import requests
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("social_report")

TZ = ZoneInfo("Europe/Kaliningrad")
now_local = datetime.now(timezone.utc).astimezone(TZ)
today_str = now_local.strftime("%Y-%m-%d")

DATA_DIR = Path("crypto/data/social_stats")
VK_FILE = DATA_DIR / "vk.json"
TG_FILE = DATA_DIR / "tg.json"

BOT_TOKEN = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
CHAT_ID = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()

def load_json(path):
    if not path.exists(): return None
    try:
        with open(path, "r", encoding="utf-8") as f: return json.load(f)
    except Exception: return None

def analyze_platform(data, name):
    if not data or not data.get("history"): return f"⚪️ <b>{name}</b>: Нет данных\n"
    
    # Берем сегодняшний день (или последний доступный)
    history = data["history"]
    today_data = next((h for h in history if h["date"] == today_str), history[-1])
    prev_data = next((h for h in reversed(history) if h["date"] < today_data["date"]), None)
    
    subs = today_data.get("subscribers", 0)
    delta_subs = subs - prev_data["subscribers"] if prev_data else 0
    sign = "+" if delta_subs >= 0 else ""
    
    posts = today_data.get("posts", [])
    lines = [f"🔹 <b>{name}</b>"]
    lines.append(f"  👥 Подписчики: {subs} (<b>{sign}{delta_subs}</b> за день)")
    
    if posts:
        total_views = sum(p.get("views", 0) for p in posts)
        avg_views = int(total_views / len(posts))
        lines.append(f"  👁 Средний охват: {avg_views} просмотров ({len(posts)} постов)")
        
        best = max(posts, key=lambda x: x.get("views", 0))
        worst = min(posts, key=lambda x: x.get("views", 0))
        
        lines.append(f"  🏆 Лучший: {best['type'].capitalize()} ({best['time']}) — {best['views']} просм.")
        lines.append(f"  📉 Слабый: {worst['type'].capitalize()} ({worst['time']}) — {worst['views']} просм.")
    else:
        lines.append("  📝 За сегодня постов не было.")
        
    return "\n".join(lines) + "\n"

def send_report(text):
    if not BOT_TOKEN or not CHAT_ID: return
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}, timeout=20)
        log.info("Report sent.")
    except Exception as e:
        log.error(f"Failed to send report: {e}")

def main():
    log.info(f"STARTING REPORTER | Date: {today_str}")
    
    vk_data = load_json(VK_FILE)
    tg_data = load_json(TG_FILE)
    
    lines = [f"📊 <b>Статистика соцсетей за {now_local.strftime('%d.%m.%Y')}</b>\n"]
    lines.append(analyze_platform(vk_data, "ВКонтакте"))
    lines.append(analyze_platform(tg_data, "Telegram"))
    
    text = "".join(lines)
    send_report(text)

if __name__ == "__main__":
    main()