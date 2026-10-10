# ============================================================
# ARGUS-Trader - TRAIN v16
# ------------------------------------------------------------
# v16: 36 features (dataset v19).
#      Stronger regularization (min_data=200, L1/L2=2.0).
#      Early stopping by IC (feval), not RMSE.
#      No SYMBOL_OVERRIDES (single param set).
# v15: honest val split, ic_val in meta, Y_CLIP=20.
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
NUM_ROUNDS = 1500
VAL_FRAC = 0.15
EARLY_STOP = 50
Y_CLIP = 20.0

PARAMS_BASE = {
    "objective": "regression",
    "boosting_type": "gbdt",
    "num_leaves": 8,
    "max_depth": 3,
    "learning_rate": 0.02,
    "feature_fraction": 0.4,
    "bagging_fraction": 0.6,
    "bagging_freq": 5,
    "min_data_in_leaf": 200,
    "lambda_l1": 2.0,
    "lambda_l2": 2.0,
    "verbose": -1,
    "seed": 42,
}

# v16: no per-symbol overrides.
SYMBOL_OVERRIDES = {}

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


def params_for(symbol):
    p = dict(PARAMS_BASE)
    rounds = NUM_ROUNDS
    early = EARLY_STOP

    ov = SYMBOL_OVERRIDES.get(symbol)
    if ov:
        for k, v in ov.items():
            if k == "num_rounds":
                rounds = v
            elif k == "early_stop":
                early = v
            else:
                p[k] = v

    return p, rounds, early


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
    yt = y_true - y_true.mean()
    yp = y_pred - y_pred.mean()
    d = np.sqrt((yt * yt).sum() * (yp * yp).sum())
    if d == 0:
        return 0.0
    return float((yt * yp).sum() / d)


