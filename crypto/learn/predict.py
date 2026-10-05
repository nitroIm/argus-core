# ============================================================
# ARGUS-Trader - PREDICT v4 [PRODUCTION]
# ------------------------------------------------------------
# v4: + cross-features built on-the-fly via dataset helpers.
#     Feature order from FEATURE_COLS (always matches model).
# v3: reads features from model_meta.json.
# ============================================================

import sys
import json
import logging
from pathlib import Path
from datetime import datetime, timezone, timedelta

import numpy as np
import lightgbm as lgb

SCRIPT_DIR = Path(__file__).resolve().parent
CRYPTO_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(CRYPTO_ROOT))
sys.path.insert(0, str(SCRIPT_DIR))

from db import get_connection

# Импортируем ВСЮ логику из dataset — одно место для
# кросс-фич. Если dataset поменяется — predict подхватит.
from dataset import (
    FEATURE_COLS,
    INTERNAL_COLS,
    CROSS_COLS,
    EXTERNAL_COLS,
    USE_EXTERNAL,
    REFERENCE,
    SYMBOLS,
    DB2_SYMBOLS,
    symbol_conn,
    fetch_features,
    fetch_candles,
    fetch_external,
    build_targets,
    build_cross_full,
    _get_feat_map,
    ext_lookup,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("crypto.learn.predict")

MODEL_FILE = SCRIPT_DIR / "models" / "lgb_model.txt"
META_FILE = SCRIPT_DIR / "models" / "model_meta.json"

# Сколько последних свечей загружать для cross-features
# (окно 24h + запас)
LOOKBACK = 500


def load_meta():
    if not META_FILE.exists():
        return None
    try:
        with open(META_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def load_all_feat_and_candles():
    """Загружает features + candles для всех SYMBOLS
    и для REFERENCE (если его нет в списке).
    Возвращает (feat_maps, candle_maps).
    """
    need = set(SYMBOLS) | {REFERENCE}
    feat_maps = {}
    candle_maps = {}

    for sym in sorted(need):
        # features
        rows = fetch_features(sym, limit=LOOKBACK)
        if not rows:
            log.warning("no features: %s", sym)
            continue
        base_cols = (
            ["symbol", "timestamp"] + INTERNAL_COLS
        )
        feat_maps[sym] = _get_feat_map(rows, base_cols)

        # candles
        candles = fetch_candles(sym, limit=LOOKBACK)
        if not candles:
            log.warning("no candles: %s", sym)
            continue
        candle_maps[sym] = {
            c["ts"]: c["close"] for c in candles
            if c["close"]
        }

    return feat_maps, candle_maps


def build_feature_row(
    symbol, ts,
    feat_maps, cross_maps,
    ext_dxy, ext_spx, ext_gold,
):
    """Строит один X-вектор в порядке FEATURE_COLS."""
    feats = feat_maps.get(symbol, {}).get(ts)
    if feats is None:
        return None

    row = []

    # --- internal ---
    for col in INTERNAL_COLS:
        v = feats.get(col, np.nan)
        row.append(v)

    # --- external ---
    if USE_EXTERNAL:
        v = ext_lookup(ext_dxy, ts)
        row.append(v if v is not None else np.nan)
        v = ext_lookup(ext_spx, ts)
        row.append(v if v is not None else np.nan)
        v = ext_lookup(ext_gold, ts)
        row.append(v if v is not None else np.nan)

    # --- cross ---
    cm = cross_maps.get(symbol, {}).get(ts, {})
    for col in CROSS_COLS:
        row.append(cm.get(col, np.nan))

    return row


def predict_one(
    symbol, model, feature_cols,
    feat_maps, cross_maps,
    ext_dxy, ext_spx, ext_gold,
):
    """Предсказание для одного символа."""
    feats = feat_maps.get(symbol)
    if not feats:
        log.warning("%s: no features", symbol)
        return None

    # последний timestamp
    ts = sorted(feats.keys())[-1]

    row = build_feature_row(
        symbol, ts, feat_maps, cross_maps,
        ext_dxy, ext_spx, ext_gold,
    )
    if row is None:
        log.warning("%s: cannot build row", symbol)
        return None

    X = np.array([row], dtype=np.float32)
    prob_up = float(model.predict(X)[0])
    direction = 1 if prob_up > 0.5 else 0

    return {
        "symbol": symbol,
        "timestamp": ts.isoformat(),
        "prob_up": round(prob_up, 4),
        "direction": direction,
        "confidence": round(
            abs(prob_up - 0.5) * 2, 4
        ),
    }


def predict_all(symbols=None):
    if symbols is None:
        symbols = list(SYMBOLS)

    if not MODEL_FILE.exists():
        log.error("model not found")
        return None

    meta = load_meta()
    if not meta:
        log.error("meta missing")
        return None

    # FEATURE_COLS из кода — источник истины.
    # Сверяем с meta.features (на случай если model устарела)
    meta_features = meta.get("features", [])
    if meta_features and meta_features != FEATURE_COLS:
        log.warning(
            "meta.features != FEATURE_COLS "
            "(%d vs %d) — модель устарела, "
            "переобучи (Crypto Learn)",
            len(meta_features), len(FEATURE_COLS),
        )
        return None

    log.info(
        "model features: %d (accuracy=%.4f)",
        len(FEATURE_COLS),
        meta.get("accuracy", 0),
    )
    log.info("REFERENCE=%s", REFERENCE)

    model = lgb.Booster(model_file=str(MODEL_FILE))

    # Загружаем все features + candles
    feat_maps, candle_maps = load_all_feat_and_candles()
    if not feat_maps:
        log.error("no data loaded")
        return None

    # Кросс-фичи для всех символов разом
    cross_maps = build_cross_full(
        feat_maps, candle_maps, REFERENCE,
    )

    # External
    if USE_EXTERNAL:
        ext_dxy = fetch_external("DXY")
        ext_spx = fetch_external("SPX")
        ext_gold = fetch_external("GOLD")
    else:
        ext_dxy = []
        ext_spx = []
        ext_gold = []

    results = []
    for symbol in symbols:
        r = predict_one(
            symbol, model, FEATURE_COLS,
            feat_maps, cross_maps,
            ext_dxy, ext_spx, ext_gold,
        )
        if r is None:
            continue
        results.append(r)
        log.info(
            "%s: prob_up=%.4f dir=%d conf=%.4f",
            r["symbol"], r["prob_up"],
            r["direction"], r["confidence"],
        )

    return {
        "predicted_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "model_accuracy": meta.get("accuracy"),
        "model_features": len(FEATURE_COLS),
        "reference": REFERENCE,
        "predictions": results,
    }


def main():
    log.info("=" * 60)
    log.info("ARGUS-Trader PREDICT v4")
    log.info("=" * 60)

    result = predict_all()
    if result is None:
        log.error("predict failed")
        return

    out = SCRIPT_DIR / "last_predictions.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    log.info("saved: %s", out.name)


if __name__ == "__main__":
    main()