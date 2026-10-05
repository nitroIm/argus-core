# ============================================================
# ARGUS-Trader - TRAIN [PRODUCTION]
# ------------------------------------------------------------
# v12: regression target (next_return). Metrics: MAE, RMSE,
#      Spearman IC. No accuracy/confusion.
# v11: PARAMS упрощены под per-symbol данные.
# v10: per-symbol модели.
# ============================================================

import os
os.environ.setdefault("USE_CROSS", "0")

import sys
import json
import shutil
import logging
from pathlib import Path
from datetime import datetime, timezone

import numpy as np
import lightgbm as lgb
from scipy.stats import spearmanr

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
log = logging.getLogger("crypto.learn.train")

MODELS_DIR = SCRIPT_DIR / "models"
MODELS_DIR.mkdir(parents=True, exist_ok=True)

PREV_DIR = MODELS_DIR / "prev"

MIN_SAMPLES = 200
NUM_ROUNDS = 200

PARAMS = {
    "objective": "regression",
    "metric": "rmse",
    "boosting_type": "gbdt",
    "num_leaves": 8,
    "max_depth": 3,
    "learning_rate": 0.03,
    "feature_fraction": 0.5,
    "bagging_fraction": 0.6,
    "bagging_freq": 5,
    "min_data_in_leaf": 80,
    "lambda_l1": 1.0,
    "lambda_l2": 1.0,
    "verbose": -1,
    "seed": 42,
}

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

def model_file(sym):
    return MODELS_DIR / ("lgb_" + sym + ".txt")

def meta_file(sym):
    return MODELS_DIR / ("meta_" + sym + ".json")

def prev_model_file(sym):
    return PREV_DIR / ("lgb_" + sym + ".txt")

def prev_meta_file(sym):
    return PREV_DIR / ("meta_" + sym + ".json")

def save_prev(sym):
    mf = model_file(sym)
    if not mf.exists():
        return
    PREV_DIR.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(mf, prev_model_file(sym))
        mfa = meta_file(sym)
        if mfa.exists():
            shutil.copy2(mfa, prev_meta_file(sym))
    except Exception as e:
        log.warning("prev save %s: %s", sym, e)

def _ic(y_true, y_pred):
    if len(y_true) < 10:
        return 0.0
    if np.std(y_true) == 0 or np.std(y_pred) == 0:
        return 0.0
    rho, _ = spearmanr(y_true, y_pred)
    if np.isnan(rho):
        return 0.0
    return float(rho)

