# ============================================================
# ARGUS-Trader - EXPORT v8
# ------------------------------------------------------------
# v8: remove EXTERNAL_COLS import (dropped in dataset v17).
# v7: remove external_market block.
# ============================================================

import os
os.environ.setdefault("USE_CROSS", "0")

import sys
import csv
import json
import shutil
import logging
from pathlib import Path
from datetime import datetime, timezone

SCRIPT_DIR = Path(__file__).resolve().parent
CRYPTO_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(CRYPTO_ROOT))
sys.path.insert(0, str(SCRIPT_DIR))

from db import get_connection
from dataset import (
    FEATURE_COLS, INTERNAL_COLS,
    TARGET_COL, HORIZON, REFERENCE,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("crypto.learn.export")

MODELS_DIR = SCRIPT_DIR / "models"
EXPORT_DIR = SCRIPT_DIR / "export"
EXPORT_DIR.mkdir(parents=True, exist_ok=True)

SYMBOLS_LIST = [
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "BNBUSDT",
]


def export_all_models():
    exported = 0
    for sym in SYMBOLS_LIST:
        src_m = MODELS_DIR / ("lgb_" + sym + ".txt")
        src_mt = MODELS_DIR / (
            "meta_" + sym + ".json"
        )
        if not src_m.exists():
            log.warning("%s: model missing", sym)
            continue
        dst_m = EXPORT_DIR / (
            "lgb_" + sym + ".txt"
        )
        shutil.copy2(src_m, dst_m)
        if src_mt.exists():
            with open(
                src_mt, "r", encoding="utf-8"
            ) as f:
                meta = json.load(f)
            meta["exported_at"] = datetime.now(
                timezone.utc
            ).isoformat()
            dst_mt = EXPORT_DIR / (
                "meta_" + sym + ".json"
            )
            with open(
                dst_mt, "w", encoding="utf-8"
            ) as f:
                json.dump(
                    meta, f,
                    ensure_ascii=False,
                    indent=2,
                )
        log.info("model -> %s", dst_m.name)
        exported += 1

    btc_m = MODELS_DIR / "lgb_BTCUSDT.txt"
    btc_mt = MODELS_DIR / "meta_BTCUSDT.json"
    if btc_m.exists():
        shutil.copy2(
            btc_m, EXPORT_DIR / "lgb_model.txt"
        )
        log.info("compat: lgb_model.txt <- BTC")
    if btc_mt.exists():
        with open(
            btc_mt, "r", encoding="utf-8"
        ) as f:
            meta = json.load(f)
        meta["exported_at"] = datetime.now(
            timezone.utc
        ).isoformat()
        with open(
            EXPORT_DIR / "model_meta.json",
            "w", encoding="utf-8",
        ) as f:
            json.dump(
                meta, f,
                ensure_ascii=False,
                indent=2,
            )
        log.info(
            "compat: model_meta.json <- BTC"
        )
    return exported


def export_dataset_csv():
    cols1 = (
        ["symbol", "timestamp"]
        + INTERNAL_COLS
        + [TARGET_COL]
    )
    out1 = EXPORT_DIR / "features_hourly.csv"
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT " + ", ".join(cols1)
                    + " FROM features_hourly "
                    + "ORDER BY timestamp"
                )
                rows = cur.fetchall()
        with open(
            out1, "w", encoding="utf-8",
            newline="",
        ) as f:
            w = csv.writer(f)
            w.writerow(cols1)
            for r in rows:
                w.writerow(r)
        log.info(
            "features -> %s (%d rows)",
            out1.name, len(rows),
        )
    except Exception as exc:
        log.error("export features: %s", exc)


def export_readme():
    readme = EXPORT_DIR / "README.md"
    lines = [
        "# ARGUS ML - Export v8",
        "",
        "## Per-symbol models",
        "- lgb_BTCUSDT.txt + meta_BTCUSDT.json",
        "- lgb_ETHUSDT.txt + meta_ETHUSDT.json",
        "- lgb_SOLUSDT.txt + meta_SOLUSDT.json",
        "- lgb_BNBUSDT.txt + meta_BNBUSDT.json",
        "",
        "## Compat",
        "- lgb_model.txt = copy of BTC",
        "- model_meta.json = copy of BTC meta",
        "",
        "## Data",
        "- features_hourly.csv - 30 internal cols",
        "",
        "## Config",
        "- HORIZON: " + str(HORIZON) + "h",
        "- REFERENCE: " + REFERENCE,
        "- FEATURE_COLS: " + str(len(FEATURE_COLS)),
        "- USE_CROSS: 0 (off)",
        "",
        "## Usage",
        "1. pip install lightgbm==4.5.0",
        "2. model = lgb.Booster(",
        "     model_file='lgb_BTCUSDT.txt')",
    ]
    with open(
        readme, "w", encoding="utf-8"
    ) as f:
        f.write("\n".join(lines))
    log.info("README -> %s", readme.name)


def main():
    log.info("=" * 60)
    log.info("ARGUS-Trader EXPORT v8")
    log.info(
        "FEATURE_COLS=%d INTERNAL=%d",
        len(FEATURE_COLS),
        len(INTERNAL_COLS),
    )
    log.info("=" * 60)

    n = export_all_models()
    export_dataset_csv()
    export_readme()

    log.info(
        "done: %d models -> %s", n, EXPORT_DIR
    )


if __name__ == "__main__":
    main()