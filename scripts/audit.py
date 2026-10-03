# ============================================================
# ARGUS — AUDIT v2 [READ-ONLY]
# ------------------------------------------------------------
# Только читает. Ничего не удаляет.
# Показывает:
#   • размер и дату каждого файла
#   • кто кого импортирует
#   • какие workflow что запускают
#   • какие env-переменные использует каждый скрипт
#   • какие секреты использует каждый workflow
#   • мёртвые файлы (никто не использует)
#   • дубликаты по содержимому
#   • какие библиотеки импортируются
#   • статистика по папкам
# Пишет: data/audit_report.json
# ============================================================

import re
import json
import hashlib
import argparse
from pathlib import Path
from datetime import datetime, timezone

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
DATA_DIR = REPO_ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
REPORT_FILE = DATA_DIR / "audit_report.json"

SCAN_DIRS = ["scripts", "crypto", "bot", "books", "data"]


def log(msg):
    print(str(msg), flush=True)


def human_size(n):
    for unit in ["B", "KB", "MB", "GB"]:
        if n < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"


def file_hash(path, chunk=1 << 20):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for c in iter(lambda: f.read(chunk), b""):
            h.update(c)
    return h.hexdigest()


def scan_tree(root):
    """Все файлы в директории, кроме служебных."""
    out = {}
    skip = {".git", "__pycache__", "node_modules",
            ".venv", "venv", ".idea", ".vscode"}
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if any(s in p.parts for s in skip):
            continue
        rel = p.relative_to(REPO_ROOT)
        out[str(rel)] = p
    return out


def get_python_info(path):
    try:
        text = path.read_text(encoding="utf-8",
                              errors="ignore")
    except Exception:
        return {}
    imports = set()
    for m in re.finditer(
        r"^\s*(?:from|import)\s+([a-zA-Z_][\w\.]*)",
        text, re.MULTILINE,
    ):
        imports.add(m.group(1).split(".")[0])
    env_vars = set(re.findall(
        r"os\.(?:getenv|environ(?:\.get)?)\([\"']([A-Z_][A-Z0-9_]*)[\"']",
        text,
    ))
    functions = set(re.findall(
        r"^\s*def\s+([a-zA-Z_]\w*)", text, re.MULTILINE,
    ))
    classes = set(re.findall(
        r"^\s*class\s+([a-zA-Z_]\w*)", text, re.MULTILINE,
    ))
    lines = text.count("\n") + 1
    return {
        "lines": lines,
        "imports": sorted(imports),
        "env_vars": sorted(env_vars),
        "functions": sorted(functions),
        "classes": sorted(classes),
    }


