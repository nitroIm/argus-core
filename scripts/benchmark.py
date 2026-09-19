# ============================================================
# ARGUS — BENCHMARK
# Проверка качества поиска: авто-recall + golden-вопросы
# ============================================================

import os
import sys
import json
import time
import random
import requests
from datetime import datetime

import faiss
import numpy as np
from sentence_transformers import SentenceTransformer

# ============================================================
# ПУТИ
# ============================================================
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPT_DIR)
DATA_DIR = os.path.join(REPO_ROOT, "data")

INDEX_FILE = os.path.join(DATA_DIR, "faiss.index")
CHUNKS_FILE = os.path.join(DATA_DIR, "chunks_for_index.json")
RESULTS_FILE = os.path.join(DATA_DIR, "benchmark_results.json")
HISTORY_FILE = os.path.join(DATA_DIR, "benchmark_history.json")

MODEL_NAME = "intfloat/multilingual-e5-small"

# ============================================================
# КОНФИГ
# ============================================================
AUTO_SAMPLE_SIZE = 50     # сколько случайных чанков проверить
TOP_K = 5                 # сколько результатов возвращает поиск
RECALL_THRESHOLD = 1      # считаем «нашлось» если в топ-K есть нужный чанк

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

    # --- Криптовалюты / ML (arxiv-статьи) ---
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
# ЗАГРУЗКА
# ============================================================
def load_json(path, default=None):
    if not os.path.exists(path):
        return default if default is not None else {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ============================================================
# ТЕСТ A — RECALL (авто)
# ============================================================
def test_recall(model, index, chunks):
    """
    Берём N случайных чанков. Используем их начало как «запрос».
    Проверяем: найдёт ли индекс этот же чанк в топ-K?
    """
    print("\n" + "=" * 50)
    print("📊 ТЕСТ A — RECALL (авто)")
    print("=" * 50)

    if len(chunks) < AUTO_SAMPLE_SIZE:
        sample_size = len(chunks)
    else:
        sample_size = AUTO_SAMPLE_SIZE

    random.seed(42)  # стабильная выборка
    sample_indices = random.sample(range(len(chunks)), sample_size)

    hits_at_1 = 0
    hits_at_k = 0
    total_time = 0

    for orig_idx in sample_indices:
        chunk = chunks[orig_idx]
        # Первые 200 символов как запрос
        query = chunk[:200].strip()
        if len(query) < 30:
            continue

        t0 = time.time()
        vec = model.encode([query], normalize_embeddings=True).astype("float32")
        distances, indices = index.search(vec, TOP_K)
        total_time += (time.time() - t0)

        found = list(indices[0])
        if orig_idx == found[0]:
            hits_at_1 += 1
        if orig_idx in found:
            hits_at_k += 1

    avg_time_ms = int((total_time / sample_size) * 1000) if sample_size else 0
    recall_1 = round(hits_at_1 / sample_size * 100, 1) if sample_size else 0
    recall_k = round(hits_at_k / sample_size * 100, 1) if sample_size else 0

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
def test_golden(model, index, chunks):
    """
    Прогоняем вручную заданные вопросы.
    Считаем: сколько ожидаемых ключевых слов попало в топ-K фрагментов.
    """
    print("\n" + "=" * 50)
    print("🎯 ТЕСТ B — GOLDEN QUESTIONS")
    print("=" * 50)

    results = []
    passed = 0

    for item in GOLDEN_QUESTIONS:
        query = item["q"]
        keywords = [k.lower() for k in item["keywords"]]

        vec = model.encode([query], normalize_embeddings=True).astype("float32")
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

        best_score = float(distances[0][0]) if len(distances[0]) else 0
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

    if not os.path.exists(INDEX_FILE):
        print(f"❌ Нет {INDEX_FILE}")
        exit(1)
    if not os.path.exists(CHUNKS_FILE):
        print(f"❌ Нет {CHUNKS_FILE}")
        exit(1)

    print("📦 Загружаю модель...")
    model = SentenceTransformer(MODEL_NAME)

    print("📦 Загружаю индекс...")
    index = faiss.read_index(INDEX_FILE)

    with open(CHUNKS_FILE, "r", encoding="utf-8") as f:
        chunks = json.load(f)

    print(f"✅ Векторов: {index.ntotal}, чанков: {len(chunks)}")

    # --- Тесты ---
    recall = test_recall(model, index, chunks)
    golden = test_golden(model, index, chunks)

    # --- Предыдущий результат для сравнения ---
    prev = load_json(RESULTS_FILE, {})
    prev_recall = prev.get("recall", {}).get(f"recall_at_{TOP_K}")
    prev_golden = prev.get("golden", {}).get("pass_rate")

    delta_recall = None
    delta_golden = None
    if prev_recall is not None:
        delta_recall = round(recall[f"recall_at_{TOP_K}"] - prev_recall, 1)
    if prev_golden is not None:
        delta_golden = round(golden["pass_rate"] - prev_golden, 1)

    # --- Итог ---
    result = {
        "generated_at": datetime.utcnow().isoformat(),
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
        "chunks": len(chunks),
        "recall_at_k": recall[f"recall_at_{TOP_K}"],
        "golden_pass_rate": golden["pass_rate"],
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
        msg += f"📚 Чанков в индексе: {len(chunks)}\n\n"

        msg += f"📊 <b>Recall@{TOP_K}:</b> {recall[f'recall_at_{TOP_K}']}%{fmt_delta(delta_recall)}\n"
        msg += f"📊 Recall@1: {recall['recall_at_1']}%\n"
        msg += f"⏱ Среднее время: {recall['avg_time_ms']} мс\n\n"

        msg += f"🎯 <b>Golden:</b> {golden['passed']}/{golden['total']} ({golden['pass_rate']}%){fmt_delta(delta_golden)}\n\n"

        # Топ провалов
        fails = [r for r in golden["details"] if not r["ok"]]
        if fails:
            msg += "<b>Не найдено:</b>\n"
            for f in fails[:5]:
                msg += f"❌ {f['query'][:60]}\n"

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