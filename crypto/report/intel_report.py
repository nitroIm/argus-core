# ============================================================
# ARGUS - INTEL REPORT (v1.4 - Compact & Reliability)
# Path: crypto/report/intel_report.py
# ============================================================

import os
import sys
import json
import logging
import requests
from pathlib import Path
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("intel_report")

SCRIPT_DIR = Path(__file__).resolve().parent
DATA_FILE = SCRIPT_DIR.parent / "data" / "intel" / "correlations.json"

BOT_TOKEN = (os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN") or "").strip()
CHAT_ID = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()
TZ = ZoneInfo("Europe/Kaliningrad")

TICKER_NAMES = {
    "^VIX": "VIX (Страх)", "^GSPC": "S&P 500", "^IXIC": "Nasdaq",
    "^N225": "Nikkei 225", "^HSI": "Гонконг", "DX-Y.NYB": "DXY (Доллар)",
    "CL=F": "Нефть", "GC=F": "Золото", "total_market_cap": "Общая капа",
    "altcoin_market_cap": "Альткоины", "eth_dominance": "Доминация ETH",
    "feargreed": "Fear & Greed", "btc_dominance": "Доминация BTC",
    "^KS11": "KOSPI (Корея)", "^FTSE": "FTSE (UK)", "^JKSE": "Джакарта",
    "^TWII": "Тайвань", "399001.SZ": "Шэньчжэнь", "^BSESN": "BSE Sensex",
    "defi_market_cap": "DeFi Market Cap"
}

def load_json(path):
    if not path.exists(): return None
    try:
        with open(path, "r", encoding="utf-8") as f: return json.load(f)
    except Exception: return None

def send_telegram(text):
    if not BOT_TOKEN or not CHAT_ID: return False
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        r = requests.post(url, json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}, timeout=20)
        return r.status_code == 200
    except Exception: return False

def main():
    fail_count = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    log.info("Формирование отчета Intel...")
    
    data = load_json(DATA_FILE)
    if not data or "history" not in data or not data["history"]:
        return

    latest = data["history"][-1]
    corrs = latest.get("correlations", {}).copy()
    btc_price = corrs.pop("current_btc_price", 0)

    if btc_price == 0:
        return

    # Собираем сильные связи (порог 0.4) и не мусорные
    strong_links = []
    for ticker, info in corrs.items():
        corr = info.get("best_corr", 0)
        beta = info.get("beta", 0)
        
        if abs(corr) < 0.4: continue
        if "Stable" in ticker or "stablecoin" in ticker: continue
        if abs(beta) > 2.5: continue
        if "hash-rate" in ticker or "difficulty" in ticker: continue
        if "miners-revenue" in ticker or "n-transactions" in ticker: continue
        
        strong_links.append((ticker, info))

    # Сортируем по силе корреляции
    strong_links.sort(key=lambda x: abs(x[1]["best_corr"]), reverse=True)

    # Делим на "проверенные" (reliability >= 3) и "новые"
    proven = [x for x in strong_links if x[1].get("reliability", 0) >= 3]
    new_links = [x for x in strong_links if x[1].get("reliability", 0) < 3]

    now = datetime.now(timezone.utc).astimezone(TZ)
    lines = [f"📊 <b>ARGUS Intel</b> — {now.strftime('%d.%m')} | BTC: ${btc_price:,.0f}\n"]

    def add_link(ticker, info):
        corr = info["best_corr"]
        beta = info["beta"]
        lag = info["best_lag_h"]
        rel = info.get("reliability", 0)
        name = TICKER_NAMES.get(ticker, ticker)
        
        # Считаем предсказание: движение на 1% актива → изменение BTC
        est_pct = beta * 1.0
        est_price = btc_price * (1 + (est_pct / 100))
        
        # Иконки
        emoji = "🟢" if corr > 0 else "🔴"
        lag_str = f"{lag:+d}ч"
        stars = "⭐" * min(rel, 5)
        
        lines.append(f"{emoji} <b>{name}</b> ({corr:+.2f}, {lag_str}) → ${est_price:,.0f} ({est_pct:+.2f}%)")
        if stars:
            lines.append(f"   {stars}")

    if proven:
        lines.append("🔥 <b>ПРОВЕРЕННЫЕ СВЯЗИ:</b>")
        for t, i in proven[:5]: add_link(t, i)
        lines.append("")

    if new_links:
        lines.append("⚡ <b>НОВЫЕ СВЯЗИ:</b>")
        for t, i in new_links[:3]: add_link(t, i)
        lines.append("")

    if not proven and not new_links:
        lines.append("<i>Сильных связей (порог 0.4) нет. Фон неопределенный.</i>\n")

    if fail_count > 0:
        lines.append(f"⚠️ <i>{fail_count} модуль(ей) упали.</i>")

    text = "\n".join(lines)
    if send_telegram(text):
        log.info("Отчет отправлен в личку.")

if __name__ == "__main__":
    main()