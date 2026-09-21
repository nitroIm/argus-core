# ============================================================
# ARGUS-Trader — RUNNER (оркестратор enrich)
# ------------------------------------------------------------
# Запускает всю цепочку enrich последовательно:
#   features → patterns → levels → events → causal
# Один workflow — все данные связаны через общий workspace.
# ------------------------------------------------------------
# v1: начальная версия
# ============================================================

import sys
import time
import logging
import traceback
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
CRYPTO_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(CRYPTO_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("crypto.runner")


STEPS = [
    ("features", "crypto.enrich.features"),
    ("patterns", "crypto.enrich.patterns"),
    ("levels", "crypto.enrich.levels"),
    ("events", "crypto.enrich.events"),
    ("causal", "crypto.enrich.causal"),
]


def run_step(name, module_name):
    log.info("")
    log.info("=" * 60)
    log.info(f"▶️  STEP: {name}")
    log.info("=" * 60)

    started = time.time()
    try:
        # Динамический импорт
        module = __import__(module_name, fromlist=["main"])
        module.main()
        elapsed = round(time.time() - started, 1)
        log.info(f"✅ {name} за {elapsed}с")
        return True, elapsed, None
    except Exception as e:
        elapsed = round(time.time() - started, 1)
        log.error(f"❌ {name} упал за {elapsed}с: {e}")
        log.error(traceback.format_exc())
        return False, elapsed, str(e)


def main():
    log.info("🚀 ARGUS-Trader ENRICH RUNNER")
    log.info(f"Время: {datetime.now(timezone.utc).isoformat()}")
    log.info("")

    results = []
    failed = 0

    for name, module_name in STEPS:
        success, elapsed, error = run_step(name, module_name)
        results.append({
            "step": name,
            "success": success,
            "elapsed": elapsed,
            "error": error,
        })
        if not success:
            failed += 1
            log.error(f"⛔ Останавливаюсь — {name} провалился")
            break

    log.info("")
    log.info("=" * 60)
    log.info("📊 РЕЗУЛЬТАТЫ")
    log.info("=" * 60)
    for r in results:
        status = "✅" if r["success"] else "❌"
        log.info(f"  {status} {r['step']:10} за {r['elapsed']:>6.1f}с")
        if r["error"]:
            log.info(f"     ошибка: {r['error'][:100]}")

    total_time = sum(r["elapsed"] for r in results)
    log.info("")
    log.info(f"Всего шагов: {len(results)}")
    log.info(f"Успешных: {len(results) - failed}")
    log.info(f"Провалено: {failed}")
    log.info(f"Общее время: {total_time:.1f}с")
    log.info("=" * 60)

    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()