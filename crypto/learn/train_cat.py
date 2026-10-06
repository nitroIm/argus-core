# ============================================================
# ARGUS-Trader - TRAIN CAT v2
# ------------------------------------------------------------
# v2: winsorize y to +-20. Logs clipped count.
#     Fixes best_iter=2 underfitting from target outliers.
# v1: CatBoost per-symbol.
# ============================================================

import os
os.environ.setdefault("USE_CROSS", "0")

import sys
import json
import logging
from pathlib import Path
from datetime import (
    datetime,
    timezone,
)

import numpy as np
from catboost import CatBoostRegressor

SCRIPT_DIR = Path(__file__).resolve().parent
CRYPTO_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(CRYPTO_ROOT))
sys.path.insert(0, str(SCRIPT_DIR))

import dataset as ds

logging.basicConfig(
    level=logging.INFO,
    format=(
        "%(asctime)s [%(levelname)s] "
        "%(message)s"
    ),
    datefmt="%H:%M:%S",
)
log = logging.getLogger(
    "crypto.learn.train_cat"
)

MODELS_DIR = SCRIPT_DIR / "models"
MODELS_DIR.mkdir(
    parents=True, exist_ok=True,
)

NUM_ROUNDS = 1000
EARLY_STOP = 30
VAL_FRAC = 0.15
Y_CLIP = 20.0


def _sym_list():
    raw = (
        os.getenv("SYMBOLS")
        or "BTCUSDT,ETHUSDT,"
           "SOLUSDT,BNBUSDT"
    )
    return [
        s.strip().upper()
        for s in raw.split(",")
        if s.strip()
    ]


def _db2_set():
    raw = (
        os.getenv("DB2_SYMBOLS")
        or "SOLUSDT,BNBUSDT"
    )
    return {
        s.strip().upper()
        for s in raw.split(",")
        if s.strip()
    }


SYMBOLS_LIST = _sym_list()
DB2_SET = _db2_set()


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


def _rmse(a, b):
    return float(
        np.sqrt(np.mean((a - b) ** 2))
    )


def _mae(a, b):
    return float(np.mean(np.abs(a - b)))


def _clip_y(y):
    return np.clip(y, -Y_CLIP, Y_CLIP)


def train_one(symbol):
    log.info("-" * 60)
    log.info("TRAIN CAT %s", symbol)
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

    X_train = data["X_train"]
    X_test = data["X_test"]
    r_train = data["r_train"]
    r_test = data["r_test"]

    n_tr = len(X_train)
    cut = int(n_tr * (1 - VAL_FRAC))

    X_tr = X_train[:cut]
    r_tr = r_train[:cut]
    X_va = X_train[cut:]
    r_va = r_train[cut:]

    n_clipped = int(
        (np.abs(r_tr) > Y_CLIP).sum()
    )
    r_tr_c = _clip_y(r_tr)
    r_va_c = _clip_y(r_va)

    log.info(
        "%s: train=%d val=%d test=%d clipped=%d",
        symbol,
        len(X_tr),
        len(X_va),
        len(X_test),
        n_clipped,
    )

    model = CatBoostRegressor(
        iterations=NUM_ROUNDS,
        learning_rate=0.03,
        depth=4,
        l2_leaf_reg=3.0,
        loss_function="RMSE",
        eval_metric="RMSE",
        random_seed=42,
        verbose=100,
        od_type="Iter",
        od_wait=EARLY_STOP,
    )

    model.fit(
        X_tr,
        r_tr_c,
        eval_set=(X_va, r_va_c),
        use_best_model=True,
    )

    best_iter = int(
        model.get_best_iteration()
    )
    log.info(
        "%s: best_iteration=%d (of %d)",
        symbol, best_iter, NUM_ROUNDS,
    )

    p_train = model.predict(X_train)
    p_test = model.predict(X_test)

    p_train = p_train.astype(np.float32)
    p_test = p_test.astype(np.float32)

    ic_tr = _ic(r_train, p_train)
    ic_te = _ic(r_test, p_test)

    mae_te = _mae(p_test, r_test)
    rmse_te = _rmse(p_test, r_test)

    log.info(
        "%s: test: IC=%.4f MAE=%.4f RMSE=%.4f",
        symbol, ic_te, mae_te, rmse_te,
    )
    log.info(
        "%s: train: IC=%.4f",
        symbol, ic_tr,
    )

    imp = model.get_feature_importance()
    feat_names = data["feature_cols"]

    pairs = []
    for i, name in enumerate(feat_names):
        if i < len(imp):
            pairs.append((name, float(imp[i])))

    pairs.sort(key=lambda x: -x[1])

    log.info("%s: top features:", symbol)
    for name, score in pairs[:5]:
        log.info("  %s: %.2f", name, score)

    model_path = MODELS_DIR / (
        "cat_" + symbol + ".cbm"
    )
    model.save_model(str(model_path))
    log.info("saved %s", model_path.name)

    trained_at = datetime.now(
        timezone.utc
    ).isoformat()

    meta = {
        "trained_at": trained_at,
        "version": "v2-cat",
        "objective": "regression",
        "algorithm": "catboost",
        "symbol": symbol,
        "y_clip": Y_CLIP,
        "n_clipped": n_clipped,
        "n_total": data["n_total"],
        "n_train": data["n_train"],
        "n_test": data["n_test"],
        "best_iteration": best_iter,
        "ic_train": round(ic_tr, 4),
        "ic_test": round(ic_te, 4),
        "mae_test": round(mae_te, 4),
        "rmse_test": round(rmse_te, 4),
        "features": feat_names,
        "top_features": [
            {
                "name": n,
                "gain": round(s, 4),
            }
            for n, s in pairs[:10]
        ],
        "horizon": data["horizon"],
    }

    meta_path = MODELS_DIR / (
        "meta_cat_" + symbol + ".json"
    )
    with open(
        meta_path, "w", encoding="utf-8"
    ) as f:
        json.dump(
            meta,
            f,
            ensure_ascii=False,
            indent=2,
        )
    log.info("saved %s", meta_path.name)

    return meta


def train():
    log.info("=" * 60)
    log.info("ARGUS-Trader TRAIN CAT v2")
    log.info(
        "SYMBOLS=%s", SYMBOLS_LIST
    )
    log.info(
        "NUM_ROUNDS=%d EARLY_STOP=%d Y_CLIP=+-%.1f",
        NUM_ROUNDS, EARLY_STOP, Y_CLIP,
    )
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
    log.info("CAT TRAIN DONE")

    for sym, m in metas.items():
        log.info(
            "  %s: IC=%.4f RMSE=%.4f best_iter=%d",
            sym,
            m["ic_test"],
            m["rmse_test"],
            m["best_iteration"],
        )

    if metas:
        total = 0.0
        for m in metas.values():
            total += m["ic_test"]
        avg = total / len(metas)
        log.info(
            "  AVG IC=%.4f (%d models)",
            avg, len(metas),
        )

    log.info("=" * 60)

    ds.SYMBOLS = [
        "BTCUSDT", "ETHUSDT",
    ]
    ds.REFERENCE = "BTCUSDT"
    ds.DB2_SYMBOLS = DB2_SET

    return metas


def main():
    metas = train()
    if not metas:
        log.error("cat train failed")
        return
    log.info(
        "DONE. %d cat models", len(metas)
    )


if __name__ == "__main__":
    main()