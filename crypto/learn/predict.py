# ============================================================
# ARGUS-Trader - PREDICT v12
# ------------------------------------------------------------
# v12: 3-model blend (LightGBM + XGBoost + CatBoost).
#      Weights: equal. Adaptive weights -> next step.
# v11: ensemble lgb+xgb.
# ============================================================

import os
os.environ.setdefault("USE_CROSS", "0")

import sys
import json
import logging
from pathlib import Path
from datetime import datetime, timezone

import numpy as np
import lightgbm as lgb
import xgboost as xgb
from catboost import CatBoostRegressor

SCRIPT_DIR = Path(__file__).resolve().parent
CRYPTO_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(CRYPTO_ROOT))
sys.path.insert(0, str(SCRIPT_DIR))

import dataset as ds

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("crypto.learn.predict")

MODELS_DIR = SCRIPT_DIR / "models"

SCALE_PCT = 4.0
PROB_MIN = 0.05
PROB_MAX = 0.95

LGB_WEIGHT = 1.0
XGB_WEIGHT = 1.0
CAT_WEIGHT = 1.0

SYMBOLS_LIST = [
    s.strip().upper()
    for s in (
        os.getenv("SYMBOLS")
        or "BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT"
    ).split(",")
    if s.strip()
]

DB2_SET = {
    s.strip().upper()
    for s in (
        os.getenv("DB2_SYMBOLS")
        or "SOLUSDT,BNBUSDT"
    ).split(",")
    if s.strip()
}


def load_lgb(sym):
    mf = MODELS_DIR / ("lgb_" + sym + ".txt")
    meta_f = MODELS_DIR / ("meta_" + sym + ".json")
    if not mf.exists() or not meta_f.exists():
        return None, None
    try:
        with open(meta_f, "r", encoding="utf-8") as f:
            meta = json.load(f)
        model = lgb.Booster(model_file=str(mf))
        return model, meta
    except Exception as exc:
        log.warning("lgb %s: %s", sym, exc)
        return None, None


def load_xgb(sym):
    mf = MODELS_DIR / ("xgb_" + sym + ".json")
    meta_f = MODELS_DIR / ("meta_xgb_" + sym + ".json")
    if not mf.exists() or not meta_f.exists():
        return None, None
    try:
        with open(meta_f, "r", encoding="utf-8") as f:
            meta = json.load(f)
        model = xgb.Booster()
        model.load_model(str(mf))
        return model, meta
    except Exception as exc:
        log.warning("xgb %s: %s", sym, exc)
        return None, None


def load_cat(sym):
    mf = MODELS_DIR / ("cat_" + sym + ".cbm")
    meta_f = MODELS_DIR / ("meta_cat_" + sym + ".json")
    if not mf.exists() or not meta_f.exists():
        return None, None
    try:
        with open(meta_f, "r", encoding="utf-8") as f:
            meta = json.load(f)
        model = CatBoostRegressor()
        model.load_model(str(mf))
        return model, meta
    except Exception as exc:
        log.warning("cat %s: %s", sym, exc)
        return None, None


def _map_ret_to_prob(p):
    x = 0.5 + float(p) / SCALE_PCT
    if x < PROB_MIN:
        return PROB_MIN
    if x > PROB_MAX:
        return PROB_MAX
    return x


def _conf(p):
    c = abs(float(p)) / SCALE_PCT
    if c < 0.0:
        return 0.0
    if c > 1.0:
        return 1.0
    return c


def _clip01(v):
    if v < 0.0:
        return 0.0
    if v > 1.0:
        return 1.0
    return v


def _get_acc(meta):
    if not meta:
        return None
    for key in (
        "accuracy", "sign_acc_test",
        "ic_test",
    ):
        v = meta.get(key)
        if v is not None:
            return v
    return None


