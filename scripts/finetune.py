# ============================================================
# ARGUS — FINETUNE v1 [EXPERIMENTAL]
# ------------------------------------------------------------
# Отдельный модуль: обучает e5-small на данных из knowledge.json.
# НЕ трогает существующие скрипты и модели.
# Результат: models/argus-embeddings-v2/ + data/model_info_v2.json
# ------------------------------------------------------------
# Использование: python scripts/finetune.py
# ============================================================

import re
import json
import random
import hashlib
from pathlib import Path
from datetime import datetime, timezone

import numpy as np
from datasets import Dataset
from sentence_transformers import (
    SentenceTransformer,
    SentenceTransformerTrainer,
    SentenceTransformerTrainingArguments,
    losses,
)

# --- Пути ---
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent

DATA_DIR = REPO_ROOT / "data"
MODELS_DIR = REPO_ROOT / "models"
KNOWLEDGE_FILE = DATA_DIR / "knowledge.json"
MODEL_INFO_V2_FILE = DATA_DIR / "model_info_v2.json"
TRAIN_DATASET_FILE = DATA_DIR / "finetune_dataset.json"

BASE_MODEL = "intfloat/multilingual-e5-small"
OUTPUT_DIR = MODELS_DIR / "argus-embeddings-v2"

# --- Настройки ---
EPOCHS = 3
BATCH_SIZE = 16
WARMUP_RATIO = 0.1
MAX_PAIRS = 2000
MIN_QUESTION_LEN = 8
MAX_QUESTION_LEN = 80
MIN_CHUNK_LEN = 200
RANDOM_SEED = 42

random.seed(RANDOM_SEED)


# --- Паттерны определений (RU) ---
DEFINITION_PATTERNS = [
    (r"^([А-ЯЁA-Z][^.!?\n]{2,60}?)\s*[—–-]\s*это\s", "Что такое {X}?"),
    (r"Под\s+([а-яёА-ЯЁ][^.!?\n]{2,60}?)\s+понимается\s", "Что такое {X}?"),
    (r"^([А-ЯЁA-Z][^.!?\n]{2,60}?)\s+называется\s", "Что такое {X}?"),
    (r"Определение\s+([а-яёА-ЯЁ][^.!?\n]{2,60}?)[:.]\s", "Что такое {X}?"),
]


def md5_text(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def extract_question(chunk_text: str):
    """Пытается извлечь вопрос из чанка по паттернам определений."""
    first = re.split(r"[.!?]", chunk_text.strip())[0]
    if len(first) > 200:
        first = first[:200]

    for pattern, template in DEFINITION_PATTERNS:
        m = re.match(pattern, first, re.IGNORECASE)
        if m:
            subject = re.sub(r"\s+", " ", m.group(1)).strip(".,;: ")
            if MIN_QUESTION_LEN <= len(subject) <= MAX_QUESTION_LEN:
                return template.format(X=subject)
    return None


def load_knowledge():
    if not KNOWLEDGE_FILE.exists():
        raise SystemExit("❌ knowledge.json не найден")
    with open(KNOWLEDGE_FILE, encoding="utf-8") as f:
        return json.load(f).get("chunks", [])


def build_pairs(chunks):
    """Создаёт пары (question, positive_chunk)."""
    print(f"📚 Чанков: {len(chunks)}")

    # Группируем по книгам
    by_book = {}
    for c in chunks:
        book = c.get("book", "unknown")
        by_book.setdefault(book, []).append(c)

    books = list(by_book.keys())
    print(f"📖 Книг: {len(books)}")

    pairs, seen = [], set()
    for c in chunks:
        text = c.get("text", "")
        if len(text) < MIN_CHUNK_LEN:
            continue

        question = extract_question(text)
        if not question:
            continue

        qh = md5_text(question)
        if qh in seen:
            continue
        seen.add(qh)

        # Hard negative — другой чанк из другой книги
        other_books = [b for b in books if b != c.get("book")]
        if not other_books:
            continue
        neg_book = random.choice(other_books)
        neg_chunk = random.choice(by_book[neg_book])

        pairs.append({
            "question": question,
            "positive": text,
            "negative": neg_chunk.get("text", ""),
        })
        if len(pairs) >= MAX_PAIRS:
            break

    print(f"✨ Собрано пар: {len(pairs)}")
    return pairs


def build_dataset(pairs):
    """Формат для SentenceTransformerTrainer."""
    return Dataset.from_dict({
        "anchor": [p["question"] for p in pairs],
        "positive": [p["positive"] for p in pairs],
    })


def train(pairs):
    print(f"\n🤖 Загрузка базовой модели: {BASE_MODEL}")
    model = SentenceTransformer(BASE_MODEL)

    dataset = build_dataset(pairs)
    print(f"🧮 Обучающих примеров: {len(dataset)}")

    train_loss = losses.MultipleNegativesRankingLoss(model)

    total_steps = (len(dataset) // BATCH_SIZE + 1) * EPOCHS
    warmup_steps = max(1, int(total_steps * WARMUP_RATIO))
    print(f"📊 Шагов: ~{total_steps}, warmup: {warmup_steps}")

    args = SentenceTransformerTrainingArguments(
        output_dir=str(OUTPUT_DIR),
        num_train_epochs=EPOCHS,
        per_device_train_batch_size=BATCH_SIZE,
        warmup_steps=warmup_steps,
        fp16=False,
        bf16=False,
        logging_steps=10,
        save_strategy="no",
        report_to="none",
    )

    trainer = SentenceTransformerTrainer(
        model=model,
        args=args,
        train_dataset=dataset,
        loss=train_loss,
    )

    print(f"\n🚀 Начинаю обучение ({EPOCHS} эпох)...")
    trainer.train()
    model.save(str(OUTPUT_DIR))
    print(f"\n💾 Модель сохранена: {OUTPUT_DIR}")


def save_info(n_pairs):
    info = {
        "model_label": "argus-finetuned-v2",
        "model_path": "models/argus-embeddings-v2",
        "base_model": BASE_MODEL,
        "dim": 384,
        "passage_prefix": "",
        "query_prefix": "",
        "is_finetuned": True,
        "train_pairs": n_pairs,
        "epochs": EPOCHS,
        "batch_size": BATCH_SIZE,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    with open(MODEL_INFO_V2_FILE, "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, indent=2)
    print(f"💾 Сохранено: {MODEL_INFO_V2_FILE}")


def main():
    print("=" * 60)
    print("🧠 ARGUS — FINETUNE v1 [EXPERIMENTAL]")
    print("=" * 60)

    chunks = load_knowledge()
    pairs = build_pairs(chunks)

    if len(pairs) < 20:
        raise SystemExit(f"❌ Слишком мало пар: {len(pairs)}. Нужно ≥ 20.")

    with open(TRAIN_DATASET_FILE, "w", encoding="utf-8") as f:
        json.dump(pairs, f, ensure_ascii=False, indent=2)
    print(f"💾 Сохранено: {TRAIN_DATASET_FILE}")

    train(pairs)
    save_info(len(pairs))

    print("\n" + "=" * 60)
    print("🎉 FINETUNE завершён!")
    print("=" * 60)
    print(f"📁 Модель: {OUTPUT_DIR}")
    print(f"📊 Пар: {len(pairs)}")
    print()
    print("⚠️ Старая модель НЕ тронута.")
    print("   Для проверки: временно поменяй MODEL_DIR в")
    print("   train_embeddings.py на argus-embeddings-v2")
    print("   и запусти benchmark.")


if __name__ == "__main__":
    main()