def train_one(symbol):
    log.info("-" * 60)
    log.info("TRAIN %s", symbol)
    log.info("-" * 60)

    ds.SYMBOLS = [symbol]
    ds.REFERENCE = symbol
    ds.DB2_SYMBOLS = (
        {symbol} if symbol in DB2_SET else set()
    )

    data = ds.prepare()
    if data is None:
        log.error("%s: no data", symbol)
        return None

    if data["n_train"] < MIN_SAMPLES:
        log.warning(
            "%s: not enough samples: %d < %d",
            symbol, data["n_train"], MIN_SAMPLES,
        )

    X_train = data["X_train"]
    X_test = data["X_test"]
    r_train = data["r_train"]
    r_test = data["r_test"]

    log.info(
        "%s: train=%d test=%d",
        symbol, len(X_train), len(X_test),
    )

    train_set = lgb.Dataset(X_train, label=r_train)

    model = lgb.train(
        PARAMS, train_set,
        num_boost_round=NUM_ROUNDS,
        callbacks=[lgb.log_evaluation(50)],
    )

    p_train = model.predict(X_train).astype(np.float32)
    p_test = model.predict(X_test).astype(np.float32)

    mae_tr = float(np.mean(np.abs(p_train - r_train)))
    mae_te = float(np.mean(np.abs(p_test - r_test)))
    rmse_tr = float(np.sqrt(np.mean((p_train - r_train) ** 2)))
    rmse_te = float(np.sqrt(np.mean((p_test - r_test) ** 2)))
    ic_tr = _ic(r_train, p_train)
    ic_te = _ic(r_test, p_test)

    # hit-rate по знаку (информативно, не метрика)
    sign_tr = float(
        np.mean(np.sign(p_train) == np.sign(r_train))
    )
    sign_te = float(
        np.mean(np.sign(p_test) == np.sign(r_test))
    )

    log.info(
        "%s: test: IC=%.4f MAE=%.4f RMSE=%.4f",
        symbol, ic_te, mae_te, rmse_te,
    )
    log.info(
        "%s: train: IC=%.4f MAE=%.4f RMSE=%.4f",
        symbol, ic_tr, mae_tr, rmse_tr,
    )
    log.info(
        "%s: sign-acc: train=%.4f test=%.4f",
        symbol, sign_tr, sign_te,
    )

    importance = model.feature_importance(
        importance_type="gain"
    )
    feat_names = data["feature_cols"]
    pairs = sorted(
        zip(feat_names, importance),
        key=lambda x: -x[1],
    )
    log.info("%s: top features:", symbol)
    for name, score in pairs[:5]:
        log.info("  %s: %.2f", name, score)

    save_prev(symbol)
    model.save_model(str(model_file(symbol)))
    log.info(
        "%s: saved %s",
        symbol, model_file(symbol).name,
    )

    meta = {
        "trained_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "version": "v12",
        "objective": "regression",
        "symbol": symbol,
        "n_total": data["n_total"],
        "n_train": data["n_train"],
        "n_test": data["n_test"],
        "ic_train": round(ic_tr, 4),
        "ic_test": round(ic_te, 4),
        "mae_train": round(mae_tr, 4),
        "mae_test": round(mae_te, 4),
        "rmse_train": round(rmse_tr, 4),
        "rmse_test": round(rmse_te, 4),
        "sign_acc_train": round(sign_tr, 4),
        "sign_acc_test": round(sign_te, 4),
        "num_trees": model.num_trees(),
        "features": feat_names,
        "top_features": [
            {"name": n, "gain": round(float(s), 2)}
            for n, s in pairs[:10]
        ],
        "horizon": data["horizon"],
    }
    with open(meta_file(symbol), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    return meta

def write_compat(symbols, metas):
    ref = "BTCUSDT" if "BTCUSDT" in symbols else symbols[0]
    src = model_file(ref)
    if src.exists():
        shutil.copy2(src, MODELS_DIR / "lgb_model.txt")
        log.info(
            "compat: lgb_model.txt <- lgb_%s.txt", ref
        )
    if metas.get(ref):
        with open(
            MODELS_DIR / "model_meta.json",
            "w", encoding="utf-8",
        ) as f:
            json.dump(
                metas[ref], f,
                ensure_ascii=False, indent=2,
            )

def train():
    log.info("=" * 60)
    log.info("ARGUS-Trader TRAIN v12 (per-symbol, regression)")
    log.info("SYMBOLS=%s", SYMBOLS_LIST)
    log.info("PARAMS: %s", PARAMS)
    log.info("=" * 60)

    metas = {}
    for sym in SYMBOLS_LIST:
        try:
            m = train_one(sym)
            if m:
                metas[sym] = m
        except Exception as e:
            log.error("%s: %s", sym, e)

    log.info("=" * 60)
    log.info("TRAIN DONE")
    for sym, m in metas.items():
        log.info(
            "  %s: IC=%.4f MAE=%.4f RMSE=%.4f",
            sym, m["ic_test"],
            m["mae_test"], m["rmse_test"],
        )
    ic_avg = (
        sum(m["ic_test"] for m in metas.values())
        / len(metas)
    ) if metas else 0.0
    log.info("  AVG IC=%.4f (%d models)", ic_avg, len(metas))
    log.info("=" * 60)

    if metas:
        write_compat(list(metas.keys()), metas)

    ds.SYMBOLS = ["BTCUSDT", "ETHUSDT"]
    ds.REFERENCE = "BTCUSDT"
    ds.DB2_SYMBOLS = DB2_SET

    return metas

def main():
    metas = train()
    if not metas:
        log.error("train failed")
        return
    log.info("DONE. %d models trained", len(metas))

if __name__ == "__main__":
    main()