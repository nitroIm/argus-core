# ============================================================
# ARGUS — BENCHMARK (проверка качества поиска) v3
# v3: FIX — файл chunks_for_index.json (не chunks_metadata.json)
#     Поддерживает ОБА формата: список строк и список словарей.
#     Читает model_info.json для правильных префиксов.
# ============================================================

import os
import sys
import json
import requests
import numpy as np
import faiss
from pathlib import Path
from datetime import datetime, timezone

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
DATA_DIR = REPO_ROOT / "data"
INDEX_FILE = DATA_DIR / "faiss.index"
META_FILE = DATA_DIR / "chunks_for_index.json"       # ← ИСПРАВЛЕНО
MODEL_INFO_FILE = DATA_DIR / "model_info.json"
MODEL_DIR = REPO_ROOT / "models" / "argus-embeddings"
QUESTIONS_FILE = DATA_DIR / "benchmark_questions.json"
RESULTS_FILE = DATA_DIR / "benchmark_results.json"
HISTORY_FILE = DATA_DIR / "benchmark_history.json"

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

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


def notify(text: str):
    if not BOT_TOKEN or not CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"},
            timeout=15,
        )
    except Exception:
        pass


def load_metadata():
    """Поддерживает оба формата: список строк (legacy) и список словарей."""
    if not META_FILE.exists():
        return []
    try:
        with open(META_FILE, encoding="utf-8") as f:
            raw = json.load(f)
    except Exception as e:
        print(f"⚠️ Ошибка чтения {META_FILE.name}: {e}")
        return []

    if not isinstance(raw, list):
        return []

    out = []
    for i, m in enumerate(raw):
        if isinstance(m, dict):
            out.append(m)
        else:
            out.append({"id": i, "text": str(m), "source": "unknown", "book": "unknown"})
    return out


def load_questions():
    if QUESTIONS_FILE.exists():
        try:
            with open(QUESTIONS_FILE, encoding="utf-8") as f:
                raw = json.load(f)
            qs = []
            for q in raw:
                if isinstance(q, dict):
                    qs.append(q.get("question") or q.get("text") or "")
                else:
                    qs.append(str(q))
            qs = [q for q in qs if q.strip()]
            if qs:
                return qs
        except Exception:
            pass
    return list(DEFAULT_QUESTIONS)


def load_model():
    """Определяет модель и префикс: сначала model_info.json, потом fallback."""
    from sentence_transformers import SentenceTransformer

    # 1. Приоритет — model_info.json (его пишет train_embeddings.py)
    if MODEL_INFO_FILE.exists():
        try:
            with open(MODEL_INFO_FILE, encoding="utf-8") as f:
                info = json.load(f)
            model_path = info.get("model_path", "intfloat/multilingual-e5-small")
            prefix = info.get("query_prefix", "")
            print(f"🧠 model_info.json: {info.get('model_label', '?')}, prefix='{prefix}'")
            return SentenceTransformer(model_path), prefix
        except Exception as e:
            print(f"⚠️ model_info.json битый: {e}")

    # 2. Fallback — детект папки fine-tuned
    if (MODEL_DIR / "config.json").exists():
        print("🧠 Загружаю fine-tuned модель (без префикса)...")
        return SentenceTransformer(str(MODEL_DIR)), ""

    # 3. Fallback — базовая e5-small
    print("🧠 Загружаю базовую multilingual-e5-small (с префиксом 'query: ')...")
    return SentenceTransformer("intfloat/multilingual-e5-small"), "query: "


def main():
    print(f"🎯 Benchmark: {datetime.now(timezone.utc).isoformat()}")

    if not INDEX_FILE.exists():
        msg = "⚠️ <b>Бенчмарк:</b> индекс ещё не построен. Сначала запусти обучение."
        print(msg)
        notify(msg)
        sys.exit(0)

    meta = load_metadata()
    if not meta:
        msg = (f"⚠️ <b>Бенчмарк:</b> метаданные пусты.\n"
               f"Проверь файл <code>{META_FILE.name}</code> — он должен существовать и быть непустым.")
        print(msg)
        notify(msg)
        sys.exit(0)

    questions = load_questions()
    model, prefix = load_model()
    index = faiss.read_index(str(INDEX_FILE))

    # Синхронизация: если meta меньше индекса — предупреждаем
    if len(meta) != index.ntotal:
        print(f"⚠️ Рассинхрон: индекс {index.ntotal} векторов, метаданных {len(meta)}")

    texts = [m.get("text", "") for m in meta]
    sources = [m.get("book") or m.get("source") or "?" for m in meta]

    results = []
    top1s = []
    top5s = []

    for q in questions:
        emb = model.encode([prefix + q], normalize_embeddings=True)
        scores, ids = index.search(emb.astype("float32"), 5)
        s = scores[0]
        top1 = float(s[0])
        top5 = float(np.mean(s))
        top1s.append(top1)
        top5s.append(top5)

        hits = []
        for sc, idx in zip(s, ids):
            if 0 <= idx < len(texts):
                hits.append({
                    "score": round(float(sc), 4),
                    "book": sources[idx],
                    "text": texts[idx][:150],
                })
        results.append({
            "question": q,
            "top1": round(top1, 4),
            "top5": round(top5, 4),
            "hits": hits,
        })
        print(f"   {top1:.3f} | {q[:50]}")

    avg1 = round(float(np.mean(top1s)), 4) if top1s else 0.0
    avg5 = round(float(np.mean(top5s)), 4) if top5s else 0.0

    prev = None
    history = []
    if HISTORY_FILE.exists():
        try:
            with open(HISTORY_FILE, encoding="utf-8") as f:
                history = json.load(f)
            if history:
                prev = history[-1].get("avg_top1")
        except Exception:
            history = []

    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "avg_top1": avg1,
        "avg_top5": avg5,
        "questions": len(questions),
    }
    history.append(entry)
    history = history[-20:]

    with open(RESULTS_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "timestamp": entry["timestamp"],
            "avg_top1": avg1,
            "avg_top5": avg5,
            "results": results,
        }, f, ensure_ascii=False, indent=2)

    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

    delta = ""
    if prev is not None:
        d = avg1 - prev
        delta = f"\n📈 Изменение с прошлого раза: {d:+.4f}"

    msg = (
        f"🎯 <b>Бенчмарк завершён</b>\n\n"
        f"Средний score top-1: <b>{avg1:.4f}</b>\n"
        f"Средний score top-5: <b>{avg5:.4f}</b>\n"
        f"Вопросов: {len(questions)}{delta}"
    )
    notify(msg)
    print(f"\n✅ avg_top1={avg1} avg_top5={avg5}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"❌ Ошибка бенчмарка: {e}")
        notify(f"❌ <b>Ошибка бенчмарка:</b>\n<code>{e}</code>")
        sys.exit(1)