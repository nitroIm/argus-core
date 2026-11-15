# ============================================================
# ARGUS - SIGNAL BOT v9.5.0
# Path: crypto/signals/signal_bot.py
#
# ИЗМЕНЕНИЯ v9.5.0:
#   - Динамический TP/SL от ATR (вместо жестких 2%/1%)
#   - Частичная фиксация (50% на TP1, перевод стопа в БУ)
#   - Трейлинг второй половины до TP2
#   - Свинг держится до 168ч (неделя), выход только по флэту
# ============================================================

import os, json, logging, base64, pickle, requests
from pathlib import Path
from datetime import datetime, timezone, timedelta
import psycopg
import pandas as pd
import numpy as np
import xgboost as xgb

VERSION = "9.5.0"
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("crypto.signal_bot")

SCRIPT_DIR = Path(__file__).resolve().parent
CRYPTO_ROOT = SCRIPT_DIR.parent
MODELS_DIR = CRYPTO_ROOT / "data" / "models"
MODEL_FILE = MODELS_DIR / "best_params.json"
SIGNALS_DIR = CRYPTO_ROOT / "data" / "signals"
CONFIG_FILE = CRYPTO_ROOT / "data" / "intel" / "bot_config.json"

SIGNALS_DIR.mkdir(parents=True, exist_ok=True)
OPENED_DEALS = SIGNALS_DIR / "opened_deals.json"
CLOSED_DEALS = SIGNALS_DIR / "closed_deals.json"
INFO_DEAL = SIGNALS_DIR / "info_deal.json"
BOT_INFO = SIGNALS_DIR / "bot_info.json"

