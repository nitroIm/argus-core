# ============================================================
# ARGUS — BENCHMARK (проверка качества поиска) v4 [PRODUCTION]
# ------------------------------------------------------------
# v4: продакшн-версия.
#   • Метрики: avg_top1, avg_top5, recall@1, recall@5, MRR, p95_latency
#   • Baseline-контроль: алерт и exit-код при деградации >порога
#   • Валидация целостности: index.ntotal == len(meta), dim проверка
#   • Читает model_info.json для правильных префиксов (fine-tuned vs base)
#   • Расширенное логирование каждого шага
#   • Сохраняет benchmark_results.json + benchmark_history.json + бэкапы
#   • Graceful: не падает без индекса, шлёт алерт в Telegram
#   • Exit code: 0 = OK, 1 = деградация, 2 = критическая ошибка
# ------------------------------------------------------------
# ВАЖНО: META_FILE = chunks_for_index.json (НЕ chunks_metadata.json!)
# ============================================================

import os
import sys
import json
import time
import shutil
import requests
import numpy as np
import faiss
from pathlib import Path
from datetime import datetime, timezone

# --- Пути ---
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
DATA_DIR = REPO_ROOT / "data"
INDEX_FILE = DATA_DIR / "faiss.index"
META_FILE = DATA_DIR / "chunks_for_index.json"
MODEL_INFO_FILE = DATA_DIR / "model_info.json"
MODEL_DIR = REPO_ROOT / "models" / "argus-embeddings"
QUESTIONS_FILE = DATA_DIR / "benchmark_questions.json"
RESULTS_FILE = DATA_DIR / "benchmark_results.json"
HISTORY_FILE = DATA_DIR / "benchmark_history.json"
RESULTS_BACKUP = DATA_DIR / "benchmark_results.prev.json"

# --- Настройки ---
TOP_K = 5
DEGRADATION_THRESHOLD = 0.05     # 5% — порог деградации, ниже которого шлём алерт
HISTORY_MAX = 30                 # сколько запусков хранить
MIN_QUESTIONS = 3                # минимум вопросов для валидного прогона

# --- Telegram ---
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# --- Базовые вопросы (fallback если нет benchmark_questions.json) ---
DEFAULT_QUESTIONS = [
    "Что такое риск-менеджмент в трейдинге?",
    "Что такое имбаланс на рынке?",
    "Как управлять капиталом в трейдинге?",
    "Что такое диверсификация портфеля?",
    "Что такое ликвидность рынка?",
    "Что такое стоп-лосс и зачем он нужен?",
    "Чем спот-рынок отличается от фьючерсного?",
    "Что такое эмоциональная дисциплина трейдера?",
    "Что такое соотношение риска и прибыли?",
    "Что такое волатильность рынка?",
]


# ============================================================
# УТИЛИТЫ
# ============================================================
def log(msg: str, level: str = "INFO"):
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{ts}] [{level}] {msg}", flush=True)


def notify(text: str, silent: bool = False):
    if not BOT_TOKEN or not CHAT_ID:
        log("Telegram не настроен, уведомление пропущено", "WARN")
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={
                "chat_id": CHAT_ID,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
                "disable_notification": silent,
            },
            timeout=15,
        )
        return r.status_code == 200
    except Exception as e:
        log(f"Telegram ошибка: {e}", "ERROR")
        return False


def safe_load_json(path: Path, default=None):
    if not path.exists():
        return default
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log(f"Не читается {path.name}: {e}", "WARN")
        return default


