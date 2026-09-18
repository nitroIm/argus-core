# ============================================================
# ARGUS — ОБРАБОТКА ОДОБРЕНИЙ
# ============================================================

import os
import sys
import json
import subprocess

SHORT_ID = sys.argv[1] if len(sys.argv) > 1 else None

if not SHORT_ID:
    print("❌ Не указан short_id")
    exit(1)

CANDIDATES_FILE = "data/scout_candidates.json"

with open(CANDIDATES_FILE, "r", encoding="utf-8") as f:
    data = json.load(f)

pending = data.get("pending", {})
item = pending.get(SHORT_ID)

if not item:
    print(f"❌ Не найден: {SHORT_ID}")
    exit(1)

url = item["url"]
title = item["title"]

print(f"📥 Скачиваю: {title}")
print(f"🔗 URL: {url}")

# Передаём в collector
result = subprocess.run(
    ["python", "scripts/collector.py", url],
    capture_output=True,
    text=True,
    timeout=600
)

print(result.stdout)
if result.returncode != 0:
    print(result.stderr)
    exit(1)

# Удаляем из pending
del pending[SHORT_ID]
data["pending"] = pending

with open(CANDIDATES_FILE, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

print(f"✅ Готово: {title}")