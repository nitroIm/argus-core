# ============================================================
# ARGUS - WEEKLY TRAINER REPORT (v2.0)
# Path: crypto/report/weekly_trainer_report.py
# ============================================================

import os
import json
import logging
import requests
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("weekly_trainer_report")

SCRIPT_DIR = Path(__file__).resolve().parent
CRYPTO_ROOT = SCRIPT_DIR.parent
MODEL_FILE = CRYPTO_ROOT / "data" / "models" / "best_params.json"

BOT_TOKEN = (os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN") or "").strip()
CHAT_ID = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()

def load_json(path, default=None):
    if not path.exists(): return default
    try:
        with open(path, "r", encoding="utf-8") as f: return json.load(f)
    except Exception: return default

def send_tg(text):
    if not BOT_TOKEN or not CHAT_ID: return False
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    r = requests.post(url, json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"}, timeout=20)
    return r.status_code == 200

def build_report(data):
    start = data.get("data_start", "?")
    end = data.get("data_end", "?")
    rows = data.get("total_rows", 0)
    
    lines = ["🏋️ <b>ARGUS TRAINER: Обучение завершено</b>"]
    lines.append(f"📅 Период данных: {start} — {end} ({rows} свечей)\n")
    
    for mode in ["intraday", "swing"]:
        mode_name = "⚡️ INTRADAY" if mode == "intraday" else "🌊 SWING"
        lines.append(f"<b>{mode_name}</b>")
        
        for side in ["long", "short"]:
            winner = data.get(mode, {}).get(side, {})
            win_source = winner.get("source", "unknown") if winner else None
            
            lines.append(f"  <b>{side.upper()}</b>:")
            
            scores = data.get("all_scores", {}).get(mode, {}).get(side, {})
            if not scores:
                lines.append("    ❌ нет данных")
                continue
                
            # Сортируем модели по score (успешные выше, упавшие внизу)
            sorted_models = sorted(
                scores.items(), 
                key=lambda x: x[1].get("score", -99999) if "error" not in x[1] else -999999, 
                reverse=True
            )
            
            for model_name, metrics in sorted_models:
                prefix = "🏆 " if model_name == win_source else "   "
                
                if "error" in metrics:
                    lines.append(f"    {prefix}{model_name}: ❌ Ошибка: {metrics['error']}")
                else:
                    sc = metrics.get("score", 0)
                    wr = metrics.get("winrate", 0) * 100
                    pnl = metrics.get("pnl", 0)
                    trades = metrics.get("trades", 0)
                    lines.append(f"    {prefix}{model_name}: Score {sc:.2f} | WR {wr:.1f}% | PnL ${pnl:+.2f} | Сделок: {trades}")
        lines.append("")
        
    return "\n".join(lines)

def main():
    log.info("STARTING WEEKLY TRAINER REPORT")
    data = load_json(MODEL_FILE)
    if not data:
        log.error(f"File not found: {MODEL_FILE}")
        send_tg("⚠️ <b>ARGUS TRAINER: ОШИБКА</b>\n\nТренер не сохранил best_params.json.")
        return
    
    text = build_report(data)
    if send_tg(text):
        log.info("Report sent.")
    else:
        log.error("Failed to send report.")

if __name__ == "__main__":
    main()