def atomic_write_json(path: Path, data):
    """Пишем через .tmp + rename, чтобы не потерять файл при сбое."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(path)


# ============================================================
# ВАЛИДАЦИЯ
# ============================================================
def validate_inputs():
    """Проверяем, что всё на месте. Возвращаем (ok, error_msg)."""
    if not INDEX_FILE.exists() or INDEX_FILE.stat().st_size == 0:
        return False, "faiss.index отсутствует или пуст. Запусти обучение."
    if not META_FILE.exists() or META_FILE.stat().st_size == 0:
        return False, f"{META_FILE.name} отсутствует или пуст. Запусти обучение."
    return True, ""


def load_metadata():
    """Читаем метаданные, поддерживаем оба формата."""
    raw = safe_load_json(META_FILE, [])
    if not isinstance(raw, list):
        log(f"{META_FILE.name} не список — пустая мета", "WARN")
        return []

    out = []
    for i, m in enumerate(raw):
        if isinstance(m, dict) and "text" in m:
            out.append({
                "id": m.get("id", i),
                "text": str(m.get("text", "")),
                "source": m.get("source") or m.get("book") or "unknown",
                "book": m.get("book") or m.get("source") or "unknown",
            })
        elif isinstance(m, str):
            out.append({"id": i, "text": m, "source": "unknown", "book": "unknown"})
    return out


def load_questions():
    """Читаем вопросы из файла, fallback — дефолт."""
    raw = safe_load_json(QUESTIONS_FILE, None)
    if raw and isinstance(raw, list):
        qs = []
        for q in raw:
            if isinstance(q, dict):
                qs.append(q.get("question") or q.get("text") or "")
            else:
                qs.append(str(q))
        qs = [q.strip() for q in qs if q.strip()]
        if len(qs) >= MIN_QUESTIONS:
            log(f"Загружено вопросов из файла: {len(qs)}")
            return qs
    log(f"Использую дефолтные вопросы ({len(DEFAULT_QUESTIONS)})")
    return list(DEFAULT_QUESTIONS)


def load_model():
    """Определяем модель + префикс. Приоритет: model_info.json → папка → fallback."""
    from sentence_transformers import SentenceTransformer

    if MODEL_INFO_FILE.exists():
        info = safe_load_json(MODEL_INFO_FILE, {})
        if info:
            model_path = info.get("model_path", "intfloat/multilingual-e5-small")
            prefix = info.get("query_prefix", "")
            log(f"Модель из model_info.json: {info.get('model_label', '?')}, prefix='{prefix}'")
            return SentenceTransformer(model_path), prefix, info.get("model_label", "?")

    if (MODEL_DIR / "config.json").exists():
        log("Fine-tuned модель (без префикса)")
        return SentenceTransformer(str(MODEL_DIR)), "", "argus-finetuned"

    log("Базовая e5-small (с префиксом 'query: ')")
    return SentenceTransformer("intfloat/multilingual-e5-small"), "query: ", "e5-small-base"


# ============================================================
# МЕТРИКИ
# ============================================================
def compute_recall_mrr(scores_1d, ids_1d, meta_count, threshold=0.5):
    """
    Recall@k и MRR для одного запроса.
    Recall@k = сколько попало в top-k с score > threshold
    MRR = 1 / rank первого попадания
    """
    hits = [1 for s in scores_1d if s >= threshold]
    recall_at_1 = 1.0 if (len(scores_1d) > 0 and scores_1d[0] >= threshold) else 0.0
    recall_at_5 = min(1.0, sum(hits) / max(1, TOP_K))

    mrr = 0.0
    for rank, s in enumerate(scores_1d, 1):
        if s >= threshold:
            mrr = 1.0 / rank
            break

    return recall_at_1, recall_at_5, mrr


# ============================================================
# MAIN
# ============================================================
def main():
    t_start = time.time()
    log(f"=== ARGUS BENCHMARK v4 ===")
    log(f"Запуск: {datetime.now(timezone.utc).isoformat()}")

    # --- 1. Валидация ---
    ok, err = validate_inputs()
    if not ok:
        log(err, "ERROR")
        notify(f"⚠️ <b>Бенчмарк не запущен</b>\n\n{err}")
        sys.exit(2)

    meta = load_metadata()
    if len(meta) < 1:
        log("Метаданные пусты после парсинга", "ERROR")
        notify("⚠️ <b>Бенчмарк:</b> метаданные пусты.")
        sys.exit(2)

    questions = load_questions()
    log(f"Чанков в базе: {len(meta)}")
    log(f"Вопросов: {len(questions)}")

    # --- 2. Загрузка модели и индекса ---
    model, prefix, model_label = load_model()
    index = faiss.read_index(str(INDEX_FILE))
    dim = index.d
    log(f"Индекс: {index.ntotal} векторов, dim={dim}")

    # Валидация размерности
    if index.ntotal != len(meta):
        log(f"РАССИНХРОН: индекс {index.ntotal} vs метаданных {len(meta)}", "WARN")
        log("Прогон продолжится, но метрики могут быть недостоверны")

    # Проверка dim через тестовый encode
    test_vec = model.encode(["test"], normalize_embeddings=True)
    if test_vec.shape[1] != dim:
        msg = f"Несовместимость: модель {test_vec.shape[1]}d, индекс {dim}d. Пересобери индекс."
        log(msg, "ERROR")
        notify(f"❌ <b>Бенчмарк:</b> {msg}")
        sys.exit(2)

    texts = [m["text"] for m in meta]
    sources = [m["book"] for m in meta]

    # --- 3. Прогон по вопросам ---
    log(f"Начинаю прогон {len(questions)} вопросов...")
    results = []
    top1s, top5s, r1s, r5s, mrrs, latencies = [], [], [], [], [], []

    for i, q in enumerate(questions, 1):
        t0 = time.time()
        try:
            emb = model.encode([prefix + q], normalize_embeddings=True).astype("float32")
            scores, ids = index.search(emb, TOP_K)
            latency_ms = (time.time() - t0) * 1000
        except Exception as e:
            log(f"  [{i}] ОШИБКА: {q[:40]} → {e}", "ERROR")
            continue

        s = scores[0]
        idx_list = ids[0]

        top1 = float(s[0]) if len(s) > 0 else 0.0
        top5 = float(np.mean(s)) if len(s) > 0 else 0.0
        r1, r5, mrr = compute_recall_mrr(s, idx_list, len(meta))

        top1s.append(top1)
        top5s.append(top5)
        r1s.append(r1)
        r5s.append(r5)
        mrrs.append(mrr)
        latencies.append(latency_ms)

        hits = []
        for sc, idx in zip(s, idx_list):
            if 0 <= idx < len(texts):
                hits.append({
                    "score": round(float(sc), 4),
                    "book": sources[idx],
                    "text": texts[idx][:200],
                })

        results.append({
            "question": q,
            "top1": round(top1, 4),
            "top5": round(top5, 4),
            "recall_at_1": round(r1, 4),
            "recall_at_5": round(r5, 4),
            "mrr": round(mrr, 4),
            "latency_ms": round(latency_ms, 1),
            "hits": hits,
        })

        log(f"  [{i}/{len(questions)}] top1={top1:.3f} r@5={r5:.2f} mrr={mrr:.2f} — {q[:50]}")

    if not results:
        log("Не удалось обработать ни одного вопроса", "ERROR")
        notify("❌ <b>Бенчмарк:</b> ни один вопрос не обработан.")
        sys.exit(2)

    # --- 4. Агрегация ---
    def safe_mean(arr):
        return round(float(np.mean(arr)), 4) if arr else 0.0

    avg1 = safe_mean(top1s)
    avg5 = safe_mean(top5s)
    avg_r1 = safe_mean(r1s)
    avg_r5 = safe_mean(r5s)
    avg_mrr = safe_mean(mrrs)
    p95_latency = round(float(np.percentile(latencies, 95)), 1) if latencies else 0.0
    avg_latency = safe_mean(latencies)

    # --- 5. Сравнение с baseline ---
    history = safe_load_json(HISTORY_FILE, []) or []
    if not isinstance(history, list):
        history = []

    prev = history[-1] if history else None
    delta_top1 = None
    degraded = False
    if prev and prev.get("avg_top1"):
        delta_top1 = avg1 - prev["avg_top1"]
        if delta_top1 < -DEGRADATION_THRESHOLD:
            degraded = True
        log(f"Δ top1 vs предыдущий: {delta_top1:+.4f}")

    # --- 6. Бэкап предыдущих результатов ---
    if RESULTS_FILE.exists():
        try:
            shutil.copy2(RESULTS_FILE, RESULTS_BACKUP)
        except Exception as e:
            log(f"Бэкап не удался: {e}", "WARN")

    # --- 7. Запись результатов (атомарно) ---
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "model_label": model_label,
        "total_chunks": len(meta),
        "questions": len(questions),
        "avg_top1": avg1,
        "avg_top5": avg5,
        "recall_at_1": avg_r1,
        "recall_at_5": avg_r5,
        "mrr": avg_mrr,
        "avg_latency_ms": avg_latency,
        "p95_latency_ms": p95_latency,
        "degraded": degraded,
    }

    atomic_write_json(RESULTS_FILE, {
        **entry,
        "results": results,
    })

    history.append(entry)
    history = history[-HISTORY_MAX:]
    atomic_write_json(HISTORY_FILE, history)

    # --- 8. Отчёт ---
    delta_str = f"\n📈 Δ top1: <b>{delta_top1:+.4f}</b>" if delta_top1 is not None else ""
    degraded_str = "\n🚨 <b>ДЕГРАДАЦИЯ! Проверь качество!</b>" if degraded else ""

    elapsed = round(time.time() - t_start, 1)
    msg = (
        f"🎯 <b>Бенчмарк завершён</b>\n\n"
        f"🤖 Модель: <code>{model_label}</code>\n"
        f"📚 Чанков: {len(meta)}\n"
        f"❓ Вопросов: {len(questions)}\n\n"
        f"<b>Метрики:</b>\n"
        f"• top-1 (avg): <b>{avg1:.4f}</b>\n"
        f"• top-5 (avg): <b>{avg5:.4f}</b>\n"
        f"• recall@1: <b>{avg_r1:.4f}</b>\n"
        f"• recall@5: <b>{avg_r5:.4f}</b>\n"
        f"• MRR: <b>{avg_mrr:.4f}</b>\n"
        f"• latency p95: {p95_latency} мс\n"
        f"• время прогона: {elapsed} с"
        f"{delta_str}{degraded_str}"
    )
    notify(msg, silent=not degraded)

    log("=" * 50)
    log(f"avg_top1={avg1}  avg_top5={avg5}")
    log(f"recall@1={avg_r1}  recall@5={avg_r5}  MRR={avg_mrr}")
    log(f"latency avg={avg_latency}ms  p95={p95_latency}ms")
    log(f"degraded={degraded}")
    log("=" * 50)

    # --- 9. Exit code ---
    if degraded:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("Прервано пользователем", "WARN")
        sys.exit(2)
    except Exception as e:
        import traceback
        log(f"КРИТИЧЕСКАЯ ОШИБКА: {e}", "ERROR")
        traceback.print_exc()
        notify(f"❌ <b>Бенчмарк упал</b>\n\n<code>{str(e)[:300]}</code>")
        sys.exit(2)