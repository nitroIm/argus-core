# ============================================================
# ARGUS - SIGNAL REPORT v5.1 (VIRTUAL BALANCE)
# Path: crypto/report/signal_bot_report.py
# ============================================================

import os, json, logging, requests
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

def get_stats_text(mode):
    info = load_json(BOT_INFO, {}).get(mode, {})
    closed = [t for t in load_json(CLOSED_DEALS, []) if t.get("mode") == mode]
    
    total = len(closed)
    wins = sum(1 for t in closed if t.get("pnl_usd", 0) > 0)
    losses = total - wins
    winrate = (wins / total * 100) if total > 0 else 0
    
    # Берем реальный баланс, если нет - 100
    balance = info.get("balance", 100.0)
    start_balance = info.get("start_balance", 100.0)
    total_pnl = balance - start_balance
    pnl_sign = "+" if total_pnl >= 0 else ""
    
    start = info.get("started_at", "?")
    if start != "?": start = datetime.fromisoformat(start).strftime("%d.%m %H:%M")

    return (f"────────────────────\n"
            f"📊 <b>Статистика {mode.upper()}:</b>\n"
            f"📅 Работает с: {start}\n"
            f"📈 Сделок: {total} ({wins} ✅ / {losses} ❌)\n"
            f"🎯 Винрейт: <b>{winrate:.1f}%</b>\n"
            f"💰 Баланс: <b>${balance:.2f}</b> (PnL: <b>{pnl_sign}${total_pnl:.2f}</b>)")

def main():
    log.info("STARTING SIGNAL REPORT v5.1")
    
    queue = load_json(INFO_DEAL, [])
    if not isinstance(queue, list) or not queue:
        return log.info("No new events in queue.")
        
    sent_any = False
    for event in queue:
        mode_tag = "⚡️ [ИНТРАДЕЙ]" if event.get("mode") == "intraday" else "🌊 [СВИНГ]"
        deal_type = event.get("deal_type", "LONG")
        text = ""
        
        if event["type"] == "ENTRY":
            entry, tp, sl = float(event["entry"]), float(event["tp"]), float(event["sl"])
            if deal_type == "LONG":
                text = f"{mode_tag} <b>ПОКУПКА BTC (LONG)</b>\n\n🟢 <b>Вход:</b> {fmt(entry)}\n🎯 <b>Тейк:</b> {fmt(tp)} ({(tp-entry)/entry*100:+.2f}%)\n🛑 <b>Стоп:</b> {fmt(sl)} ({(sl-entry)/entry*100:+.2f}%)\n\n"
            else:
                text = f"{mode_tag} <b>ПРОДАЖА BTC (SHORT)</b>\n\n🟢 <b>Вход:</b> {fmt(entry)}\n🎯 <b>Тейк:</b> {fmt(tp)} ({(entry-tp)/entry*100:+.2f}%)\n🛑 <b>Стоп:</b> {fmt(sl)} ({(entry-sl)/entry*100:+.2f}%)\n\n"
        
        elif event["type"] == "UPDATE":
            text = f"{mode_tag} <b>Трейлинг-стоп (LONG)</b>\n\n🛑 <b>Новый стоп:</b> {fmt(event['new_sl'])} ({event['sl_pct']:+.2f}%)\n\n"
        
        elif event["type"] == "CLOSE":
            pnl = event["pnl_usd"]
            sign = "+" if pnl >= 0 else ""
            status = "🛡️ ТРЕЙЛИНГ-СТОП" if event["reason"] == "TRAILING_STOP" else ("✅ УСПЕХ" if pnl > 0 else "❌ УБЫТОК")
            
            # Достаем новый баланс из события
            new_bal = event.get("new_balance", 0)
            text = (f"{mode_tag} <b>{status} ({deal_type})</b>\n\n"
                    f"📊 Причина: {event['reason']}\n"
                    f"💰 Результат: <b>{sign}{event['pnl_pct']}%</b> (<b>{sign}${pnl:.2f}</b>)\n"
                    f"💼 Баланс: <b>${new_bal:.2f}</b>\n\n")

        text += get_stats_text(event.get("mode", "swing"))
        
        if send_tg(text):
            log.info(f"Sent {event['type']} ({event.get('mode')}) to TG.")
            sent_any = True

    if sent_any:
        save_json(INFO_DEAL, [])
        log.info("Queue cleared.")

if __name__ == "__main__":
    main()