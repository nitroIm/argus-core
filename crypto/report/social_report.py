# ============================================================
# ARGUS - SOCIAL REPORT (v1.2.1) - WITH NAMES
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
MARKER_FILE = DATA_DIR / "last_report.txt"

BOT_TOKEN = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
CHAT_ID = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()

def load_json(path):
    if not path.exists(): return None
    try:
        with open(path, "r", encoding="utf-8") as f: return json.load(f)
    except Exception: return None

def analyze_platform(data, default_name, is_tg=False):
    if not data or not data.get("history"): return f"🔹 <b>{default_name}</b>: Нет данных\n"
    
    history = data["history"]
    today_data = next((h for h in history if h["date"] == today_str), history[-1] if history else None)
    if not today_data: return f"🔹 <b>{default_name}</b>: Пустая история\n"
    
    # Берем название из данных или дефолтное
    platform_name = today_data.get("name", default_name)
    
    prev_data = next((h for h in reversed(history) if h["date"] < today_data["date"]), None)
    subs = today_data.get("subscribers", 0)
    delta_subs = subs - prev_data["subscribers"] if prev_data else 0
    sign = "+" if delta_subs >= 0 else ""
    
    posts = today_data.get("posts", [])
    lines = [f"🔹 <b>{platform_name}</b>"]
    lines.append(f"  👥 Подписчики: {subs} (<b>{sign}{delta_subs}</b> за день)")
    
    if posts:
        total_views = sum(p.get("views", 0) for p in posts)
        avg_views = int(total_views / len(posts))
        reach_pct = (avg_views / subs * 100) if subs > 0 else 0
        
        total_engagements = sum(
            p.get("likes", 0) + p.get("comments", 0) + (p.get("forwards", 0) if is_tg else p.get("reposts", 0)) 
            for p in posts
        )
        er_pct = (total_engagements / total_views * 100) if total_views > 0 else 0
        
        lines.append(f"  👁 Средний охват: {avg_views} просм. ({reach_pct:.1f}% от подписчиков)")
        lines.append(f"  ❤️ ER (вовлеченность): {er_pct:.2f}%")
        
        best = max(posts, key=lambda x: x.get("views", 0))
        worst = min(posts, key=lambda x: x.get("views", 0))
        lines.append(f"  🏆 Лучший: {best['type'].capitalize()} ({best['time']}) — {best['views']} просм.")
        lines.append(f"  📉 Слабый: {worst['type'].capitalize()} ({worst['time']}) — {worst['views']} просм.")
        
        types_stats = {}
        for p in posts:
            t = p.get("type", "other").capitalize()
            if t not in types_stats: types_stats[t] = {"views": 0, "count": 0}
            types_stats[t]["views"] += p.get("views", 0)
            types_stats[t]["count"] += 1
        
        lines.append("  📊 По типам контента:")
        for t, stat in types_stats.items():
            avg_t_views = int(stat["views"] / stat["count"])
            lines.append(f"     - {t}: {avg_t_views} просм. ({stat['count']} шт.)")
    else:
        lines.append("  📝 За сегодня постов не было.")
        
    return "\n".join(lines) + "\n"

def send_report(text):
    if not BOT_TOKEN or not CHAT_ID: return False
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        r = requests.post(url, json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}, timeout=20)
        if r.status_code == 200:
            log.info("Report sent.")
            return True
    except Exception as e:
        log.error(f"Failed to send report: {e}")
    return False

def main():
    log.info(f"STARTING REPORTER | Date: {today_str}")
    
    if MARKER_FILE.exists():
        try:
            with open(MARKER_FILE, "r") as f:
                if f.read().strip() == today_str:
                    log.info("Report already sent today. Skipping.")
                    return
        except Exception: pass
        
    vk_data = load_json(VK_FILE)
    tg_data = load_json(TG_FILE)
    
    lines = [f"📊 <b>Статистика соцсетей за {now_local.strftime('%d.%m.%Y')}</b>\n"]
    lines.append(analyze_platform(vk_data, "ВКонтакте", is_tg=False))
    lines.append(analyze_platform(tg_data, "Telegram", is_tg=True))
    
    text = "".join(lines)
    if send_report(text):
        with open(MARKER_FILE, "w") as f: f.write(today_str)
        log.info("Marker saved.")

if __name__ == "__main__":
    main()