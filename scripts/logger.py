# ============================================================
# ARGUS — ЛОГИРОВАНИЕ
# ============================================================

import os
import json
from datetime import datetime

LOG_FILE = "logs/argus.log"


def log_action(action, query=None, found_chunks=0, avg_distance=None,
               response_time_ms=0, error=None, extra=None):
    os.makedirs("logs", exist_ok=True)

    entry = {
        "time": datetime.utcnow().isoformat(),
        "action": action,
        "query": query,
        "found_chunks": found_chunks,
        "avg_distance": avg_distance,
        "response_time_ms": response_time_ms,
        "error": error
    }

    if extra:
        entry["extra"] = extra

    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")