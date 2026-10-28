# ============================================================
# ARGUS - SIGNAL REPORT (SENDER v3.0)
# ------------------------------------------------------------
# Читает info_deal.json и отправляет события в Telegram.
# Добавляет реальную статистику бота.
# ============================================================

import os
import json
import logging
import requests
from pathlib import Path
from datetime import datetime

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("crypto.signal_report")

SCRIPT_DIR = Path(__file__).resolve().parent
CRYPTO_ROOT = SCRIPT_DIR.parent
SIGNALS_DIR = CRYPTO_ROOT / "data" / "signals"

INFO_DEAL = SIGNALS_DIR / "info_deal.json"
CLOSED_DEALS = SIGNALS_DIR / "closed_deals.json"
BOT_INFO = SIGNALS_DIR / "bot_info.json"

BOT_TOKEN = (os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN") or "").strip()
CHAT_ID = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()

def load_json(path, default=None):
    if not path.exists(): return default if default is not None else {}
    try:
        with open(path, "r", encoding="utf-8") as f: return json.load(f)
    except Exception: return default if default is not None else {}

def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f: json.dump(data, f, ensure_ascii=False, indent=2)

def send_tg(text):
    if not BOT_TOKEN or not CHAT_ID: return False
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    r = requests.post(url, json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"}, timeout=20)
    return r.status_code == 200

def fmt(p): return "$" + format(int(p), ",") if p >= 1000 else "$" + format(p, ".2f")

def get_stats_text():
    info = load_json(BOT_INFO, {})
    closed = load_json(CLOSED_DEALS, [])
    
    start_date = "?"
    if info.get("started_at"):
        dt = datetime.fromisoformat(info["started_at"])
        start_date = dt.strftime("%d.%m.%Y %H:%M")

    total = len(closed)
    wins = sum(1 for t in closed if t.get("pnl_usd", 0) > 0)
    losses = total - wins
    winrate = (wins / total * 100) if total > 0 else 0
    total_pnl = sum(t.get("pnl_usd", 0) for t in closed)
    pnl_sign = "+" if total_pnl >= 0 else ""

    text = "────────────────────\n"
    text += "📊 <b>Статистика бота:</b>\n"
    text += f"📅 Работает с: {start_date}\n"
    text += f"📈 Сделок: {total} ({wins} ✅ / {losses} ❌)\n"
    text += f"🎯 Винрейт (факт): <b>{winrate:.1f}%</b>\n"
    text += f"💰 Общий PnL: <b>{pnl_sign}${total_pnl:.2f}</b>"
    return text

def main():
    log.info("STARTING SIGNAL REPORT v3.0")
    
    event = load_json(INFO_DEAL, {})
    if not event or event.get("sent") == True:
        return log.info("No new events.")

    text = ""
    if event["type"] == "ENTRY":
        text = "🚀 <b>ARGUS SIGNAL: ПОКУПКА BTC</b>\n\n"
        text += f"🟢 <b>Вход:</b> {fmt(event['entry'])}\n"
        text += f"🎯 <b>Тейк (TP):</b> {fmt(event['tp'])} (+8.0%)\n"
        text += f"🛑 <b>Стоп (SL):</b> {fmt(event['sl'])} (-5.0%)\n\n"
        text += get_stats_text()
    
    elif event["type"] == "CLOSE":
        pnl = event["pnl_usd"]
        sign = "+" if pnl >= 0 else ""
        status = "✅ УСПЕШНАЯ СДЕЛКА" if pnl > 0 else "❌ УБЫТОЧНАЯ СДЕЛКА"
        text = f"🏁 <b>{status}</b>\n\n"
        text += f"📊 Причина: {event['reason']}\n"
        text += f"💰 Результат: <b>{sign}{event['pnl_pct']}%</b> (на $100: <b>{sign}${pnl:.2f}</b>)\n\n"
        text += get_stats_text()

    if text and send_tg(text):
        log.info(f"Sent {event['type']} to TG.")
        event["sent"] = True
        save_json(INFO_DEAL, event)

if __name__ == "__main__":
    main()