def predict_one(symbol):
    expected = len(ds.FEATURE_COLS)

    lgb_m, lgb_meta = load_lgb(symbol)
    xgb_m, xgb_meta = load_xgb(symbol)
    cat_m, cat_meta = load_cat(symbol)

    if lgb_m is not None and \
            lgb_m.num_feature() != expected:
        log.warning(
            "%s: lgb nf=%d need %d -> skip",
            symbol, lgb_m.num_feature(), expected,
        )
        lgb_m = None

    if xgb_m is not None:
        try:
            nf = xgb_m.num_features()
            if nf != expected:
                log.warning(
                    "%s: xgb nf=%d need %d -> skip",
                    symbol, nf, expected,
                )
                xgb_m = None
        except Exception:
            pass

    if cat_m is not None:
        try:
            nf = cat_m.n_features_in_
            if nf != expected:
                log.warning(
                    "%s: cat nf=%d need %d -> skip",
                    symbol, nf, expected,
                )
                cat_m = None
        except Exception:
            pass

    if lgb_m is None and xgb_m is None \
            and cat_m is None:
        log.warning("%s: no models", symbol)
        return None

    ds.SYMBOLS = [symbol]
    ds.REFERENCE = symbol
    ds.DB2_SYMBOLS = (
        {symbol} if symbol in DB2_SET else set()
    )

    ts, row = ds.prepare_one(symbol)
    if row is None:
        log.warning("%s: no features", symbol)
        return None

    if len(row) != expected:
        log.warning(
            "%s: row=%d need %d -> skip",
            symbol, len(row), expected,
        )
        return None

    X = np.array([row], dtype=np.float32)

    preds = []
    weights = []
    sources = []

    if lgb_m is not None:
        try:
            p = float(lgb_m.predict(X)[0])
            preds.append(p)
            weights.append(LGB_WEIGHT)
            sources.append("lgb")
        except Exception as exc:
            log.warning("lgb pred %s: %s", symbol, exc)

    if xgb_m is not None:
        try:
            d = xgb.DMatrix(X)
            bi = getattr(xgb_m, "best_iteration", None)
            if bi is not None:
                p = float(xgb_m.predict(
                    d, iteration_range=(0, bi + 1)
                )[0])
            else:
                p = float(xgb_m.predict(d)[0])
            preds.append(p)
            weights.append(XGB_WEIGHT)
            sources.append("xgb")
        except Exception as exc:
            log.warning("xgb pred %s: %s", symbol, exc)

    if cat_m is not None:
        try:
            p = float(cat_m.predict(X)[0])
            preds.append(p)
            weights.append(CAT_WEIGHT)
            sources.append("cat")
        except Exception as exc:
            log.warning("cat pred %s: %s", symbol, exc)

    if not preds:
        return None

    wsum = sum(weights)
    pred_pct = sum(
        p * w for p, w in zip(preds, weights)
    ) / wsum

    prob_up = _map_ret_to_prob(pred_pct)
    conf = _conf(pred_pct)
    direction = 1 if pred_pct > 0 else 0

    prob_up = _clip01(prob_up)
    conf = _clip01(conf)

    meta = lgb_meta or xgb_meta or cat_meta
    acc = _get_acc(meta)

    out = {
        "symbol": symbol,
        "timestamp": ts.isoformat(),
        "prob_up": round(prob_up, 4),
        "direction": direction,
        "confidence": round(conf, 4),
        "predicted_return_pct": round(
            float(pred_pct), 4
        ),
        "model_acc": acc,
        "objective": "regression",
        "model_version": "v12-triple",
        "sources": sources,
        "blend": len(sources) > 1,
        "features_used": expected,
    }
    if lgb_m is not None and "lgb" in sources:
        out["lgb_pred"] = round(
            float(preds[sources.index("lgb")]), 4
        )
    if xgb_m is not None and "xgb" in sources:
        out["xgb_pred"] = round(
            float(preds[sources.index("xgb")]), 4
        )
    if cat_m is not None and "cat" in sources:
        out["cat_pred"] = round(
            float(preds[sources.index("cat")]), 4
        )
    return out


def main():
    log.info("=" * 60)
    log.info("ARGUS-Trader PREDICT v12 (lgb+xgb+cat)")
    log.info("SYMBOLS=%s", SYMBOLS_LIST)
    log.info("FEATURE_COLS=%d", len(ds.FEATURE_COLS))
    log.info("=" * 60)

    results = []
    accs = []

    for sym in SYMBOLS_LIST:
        r = predict_one(sym)
        if not r:
            continue
        results.append(r)
        a = r.get("model_acc")
        if a is not None:
            accs.append(a)

        parts = []
        for k in ("lgb_pred", "xgb_pred", "cat_pred"):
            if k in r:
                parts.append(
                    k.split("_")[0] + "=%.4f" % r[k]
                )
        extra = " " + " ".join(parts) if parts else ""

        log.info(
            "%s: prob_up=%.4f dir=%d "
            "conf=%.4f ret=%.4f%% [%s]%s",
            r["symbol"], r["prob_up"],
            r["direction"], r["confidence"],
            r["predicted_return_pct"],
            "+".join(r["sources"]),
            extra,
        )

    avg_acc = None
    if accs:
        avg_acc = round(sum(accs) / len(accs), 4)

    out = {
        "predicted_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "model_accuracy": avg_acc,
        "model_accuracy_avg": avg_acc,
        "feature_count": len(ds.FEATURE_COLS),
        "mode": "triple_blend",
        "symbols": SYMBOLS_LIST,
        "predictions": results,
    }

    out_path = SCRIPT_DIR / "last_predictions.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    log.info("saved: %s", out_path.name)


if __name__ == "__main__":
    main()