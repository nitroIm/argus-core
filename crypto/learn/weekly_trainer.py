# ============================================================
# ARGUS - WEEKLY TRAINER ORCHESTRATOR v7.5.0
# Path: crypto/learn/weekly_trainer.py
#
# ИЗМЕНЕНИЯ v7.5.0:
#   - Модели в отдельных файлах (.xgb/.txt/.cbm/.pkl), best_params.json ~5KB
#   - Автомиграция старого формата model_b64 -> файлы при старте
#   - При None от всех моделей — сохраняем старую (не пишем null)
#   - min_score из backtest: отрицательный Score не деплоится
#   - Лог all_scores по всем 5 моделям после каждого (mode, side)
#   - merge_asof с tolerance по каждой макро-таблице
#   - Без bfill (look-ahead), pre-29.10.2026 макро = NaN
#   - Свежие данные из БД приоритет над parquet (keep='last')
#   - lookback_days из bot_config.json (default 2190 = 6 лет)
# ============================================================

import os, json, logging, importlib.util, subprocess, base64
from pathlib import Path
from datetime import datetime, timezone
import pandas as pd
import numpy as np
import psycopg
import warnings

warnings.filterwarnings("ignore", message=".*SQLAlchemy.*")

VERSION = "7.5.0"
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("crypto.trainer")

# ── Пути ─────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
DATA_FILE = SCRIPT_DIR.parent / "collect" / "vision_out" / "BTCUSDT_spot_1h.parquet"
MODELS_DIR = SCRIPT_DIR.parent / "data" / "models"
MODEL_OUT_FILE = MODELS_DIR / "best_params.json"
CONFIG_FILE = SCRIPT_DIR.parent / "data" / "intel" / "bot_config.json"

MODELS_DIR.mkdir(parents=True, exist_ok=True)
DB_URL = (os.getenv("AIVEN_DB_URL") or "").strip()

# ── Индикаторы ───────────────────────────────────────────────
def calculate_ema(series, period):
    return series.ewm(span=period, adjust=False).mean()

def calculate_rsi(series, period=14):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def calculate_macd(series, fast=12, slow=26, signal=9):
    ema_fast = calculate_ema(series, fast)
    ema_slow = calculate_ema(series, slow)
    macd_line = ema_fast - ema_slow
    signal_line = calculate_ema(macd_line, signal)
    return macd_line - signal_line

