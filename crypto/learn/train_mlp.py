# ============================================================
# ARGUS-Trader - TRAIN MLP v1
# ------------------------------------------------------------
# v1: Multi-layer perceptron per-symbol.
#     First real neural network in the ensemble.
#     Saves mlp_{sym}.joblib + scaler_mlp_{sym}.joblib.
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
from sklearn.neural_network import MLPRegressor
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
log = logging.getLogger("crypto.learn.train_mlp")

MODELS_DIR = SCRIPT_DIR / "models"
MODELS_DIR.mkdir(parents=True, exist_ok=True)

HIDDEN = (64, 32)
MAX_ITER = 500
ALPHA = 0.001
LR = 0.001

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
    return np.nan_to_num(
        X,
        nan=0.0,
        posinf=0.0,
        neginf=0.0,
    )


def train_one(symbol):
    log.info("-" * 60)
    log.info("TRAIN MLP %s", symbol)
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

    model = MLPRegressor(
        hidden_layer_sizes=HIDDEN,
        activation="relu",
        solver="adam",
        alpha=ALPHA,
        learning_rate_init=LR,
        max_iter=MAX_ITER,
        early_stopping=True,
        validation_fraction=0.15,
        n_iter_no_change=20,
        random_state=42,
        verbose=False,
    )

    model.fit(X_tr, r_train)

    log.info(
        "%s: mlp iterations=%d",
        symbol, model.n_iter_,
    )

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

    model_path = MODELS_DIR / (
        "mlp_" + symbol + ".joblib"
    )
    scaler_path = MODELS_DIR / (
        "scaler_mlp_" + symbol + ".joblib"
    )
    joblib.dump(model, str(model_path))
    joblib.dump(scaler, str(scaler_path))
    log.info("saved %s", model_path.name)

    meta = {
        "trained_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "version": "v1-mlp",
        "objective": "regression",
        "algorithm": "mlp",
        "symbol": symbol,
        "hidden": list(HIDDEN),
        "alpha": ALPHA,
        "lr": LR,
        "iterations": int(model.n_iter_),
        "n_total": data["n_total"],
        "n_train": data["n_train"],
        "n_test": data["n_test"],
        "ic_train": round(ic_tr, 4),
        "ic_test": round(ic_te, 4),
        "mae_test": round(mae_te, 4),
        "rmse_test": round(rmse_te, 4),
        "features": data["feature_cols"],
        "horizon": data["horizon"],
    }
    meta_path = MODELS_DIR / (
        "meta_mlp_" + symbol + ".json"
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
    log.info("ARGUS-Trader TRAIN MLP v1")
    log.info("SYMBOLS=%s", SYMBOLS_LIST)
    log.info("HIDDEN=%s MAX_ITER=%d",
             HIDDEN, MAX_ITER)
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
    log.info("MLP TRAIN DONE")
    for sym, m in metas.items():
        log.info(
            "  %s: IC=%.4f iter=%d",
            sym, m["ic_test"], m["iterations"],
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
        log.error("mlp train failed")
        return
    log.info("DONE. %d mlp models", len(metas))


if __name__ == "__main__":
    main()