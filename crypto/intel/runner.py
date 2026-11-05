# ============================================================
# ARGUS - INTEL RUNNER (v1.1.1)
# Path: crypto/intel/runner.py
# ============================================================

import os
import sys
import logging
import subprocess
from pathlib import Path
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("intel_runner")

SCRIPT_DIR = Path(__file__).resolve().parent

# Сюда добавляем файлы по мере их создания.
PIPELINE = [
    "correlation.py",
    # "news.py",
    # "brain.py"
]

def run_script(script_name):
    path = SCRIPT_DIR / script_name
    if not path.exists():
        log.warning(f"⚠️ Файл не найден, пропускаем: {script_name}")
        return False
    
    log.info(f"▶️ Запуск модуля: {script_name}")
    start_time = datetime.now(timezone.utc)
    
    try:
        result = subprocess.run(
            [sys.executable, str(path)], 
            check=True, 
            capture_output=False
        )
        elapsed = (datetime.now(timezone.utc) - start_time).total_seconds()
        log.info(f"✅ Модуль {script_name} успешно отработал за {elapsed:.1f} сек.")
        return True
        
    except subprocess.CalledProcessError as e:
        log.error(f"❌ Ошибка в модуле {script_name} (Код выхода: {e.returncode})")
        return False
    except Exception as e:
        log.error(f"❌ Критическая ошибка при запуске {script_name}: {e}")
        return False

def main():
    log.info("========================================")
    log.info("СТАРТ INTEL (Модуль Аудита)")
    log.info("========================================")
    
    if not os.getenv("AIVEN_DB_URL"):
        log.error("AIVEN_DB_URL не задан! Модули не смогут подключиться к БД.")
        sys.exit(1)

    success_count = 0
    fail_count = 0

    for script in PIPELINE:
        if run_script(script):
            success_count += 1
        else:
            fail_count += 1

    log.info("========================================")
    log.info(f"ФИНИШ INTEL. Успешно: {success_count} | Ошибок: {fail_count}")
    log.info("========================================")

    if fail_count > 0:
        sys.exit(1)

if __name__ == "__main__":
    main()