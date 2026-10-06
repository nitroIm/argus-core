# ============================================================
# ARGUS-Trader - PREDICT v16
# ------------------------------------------------------------
# v16: sanity filter on model outputs.
#      Skips models with |pred| > MAX_ABS_PCT.
#      Fixes +27000% bug from broken mlp/ridge.
#      Also clamps final blend to +-MAX_ABS_PCT.
# v15: 6-model blend. + lstm.
# ============================================================

import os
os.environ.setdefault("USE_CROSS", "0")

import sys
import json
import logging
from pathlib import Path
from datetime import datetime, timezone

import numpy as np
import joblib
import lightgbm as lgb
import xgboost as xgb
from catboost import CatBoostRegressor
import torch

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
MAX_ABS_PCT = 15.0

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


class LSTMModel(torch.nn.Module):
    def __init__(self, n_feat, hidden, layers, dropout):
        super().__init__()
        self.lstm = torch.nn.LSTM(
            n_feat,
            hidden,
            num_layers=layers,
            batch_first=True,
            dropout=dropout,
        )
        self.head = torch.nn.Sequential(
            torch.nn.Dropout(dropout),
            torch.nn.Linear(hidden, 16),
            torch.nn.ReLU(),
            torch.nn.Linear(16, 1),
        )

    def forward(self, x):
        out, _ = self.lstm(x)
        last = out[:, -1, :]
        return self.head(last).squeeze(-1)


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
    if not wdata:
        return {}, True
    entry = wdata.get("symbols", {}).get(sym)
    if not entry:
        return {}, True
    w = entry.get("weights") or {}
    allowed = entry.get("trade_allowed", True)
    return w, allowed


def _nan_safe_row(row):
    arr = np.array([row], dtype=np.float32)
    return np.nan_to_num(
        arr,
        nan=0.0,
        posinf=0.0,
        neginf=0.0,
    )


def _sane(p):
    """Check model output is finite and within sane range."""
    if p is None:
        return False
    try:
        v = float(p)
    except Exception:
        return False
    if not np.isfinite(v):
        return False
    if abs(v) > MAX_ABS_PCT:
        return False
    return True


def _prepare_seq(symbol, seq_len):
    d = ds._load_symbol(symbol)
    if d is None:
        return None, None
    feats = d["feats"]
    if len(feats) < seq_len:
        return None, None

    ts_sorted = sorted(feats.keys())
    ts_last = ts_sorted[-1]
    ts_seq = ts_sorted[-seq_len:]

    asia = ds.fetch_asia_market()

    rows = []
    for ts in ts_seq:
        row = ds._row_for(
            symbol, d, asia, ts,
            [], [], [],
        )
        rows.append(row)
    return ts_last, rows


def predict_lgb(sym, X, expected):
    mf = MODELS_DIR / ("lgb_" + sym + ".txt")
    if not mf.exists():
        return None
    try:
        m = lgb.Booster(model_file=str(mf))
        if m.num_feature() != expected:
            return None
        return float(m.predict(X)[0])
    except Exception as exc:
        log.warning("lgb %s: %s", sym, exc)
        return None


def predict_xgb(sym, X, expected):
    mf = MODELS_DIR / ("xgb_" + sym + ".json")
    if not mf.exists():
        return None
    try:
        m = xgb.Booster()
        m.load_model(str(mf))
        if m.num_features() != expected:
            return None
        d = xgb.DMatrix(X)
        bi = getattr(m, "best_iteration", None)
        if bi is not None:
            return float(m.predict(
                d,
                iteration_range=(0, bi + 1),
            )[0])
        return float(m.predict(d)[0])
    except Exception as exc:
        log.warning("xgb %s: %s", sym, exc)
        return None


def predict_cat(sym, X, expected):
    mf = MODELS_DIR / ("cat_" + sym + ".cbm")
    if not mf.exists():
        return None
    try:
        m = CatBoostRegressor()
        m.load_model(str(mf))
        if m.n_features_in_ != expected:
            return None
        return float(m.predict(X)[0])
    except Exception as exc:
        log.warning("cat %s: %s", sym, exc)
        return None


def predict_ridge(sym, X, expected):
    mf = MODELS_DIR / (
        "ridge_" + sym + ".joblib"
    )
    sf = MODELS_DIR / (
        "scaler_ridge_" + sym + ".joblib"
    )
    if not mf.exists() or not sf.exists():
        return None
    try:
        m = joblib.load(str(mf))
        s = joblib.load(str(sf))
        Xs = s.transform(X)
        return float(m.predict(Xs)[0])
    except Exception as exc:
        log.warning("ridge %s: %s", sym, exc)
        return None