def calculate_atr(df, period=14):
    high_low = df['high'] - df['low']
    high_close = (df['high'] - df['close'].shift()).abs()
    low_close = (df['low'] - df['close'].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return tr.rolling(window=period).mean()

# ── Утилиты ──────────────────────────────────────────────────
def load_json(path, default=None):
    if not path.exists():
        return default if default is not None else {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log.error(f"Failed to read {path}: {e}")
        return default if default is not None else {}

def save_json(path, data):
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)

# ── Миграция старого формата best_params.json ────────────────
def migrate_old_format():
    """
    Если в best_params.json есть model_b64 — извлекаем в отдельные файлы,
    заменяем на model_file. Идемпотентно: повторный вызов ничего не делает.
    """
    if not MODEL_OUT_FILE.exists():
        return
    data = load_json(MODEL_OUT_FILE)
    migrated = False
    for mode in ["intraday", "swing"]:
        for side in ["long", "short"]:
            node = data.get(mode, {}).get(side)
            if not node or "params" not in node:
                continue
            p = node["params"]
            if "model_b64" not in p:
                continue
            mt = p.get("model_type", "")
            ext = {"xgboost_reg": "xgb", "lgbm_reg": "txt", "catboost_reg": "cbm",
                   "random_forest_reg": "pkl", "linear_reg": "pkl"}.get(mt, "bin")
            fname = f"model_{mt}_{mode}_{side}.{ext}"
            fpath = MODELS_DIR / fname
            try:
                raw = base64.b64decode(p["model_b64"])
                with open(fpath, "wb") as f:
                    f.write(raw)
                p["model_file"] = fname
                del p["model_b64"]
                # добавим time_exit_h, если нет
                if "time_exit_h" not in p:
                    p["time_exit_h"] = p.get("horizon", 24)
                migrated = True
                log.info(f"  ↪ Migrated {mode}/{side} -> {fname} ({len(raw)} bytes)")
            except Exception as e:
                log.error(f"  ✗ Migration failed {mode}/{side}: {e}")
    if migrated:
        save_json(MODEL_OUT_FILE, data)
        log.info("Migration complete")

# ── Загрузка макро из БД ─────────────────────────────────────
def fetch_macro_data(start_ts):
    """
    Возвращает DataFrame с макро-фичами на 1h сетке.
    Только реальные данные, без bfill. Где нет — NaN.
    """
    log.info("Fetching Macro Data from DB...")
    if not DB_URL:
        return pd.DataFrame()
    try:
        conn = psycopg.connect(DB_URL, connect_timeout=30)
        frames = []

        # market_indices (индексы, DXY, gold, oil) — только close, только когда рынок торговал
        df_mi = pd.read_sql(
            "SELECT ticker, open_time AS ts, close FROM market_indices "
            "WHERE open_time >= %s ORDER BY open_time ASC",
            conn, params=(start_ts,)
        )
        if not df_mi.empty:
            df_mi['ts'] = pd.to_datetime(df_mi['ts'], utc=True)
            rename = {'^IXIC': 'nasdaq', 'DX-Y.NYB': 'dxy', '^GSPC': 'sp500',
                      'GC=F': 'gold', 'CL=F': 'oil', '^VIX': 'vix',
                      '^N225': 'nikkei', '^HSI': 'hsi', '^GDAXI': 'dax'}
            for ticker, col in rename.items():
                sub = df_mi[df_mi['ticker'] == ticker][['ts', 'close']].copy()
                if sub.empty:
                    continue
                sub = sub.rename(columns={'close': col}).sort_values('ts')
                frames.append(sub.set_index('ts'))

        # global_metrics
        df_gm = pd.read_sql(
            "SELECT last_updated AS ts, btc_dominance, total_market_cap, "
            "stablecoin_market_cap FROM global_metrics "
            "WHERE last_updated >= %s ORDER BY last_updated ASC",
            conn, params=(start_ts,)
        )
        if not df_gm.empty:
            df_gm['ts'] = pd.to_datetime(df_gm['ts'], utc=True)
            frames.append(df_gm.set_index('ts'))

        # stablecoins — суммарный circulating
        df_st = pd.read_sql(
            "SELECT timestamp AS ts, SUM(circulating) AS stablecoin_supply "
            "FROM stablecoins WHERE timestamp >= %s GROUP BY timestamp ORDER BY timestamp ASC",
            conn, params=(start_ts,)
        )
        if not df_st.empty:
            df_st['ts'] = pd.to_datetime(df_st['ts'], utc=True)
            frames.append(df_st.set_index('ts'))

        # fear_greed
        df_fg = pd.read_sql(
            "SELECT timestamp AS ts, value AS fear_greed FROM fear_greed "
            "WHERE timestamp >= %s ORDER BY timestamp ASC",
            conn, params=(start_ts,)
        )
        if not df_fg.empty:
            df_fg['ts'] = pd.to_datetime(df_fg['ts'], utc=True)
            frames.append(df_fg.set_index('ts'))

        conn.close()
        if not frames:
            return pd.DataFrame()
        macro = pd.concat(frames, axis=1, sort=True)
        return macro
    except Exception as e:
        log.error(f"Failed to fetch macro: {e}")
        return pd.DataFrame()

# ── Свежие свечи из БД ───────────────────────────────────────
def fetch_fresh_candles():
    if not DB_URL:
        return pd.DataFrame()
    try:
        conn = psycopg.connect(DB_URL, connect_timeout=30)
        df = pd.read_sql(
            "SELECT open_time, open, high, low, close, volume, taker_buy_volume, count "
            "FROM candles WHERE symbol='BTCUSDT' AND timeframe='15m' "
            "AND market_type='spot' AND open_time > NOW() - INTERVAL '90 days' "
            "ORDER BY open_time ASC", conn
        )
        conn.close()
        if df.empty:
            return df
        df['open_time'] = pd.to_datetime(df['open_time'], utc=True)
        df = df.set_index('open_time')
        return df.resample('1h').agg({
            'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last',
            'volume': 'sum', 'taker_buy_volume': 'sum', 'count': 'sum'
        }).dropna().reset_index()
    except Exception as e:
        log.error(f"Failed to fetch fresh candles: {e}")
        return pd.DataFrame()

# ── Загрузка моделей ─────────────────────────────────────────
def load_models():
    models = []
    for file_path in sorted(SCRIPT_DIR.glob("model_*.py")):
        try:
            spec = importlib.util.spec_from_file_location(file_path.stem, file_path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            if hasattr(module, "train"):
                models.append((file_path.stem, module))
                log.info(f"  ✓ Loaded {file_path.stem}")
        except Exception as e:
            log.error(f"  ✗ Failed to load {file_path.stem}: {e}")
    return models

# ── Мерж макро к свечам через merge_asof ─────────────────────
def merge_macro(df_btc, macro):
    """
    df_btc: свечи 1h с open_time
    macro: макро на своих TS
    Мёржим backward, tolerance по каждой колонке своя.
    """
    if macro.empty:
        return df_btc
    df_btc = df_btc.sort_values('open_time').reset_index(drop=True)
    macro = macro.sort_index()

    # tolerance для разных групп (в часах)
    # индексы/DXY/gold/oil — торгуются в будни, до 48ч (уикенд)
    # global_metrics/fear_greed/stablecoins — обновляются часто
    result = df_btc.copy()
    index_cols = ['nasdaq', 'dxy', 'sp500', 'gold', 'oil', 'vix',
                  'nikkei', 'hsi', 'dax']
    gm_cols = ['btc_dominance', 'total_market_cap', 'stablecoin_market_cap']
    st_cols = ['stablecoin_supply']
    fg_cols = ['fear_greed']

    def asof_merge(left, right_cols, tolerance_hours):
        cols = [c for c in right_cols if c in macro.columns]
        if not cols:
            return left
        right = macro[cols].dropna(how='all').reset_index()
        right = right.rename(columns={right.columns[0]: 'ts'})
        right = right.sort_values('ts')
        left_sorted = left.sort_values('open_time').rename(columns={'open_time': 'ts'})
        merged = pd.merge_asof(
            left_sorted, right,
            on='ts', direction='backward', allow_exact_matches=True,
            tolerance=pd.Timedelta(hours=tolerance_hours)
        )
        return merged.rename(columns={'ts': 'open_time'})

    result = asof_merge(result, index_cols, 72)
    result = asof_merge(result, gm_cols, 6)
    result = asof_merge(result, st_cols, 6)
    result = asof_merge(result, fg_cols, 48)
    return result

# ── Main ─────────────────────────────────────────────────────
def main():
    log.info(f"STARTING WEEKLY TRAINER ORCHESTRATOR v{VERSION}")

    config = load_json(CONFIG_FILE, {})
    bt_cfg = config.get("backtest", {})
    lookback_days = int(bt_cfg.get("lookback_days", 2190))
    min_score = float(bt_cfg.get("min_score", 0.0))

    migrate_old_format()

    if not DATA_FILE.exists():
        return log.error(f"DATA FILE NOT FOUND: {DATA_FILE}")

    # ── свечи из parquet ────────────────────────────────────
    df_hist = pd.read_parquet(DATA_FILE)
    df_hist['open_time'] = pd.to_datetime(
        df_hist['open_time'].apply(lambda x: x / 1000 if x > 1e14 else x),
        unit='ms', utc=True
    )
    if 'taker_buy_volume' not in df_hist.columns:
        df_hist['taker_buy_volume'] = df_hist['volume'] * 0.5
    if 'count' not in df_hist.columns:
        df_hist['count'] = 100

    # ── свежие из БД (приоритет) ────────────────────────────
    df_fresh = fetch_fresh_candles()
    df_btc = pd.concat([df_hist, df_fresh], ignore_index=True) \
        .drop_duplicates(subset=['open_time'], keep='last') \
        .sort_values('open_time').reset_index(drop=True)

    max_date = df_btc['open_time'].max()
    cutoff_date = max_date - pd.Timedelta(days=lookback_days)
    df_btc = df_btc[df_btc['open_time'] >= cutoff_date].reset_index(drop=True)
    log.info(f"BTC candles: {len(df_btc)} rows, {df_btc['open_time'].min()} .. {max_date}")

    # ── технические индикаторы ──────────────────────────────
    df_btc['rsi'] = calculate_rsi(df_btc['close'], 14)
    df_btc['rsi_7'] = calculate_rsi(df_btc['close'], 7)
    df_btc['rsi_21'] = calculate_rsi(df_btc['close'], 21)
    df_btc['macd_hist'] = calculate_macd(df_btc['close'], 12, 26, 9)
    df_btc['ema_50'] = calculate_ema(df_btc['close'], 50)
    df_btc['ema_20'] = calculate_ema(df_btc['close'], 20)
    df_btc['ema_200'] = calculate_ema(df_btc['close'], 200)
    df_btc['vol_sma'] = df_btc['volume'].rolling(window=20).mean()
    df_btc['atr_7'] = calculate_atr(df_btc, 7)
    df_btc['atr_14'] = calculate_atr(df_btc, 14)
    df_btc['atr_50'] = calculate_atr(df_btc, 50)
    df_btc['atr_100'] = calculate_atr(df_btc, 100)
    df_btc['buy_ratio'] = df_btc['taker_buy_volume'] / df_btc['volume'].replace(0, 1)
    df_btc['avg_trade_size'] = df_btc['volume'] / df_btc['count'].replace(0, 1)
    df_btc['price_change_1h'] = df_btc['close'].pct_change(1)
    df_btc['price_change_4h'] = df_btc['close'].pct_change(4)
    df_btc['price_change_24h'] = df_btc['close'].pct_change(24)

    # ── макро ───────────────────────────────────────────────
    macro = fetch_macro_data(cutoff_date)
    if not macro.empty:
        df = merge_macro(df_btc, macro)
        log.info(f"Merged macro. Rows: {len(df)}, macro cols: {len([c for c in df.columns if c not in df_btc.columns])}")
    else:
        log.warning("No macro data. Training BTC-only.")
        df = df_btc

    if len(df) < 1000:
        return log.error(f"CRITICAL: Not enough rows ({len(df)})")

    log.info(f"Training window: {df['open_time'].min().strftime('%Y-%m-%d')} .. "
             f"{df['open_time'].max().strftime('%Y-%m-%d')} | Rows: {len(df)}")

    # ── модели ──────────────────────────────────────────────
    models = load_models()
    if not models:
        return log.error("No models loaded")

    old_data = load_json(MODEL_OUT_FILE)
    out_data = {
        "trainer_version": VERSION,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "intraday": {"long": None, "short": None},
        "swing": {"long": None, "short": None},
        "all_scores": {}
    }

    for mode in ["intraday", "swing"]:
        out_data["all_scores"][mode] = {}
        for side in ["long", "short"]:
            log.info(f"--- Searching best model for {mode.upper()} / {side.upper()} ---")
            best_score = -99999.0
            best_model_data = None
            model_scores = {}

            for model_name, module in models:
                try:
                    res = module.train(df, side=side, mode=mode,
                                       models_dir=MODELS_DIR, cfg=config)
                    if res and res.get("metrics"):
                        sc = res["metrics"].get("score", -99999.0)
                        model_scores[model_name] = res["metrics"]
                        log.info(f"  · {model_name}: score={sc}, trades={res['metrics'].get('trades',0)}, "
                                 f"winrate={res['metrics'].get('winrate',0)}, "
                                 f"pnl={res['metrics'].get('pnl',0)}, "
                                 f"max_dd={res['metrics'].get('max_dd',0)}")
                        if sc > best_score:
                            best_score = sc
                            best_model_data = res
                            log.info(f"  🏆 New best: {model_name} (Score: {sc})")
                    else:
                        log.info(f"  ⏭ {model_name}: returned None (no trades or error)")
                        model_scores[model_name] = {"error": "no_result"}
                except Exception as e:
                    log.error(f"  ❌ {model_name} crashed: {e}")
                    model_scores[model_name] = {"error": str(e)}

            old_mode = old_data.get(mode) or {}
            old_side = old_mode.get(side) or {}
            old_metrics = old_side.get("metrics") or {}
            old_score = old_metrics.get("score", -99999.0)

            # ── логика деплоя ────────────────────────────────
            if best_model_data is None:
                if old_side:
                    log.info(f"  🛡️ All models failed -> keeping OLD ({old_score})")
                    out_data[mode][side] = old_side
                else:
                    log.warning(f"  ⚠ No new, no old for {mode}/{side} -> null")
                    out_data[mode][side] = None
            elif best_score < min_score:
                if old_side and old_score >= min_score:
                    log.info(f"  🛡️ New {best_score} < min_score {min_score} -> keeping OLD ({old_score})")
                    out_data[mode][side] = old_side
                elif old_side:
                    log.info(f"  🛡️ New {best_score} < min_score, old also low ({old_score}) -> keeping OLD")
                    out_data[mode][side] = old_side
                else:
                    log.warning(f"  ⚠ New {best_score} < min_score {min_score} and no old -> null")
                    out_data[mode][side] = None
            elif old_side and best_score <= old_score:
                log.info(f"  🛡️ Keeping OLD (New: {best_score}, Old: {old_score})")
                out_data[mode][side] = old_side
            else:
                log.info(f"  🚀 Deploy NEW (New: {best_score}, Old: {old_score})")
                out_data[mode][side] = best_model_data

            out_data["all_scores"][mode][side] = model_scores

    save_json(MODEL_OUT_FILE, out_data)
    log.info(f"Saved to {MODEL_OUT_FILE}")

    # отчёт
    try:
        subprocess.run(["python", "crypto/report/weekly_trainer_report.py"], check=False)
    except Exception as e:
        log.error(f"Report failed: {e}")

if __name__ == "__main__":
    main()