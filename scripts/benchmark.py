# ============================================================
# ARGUS — BENCHMARK (v2)
# v2: синхронизация с build_index (meta.json, префиксы E5, pathlib, timezone)
# ============================================================

import os
import sys
import json
import time
import random
import requests
from datetime import datetime, timezone
from pathlib import Path
from html import escape as hesc

import faiss
import numpy as np
from sentence_transformers import SentenceTransformer

# ============================================================
# ПУТИ
# ============================================================
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
DATA_DIR = REPO_ROOT / "data"
MODELS_DIR = REPO_ROOT / "models"

INDEX_FILE = DATA_DIR / "faiss.index"
META_FILE = DATA_DIR / "chunks_meta.json"  # <-- ИСПРАВЛЕНО: читаем метаданные
RESULTS_FILE = DATA_DIR / "benchmark_results.json"
HISTORY_FILE = DATA_DIR / "benchmark_history.json"

BASE_MODEL = "intfloat/multilingual-e5-small"

# ============================================================
# КОНФИГ
# ============================================================
AUTO_SAMPLE_SIZE = 50     # сколько случайных чанков проверить
TOP_K = 5                 # сколько результатов возвращает поиск

# ============================================================
# GOLDEN SET — вопросы по книгам
# ============================================================
GOLDEN_QUESTIONS = [
    # --- Алготрейдинг ---
    {"q": "Что такое RSI и как его использовать в трейдинге?",
     "keywords": ["rsi", "индекс", "сил", "перекуплен", "перепродан"]},
    {"q": "Как работают скользящие средние в трейдинге?",
     "keywords": ["средн", "ma", "moving", "average", "скользящ"]},
    {"q": "Что такое риск-менеджмент в алгоритмической торговле?",
     "keywords": ["риск", "risk", "менеджмент", "capital", "позици"]},
    {"q": "Стратегии возврата к среднему (mean reversion)",
     "keywords": ["mean", "reversion", "возврат", "средн"]},
    {"q": "Как построить торгового робота на Python?",
     "keywords": ["python", "робот", "bot", "trade", "backtest"]},

    # --- Бэкон, философия ---
    {"q": "Что описывает Бэкон в Новой Атлантиде?",
     "keywords": ["атлантид", "бэкон", "остров", "бенсалем", "научн"]},
    {"q": "Как устроено общество в утопии Бэкона?",
     "keywords": ["атлантид", "общество", "бенсалем", "дом", "соломон"]},
    {"q": "Идея научного прогресса у Фрэнсиса Бэкона",
     "keywords": ["наук", "бэкон", "прогресс", "эксперимент", "опыт"]},

    # --- Криптовалюты / ML ---
    {"q": "Как коррелируют между собой разные криптовалюты?",
     "keywords": ["коррел", "correlation", "cryptocurrenc", "bitcoin", "сет"]},
    {"q": "Что такое обучающие кривые в машинном обучении?",
     "keywords": ["learning", "curve", "обучен", "крив", "decision"]},
    {"q": "Как выбрать данные для обучения ML-модели?",
     "keywords": ["data", "данн", "sources", "обучен", "выбор"]},
    {"q": "Применение активного обучения к потокам данных",
     "keywords": ["active", "learning", "stream", "активн", "поток"]},

    # --- Провалы (ARGS должен их не найти, но мы проверяем уверенность) ---
    {"q": "Как работает квантовая запутанность в биологии?",
     "keywords": ["квант", "quantum", "запутан", "биолог"]},
    {"q": "Экономика марсианской колонии",
     "keywords": ["марс", "mars", "колони", "эконом"]},
]


