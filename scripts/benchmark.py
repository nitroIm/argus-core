# ============================================================
# ARGUS — BENCHMARK v5 [PRODUCTION]
# ------------------------------------------------------------
# v5: разделение positive/negative вопросов, threshold 0.65,
#     отдельные метрики для positive и negative.
# ------------------------------------------------------------
# ВАЖНО: META_FILE = chunks_for_index.json
# Exit codes: 0 = OK, 1 = деградация, 2 = критическая ошибка
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
THRESHOLD = 0.65           # порог "уверенного совпадения"
DEGRADATION_THRESHOLD = 0.05
HISTORY_MAX = 30
MIN_QUESTIONS = 3

# Negative: top-1 не должен быть выше этого значения
NEGATIVE_MAX_TOP1 = 0.55

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


# ============================================================
# УТИЛИТЫ
# ============================================================
def log(msg: str, level: str = "INFO"):
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{ts}] [{level}] {msg}", flush=True)


def notify(text: str, silent: bool = False):
    if not BOT_TOKEN or not CHAT_ID:
        log("Telegram не настроен", "WARN")
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
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(path)


# ============================================================
# ВАЛИДАЦИЯ
# ============================================================
def validate_inputs():
    if not INDEX_FILE.exists() or INDEX_FILE.stat().st_size == 0:
        return False, "faiss.index отсутствует или пуст. Запусти обучение."
    if not META_FILE.exists() or META_FILE.stat().st_size == 0:
        return False, f"{META_FILE.name} отсутствует или пуст. Запусти обучение."
    return True, ""


def load_metadata():
    raw = safe_load_json(META_FILE, [])
    if not isinstance(raw, list):
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
    """Возвращает (positive_list, negative_list)."""
    raw = safe_load_json(QUESTIONS_FILE, None)
    positives, negatives = [], []

    if raw:
        # Формат v2: {"questions": [{"question": "...", "type": "positive"}, ...]}
        items = raw.get("questions", raw) if isinstance(raw, dict) else raw
        if isinstance(items, list):
            for q in items:
                if isinstance(q, dict):
                    text = q.get("question") or q.get("text") or ""
                    qtype = q.get("type", "positive").lower()
                else:
                    text = str(q)
                    qtype = "positive"
                text = text.strip()
                if not text:
                    continue
                if qtype == "negative":
                    negatives.append(text)
                else:
                    positives.append(text)

    # Fallback, если ничего не нашли
    if not positives:
        log("Нет positive-вопросов в файле, использую дефолт", "WARN")
        positives = [
            "Что такое риск-менеджмент в трейдинге?",
            "Что такое имбаланс на рынке?",
            "Как управлять капиталом в трейдинге?",
            "Что такое диверсификация портфеля?",
            "Что такое ликвидность рынка?",
        ]
    if not negatives:
        log("Нет negative-вопросов — метрики могут быть завышены", "WARN")

    return positives, negatives


def load_model():
    from sentence_transformers import SentenceTransformer

    if MODEL_INFO_FILE.exists():
        info = safe_load_json(MODEL_INFO_FILE, {})
        if info:
            model_path = info.get("model_path", "intfloat/multilingual-e5-small")
            prefix = info.get("query_prefix", "")
            log(f"Модель из model_info.json: {info.get('model_label', '?')}, prefix='{prefix}'")
            return SentenceTransformer(model_path), prefix, info.get("model_label", "?")

    if (MODEL TH_DIR / "config.json").exists():
       RES log("Fine-tuned модель (безH префиксаOLD)")
        return SentenceTransformer(str(M)ODEL_DIR)), "", "argus-finetuned"

    log("Базовая e5-small (с префиксом 'query: ')")
    return SentenceTransformer("intfloat/multilingual-e5-small"), "query: ", "e5-small-base"


