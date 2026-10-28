# ============================================================
# ARGUS - SIGNAL REPORT (SENDER v1.4.1)
# ------------------------------------------------------------
# Читает opened_deals.json.
# Если есть сделка (sent_to_tg: False), отправляет в TG 
# и ставит флаг (sent_to_tg: True).
# ============================================================

import os
import json
import logging
import requests
from pathlib import Path

VERSION = "1.4.1"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("crypto.signal_report")

SCRIPT_DIR = Path(__file__).resolve().parent
CRYPTO_ROOT = SCRIPT_DIR.parent

# Читаем НОВЫЙ файл открытой сделки
OPENED_DEALS_FILE = CRYPTO_ROOT / "data" / "signals" / "opened_deals.json"

BOT_TOKEN = (
    os.getenv("TELEGRAM_BOT_TOKEN")
    or os.getenv("BOT_TOKEN")
    or ""
).strip()
CHAT_ID = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()

def load_json(path, default=None):
    if not path.exists(): return default if default is not None else {}
    try:
        with open(path, "r", encoding="utf-8") as f: return json.load(f)
    except Exception: return default if default is not None else {}

def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def send_telegram(text, chat_id):
    if not BOT_TOKEN or not chat_id:
        log.warning("TG not configured")
        return False
    try:
        url = "https://api.telegram.org/bot" + BOT_TOKEN + "/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        r = requests.post(url, json=payload, timeout=20)
        if r.status_code != 200:
            log.error(f"TG err {r.status_code}: {r.text[:100]}")
            return False
        return True
    except Exception as e:
        log.error(f"TG send error: {e}")
        return False

def fmt_price(p):
    if p >= 1000: return "$" + format(int(p), ",")
    if p >= 1: return "$" + format(p, ".2f")
    return "$" + format(p, ".4f")

def main():
    log.info(f"STARTING SIGNAL REPORT v{VERSION}")
    
    # 1. Читаем файл с открытой сделкой
    deal = load_json(OPENED_DEALS_FILE, None)
    
    # 2. СТРОГАЯ ПРОВЕРКА: Если пусто ИЛИ нет ключа 'entry'
    if not deal or "entry" not in deal:
        log.info("No open deals. Skipping.")
        return

    # 3. Проверяем, не отправляли ли уже
    if deal.get("sent_to_tg") == True:
        log.info("Deal already sent to Telegram.")
        return

    # 4. Достаем данные
    entry = float(deal['entry'])
    tp = float(deal['tp'])
    sl = float(deal['sl'])
    
    # Считаем проценты для красоты
    tp_pct = ((tp - entry) / entry) * 100
    sl_pct = ((entry - sl) / entry) * 100

    text = "🚀 <b>ARGUS SIGNAL — LONG!</b>\n\n"
    text += "Монета: <b>BTC/USDT</b>\n"
    text += f"Цена входа: <b>{fmt_price(entry)}</b>\n\n"
    text += f"🎯 Тейк-профит: {fmt_price(tp)} (+{tp_pct:.1f}%)\n"
    text += f"🛑 Стоп-лосс:  {fmt_price(sl)} (-{sl_pct:.1f}%)\n\n"
    text += "<i>Открыта виртуальная сделка. Ждем закрытия.</i>"

    # 5. Отправляем в TG
    if send_telegram(text, CHAT_ID):
        log.info("Signal sent to Telegram successfully.")
        # Ставим флаг прямо в файле сделки
        deal["sent_to_tg"] = True
        save_json(OPENED_DEALS_FILE, deal)
    else:
        log.error("Failed to send TG message. Will try next time.")

if __name__ == "__main__":
    main()