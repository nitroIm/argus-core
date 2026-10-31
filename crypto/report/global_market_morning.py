# ============================================================
# ARGUS - GLOBAL MARKET MORNING REPORT (v1.0.3)
# Path: crypto/report/global_market_morning.py
# ============================================================

import os
import sys
import requests
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import psycopg

DB_URL = (os.getenv("AIVEN_DB_URL") or "").strip()
BOT_TOKEN = (os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN") or "").strip()
CHAT_ID = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()

TZ = ZoneInfo("Europe/Kaliningrad")

TICKER_NAMES = {
    "^N225": "Nikkei 225", "^HSI": "Hang Seng", "000001.SS": "Shanghai Comp",
    "399001.SZ": "Shenzhen Comp", "^KS11": "KOSPI", "^TWII": "TAIEX",
    "^STI": "Straits Times", "^JKSE": "IDX Comp", "^KLSE": "KLCI",
    "^SET.BK": "SET Index", "^BSESN": "BSE Sensex",
    "^GDAXI": "DAX", "^FTSE": "FTSE 100", "^FCHI": "CAC 40",
    "^GSPC": "S&P 500", "^IXIC": "NASDAQ", "^VIX": "VIX (Страх)",
    "DX-Y.NYB": "DXY (Доллар)", "GC=F": "Золото", "CL=F": "Нефть"
}

def get_conn():
    if not DB_URL: raise RuntimeError("AIVEN_DB_URL not set")
    return psycopg.connect(DB_URL, connect_timeout=30, application_name="argus-global-report")

def add_hours(time_str, hours_to_add):
    h, m = map(int, time_str.split(':'))
    h = (h + hours_to_add) % 24
    return f"{h:02d}:{m:02d}"

def get_missing_data_reason(h_row, now_utc):
    ticker, region, tz, open_utc, close_utc, lunch_start, lunch_end, trading_days = h_row
    current_day = now_utc.isoweekday()
    allowed_days = [1, 2, 3, 4, 5] if trading_days == "1-5" else [7, 1, 2, 3, 4] if trading_days == "0-4" else [1, 2, 3, 4, 5]
    
    if current_day not in allowed_days:
        return "(выходной)"
        
    current_time = now_utc.strftime("%H:%M")
    is_overnight = open_utc > close_utc
    
    if not is_overnight:
        if current_time < open_utc:
            open_kld = add_hours(open_utc, 2)
            return f"(откроется в {open_kld} КЛГ)"
        elif current_time >= close_utc:
            return "(закрыт)"
        else:
            if lunch_start and lunch_end and (lunch_start <= current_time < lunch_end):
                return "(обеденный перерыв)"
            return "(торгуется, ожидание)"
    else:
        if close_utc <= current_time < open_utc:
            open_kld = add_hours(open_utc, 2)
            return f"(откроется в {open_kld} КЛГ)"
        else:
            return "(торгуется, ожидание)"

def is_market_open(market_row, now_utc):
    ticker, region, tz, open_utc, close_utc, lunch_start, lunch_end, trading_days = market_row
    current_day = now_utc.isoweekday()
    allowed_days = [1, 2, 3, 4, 5] if trading_days == "1-5" else [7, 1, 2, 3, 4] if trading_days == "0-4" else [1, 2, 3, 4, 5]
    if current_day not in allowed_days: return False
    current_time = now_utc.strftime("%H:%M")
    if open_utc > close_utc:
        is_time_open = current_time >= open_utc or current_time < close_utc
    else:
        is_time_open = open_utc <= current_time < close_utc
    if not is_time_open: return False
    if lunch_start and lunch_end and (lunch_start <= current_time < lunch_end): return False
    return True

def fetch_data():
    try:
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute("SELECT ticker, region, timezone, open_utc, close_utc, lunch_start_utc, lunch_end_utc, trading_days FROM market_hours")
            hours = {r[0]: r for r in cur.fetchall()}
            
            cur.execute("""
                SELECT ticker, name, region, country,
                       (array_agg(close ORDER BY open_time DESC))[1] as current_price,
                       (array_agg(open ORDER BY open_time ASC))[1] as day_open
                FROM market_indices
                WHERE open_time >= NOW() - INTERVAL '7 days'
                GROUP BY ticker, name, region, country
            """)
            indices = {r[0]: r for r in cur.fetchall()}
        conn.close()
        return hours, indices
    except Exception as e:
        print(f"DB Error: {e}")
        sys.exit(1)

def build_report(hours, indices):
    now_utc = datetime.now(timezone.utc)
    now_kld = now_utc.astimezone(TZ)
    
    lines = [
        "🌍 <b>ARGUS — Global Market (Утро)</b>",
        f"📅 {now_kld.strftime('%d.%m.%Y, %H:%M')} КЛГ",
        ""
    ]

    regions = {
        "Asia": {"flag": "🌏 Азия", "lines": []},
        "Europe": {"flag": "🇪🇺 Европа", "lines": []},
        "US": {"flag": "🇺🇸 США", "lines": []},
        "Global": {"flag": "💵 Сырье и Валюта", "lines": []}
    }

    for ticker, h_row in hours.items():
        region = h_row[1]
        if region not in regions: continue
        
        idx = indices.get(ticker)
        disp_name = TICKER_NAMES.get(ticker, ticker)
        
        if idx:
            curr_price = float(idx[4])
            day_open = float(idx[5])
            pct = ((curr_price - day_open) / day_open * 100) if day_open else 0
            arrow = "🔼" if pct > 0 else "🔽" if pct < 0 else "⏸️"
            
            # Форматирование в зависимости от актива
            if ticker in ("GC=F", "CL=F"):
                price_str = f"${curr_price:,.2f}"
            else:
                price_str = f"{curr_price:,.2f} п."
                
            line = f"  {disp_name}: {price_str} ({arrow} {pct:+.1f}%)"
        else:
            reason = get_missing_data_reason(h_row, now_utc)
            line = f"  {disp_name}: нет данных {reason}"
            
        regions[region]["lines"].append(line)

    for r_key in ["Asia", "Europe", "US", "Global"]:
        r_data = regions[r_key]
        if not r_data["lines"]: continue
        
        sample_ticker = next((t for t, h in hours.items() if h[1] == r_key), None)
        if sample_ticker:
            h = hours[sample_ticker]
            open_kld = add_hours(h[3], 2)
            close_kld = add_hours(h[4], 2)
            is_open = is_market_open(h, now_utc)
            
            is_trading_day = now_utc.isoweekday() in ([1,2,3,4,5] if h[7]=="1-5" else [7,1,2,3,4])
            if not is_trading_day:
                status = "🔴 Выходной"
            elif is_open:
                status = "🟢 Открыт"
            else:
                status = "🔴 Закрыт"
            
            time_str = "(Торги: 24/5)" if r_key == "Global" else f"(Торги: {open_kld} - {close_kld} КЛГ)"
            lines.append(f"{r_data['flag']} {time_str} {status}:")
        else:
            lines.append(f"{r_data['flag']}:")
            
        lines.extend(r_data["lines"])
        lines.append("")

    sp500 = indices.get("^GSPC")
    dxy = indices.get("DX-Y.NYB")
    
    if sp500 and dxy:
        sp_pct = ((float(sp500[4]) - float(sp500[5])) / float(sp500[5]) * 100) if float(sp500[5]) else 0
        dxy_pct = ((float(dxy[4]) - float(dxy[5])) / float(dxy[5]) * 100) if float(dxy[5]) else 0
        
        lines.append("────────────────────")
        lines.append("💡 <b>Макро-контекст:</b>")
        
        if sp_pct > 0 and dxy_pct < 0:
            lines.append("  Рынок в режиме <b>Risk-On</b> (аппетит к риску растет).")
            lines.append("  S&P 500 растет, доллар слабеет — благоприятный")
            lines.append("  фон для роста криптовалют.")
        elif sp_pct < 0 and dxy_pct > 0:
            lines.append("  Рынок в режиме <b>Risk-Off</b> (уход от рисков).")
            lines.append("  S&P 500 падает, доллар укрепляется — инвесторы")
            lines.append("  уходят в защитные активы, давление на крипту.")
        else:
            lines.append("  Смешанный фон. Рынки разнонаправлены,")
            lines.append("  явного перевеса быков или медведей нет.")

    return "\n".join(lines)

def send_message(text):
    if not BOT_TOKEN or not CHAT_ID:
        print("no token/chat")
        return False
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        r = requests.post(url, json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}, timeout=20)
        return r.status_code == 200
    except Exception as e:
        print(f"Send error: {e}")
        return False

def main():
    hours, indices = fetch_data()
    text = build_report(hours, indices)
    print(f"Report len: {len(text)}")
    send_message(text)

if __name__ == "__main__":
    main()