def predict_mlp(sym, X, expected):
    mf = MODELS_DIR / (
        "mlp_" + sym + ".joblib"
    )
    sf = MODELS_DIR / (
        "scaler_mlp_" + sym + ".joblib"
    )
    if not mf.exists() or not sf.exists():
        return None
    try:
        m = joblib.load(str(mf))
        s = joblib.load(str(sf))
        Xs = s.transform(X)
        return float(m.predict(Xs)[0])
    except Exception as exc:
        log.warning("mlp %s: %s", sym, exc)
        return None


def predict_lstm(sym, expected):
    mf = MODELS_DIR / ("lstm_" + sym + ".pt")
    sf = MODELS_DIR / (
        "scaler_lstm_" + sym + ".joblib"
    )
    if not mf.exists() or not sf.exists():
        return None
    try:
        payload = torch.load(
            str(mf), map_location="cpu"
        )
        seq_len = int(payload.get("seq_len", 50))
        hidden = int(payload.get("hidden", 32))
        layers = int(payload.get("layers", 2))
        dropout = float(payload.get("dropout", 0.3))
        n_feat = int(payload.get("n_feat", expected))

        if n_feat != expected:
            return None

        ts_last, rows = _prepare_seq(sym, seq_len)
        if rows is None:
            return None

        arr = np.asarray(rows, dtype=np.float32)
        arr = np.nan_to_num(
            arr,
            nan=0.0,
            posinf=0.0,
            neginf=0.0,
        )

        scaler = joblib.load(str(sf))
        arr = scaler.transform(arr)

        seq = arr.reshape(1, seq_len, expected)
        xb = torch.from_numpy(seq)

        model = LSTMModel(
            expected, hidden, layers, dropout
        )
        model.load_state_dict(payload["state"])
        model.eval()
        with torch.no_grad():
            p = float(model(xb).numpy()[0])
        return p
    except Exception as exc:
        log.warning("lstm %s: %s", sym, exc)
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
    """Read ic_test from lgb meta (current model)."""
    p = MODELS_DIR / ("meta_lgb_" + sym + ".json")
    if not p.exists():
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
    weights, allowed = _sym_weights(
        wdata, symbol
    )

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

    X = _nan_safe_row(row)

    preds = {}
    skipped = []

    def _try(name, val):
        if val is None:
            return
        if not _sane(val):
            skipped.append(name)
            log.warning(
                "%s: %s insane value %.2f, skip",
                symbol, name, float(val),
            )
            return
        preds[name] = float(val)

    _try("lgb", predict_lgb(symbol, X, expected))
    _try("xgb", predict_xgb(symbol, X, expected))
    _try("cat", predict_cat(symbol, X, expected))
    _try("ridge", predict_ridge(symbol, X, expected))
    _try("mlp", predict_mlp(symbol, X, expected))
    _try("lstm", predict_lstm(symbol, expected))

    if not preds:
        log.warning("%s: no valid models", symbol)
        return None

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

    if pred_pct > MAX_ABS_PCT:
        pred_pct = MAX_ABS_PCT
    elif pred_pct < -MAX_ABS_PCT:
        pred_pct = -MAX_ABS_PCT

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
        "model_version": "v16-6models-sane",
        "sources": list(preds.keys()),
        "skipped": skipped,
        "blend": len(preds) > 1,
        "features_used": expected,
        "trade_allowed": allowed,
        "action_hint": action,
        "weights": {
            k: round(w_use.get(k, 0.0), 4)
            for k in preds
        },
    }
    for k, v in preds.items():
        out[k + "_pred"] = round(v, 4)
    return out


def main():
    log.info("=" * 60)
    log.info("ARGUS-Trader PREDICT v16")
    log.info("SYMBOLS=%s", SYMBOLS_LIST)
    log.info("FEATURE_COLS=%d", len(ds.FEATURE_COLS))
    log.info("MAX_ABS_PCT=%.1f", MAX_ABS_PCT)
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
        for k in (
            "lgb", "xgb", "cat",
            "ridge", "mlp", "lstm",
        ):
            pk = k + "_pred"
            if pk in r:
                extra += " %s=%.4f" % (k, r[pk])

        wstr = ""
        for k, v in r.get("weights", {}).items():
            wstr += " %s=%.2f" % (k, v)

        sk = ""
        if r.get("skipped"):
            sk = " SKIP=" + ",".join(r["skipped"])

        log.info(
            "%s: ret=%.4f%% dir=%d conf=%.4f "
            "[%s] allowed=%s |%s |%s%s",
            r["symbol"],
            r["predicted_return_pct"],
            r["direction"],
            r["confidence"],
            "+".join(r["sources"]),
            r["trade_allowed"],
            extra,
            wstr,
            sk,
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
        "mode": "6model_blend_sane",
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