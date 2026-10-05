# ============================================================
# ARGUS-Trader - PREDICT v13
# ------------------------------------------------------------
# v13: per-symbol dynamic weights from
#      models/ensemble_weights.json.
#      Trade filter: if trade_allowed=False,
#      emit WAIT with conf=0.
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


def load_weights():
    p = MODELS_DIR / "ensemble_weights.json"
    if not p.exists():
        return {}
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        log.warning("weights: %s", exc)
        return {}


def _sym_weights(wdata, sym):
    """Returns (dict algo->w, trade_allowed)."""
    if not wdata:
        return {}, True
    entry = wdata.get("symbols", {}).get(sym)
    if not entry:
        return {}, True
    w = entry.get("weights") or {}
    allowed = entry.get("trade_allowed", True)
    return w, allowed


def load_lgb(sym):
    mf = MODELS_DIR / ("lgb_" + sym + ".txt")
    if not mf.exists():
        return None
    try:
        return lgb.Booster(model_file=str(mf))
    except Exception as exc:
        log.warning("lgb %s: %s", sym, exc)
        return None


def load_xgb(sym):
    mf = MODELS_DIR / ("xgb_" + sym + ".json")
    if not mf.exists():
        return None
    try:
        m = xgb.Booster()
        m.load_model(str(mf))
        return m
    except Exception as exc:
        log.warning("xgb %s: %s", sym, exc)
        return None


def load_cat(sym):
    mf = MODELS_DIR / ("cat_" + sym + ".cbm")
    if not mf.exists():
        return None
    try:
        m = CatBoostRegressor()
        m.load_model(str(mf))
        return m
    except Exception as exc:
        log.warning("cat %s: %s", sym, exc)
        return None


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


def _acc_from_meta(sym):
    p = MODELS_DIR / ("meta_" + sym + ".json")
    if not p.exists():
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            m = json.load(f)
        for k in (
            "ic_test", "sign_acc_test",
            "accuracy",
        ):
            v = m.get(k)
            if v is not None:
                return v
    except Exception:
        pass
    return None


def predict_one(symbol, wdata):
    expected = len(ds.FEATURE_COLS)

    weights, allowed = _sym_weights(wdata, symbol)

    lgb_m = load_lgb(symbol)
    xgb_m = load_xgb(symbol)
    cat_m = load_cat(symbol)

    if lgb_m is not None and \
            lgb_m.num_feature() != expected:
        log.warning(
            "%s: lgb nf mismatch -> skip", symbol
        )
        lgb_m = None
    if xgb_m is not None:
        try:
            if xgb_m.num_features() != expected:
                xgb_m = None
        except Exception:
            pass
    if cat_m is not None:
        try:
            if cat_m.n_features_in_ != expected:
                cat_m = None
        except Exception:
            pass

    if not (lgb_m or xgb_m or cat_m):
        log.warning("%s: no models", symbol)
        return None

    ds.SYMBOLS = [symbol]
    ds.REFERENCE = symbol
    ds.DB2_SYMBOLS = (
        {symbol} if symbol in DB2_SET
        else set()
    )

    ts, row = ds.prepare_one(symbol)
    if row is None or len(row) != expected:
        log.warning("%s: bad row", symbol)
        return None

    X = np.array([row], dtype=np.float32)

    preds = {}
    used = []

    if lgb_m is not None:
        try:
            preds["lgb"] = float(
                lgb_m.predict(X)[0]
            )
            used.append("lgb")
        except Exception as exc:
            log.warning("lgb pred %s: %s", symbol, exc)

    if xgb_m is not None:
        try:
            d = xgb.DMatrix(X)
            bi = getattr(
                xgb_m, "best_iteration", None
            )
            if bi is not None:
                p = float(xgb_m.predict(
                    d,
                    iteration_range=(0, bi + 1),
                )[0])
            else:
                p = float(xgb_m.predict(d)[0])
            preds["xgb"] = p
            used.append("xgb")
        except Exception as exc:
            log.warning("xgb pred %s: %s", symbol, exc)

    if cat_m is not None:
        try:
            preds["cat"] = float(
                cat_m.predict(X)[0]
            )
            used.append("cat")
        except Exception as exc:
            log.warning("cat pred %s: %s", symbol, exc)

    if not preds:
        return None

    # Weights: use per-symbol if available,
    # else equal.
    if weights:
        w_use = {
            k: weights.get(k, 0.0)
            for k in preds
        }
    else:
        w_use = {k: 1.0 for k in preds}

    wsum = sum(w_use.values())
    if wsum <= 0:
        w_use = {k: 1.0 for k in preds}
        wsum = float(len(preds))

    pred_pct = sum(
        preds[k] * w_use[k] for k in preds
    ) / wsum

    # Filter if not allowed
    if not allowed:
        pred_pct = 0.0
        conf = 0.0
        prob_up = 0.5
        direction = 0
        action = "WAIT"
    else:
        prob_up = _map_ret_to_prob(pred_pct)
        conf = _conf(pred_pct)
        direction = 1 if pred_pct > 0 else 0
        action = "OK"

    prob_up = _clip01(prob_up)
    conf = _clip01(conf)

    acc = _acc_from_meta(symbol)

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
        "model_version": "v13-weighted",
        "sources": used,
        "blend": len(used) > 1,
        "features_used": expected,
        "trade_allowed": allowed,
        "action_hint": action,
        "weights": {
            k: round(w_use.get(k, 0.0), 4)
            for k in used
        },
    }
    for k in ("lgb", "xgb", "cat"):
        if k in preds:
            out[k + "_pred"] = round(
                preds[k], 4
            )
    return out


def main():
    log.info("=" * 60)
    log.info("ARGUS-Trader PREDICT v13")
    log.info("SYMBOLS=%s", SYMBOLS_LIST)
    log.info("FEATURE_COLS=%d", len(ds.FEATURE_COLS))
    log.info("=" * 60)

    wdata = load_weights()
    n_sym = len(wdata.get("symbols", {}))
    log.info("weights loaded: %d symbols", n_sym)

    results = []
    accs = []

    for sym in SYMBOLS_LIST:
        r = predict_one(sym, wdata)
        if not r:
            continue
        results.append(r)
        a = r.get("model_acc")
        if a is not None:
            accs.append(a)

        extra = ""
        for k in ("lgb", "xgb", "cat"):
            pk = k + "_pred"
            if pk in r:
                extra += " %s=%.4f" % (k, r[pk])

        wstr = ""
        for k, v in r.get("weights", {}).items():
            wstr += " %s=%.2f" % (k, v)

        log.info(
            "%s: ret=%.4f%% dir=%d conf=%.4f "
            "[%s] allowed=%s |%s |%s",
            r["symbol"],
            r["predicted_return_pct"],
            r["direction"],
            r["confidence"],
            "+".join(r["sources"]),
            r["trade_allowed"],
            extra,
            wstr,
        )

    avg_acc = None
    if accs:
        avg_acc = round(
            sum(accs) / len(accs), 4
        )

    out = {
        "predicted_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "model_accuracy": avg_acc,
        "model_accuracy_avg": avg_acc,
        "feature_count": len(ds.FEATURE_COLS),
        "mode": "weighted_blend",
        "weights_version": wdata.get(
            "computed_at"
        ),
        "symbols": SYMBOLS_LIST,
        "predictions": results,
    }

    out_path = SCRIPT_DIR / "last_predictions.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(
            out, f,
            ensure_ascii=False, indent=2,
        )
    log.info("saved: %s", out_path.name)


if __name__ == "__main__":
    main()