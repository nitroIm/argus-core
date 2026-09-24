# ============================================================
# ARGUS-Trader - EXPORT
# ------------------------------------------------------------
# Экспорт модели + данных для миграции.
# Кладет в crypto/learn/export/:
#   - lgb_model.txt
#   - model_meta.json
#   - features_hourly.csv
#   - README.md
# ============================================================

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
from dataset import FEATURE_COLS, TARGET_COL

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("crypto.learn.export")

MODELS_DIR = SCRIPT_DIR / "models"
EXPORT_DIR = SCRIPT_DIR / "export"
EXPORT_DIR.mkdir(parents=True, exist_ok=True)


def export_model():
    """Копирует модель в export/."""
    src = MODELS_DIR / "lgb_model.txt"
    if not src.exists():
        log.warning("no model file")
        return
    dst = EXPORT_DIR / "lgb_model.txt"
    shutil.copy2(src, dst)
    log.info("model -> %s", dst.name)


def export_meta():
    """Копирует meta модели в export/."""
    src = MODELS_DIR / "model_meta.json"
    if not src.exists():
        log.warning("no meta file")
        return
    dst = EXPORT_DIR / "model_meta.json"
    with open(src, "r", encoding="utf-8") as f:
        meta = json.load(f)
    meta["exported_at"] = datetime.now(
        timezone.utc
    ).isoformat()
    with open(dst, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    log.info("meta -> %s", dst.name)


def export_dataset_csv():
    """Экспорт features_hourly в CSV."""
    cols = (
        ["symbol", "timestamp"]
        + FEATURE_COLS
        + [TARGET_COL]
    )
    sql = (
        "SELECT " + ", ".join(cols)
        + " FROM features_hourly ORDER BY timestamp"
    )

    out = EXPORT_DIR / "features_hourly.csv"
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql)
                rows = cur.fetchall()
        with open(out, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(cols)
            for r in rows:
                w.writerow(r)
        log.info(
            "exported %d rows -> %s",
            len(rows), out.name,
        )
        return len(rows)
    except Exception as e:
        log.error("export csv: %s", e)
        return 0


def export_readme():
    """Инструкция для миграции."""
    readme = EXPORT_DIR / "README.md"
    lines = [
        "# ARGUS ML - Export",
        "",
        "## Что внутри",
        "- lgb_model.txt - LightGBM модель",
        "- model_meta.json - метрики + список features",
        "- features_hourly.csv - данные для переобучения",
        "- README.md - этот файл",
        "",
        "## Как использовать на новой машине",
        "",
        "1. Установить зависимости:",
        "pip install lightgbm==4.5.0 scikit-learn==1.5.2 pandas",
        "",
        "2. Загрузить модель:",
        "import lightgbm as lgb",
        "model = lgb.Booster(model_file='lgb_model.txt')",
        "",
        "3. Признаки (порядок важен):",
        ", ".join(FEATURE_COLS),
        "",
        "4. Целевая: " + TARGET_COL + " (0/1)",
        "",
        "5. Переобучение:",
        "python train.py",
        "",
        "## Формат CSV",
        "symbol, timestamp, "
        + ", ".join(FEATURE_COLS)
        + ", " + TARGET_COL,
        "",
        "## Заметка",
        "Модель переносима. Привязок к БД нет.",
    ]
    with open(readme, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    log.info("README -> %s", readme.name)


def main():
    log.info("=" * 60)
    log.info("ARGUS-Trader EXPORT")
    log.info("=" * 60)

    export_model()
    export_meta()
    export_dataset_csv()
    export_readme()

    log.info("done -> %s", EXPORT_DIR)


if __name__ == "__main__":
    main()