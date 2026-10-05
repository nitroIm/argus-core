# ============================================================
# ARGUS-Trader - TRAIN RIDGE v1
# ------------------------------------------------------------
# v1: Ridge regression per-symbol. Linear baseline.
#     Saves ridge_{sym}.joblib + meta_ridge_{sym}.json.
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
from sklearn.linear_model import Ridge
from sklearn.preprocessing import StandardScaler

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
log = logging.getLogger("crypto.learn.train_ridge")

MODELS_DIR = SCRIPT_DIR / "models"
MODELS_DIR.mkdir(parents=True, exist_ok=True)

ALPHA = 1.0

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


def _ic(y_true, y_pred):
    if len(y_true) < 10:
        return 0.0
    if np.std(y_true) == 0:
        return 0.0
    if np.std(y_pred) == 0:
        return 0.0
    yt = y_true - y_true.mean()
    yp = y_pred - y_pred.mean()
    d = (yt * yt).sum() * (yp * yp).sum()
    if d <= 0:
        return 0.0
    d = float(np.sqrt(d))
    if d == 0:
        return 0.0
    return float((yt * yp).sum() / d)


def _nan_safe(X):
    """Replace NaN/inf with 0. Ridge needs finite."""
    X = np.nan_to_num(
        X,
        nan=0.0,
        posinf=0.0,
        neginf=0.0,
    )
    return X


def train_one(symbol):
    log.info("-" * 60)
    log.info("TRAIN RIDGE %s", symbol)
    log.info("-" * 60)

    ds.SYMBOLS = [symbol]
    ds.REFERENCE = symbol
    if symbol in DB2_SET:
        ds.DB2_SYMBOLS = {symbol}
    else:
        ds.DB2_SYMBOLS = set()

    data = ds.prepare()
    if data is None:
        log.error("%s: no data", symbol)
        return None

    X_train = _nan_safe(data["X_train"])
    X_test = _nan_safe(data["X_test"])
    r_train = data["r_train"]
    r_test = data["r_test"]

    log.info(
        "%s: train=%d test=%d",
        symbol, len(X_train), len(X_test),
    )

    scaler = StandardScaler()
    X_tr = scaler.fit_transform(X_train)
    X_te = scaler.transform(X_test)

    model = Ridge(
        alpha=ALPHA,
        fit_intercept=True,
        random_state=42,
    )
    model.fit(X_tr, r_train)

    p_train = model.predict(X_tr).astype(np.float32)
    p_test = model.predict(X_te).astype(np.float32)

    ic_tr = _ic(r_train, p_train)
    ic_te = _ic(r_test, p_test)
    mae_te = float(np.mean(np.abs(p_test - r_test)))
    rmse_te = float(
        np.sqrt(np.mean((p_test - r_test) ** 2))
    )

    log.info(
        "%s: test: IC=%.4f MAE=%.4f RMSE=%.4f",
        symbol, ic_te, mae_te, rmse_te,
    )
    log.info(
        "%s: train: IC=%.4f", symbol, ic_tr,
    )

    coef = model.coef_
    feat_names = data["feature_cols"]
    pairs = sorted(
        zip(feat_names, coef),
        key=lambda x: -abs(float(x[1])),
    )
    log.info("%s: top coefficients:", symbol)
    for name, c in pairs[:5]:
        log.info("  %s: %+.4f", name, float(c))

    model_path = MODELS_DIR / (
        "ridge_" + symbol + ".joblib"
    )
    scaler_path = MODELS_DIR / (
        "scaler_ridge_" + symbol + ".joblib"
    )
    joblib.dump(model, str(model_path))
    joblib.dump(scaler, str(scaler_path))
    log.info("saved %s", model_path.name)

    meta = {
        "trained_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "version": "v1-ridge",
        "objective": "regression",
        "algorithm": "ridge",
        "symbol": symbol,
        "alpha": ALPHA,
        "n_total": data["n_total"],
        "n_train": data["n_train"],
        "n_test": data["n_test"],
        "ic_train": round(ic_tr, 4),
        "ic_test": round(ic_te, 4),
        "mae_test": round(mae_te, 4),
        "rmse_test": round(rmse_te, 4),
        "features": feat_names,
        "top_features": [
            {"name": n, "coef": round(float(c), 4)}
            for n, c in pairs[:10]
        ],
        "horizon": data["horizon"],
    }
    meta_path = MODELS_DIR / (
        "meta_ridge_" + symbol + ".json"
    )
    with open(
        meta_path, "w", encoding="utf-8"
    ) as f:
        json.dump(
            meta, f,
            ensure_ascii=False, indent=2,
        )
    log.info("saved %s", meta_path.name)

    return meta


def train():
    log.info("=" * 60)
    log.info("ARGUS-Trader TRAIN RIDGE v1")
    log.info("SYMBOLS=%s", SYMBOLS_LIST)
    log.info("ALPHA=%.2f", ALPHA)
    log.info("=" * 60)

    metas = {}
    for sym in SYMBOLS_LIST:
        try:
            m = train_one(sym)
            if m:
                metas[sym] = m
        except Exception as exc:
            log.error("%s: %s", sym, exc)

    log.info("=" * 60)
    log.info("RIDGE TRAIN DONE")
    for sym, m in metas.items():
        log.info(
            "  %s: IC=%.4f MAE=%.4f",
            sym, m["ic_test"], m["mae_test"],
        )
    if metas:
        avg = sum(
            m["ic_test"] for m in metas.values()
        ) / len(metas)
        log.info(
            "  AVG IC=%.4f (%d models)",
            avg, len(metas),
        )
    log.info("=" * 60)

    ds.SYMBOLS = ["BTCUSDT", "ETHUSDT"]
    ds.REFERENCE = "BTCUSDT"
    ds.DB2_SYMBOLS = DB2_SET

    return metas


def main():
    metas = train()
    if not metas:
        log.error("ridge train failed")
        return
    log.info("DONE. %d ridge models", len(metas))


if __name__ == "__main__":
    main()