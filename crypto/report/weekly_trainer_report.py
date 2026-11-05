# ============================================================
# ARGUS - WEEKLY TRAINER REPORT (v1.0)
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
    lines = ["🏋️ <b>ARGUS TRAINER: Обучение завершено</b>", ""]
    
    for mode in ["intraday", "swing"]:
        mode_name = "⚡️ INTRADAY" if mode == "intraday" else "🌊 SWING"
        lines.append(f"<b>{mode_name}</b>")
        
        for side in ["long", "short"]:
            d = data.get(mode, {}).get(side, {})
            if not d or not d.get("params"):
                lines.append(f"  {side.upper()}: ❌ нет данных")
                continue
                
            p = d["params"]
            m = d["metrics"]
            source = d.get("source", "unknown")
            
            lines.append(f"  <b>{side.upper()}</b>: {source}")
            lines.append(f"    Score: {m.get('score', 0):.2f} | WR: {m.get('winrate', 0)*100:.1f}% | PnL: ${m.get('pnl', 0):+.2f} | Trades: {m.get('trades', 0)}")
            
            if source == "model_base":
                lines.append(f"    RSI: {p.get('rsi_min', '?')}-{p.get('rsi_max', '?')} | TP: {p.get('tp_pct', '?')}% | SL: {p.get('sl_pct', '?')}% | Time: {p.get('time_exit_h', '?')}h")
        lines.append("")

    all_scores = data.get("all_scores", {})
    if all_scores:
        lines.append("────────────────────")
        lines.append("<b>Все модели (score):</b>")
        for mode in ["intraday", "swing"]:
            for side in ["long", "short"]:
                scores = all_scores.get(mode, {}).get(side, {})
                if not scores: continue
                parts = []
                for model_name, metrics in scores.items():
                    if "error" in metrics:
                        parts.append(f"{model_name}: ❌")
                    else:
                        parts.append(f"{model_name}: {metrics.get('score', 0):.2f}")
                lines.append(f"  {mode.upper()}/{side.upper()}: " + " | ".join(parts))
    
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