# ============================================================
# УТИЛИТЫ
# ============================================================
def load_json(path, default=None):
    if not path.exists():
        return default if default is not None else {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ============================================================
# ТЕСТ A — RECALL (авто)
# ============================================================
def test_recall(model, index, chunks, use_prefix):
    print("\n" + "=" * 50)
    print("📊 ТЕСТ A — RECALL (авто)")
    print("=" * 50)

    sample_size = min(AUTO_SAMPLE_SIZE, len(chunks))
    if sample_size == 0:
        print("⚠️ Нет чанков для тестирования")
        return {}

    random.seed(42)
    sample_indices = random.sample(range(len(chunks)), sample_size)

    hits_at_1 = 0
    hits_at_k = 0
    total_time = 0.0

    for orig_idx in sample_indices:
        chunk_text = chunks[orig_idx]
        query = chunk_text[:200].strip()
        if len(query) < 30:
            continue

        # Добавляем префикс, если используем базовую E5
        search_q = f"query: {query}" if use_prefix else query

        t0 = time.time()
        vec = model.encode([search_q], normalize_embeddings=True).astype("float32")
        distances, indices = index.search(vec, TOP_K)
        total_time += (time.time() - t0)

        found = list(indices[0])
        if orig_idx == found[0]:
            hits_at_1 += 1
        if orig_idx in found:
            hits_at_k += 1

    avg_time_ms = int((total_time / sample_size) * 1000) if sample_size else 0
    recall_1 = round(hits_at_1 / sample_size * 100, 1)
    recall_k = round(hits_at_k / sample_size * 100, 1)

    print(f"Проверено чанков: {sample_size}")
    print(f"Recall@1: {recall_1}%")
    print(f"Recall@{TOP_K}: {recall_k}%")
    print(f"Среднее время: {avg_time_ms} мс")

    return {
        "sample_size": sample_size,
        "recall_at_1": recall_1,
        f"recall_at_{TOP_K}": recall_k,
        "avg_time_ms": avg_time_ms,
    }


# ============================================================
# ТЕСТ B — GOLDEN
# ============================================================
def test_golden(model, index, chunks, use_prefix):
    print("\n" + "=" * 50)
    print("🎯 ТЕСТ B — GOLDEN QUESTIONS")
    print("=" * 50)

    results = []
    passed = 0

    for item in GOLDEN_QUESTIONS:
        query = item["q"]
        keywords = [k.lower() for k in item["keywords"]]
        
        search_q = f"query: {query}" if use_prefix else query

        vec = model.encode([search_q], normalize_embeddings=True).astype("float32")
        distances, indices = index.search(vec, TOP_K)

        # Собираем весь текст найденных фрагментов
        found_text = " ".join(
            chunks[idx].lower()
            for idx in indices[0]
            if 0 <= idx < len(chunks)
        )

        # Сколько ключевых слов найдено
        matched = [k for k in keywords if k in found_text]
        hit_ratio = len(matched) / len(keywords) if keywords else 0

        # Считаем «прошло», если хотя бы половина ключей в результатах
        ok = hit_ratio >= 0.5
        if ok:
            passed += 1

        best_score = float(distances[0][0]) if len(distances[0]) else 0.0
        results.append({
            "query": query,
            "ok": ok,
            "hit_ratio": round(hit_ratio, 2),
            "matched": matched,
            "keywords": keywords,
            "top_score": round(best_score, 3),
        })

        icon = "✅" if ok else "❌"
        print(f"{icon} {query[:60]}… ({len(matched)}/{len(keywords)} ключей, score {best_score:.2f})")

    total = len(GOLDEN_QUESTIONS)
    pass_rate = round(passed / total * 100, 1) if total else 0

    print(f"\nПройдено: {passed}/{total} ({pass_rate}%)")

    return {
        "total": total,
        "passed": passed,
        "pass_rate": pass_rate,
        "details": results,
    }


# ============================================================
# ОСНОВНОЕ
# ============================================================
def main():
    print("🧪 ARGUS BENCHMARK")
    print("=" * 50)

    if not INDEX_FILE.exists():
        print(f"❌ Нет {INDEX_FILE}")
        sys.exit(1)
    if not META_FILE.exists():
        print(f"❌ Нет {META_FILE}")
        sys.exit(1)

    # --- Выбор модели (как в build_index и ask) ---
    TRAINED_MODEL = MODELS_DIR / "argus-embeddings"
    if TRAINED_MODEL.exists() and (TRAINED_MODEL / "config.json").exists():
        model_path = str(TRAINED_MODEL)
        use_prefix = False
        print(f"🎓 Тестируем обученную модель: {model_path}")
    else:
        model_path = BASE_MODEL
        use_prefix = True
        print(f"📦 Тестируем базовую модель: {model_path} (с префиксом 'query: ')")

    print("📦 Загружаю модель...")
    model = SentenceTransformer(model_path)

    print("📦 Загружаю индекс...")
    index = faiss.read_index(str(INDEX_FILE))

    # Читаем метаданные и извлекаем только тексты для бенчмарка
    meta_chunks = load_json(META_FILE, [])
    chunks = [m.get("text", "") for m in meta_chunks]

    print(f"✅ Векторов: {index.ntotal}, чанков: {len(chunks)}")

    # --- Тесты ---
    recall = test_recall(model, index, chunks, use_prefix)
    golden = test_golden(model, index, chunks, use_prefix)

    # --- Предыдущий результат для сравнения ---
    prev = load_json(RESULTS_FILE, {})
    prev_recall = prev.get("recall", {}).get(f"recall_at_{TOP_K}")
    prev_golden = prev.get("golden", {}).get("pass_rate")

    delta_recall = None
    delta_golden = None
    if prev_recall is not None and f"recall_at_{TOP_K}" in recall:
        delta_recall = round(recall[f"recall_at_{TOP_K}"] - prev_recall, 1)
    if prev_golden is not None and "pass_rate" in golden:
        delta_golden = round(golden["pass_rate"] - prev_golden, 1)

    # --- Итог ---
    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model_used": model_path,
        "chunks_total": len(chunks),
        "index_total": index.ntotal,
        "recall": recall,
        "golden": golden,
        "delta": {
            "recall_at_k": delta_recall,
            "golden_pass_rate": delta_golden,
        },
    }

    save_json(RESULTS_FILE, result)

    # История
    history = load_json(HISTORY_FILE, {"runs": []})
    history["runs"].append({
        "time": result["generated_at"],
        "model": model_path.split("/")[-1],
        "chunks": len(chunks),
        "recall_at_k": recall.get(f"recall_at_{TOP_K}", 0),
        "golden_pass_rate": golden.get("pass_rate", 0),
    })
    if len(history["runs"]) > 100:
        history["runs"] = history["runs"][-100:]
    save_json(HISTORY_FILE, history)

    # --- Отчёт в Telegram ---
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if bot_token and chat_id:
        def fmt_delta(v):
            if v is None:
                return ""
            if v > 0:
                return f" <b>+{v}</b> ⬆️"
            if v < 0:
                return f" <b>{v}</b> ⬇️"
            return " (=)"

        msg = "🧪 <b>ARGUS BENCHMARK</b>\n\n"
        msg += f"🧠 Модель: <code>{model_path.split('/')[-1]}</code>\n"
        msg += f"📚 Чанков в индексе: {len(chunks)}\n\n"

        if recall:
            msg += f"📊 <b>Recall@{TOP_K}:</b> {recall.get(f'recall_at_{TOP_K}', 0)}%{fmt_delta(delta_recall)}\n"
            msg += f"📊 Recall@1: {recall.get('recall_at_1', 0)}%\n"
            msg += f"⏱ Среднее время: {recall.get('avg_time_ms', 0)} мс\n\n"

        if golden:
            msg += f"🎯 <b>Golden:</b> {golden['passed']}/{golden['total']} ({golden['pass_rate']}%){fmt_delta(delta_golden)}\n\n"

            # Топ провалов
            fails = [r for r in golden["details"] if not r["ok"]]
            if fails:
                msg += "<b>Не найдено:</b>\n"
                for f in fails[:5]:
                    # Экранируем HTML, чтобы спецсимволы не ломали сообщение
                    safe_q = hesc(f['query'][:60])
                    msg += f"❌ {safe_q}\n"

        try:
            requests.post(
                f"https://api.telegram.org/bot{bot_token}/sendMessage",
                json={"chat_id": chat_id, "text": msg[:4000], "parse_mode": "HTML"},
                timeout=15,
            )
            print("\n📤 Отчёт отправлен в Telegram")
        except Exception as e:
            print(f"⚠️ Telegram: {e}")

    print("\n" + "=" * 50)
    print("✅ Готово")
    print("=" * 50)


if __name__ == "__main__":
    main()
