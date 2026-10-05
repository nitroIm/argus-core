# ============================================================
# ARGUS-Trader - CLEAN MODELS
# ------------------------------------------------------------
# v1: removes model artifacts before retrain.
#     Deletes: lgb_*.txt, meta_*.json, model_meta.json.
#     Keeps: best_params.json, prev/ directory.
#     Use before manual Learn run to force fresh cycle.
# ============================================================

import sys
import logging
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
MODELS_DIR = SCRIPT_DIR / "models"

KEEP_FILES = {"best_params.json"}
KEEP_DIRS = {"prev"}

PATTERNS = [
    "lgb_*.txt",
    "meta_*.json",
]

EXTRA_FILES = [
    "model_meta.json",
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("crypto.learn.clean")


def collect_targets():
    targets = set()
    for pat in PATTERNS:
        for fp in MODELS_DIR.glob(pat):
            if fp.is_file():
                targets.add(fp)
    for name in EXTRA_FILES:
        fp = MODELS_DIR / name
        if fp.is_file():
            targets.add(fp)
    return sorted(targets)


def main():
    log.info("=" * 60)
    log.info("ARGUS CLEAN MODELS")
    log.info("DIR: %s", MODELS_DIR)
    log.info("=" * 60)

    if not MODELS_DIR.exists():
        log.error("models dir missing: %s", MODELS_DIR)
        sys.exit(1)

    # safety: verify keep list exists
    for name in KEEP_FILES:
        fp = MODELS_DIR / name
        if fp.exists():
            log.info("keep file: %s", fp.name)
    for name in KEEP_DIRS:
        fp = MODELS_DIR / name
        if fp.exists() and fp.is_dir():
            log.info("keep dir:  %s/", fp.name)

    targets = collect_targets()
    if not targets:
        log.info("nothing to remove")
        return

    log.info("targets: %d", len(targets))
    removed = 0
    for fp in targets:
        try:
            fp.unlink()
            removed += 1
            log.info("removed: %s", fp.name)
        except Exception as e:
            log.warning("rm %s: %s", fp.name, e)

    log.info("=" * 60)
    log.info("DONE. removed %d files", removed)
    log.info("=" * 60)


if __name__ == "__main__":
    main()