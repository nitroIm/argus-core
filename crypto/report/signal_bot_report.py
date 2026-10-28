# ============================================================
# ARGUS - SIGNAL REPORT (SENDER)
# ------------------------------------------------------------
# Version: 1.0.0
# Description: 
#   Читает current_status.json.
#   Если есть новый сигнал (sent_to_tg: False), отправляет
#   его в Telegram (личку) и обновляет статус.
# ============================================================

import os
import json
import logging
import requests
from pathlib import Path

VERSION = "1.0.0"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("crypto.signal_report")

SCRIPT_DIR = Path(__file__).resolve().parent
CRYPTO_ROOT = SCRIPT_DIR.parent

# Путь к статусу (создается первым ботом)
STATUS_FILE = CRYPTO_ROOT / "data" / "signals" / "current_status.json"

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
    
    # 1. Читаем статус
    status = load_json(STATUS_FILE)
    if not status or not status.get("in_pos"):
        log.info("No active positions. Skipping.")
        return

    # 2. Проверяем, не отправляли ли уже
    if status.get("sent_to_tg"):
        log.info("Signal already sent to Telegram.")
        return

    sig = status.get("signal")
    if not sig:
        log.warning("No signal data in status.")
        return

    # 3. Формируем сообщение
    entry = float(sig['entry_price'])
    tp = float(sig['tp'])
    sl = float(sig['sl'])
    
    # Считаем профит/лосс в процентах (для наглядности)
    # Берем проценты из настроек модели, если они там есть. Если нет — считаем сами.
    tp_pct = ((tp - entry) / entry) * 100
    sl_pct = ((entry - sl) / entry) * 100

    text = "🚀 <b>ARGUS SIGNAL — LONG!</b>\n\n"
    text += "Монета: <b>BTC/USDT</b>\n"
    text += f"Цена входа: <b>{fmt_price(entry)}</b>\n\n"
    text += f"🎯 Тейк-профит: {fmt_price(tp)} (+{tp_pct:.1f}%)\n"
    text += f"🛑 Стоп-лосс:  {fmt_price(sl)} (-{sl_pct:.1f}%)\n\n"
    text += "<i>Открыта виртуальная сделка. Ждем закрытия.</i>"

    # 4. Отправляем в TG
    if send_telegram(text, CHAT_ID):
        log.info("Signal sent to Telegram successfully.")
        # Ставим флаг, чтобы больше не отправлять
        status["sent_to_tg"] = True
        save_json(STATUS_FILE, status)
    else:
        log.error("Failed to send TG message. Will try next time.")

if __name__ == "__main__":
    main()