DB_URL = (os.getenv("AIVEN_DB_URL") or "").strip()
SYMBOL = "BTCUSDT"
TG_TOKEN = (os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN") or "").strip()
TG_CHAT = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()

def get_conn():
    if not DB_URL: raise RuntimeError("AIVEN_DB_URL not set")
    return psycopg.connect(DB_URL, connect_timeout=30)

def load_json(path, default=None):
    if not path.exists(): return default if default is not None else {}
    try:
        with open(path, "r", encoding="utf-8") as f: return json.load(f)
    except Exception: return default if default is not None else {}

def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f: json.dump(data, f, ensure_ascii=False, indent=2)

def append_event(event):
    queue = load_json(INFO_DEAL, [])
    if not isinstance(queue, list): queue = []
    queue.append(event)
    save_json(INFO_DEAL, queue)

def send_tg_text(text):
    if not TG_TOKEN or not TG_CHAT: return False
    try:
        requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage", json={"chat_id": TG_CHAT, "text": text, "parse_mode": "HTML"}, timeout=10)
        return True
    except Exception as e:
        log.error(f"TG alert failed: {e}")
        return False

def check_data_freshness(df_15m, config, bot_info):
    if df_15m.empty: return False
    last_time = df_15m['open_time'].iloc[-1]
    now_utc = datetime.now(timezone.utc)
    diff_min = (now_utc - last_time).total_seconds() / 60.0
    limit_min = float(config.get("global", {}).get("stale_data_limit_min", 45.0))
    if diff_min > limit_min:
        last_alert = bot_info.get("last_stale_alert_ts")
        should_alert = True
        if last_alert:
            try:
                if (now_utc - datetime.fromisoformat(last_alert)).total_seconds() < 3600: should_alert = False
            except: pass
        if should_alert:
            send_tg_text(f"⚠️ <b>ARGUS: НЕТ ДАННЫХ</b>\nПоследняя свеча: {last_time.strftime('%H:%M')} UTC\nПрошло: {diff_min:.0f} мин.\nБот приостановлен.")
            bot_info["last_stale_alert_ts"] = now_utc.isoformat()
            save_json(BOT_INFO, bot_info)
        return False
    return True

def ensure_bot_info(bot_info):
    for mode in ["intraday", "swing"]:
        if mode not in bot_info: bot_info[mode] = {"score_history": [], "score_archive": []}
        if "started_at" not in bot_info[mode]: bot_info[mode]["started_at"] = datetime.now(timezone.utc).isoformat()
        if "balance" not in bot_info[mode]: bot_info[mode]["balance"] = 100.0
        if "start_balance" not in bot_info[mode]: bot_info[mode]["start_balance"] = 100.0
        if "last_check_ts" not in bot_info[mode]: bot_info[mode]["last_check_ts"] = datetime.now(timezone.utc).isoformat()
    save_json(BOT_INFO, bot_info)
    return bot_info

def calculate_indicators(df_1h):
    close = df_1h['close']
    df_1h['rsi'] = 100 - 100 / (1 + close.diff().where(close.diff() > 0, 0).rolling(14).mean() / (-close.diff().where(close.diff() < 0, 0)).rolling(14).mean())
    df_1h['rsi_7'] = 100 - 100 / (1 + close.diff().where(close.diff() > 0, 0).rolling(7).mean() / (-close.diff().where(close.diff() < 0, 0)).rolling(7).mean())
    df_1h['rsi_21'] = 100 - 100 / (1 + close.diff().where(close.diff() > 0, 0).rolling(21).mean() / (-close.diff().where(close.diff() < 0, 0)).rolling(21).mean())
    df_1h['ema_20'] = close.ewm(span=20).mean()
    df_1h['ema_50'] = close.ewm(span=50).mean()
    df_1h['ema_200'] = close.ewm(span=200).mean()
    macd = close.ewm(span=12).mean() - close.ewm(span=26).mean()
    df_1h['macd_hist'] = macd - macd.ewm(span=9).mean()
    df_1h['vol_sma'] = df_1h['volume'].rolling(20).mean()
    df_1h['atr_7'] = (df_1h['high'] - df_1h['low']).rolling(7).mean()
    df_1h['atr_14'] = (df_1h['high'] - df_1h['low']).rolling(14).mean()
    df_1h['atr_50'] = (df_1h['high'] - df_1h['low']).rolling(50).mean()
    df_1h['atr_100'] = (df_1h['high'] - df_1h['low']).rolling(100).mean()
    df_1h['buy_ratio'] = df_1h['taker_buy_volume'] / df_1h['volume'].replace(0, 1)
    df_1h['avg_trade_size'] = df_1h['volume'] / df_1h['count'].replace(0, 1)
    df_1h['price_change_1h'] = close.pct_change(1)
    df_1h['price_change_4h'] = close.pct_change(4)
    df_1h['price_change_24h'] = close.pct_change(24)
    return df_1h.dropna()

def fetch_macro_for_bot():
    if not DB_URL: return pd.DataFrame()
    try:
        conn = get_conn()
        frames = []
        df_mi = pd.read_sql("SELECT ticker, open_time AS ts, close FROM market_indices WHERE open_time > NOW() - INTERVAL '7 days' ORDER BY open_time ASC", conn)
        if not df_mi.empty:
            df_mi['ts'] = pd.to_datetime(df_mi['ts'], utc=True)
            for t, c in {'^IXIC':'nasdaq','DX-Y.NYB':'dxy'}.items():
                sub = df_mi[df_mi['ticker']==t][['ts','close']].copy()
                if sub.empty: continue
                frames.append(sub.rename(columns={'close':c}).sort_values('ts').set_index('ts'))
        df_gm = pd.read_sql("SELECT last_updated AS ts, btc_dominance, stablecoin_market_cap FROM global_metrics WHERE last_updated > NOW() - INTERVAL '7 days' ORDER BY last_updated ASC", conn)
        if not df_gm.empty: df_gm['ts'] = pd.to_datetime(df_gm['ts'], utc=True); frames.append(df_gm.set_index('ts'))
        df_st = pd.read_sql("SELECT timestamp AS ts, SUM(circulating) AS stablecoin_supply FROM stablecoins WHERE timestamp > NOW() - INTERVAL '7 days' GROUP BY timestamp ORDER BY timestamp ASC", conn)
        if not df_st.empty: df_st['ts'] = pd.to_datetime(df_st['ts'], utc=True); frames.append(df_st.set_index('ts'))
        df_fg = pd.read_sql("SELECT timestamp AS ts, value AS fear_greed FROM fear_greed WHERE timestamp > NOW() - INTERVAL '7 days' ORDER BY timestamp ASC", conn)
        if not df_fg.empty: df_fg['ts'] = pd.to_datetime(df_fg['ts'], utc=True); frames.append(df_fg.set_index('ts'))
        conn.close()
        return pd.concat(frames, axis=1, sort=True) if frames else pd.DataFrame()
    except Exception as e:
        log.error(f"Macro fetch failed: {e}"); return pd.DataFrame()

def merge_macro_for_bot(df_btc, macro):
    if macro.empty: return df_btc
    df_btc = df_btc.sort_values('open_time').reset_index(drop=True)
    macro = macro.sort_index()
    result = df_btc.copy()
    def asof(left, cols, tol_h):
        cols = [c for c in cols if c in macro.columns]
        if not cols: return left
        right = macro[cols].dropna(how='all').reset_index().rename(columns={macro.index.name or 'index': 'ts'}).sort_values('ts')
        left_s = left.sort_values('open_time').rename(columns={'open_time':'ts'})
        m = pd.merge_asof(left_s, right, on='ts', direction='backward', allow_exact_matches=True, tolerance=pd.Timedelta(hours=tol_h))
        return m.rename(columns={'ts':'open_time'})
    result = asof(result, ['nasdaq','dxy'], 72)
    result = asof(result, ['btc_dominance','stablecoin_market_cap'], 6)
    result = asof(result, ['stablecoin_supply'], 6)
    result = asof(result, ['fear_greed'], 48)
    return result

def get_features_for_ml(df_1h, feature_cols):
    df = df_1h.copy()
    df['rsi_norm'] = df['rsi'] / 100.0
    df['rsi_7_norm'] = df['rsi_7'] / 100.0
    df['rsi_21_norm'] = df['rsi_21'] / 100.0
    df['macd_norm'] = df['macd_hist'] / df['close']
    df['ema50_diff'] = (df['close'] - df['ema_50']) / df['ema_50']
    df['ema20_diff'] = (df['close'] - df['ema_20']) / df['ema_20']
    df['ema200_diff'] = (df['close'] - df['ema_200']) / df['ema_200']
    df['vol_ratio'] = df['volume'] / df['vol_sma']
    df['atr7_pct'] = df['atr_7'] / df['close']
    df['atr14_pct'] = df['atr_14'] / df['close']
    df['atr50_pct'] = df['atr_50'] / df['close']
    df['atr100_pct'] = df['atr_100'] / df['close']
    df['avg_trade_norm'] = df['avg_trade_size'] / df['volume'].rolling(20).mean()
    df['pc1'] = df['price_change_1h']; df['pc4'] = df['price_change_4h']; df['pc24'] = df['price_change_24h']
    for lag in [1, 3, 6, 12, 24]:
        for f in ['rsi_norm','macd_norm','ema50_diff','vol_ratio','buy_ratio']:
            df[f'{f}_lag{lag}'] = df[f].shift(lag)
    df = df.dropna()
    if df.empty: return None
    macro = fetch_macro_for_bot()
    if not macro.empty:
        df_m = merge_macro_for_bot(df.reset_index(), macro).set_index('open_time')
        if 'nasdaq' in df_m.columns:
            df_m['nasdaq_pct_1h'] = df_m['nasdaq'].pct_change(1); df_m['nasdaq_pct_24h'] = df_m['nasdaq'].pct_change(24)
        if 'dxy' in df_m.columns:
            df_m['dxy_pct_1h'] = df_m['dxy'].pct_change(1); df_m['dxy_pct_24h'] = df_m['dxy'].pct_change(24)
        if 'fear_greed' in df_m.columns: df_m['fear_greed_norm'] = df_m['fear_greed'] / 100.0
        if 'btc_dominance' in df_m.columns: df_m['btc_dom_pct'] = df_m['btc_dominance'].pct_change(24)
        if 'stablecoin_supply' in df_m.columns:
            df_m['stable_flow_1h'] = df_m['stablecoin_supply'].pct_change(1); df_m['stable_flow_24h'] = df_m['stablecoin_supply'].pct_change(24)
        df = df_m
    cols = [c for c in feature_cols if c in df.columns]
    if not cols: return None
    return df[cols].iloc[[-1]].values

def load_ml_model(node):
    if not node or "params" not in node: return None, None
    p = node["params"]; mt = p.get("model_type", "")
    if "model_file" in p and p["model_file"]:
        fpath = MODELS_DIR / p["model_file"]
        if not fpath.exists(): return None, None
        try:
            if "xgb" in mt: bst = xgb.Booster(); bst.load_model(str(fpath)); return "xgb", bst
            elif "lgbm" in mt: import lightgbm as lgb; return "lgbm", lgb.Booster(model_file=str(fpath))
            elif "catboost" in mt:
                from catboost import CatBoostRegressor; m = CatBoostRegressor(); m.load_model(str(fpath)); return "catboost", m
            else:
                with open(fpath, "rb") as f: return "sklearn", pickle.load(f)
        except Exception: return None, None
    return None, None

def predict_ml(node, df_1h):
    if not node or "params" not in node: return None
    feat = node["params"].get("features", [])
    mt, model = load_ml_model(node)
    if model is None: return None
    try:
        X = get_features_for_ml(df_1h, feat)
        if X is None or len(X) == 0: return None
        if mt == "xgb": return float(model.predict(xgb.DMatrix(X))[0])
        else: return float(model.predict(X)[0])
    except Exception: return None

def is_ml_ready(node, cfg):
    if not node: return False
    m = node.get("metrics", {}) or {}
    ml_cfg = cfg.get("ml_deploy", {})
    return int(m.get("trades", 0) or 0) >= int(ml_cfg.get("min_trades_for_live", 10)) and float(m.get("score", -99999) or -99999) >= float(ml_cfg.get("min_score_for_live", 0.0))

def process_mode(mode, deals, bot_info, models, df_1h, config):
    cfg = config.get(mode, {})
    rm = config.get("risk_management", {})
    fee_pct = config.get("global", {}).get("fee_pct", 0.001)
    now_utc = datetime.now(timezone.utc)

    if mode in deals and "entry" in deals[mode]:
        deal = deals[mode]
        deal_type = deal.get("type", "LONG")
        entry = float(deal['entry'])
        sl = float(deal['sl'])
        tp1 = float(deal.get('tp1', deal.get('tp', 0)))
        tp2 = float(deal.get('tp2', 0))
        size_left = float(deal.get('size_left_pct', 100))
        tp1_hit = bool(deal.get('tp1_hit', False))
        
        deal_open_time = datetime.fromisoformat(deal["open_time"])
        df_to_check = df_1h[df_1h['open_time'] >= deal_open_time - timedelta(hours=1)]
        max_price, min_price = entry, entry
        trail_mult = float(rm.get("trail_atr_mult", 2.0))
        
        log.info(f"🔄 [{mode.upper()}] {deal_type} | SL:{sl:.1f} TP1:{tp1:.1f} TP2:{tp2:.1f} | Price:{df_1h.iloc[-1]['close']:.1f} | Left:{size_left}%")

        for _, row in df_to_check.iterrows():
            c_high, c_low = float(row['high']), float(row['low'])
            c_atr = float(row['atr_14']) if pd.notna(row['atr_14']) else 0.0
            
            if deal_type == "LONG":
                if c_high > max_price: max_price = c_high
                if c_low < min_price: min_price = c_low
            else:
                if c_low < min_price: min_price = c_low
                if c_high > max_price: max_price = c_high

            # 1. Проверка SL (если сработал - выходим из остатка)
            if (deal_type == "LONG" and c_low <= sl) or (deal_type == "SHORT" and c_high >= sl):
                exit_price = sl
                reason = "TRAILING_STOP" if (tp1_hit or sl > entry if deal_type=="LONG" else sl < entry) else "STOP_LOSS"
                _close_deal(mode, deal, bot_info, exit_price, reason, size_left, fee_pct, now_utc)
                return

            # 2. Достижение TP2 (закрываем остаток)
            if (deal_type == "LONG" and c_high >= tp2) or (deal_type == "SHORT" and c_low <= tp2):
                _close_deal(mode, deal, bot_info, tp2, "TAKE_PROFIT_2", size_left, fee_pct, now_utc)
                return

            # 3. Достижение TP1 (фиксируем 50%, переводим SL в БУ)
            if not tp1_hit and ((deal_type == "LONG" and c_high >= tp1) or (deal_type == "SHORT" and c_low <= tp1)):
                tp1_hit = True
                close_pct = float(rm.get("partial_exit_pct", 50))
                _close_deal(mode, deal, bot_info, tp1, "TAKE_PROFIT_1", close_pct, fee_pct, now_utc)
                size_left -= close_pct
                # Переводим стоп в безубыток (с учетом комиссии)
                sl = entry * (1 + fee_pct*2) if deal_type == "LONG" else entry * (1 - fee_pct*2)
                deal['tp1_hit'] = True
                deal['sl'] = sl
                save_json(OPENED_DEALS, deals)
                log.info(f"💰 [{mode.upper()}] TP1 HIT! Closed {close_pct}%. SL moved to BE: {sl:.2f}")

            # 4. Трейлинг стопа (только если TP1 взят)
            if tp1_hit and c_atr > 0:
                if deal_type == "LONG":
                    new_sl = max_price - (c_atr * trail_mult)
                    if new_sl > sl + 0.01:
                        sl = round(new_sl, 2)
                        deal['sl'] = sl
                else:
                    new_sl = min_price + (c_atr * trail_mult)
                    if new_sl < sl - 0.01:
                        sl = round(new_sl, 2)
                        deal['sl'] = sl

        # Проверка выхода по времени
        time_held = (now_utc - deal_open_time).total_seconds() / 3600.0
        close_h = cfg.get("close_hour_utc")
        
        # Intraday: жесткое закрытие в 19:00 UTC
        if mode == "intraday" and close_h is not None and now_utc.hour >= close_h:
            _close_deal(mode, deal, bot_info, float(df_1h.iloc[-1]['close']), "TIME_EXIT_DAY", size_left, fee_pct, now_utc)
            return
            
        # Swing: умный выход (если висит долго и рынок во флэте)
        max_hold = float(cfg.get("time_exit_h", 168))
        if mode == "swing" and time_held >= max_hold:
            flat_thresh = entry * (rm.get("swing_flat_atr_mult", 2.0) * c_atr / entry) if c_atr > 0 else 0.02
            price_range = max_price - min_price
            if price_range < flat_thresh:
                _close_deal(mode, deal, bot_info, float(df_1h.iloc[-1]['close']), "TIME_EXIT_FLAT", size_left, fee_pct, now_utc)
                return

        deal['sl'] = sl
        deal['size_left_pct'] = size_left
        save_json(BOT_INFO, bot_info)
        save_json(OPENED_DEALS, deals)
        return

    # --- ОТКРЫТИЕ НОВОЙ ПОЗИЦИИ ---
    node_long = models.get(mode, {}).get("long")
    node_short = models.get(mode, {}).get("short")
    
    price = float(df_1h.iloc[-1]['close'])
    atr = float(df_1h.iloc[-1]['atr_14'])
    if pd.isna(atr) or atr == 0: atr = price * 0.01 # fallback
    
    rsi = float(df_1h.iloc[-1]['rsi'])
    macd_hist = float(df_1h.iloc[-1]['macd_hist'])
    ema = float(df_1h.iloc[-1]['ema_50'])
    vol_sma = float(df_1h.iloc[-1]['vol_sma'])
    cur_vol = float(df_1h.iloc[-1]['volume'])
    w = cfg.get("weights", {})

    score_long, active_long = 0.0, []
    pred_long = predict_ml(node_long, df_1h) if is_ml_ready(node_long, config) else None
    if pred_long is not None:
        score_long = pred_long * 10; active_long = ["ml"]
    else:
        if macd_hist > 0: score_long += w.get("macd",1.0); active_long.append("macd")
        if price > ema: score_long += w.get("ema",1.0); active_long.append("ema")
        if cur_vol > vol_sma: score_long += w.get("volume",1.0); active_long.append("volume")
        if rsi < 30: score_long += w.get("rsi_low",1.0); active_long.append("rsi_low")
        elif rsi < 70: score_long += w.get("rsi_mid",1.0); active_long.append("rsi_mid")

    score_short, active_short = 0.0, []
    pred_short = predict_ml(node_short, df_1h) if is_ml_ready(node_short, config) else None
    if pred_short is not None:
        score_short = -pred_short * 10; active_short = ["ml"]
    else:
        if macd_hist < 0: score_short += w.get("macd",1.0); active_short.append("macd")
        if price < ema: score_short += w.get("ema",1.0); active_short.append("ema")
        if cur_vol > vol_sma: score_short += w.get("volume",1.0); active_short.append("volume")
        if rsi > 70: score_short += w.get("rsi_high",1.0); active_short.append("rsi_high")
        elif rsi > 30: score_short += w.get("rsi_mid",1.0); active_short.append("rsi_mid")

    thresh = cfg.get("threshold", 2.0)
    can_open = True
    no_entry_str = cfg.get("no_entry_time_utc")
    if mode == "intraday" and no_entry_str:
        try:
            ne_h, ne_m = map(int, no_entry_str.split(":"))
            if now_utc.hour > ne_h or (now_utc.hour == ne_h and now_utc.minute >= ne_m): can_open = False
        except Exception: pass

    log.info(f"[{mode.upper()}] Price:{price:.1f} | LONG:{score_long:.2f} | SHORT:{score_short:.2f} | Thresh:{thresh:.2f}")

    if can_open and score_long >= thresh and score_long > score_short:
        sl_pct = max(rm.get("min_sl_pct",0.5), min(rm.get("max_sl_pct",3.0), atr * rm.get("atr_mult_sl",1.5) / price * 100))
        tp1_pct = max(rm.get("min_tp1_pct",0.8), min(rm.get("max_tp1_pct",2.0), atr * rm.get("atr_mult_tp1",1.0) / price * 100))
        tp2_pct = max(rm.get("min_tp2_pct",2.0), min(rm.get("max_tp2_pct",8.0), atr * rm.get("atr_mult_tp2",2.5) / price * 100))
        
        deal = {"type":"LONG","entry":price,"sl":price*(1-sl_pct/100),"tp1":price*(1+tp1_pct/100),"tp2":price*(1+tp2_pct/100),
                "size_left_pct":100,"tp1_hit":False,"open_time":now_utc.isoformat(),"active_signals":active_long}
        deals[mode] = deal
        append_event({"type":"ENTRY","mode":mode,"deal_type":"LONG","entry":price,"tp1":deal["tp1"],"tp2":deal["tp2"],"sl":deal["sl"]})
        log.info(f"🚀 [{mode.upper()}] LONG SIGNAL! SL:{sl_pct:.2f}% TP1:{tp1_pct:.2f}% TP2:{tp2_pct:.2f}%")
        
    elif can_open and score_short >= thresh and score_short > score_long:
        sl_pct = max(rm.get("min_sl_pct",0.5), min(rm.get("max_sl_pct",3.0), atr * rm.get("atr_mult_sl",1.5) / price * 100))
        tp1_pct = max(rm.get("min_tp1_pct",0.8), min(rm.get("max_tp1_pct",2.0), atr * rm.get("atr_mult_tp1",1.0) / price * 100))
        tp2_pct = max(rm.get("min_tp2_pct",2.0), min(rm.get("max_tp2_pct",8.0), atr * rm.get("atr_mult_tp2",2.5) / price * 100))
        
        deal = {"type":"SHORT","entry":price,"sl":price*(1+sl_pct/100),"tp1":price*(1-tp1_pct/100),"tp2":price*(1-tp2_pct/100),
                "size_left_pct":100,"tp1_hit":False,"open_time":now_utc.isoformat(),"active_signals":active_short}
        deals[mode] = deal
        append_event({"type":"ENTRY","mode":mode,"deal_type":"SHORT","entry":price,"tp1":deal["tp1"],"tp2":deal["tp2"],"sl":deal["sl"]})
        log.info(f"🩸 [{mode.upper()}] SHORT SIGNAL! SL:{sl_pct:.2f}% TP1:{tp1_pct:.2f}% TP2:{tp2_pct:.2f}%")

    bot_info[mode]["last_check_ts"] = now_utc.isoformat()
    save_json(BOT_INFO, bot_info)

def _close_deal(mode, deal, bot_info, exit_price, reason, close_pct, fee_pct, now_utc):
    entry = float(deal['entry'])
    deal_type = deal.get("type", "LONG")
    pos_notional_pct = close_pct / 100.0
    
    pnl_pct = (exit_price - entry) / entry * 100 if deal_type == "LONG" else (entry - exit_price) / entry * 100
    pnl_pct -= (fee_pct * 2 * 100)
    
    # PnL считается только от закрываемой части баланса
    pnl_usd = bot_info[mode]["balance"] * pos_notional_pct * (pnl_pct / 100)
    bot_info[mode]["balance"] = round(bot_info[mode]["balance"] + pnl_usd, 2)
    
    append_event({"type":"CLOSE","mode":mode,"deal_type":deal_type,"reason":reason,"pnl_pct":round(pnl_pct,2),"pnl_usd":round(pnl_usd,2),"new_balance":bot_info[mode]["balance"],"entry":entry,"exit":exit_price})
    
    closed = load_json(CLOSED_DEALS, [])
    closed.append({"type":deal_type,"entry":entry,"exit":exit_price,"pnl_pct":round(pnl_pct,2),"pnl_usd":round(pnl_usd,2),"reason":reason,"mode":mode,"close_time":now_utc.isoformat(),"balance_after":bot_info[mode]["balance"],"active_signals":deal.get("active_signals",[])})
    save_json(CLOSED_DEALS, closed)
    
    # Если это был полный выход (TP2, SL, TIME_EXIT) — удаляем сделку
    if close_pct >= 99.9 or "TP1" not in reason:
        deals = load_json(OPENED_DEALS, {})
        if mode in deals: del deals[mode]
        save_json(OPENED_DEALS, deals)

def main():
    log.info(f"STARTING DUAL BOT v{VERSION}")
    config = load_json(CONFIG_FILE, {})
    if not config: return log.error("bot_config.json не найден")
    bot_info = ensure_bot_info(load_json(BOT_INFO, {}))
    models = load_json(MODEL_FILE)
    deals = load_json(OPENED_DEALS, {})
    if "intraday" not in deals: deals["intraday"] = {}
    if "swing" not in deals: deals["swing"] = {}

    try:
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute("SELECT open_time, open, high, low, close, volume, taker_buy_volume, count FROM candles WHERE symbol=%s AND timeframe='15m' AND market_type='spot' ORDER BY open_time DESC LIMIT 500", (SYMBOL,))
            rows = list(reversed(cur.fetchall()))
        conn.close()
    except Exception as e: return log.error(f"DB error: {e}")

    df = pd.DataFrame([(r[0], float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]), float(r[6]), int(r[7])) for r in rows], columns=['open_time','open','high','low','close','volume','taker_buy_volume','count'])
    df['open_time'] = pd.to_datetime(df['open_time'], utc=True)
    
    if not check_data_freshness(df, config, bot_info):
        return log.warning("Stale data. Bot paused.")

    df_1h = df.set_index('open_time').resample('1h').agg({'open':'first','high':'max','low':'min','close':'last','volume':'sum','taker_buy_volume':'sum','count':'sum'}).dropna().reset_index()
    df_1h = calculate_indicators(df_1h.set_index('open_time')).reset_index()

    process_mode("intraday", deals, bot_info, models, df_1h, config)
    process_mode("swing", deals, bot_info, models, df_1h, config)

    save_json(OPENED_DEALS, deals); save_json(BOT_INFO, bot_info)

if __name__ == "__main__":
    main()