def _ic_feval(preds, eval_data):
    y = eval_data.get_label()
    if len(y) < 10:
        return "ic", 0.0, True
    yt = y - y.mean()
    yp = preds - preds.mean()
    d = np.sqrt((yt * yt).sum() * (yp * yp).sum())
    if d == 0:
        return "ic", 0.0, True
    ic = float((yt * yp).sum() / d)
    return "ic", ic, True


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

    r_train = np.clip(r_train, -Y_CLIP, Y_CLIP)
    r_test_c = np.clip(r_test, -Y_CLIP, Y_CLIP)

    n_tr = len(X_train)
    cut = int(n_tr * (1 - VAL_FRAC))

    purge = ds.PURGE_HOURS
    cut_clean = max(cut - purge, int(n_tr * 0.5))

    X_tr = X_train[:cut_clean]
    r_tr = r_train[:cut_clean]
    X_va = X_train[cut:]
    r_va = r_train[cut:]

    log.info(
        "%s: train=%d val=%d test=%d (gap=%dh)",
        symbol, len(X_tr), len(X_va),
        len(X_test), cut - cut_clean,
    )

    params, num_rounds, early_stop = params_for(symbol)
    log.info(
        "%s: features=%d rounds=%d early=%d",
        symbol, X_tr.shape[1],
        num_rounds, early_stop,
    )

    train_set = lgb.Dataset(X_tr, label=r_tr)
    val_set = lgb.Dataset(
        X_va, label=r_va, reference=train_set,
    )

    model = lgb.train(
        params, train_set,
        num_boost_round=num_rounds,
        valid_sets=[val_set],
        feval=_ic_feval,
        callbacks=[
            lgb.early_stopping(
                early_stop,
                verbose=False,
                first_metric_only=True,
            ),
            lgb.log_evaluation(100),
        ],
    )

    best_iter = (
        model.best_iteration
        or model.num_trees()
    )
    log.info(
        "%s: best_iteration=%d (of %d)",
        symbol, best_iter, num_rounds,
    )

    p_tr = model.predict(X_tr).astype(np.float32)
    p_va = model.predict(X_va).astype(np.float32)
    p_te = model.predict(X_test).astype(np.float32)

    mae_tr = float(np.mean(np.abs(p_tr - r_tr)))
    mae_va = float(np.mean(np.abs(p_va - r_va)))
    mae_te = float(
        np.mean(np.abs(p_te - r_test_c))
    )
    rmse_tr = float(
        np.sqrt(np.mean((p_tr - r_tr) ** 2))
    )
    rmse_va = float(
        np.sqrt(np.mean((p_va - r_va) ** 2))
    )
    rmse_te = float(
        np.sqrt(np.mean((p_te - r_test_c) ** 2))
    )
    ic_tr = _ic(r_tr, p_tr)
    ic_va = _ic(r_va, p_va)
    ic_te = _ic(r_test_c, p_te)

    sign_tr = float(
        np.mean(np.sign(p_tr) == np.sign(r_tr))
    )
    sign_va = float(
        np.mean(np.sign(p_va) == np.sign(r_va))
    )
    sign_te = float(
        np.mean(np.sign(p_te) == np.sign(r_test_c))
    )

    log.info(
        "%s: test:  IC=%.4f MAE=%.4f RMSE=%.4f",
        symbol, ic_te, mae_te, rmse_te,
    )
    log.info(
        "%s: val:   IC=%.4f MAE=%.4f RMSE=%.4f",
        symbol, ic_va, mae_va, rmse_va,
    )
    log.info(
        "%s: train: IC=%.4f MAE=%.4f RMSE=%.4f",
        symbol, ic_tr, mae_tr, rmse_tr,
    )
    log.info(
        "%s: sign-acc: tr=%.4f va=%.4f te=%.4f",
        symbol, sign_tr, sign_va, sign_te,
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
    for name, score in pairs[:8]:
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
        "version": "v16",
        "objective": "regression",
        "symbol": symbol,
        "n_total": data["n_total"],
        "n_train": data["n_train"],
        "n_test": data["n_test"],
        "n_tr_inner": len(X_tr),
        "n_val_inner": len(X_va),
        "purge_hours": int(purge),
        "gap_tr_va": int(cut - cut_clean),
        "y_clip": Y_CLIP,
        "best_iteration": best_iter,
        "ic_train": round(ic_tr, 4),
        "ic_val": round(ic_va, 4),
        "ic_test": round(ic_te, 4),
        "mae_train": round(mae_tr, 4),
        "mae_val": round(mae_va, 4),
        "mae_test": round(mae_te, 4),
        "rmse_train": round(rmse_tr, 4),
        "rmse_val": round(rmse_va, 4),
        "rmse_test": round(rmse_te, 4),
        "sign_acc_train": round(sign_tr, 4),
        "sign_acc_val": round(sign_va, 4),
        "sign_acc_test": round(sign_te, 4),
        "num_trees": model.num_trees(),
        "features": feat_names,
        "top_features": [
            {"name": n, "gain": round(float(s), 2)}
            for n, s in pairs[:10]
        ],
        "horizon": data["horizon"],
        "params_used": params,
        "num_rounds": num_rounds,
        "early_stop": early_stop,
        "override": False,
    }
    with open(
        meta_file(symbol), "w", encoding="utf-8",
    ) as f:
        json.dump(
            meta, f,
            ensure_ascii=False, indent=2,
        )

    return meta


def write_compat(symbols, metas):
    ref = (
        "BTCUSDT"
        if "BTCUSDT" in symbols else symbols[0]
    )
    src = model_file(ref)
    if src.exists():
        shutil.copy2(
            src, MODELS_DIR / "lgb_model.txt"
        )
        log.info(
            "compat: lgb_model.txt <- lgb_%s.txt",
            ref,
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
    log.info(
        "ARGUS-Trader TRAIN v16 "
        "(36feat, purge=%dh, ic-stop)",
        ds.PURGE_HOURS,
    )
    log.info("SYMBOLS=%s", SYMBOLS_LIST)
    log.info(
        "BASE: rounds=%d early=%d val=%.2f",
        NUM_ROUNDS, EARLY_STOP, VAL_FRAC,
    )
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
            "  %s: IC_te=%.4f IC_va=%.4f "
            "best_iter=%d",
            sym, m["ic_test"],
            m["ic_val"], m["best_iteration"],
        )
    if metas:
        ic_avg = (
            sum(m["ic_test"] for m in metas.values())
            / len(metas)
        )
        log.info(
            "  AVG IC_test=%.4f (%d models)",
            ic_avg, len(metas),
        )
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