# ============================================================
# МЕТРИКИ
# ============================================================
def compute_metrics(scores_1d):
    """Recall@1, Recall@5, MRR по threshold."""
    recall_at_1 = 1.0 if (len(scores_1d) > 0 and scores_1d[0] >= else 0.0
    hits_in_5 = sum(1 for s in scores_1d[:TOP_K] if s >= THRESHOLD)
    recall_at_5 = min(1.0, hits_in_5 / max(1, TOP_K))

    mrr = 0.0
    for rank, s in enumerate(scores_1d, 1):
        if s >= THRESHOLD:
            mrr = 1.0 / rank
            break
    return recall_at_1, recall_at_5, mrr


# ============================================================
# MAIN
# ============================================================
def main():
    t_start = time.time()
    log(f"=== ARGUS BENCHMARK v5 ===")
    log(f"Threshold: {THRESHOLD}")

    ok, err = validate_inputs()
    if not ok:
        log(err, "ERROR")
        notify(f"⚠️ <b>Бенчмарк не запущен</b>\n\n{err}")
        sys.exit(2)

    meta = load_metadata()
    if len(meta) < 1:
        notify("⚠️ <b>Бенчмарк:</b> метаданные пусты.")
        sys.exit(2)

    positives, negatives = load_questions()
    log(f"Чанков: {len(meta)}")
    log(f"Positive вопросов: {len(positives)}")
    log(f"Negative вопросов: {len(negatives)}")

    model, prefix, model_label = load_model()
    index = faiss.read_index(str(INDEX_FILE))
    dim = index.d
    log(f"Индекс: {index.ntotal} векторов, dim={dim}")

    test_vec = model.encode(["test"], normalize_embeddings=True)
    if test_vec.shape[1] != dim:
        msg = f"Модель {test_vec.shape[1]}d vs индекс {dim}d — пересобери индекс."
        log(msg, "ERROR")
        notify(f"❌ <b>Бенчмарк:</b> {msg}")
        sys.exit(2)

    if index.ntotal != len(meta):
        log(f"РАССИНХРОН: индекс {index.ntotal} vs мета {len(meta)}", "WARN")

    texts = [m["text"] for m in meta]
    sources = [m["book"] for m in meta]

    def run_query(q, qtype="positive"):
        t0 = time.time()
        emb = model.encode([prefix + q], normalize_embeddings=True).astype("float32")
        scores, ids = index.search(emb, TOP_K)
        latency_ms = (time.time() - t0) * 1000

        s = scores[0]
        idx_list = ids[0]
        top1 = float(s[0]) if len(s) > 0 else 0.0
        top5 = float(np.mean(s)) if len(s) > 0 else 0.0
        r1, r5, mrr = compute_metrics(s)

        hits = []
        for sc, idx in zip(s, idx_list):
            if 0 <= idx < len(texts):
                hits.append({
                    "score": round(float(sc), 4),
                    "book": sources[idx],
                    "text": texts[idx][:200],
                })

        return {
            "question": q,
            "type": qtype,
            "top1": round(top1, 4),
            "top5": round(top5, 4),
            "recall_at_1": round(r1, 4),
            "recall_at_5": round(r5, 4),
            "mrr": round(mrr, 4),
            "latency_ms": round(latency_ms, 1),
            "hits": hits,
        }

    # --- Positive ---
    log(f"\n--- POSITIVE ({len(positives)}) ---")
    pos_results = []
    for i, q in enumerate(positives, 1):
        r = run_query(q, "positive")
        pos_results.append(r)
        log(f"  [{i}/{len(positives)}] top1={r['top1']:.3f} r@5={r['recall_at_5']:.2f} — {q[:50]}")

    # --- Negative ---
    log(f"\n--- NEGATIVE ({len(negatives)}) ---")
    neg_results = []
    for i, q in enumerate(negatives, 1):
        r = run_query(q, "negative")
        neg_results.append(r)
        log(f"  [{i}/{len(negatives)}] top1={r['top1']:.3f} (должно быть <{NEGATIVE_MAX_TOP1}) — {q[:50]}")

    # --- Агрегация positive ---
    def mean(arr, key):
        vals = [r[key] for r in arr]
        return round(float(np.mean(vals)), 4) if vals else 0.0

    pos_avg1 = mean(pos_results, "top1")
    pos_avg5 = mean(pos_results, "top5")
    pos_r1 = mean(pos_results, "recall_at_1")
    pos_r5 = mean(pos_results, "recall_at_5")
    pos_mrr = mean(pos_results, "mrr")
    pos_lat = mean(pos_results, "latency_ms")

    # --- Агрегация negative ---
    neg_avg1 = mean(neg_results, "top1") if neg_results else 0.0
    neg_false_positives = sum(1 for r in neg_results if r["top1"] >= NEGATIVE_MAX_TOP1)
    neg_fp_rate = round(neg_false_positives / len(neg_results), 4) if neg_results else 0.0

    # --- Latency p95 ---
    all_latencies = [r["latency_ms"] for r in pos_results + neg_results]
    p95_lat = round(float(np.percentile(all_latencies, 95)), 1) if all_latencies else 0.0

    # --- Сравнение с baseline ---
    history = safe_load_json(HISTORY_FILE, []) or []
    if not isinstance(history, list):
        history = []
    prev = history[-1] if history else None
    delta_top1 = None
    degraded = False
    if prev and prev.get("avg_top1"):
        delta_top1 = pos_avg1 - prev["avg_top1"]
        if delta_top1 < -DEGRADATION_THRESHOLD:
            degraded = True
        log(f"Δ top1 vs предыдущий: {delta_top1:+.4f}")

    # Если negative FP rate > 30% — деградация
    if neg_results and neg_fp_rate > 0.3:
        degraded = True
        log(f"❌ Слишком много false-positives на negative: {neg_fp_rate:.2%}", "WARN")

    # --- Бэкап + запись ---
    if RESULTS_FILE.exists():
        try:
            shutil.copy2(RESULTS_FILE, RESULTS_BACKUP)
        except Exception as e:
            log(f"Бэкап не удался: {e}", "WARN")

    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "model_label": model_label,
        "total_chunks": len(meta),
        "threshold": THRESHOLD,
        "positives": len(pos_results),
        "negatives": len(neg_results),
        "avg_top1": pos_avg1,
        "avg_top5": pos_avg5,
        "recall_at_1": pos_r1,
        "recall_at_5": pos_r5,
        "mrr": pos_mrr,
        "neg_avg_top1": neg_avg1,
        "neg_fp_rate": neg_fp_rate,
        "avg_latency_ms": pos_lat,
        "p95_latency_ms": p95_lat,
        "degraded": degraded,
    }

    atomic_write_json(RESULTS_FILE, {**entry, "results": pos_results + neg_results})

    history.append(entry)
    history = history[-HISTORY_MAX:]
    atomic_write_json(HISTORY_FILE, history)

    # --- Отчёт ---
    delta_str = f"\n📈 Δ top1: <b>{delta_top1:+.4f}</b>" if delta_top1 is not None else ""
    degraded_str = "\n🚨 <b>ДЕГРАДАЦИЯ!</b>" if degraded else ""

    elapsed = round(time.time() - t_start, 1)
    msg = (
        f"🎯 <b>Бенчмарк v5 завершён</b>\n\n"
        f"🤖 Модель: <code>{model_label}</code>\n"
        f"📚 Чанков: {len(meta)}\n"
        f"⚙️ Threshold: {THRESHOLD}\n\n"
        f"<b>POSITIVE ({len(pos_results)}):</b>\n"
        f"• top-1 avg: <b>{pos_avg1:.4f}</b>\n"
        f"• top-5 avg: {pos_avg5:.4f}\n"
        f"• recall@1: <b>{pos_r1:.4f}</b>\n"
        f"• recall@5: <b>{pos_r5:.4f}</b>\n"
        f"• MRR: <b>{pos_mrr:.4f}</b>\n\n"
        f"<b>NEGATIVE ({len(neg_results)}):</b>\n"
        f"• top-1 avg: {neg_avg1:.4f}\n"
        f"• false-positive rate: <b>{neg_fp_rate:.2%}</b>\n\n"
        f"<b>Производительность:</b>\n"
        f"• latency avg: {pos_lat} мс\n"
        f"• latency p95: {p95_lat} мс\n"
        f"• время прогона: {elapsed} с"
        f"{delta_str}{degraded_str}"
    )
    notify(msg, silent=not degraded)

    log("=" * 50)
    log(f"POSITIVE: top1={pos_avg1}  recall@1={pos_r1}  recall@5={pos_r5}  MRR={pos_mrr}")
    log(f"NEGATIVE: top1={neg_avg1}  FP rate={neg_fp_rate}")
    log(f"Latency: avg={pos_lat}ms  p95={p95_lat}ms")
    log(f"degraded={degraded}")
    log("=" * 50)

    sys.exit(1 if degraded else 0)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(2)
    except Exception as e:
        import traceback
        log(f"КРИТИЧЕСКАЯ: {e}", "ERROR")
        traceback.print_exc()
        notify(f"❌ <b>Бенчмарк упал</b>\n\n<code>{str(e)[:300]}</code>")
        sys.exit(2)