def get_workflow_info(path):
    try:
        text = path.read_text(encoding="utf-8",
                              errors="ignore")
    except Exception:
        return {}
    scripts = set(re.findall(
        r"python3?\s+((?:scripts|crypto|bot)/[\w/\.]+\.py)",
        text,
    ))
    secrets = set(re.findall(
        r"secrets\.([A-Z_][A-Z0-9_]*)", text,
    ))
    triggers = set()
    if "workflow_dispatch" in text:
        triggers.add("manual")
    if "repository_dispatch" in text:
        triggers.add("dispatch")
    if "schedule" in text or "cron" in text:
        triggers.add("cron")
    if "push" in text:
        triggers.add("push")
    if "pull_request" in text:
        triggers.add("pr")
    return {
        "scripts": sorted(scripts),
        "secrets": sorted(secrets),
        "triggers": sorted(triggers),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    log("=" * 60)
    log("ARGUS AUDIT v2 [READ-ONLY]")
    log("=" * 60)

    all_files = {}
    for d in SCAN_DIRS:
        root = REPO_ROOT / d
        if root.exists():
            all_files.update(scan_tree(root))

    log(f"Total files scanned: {len(all_files)}")

    # ---- Python ----
    py_files = {
        k: v for k, v in all_files.items()
        if k.endswith(".py")
    }
    py_info = {}
    for name, path in py_files.items():
        info = get_python_info(path)
        info["size"] = human_size(path.stat().st_size)
        info["mtime"] = datetime.fromtimestamp(
            path.stat().st_mtime, tz=timezone.utc
        ).isoformat()
        info["hash"] = file_hash(path)
        py_info[name] = info

    # ---- Workflows ----
    wf_dir = REPO_ROOT / ".github" / "workflows"
    wf_files = {}
    if wf_dir.exists():
        for p in wf_dir.glob("*.yml"):
            wf_files[p.name] = p
    wf_info = {}
    for name, path in wf_files.items():
        info = get_workflow_info(path)
        info["size"] = human_size(path.stat().st_size)
        info["mtime"] = datetime.fromtimestamp(
            path.stat().st_mtime, tz=timezone.utc
        ).isoformat()
        wf_info[name] = info

    # ---- Граф импортов ----
    graph = {name: set() for name in py_files}
    for name, info in py_info.items():
        for other in py_files:
            stem = Path(other).stem
            if stem in info.get("imports", []):
                graph[name].add(other)

    # ---- Кто кого использует ----
    referenced = set()
    for src, targets in graph.items():
        referenced |= targets

    entry_points = set()
    for wf, info in wf_info.items():
        for s in info.get("scripts", []):
            if s in py_files:
                entry_points.add(s)
                referenced.add(s)

    dead = [
        f for f in py_files
        if f not in referenced and f not in entry_points
    ]

    # ---- Дубликаты ----
    by_hash = {}
    for name, info in py_info.items():
        h = info.get("hash", "")
        if h:
            by_hash.setdefault(h, []).append(name)
    duplicates = {
        h: names for h, names in by_hash.items()
        if len(names) > 1
    }

    # ---- Статистика по папкам ----
    by_folder = {}
    for name in all_files:
        folder = name.split("/", 1)[0]
        by_folder[folder] = by_folder.get(folder, 0) + 1

    # ---- Env vars (union) ----
    all_env = set()
    for info in py_info.values():
        all_env |= set(info.get("env_vars", []))

    # ---- Секреты workflow ----
    all_secrets = set()
    for info in wf_info.values():
        all_secrets |= set(info.get("secrets", []))

    # ---- Печать ----
    log("")
    log("=" * 60)
    log(f"STATS BY FOLDER")
    log("=" * 60)
    for folder, n in sorted(by_folder.items(),
                            key=lambda x: -x[1]):
        log(f"  {folder}: {n} files")

    log("")
    log("=" * 60)
    log(f"PYTHON FILES ({len(py_files)})")
    log("=" * 60)
    for name in sorted(py_files):
        info = py_info[name]
        line = f"  {name}  "
        line += f"[{info['size']}, "
        line += f"{info['lines']} lines]"
        log(line)

    log("")
    log("=" * 60)
    log(f"WORKFLOWS ({len(wf_files)})")
    log("=" * 60)
    for name in sorted(wf_files):
        info = wf_info[name]
        trig = ",".join(info.get("triggers", []))
        line = f"  {name}  [{trig}]"
        log(line)

    log("")
    log("=" * 60)
    log(f"ENTRY POINTS ({len(entry_points)})")
    log("=" * 60)
    for f in sorted(entry_points):
        log(f"  ✅ {f}")

    log("")
    log("=" * 60)
    log(f"DEAD FILES ({len(dead)}) [NOT deleted, only listed]")
    log("=" * 60)
    if not dead:
        log("  (none)")
    for f in sorted(dead):
        log(f"  ⚠️  {f}")

    log("")
    log("=" * 60)
    log(f"WORKFLOW -> SCRIPTS")
    log("=" * 60)
    for wf in sorted(wf_info):
        info = wf_info[wf]
        scripts = info.get("scripts", [])
        if not scripts:
            continue
        log(f"  {wf}:")
        for s in scripts:
            log(f"    -> {s}")

    log("")
    log("=" * 60)
    log(f"ENV VARS USED ({len(all_env)})")
    log("=" * 60)
    for v in sorted(all_env):
        log(f"  {v}")

    log("")
    log("=" * 60)
    log(f"SECRETS IN WORKFLOWS ({len(all_secrets)})")
    log("=" * 60)
    for v in sorted(all_secrets):
        log(f"  {v}")

    log("")
    log("=" * 60)
    log(f"DUPLICATES ({len(duplicates)} groups)")
    log("=" * 60)
    if not duplicates:
        log("  (none)")
    for h, names in duplicates.items():
        log(f"  {h[:8]}:")
        for n in names:
            log(f"    {n}")

    log("")
    log("=" * 60)
    log("IMPORT GRAPH (files with deps)")
    log("=" * 60)
    for src in sorted(graph.keys()):
        targets = graph[src]
        if not targets:
            continue
        log(f"  {src} ({len(targets)}):")
        for t in sorted(targets):
            log(f"    -> {t}")

    # ---- Report ----
    report = {
        "generated_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "stats_by_folder": by_folder,
        "python_files": py_info,
        "workflows": wf_info,
        "entry_points": sorted(entry_points),
        "dead_files": sorted(dead),
        "duplicates": {
            h: names
            for h, names in duplicates.items()
        },
        "import_graph": {
            k: sorted(v) for k, v in graph.items()
        },
        "env_vars": sorted(all_env),
        "secrets_in_workflows": sorted(all_secrets),
    }
    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False,
                  indent=2)
    log("")
    log(f"Report: {REPORT_FILE}")


if __name__ == "__main__":
    main()