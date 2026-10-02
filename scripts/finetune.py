# ============================================================
# ARGUS — FINETUNE v1.2 [EXPERIMENTAL]
# ------------------------------------------------------------
# v1.2: CosineSimilarityLoss вместо MultipleNegativesRankingLoss.
#       Формат датасета: sentence1/sentence2/label.
#       Learning rate снижен до 2e-5, эпохи = 2.
# ------------------------------------------------------------
# v1.1: расширенные паттерны определений, поиск по всем
#       предложениям чанка, снижен MIN_QUESTION_LEN.
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
EPOCHS = 2
BATCH_SIZE = 16
LEARNING_RATE = 2e-5
WARMUP_RATIO = 0.1
MAX_PAIRS = 3000
MIN_QUESTION_LEN = 5
MAX_QUESTION_LEN = 100
MIN_CHUNK_LEN = 200
MAX_SENTENCES_TO_CHECK = 5
RANDOM_SEED = 42

random.seed(RANDOM_SEED)


# --- Расширенные паттерны определений (RU) ---
DEFINITION_PATTERNS = [
    (r"([А-ЯЁ][^.!?\n]{2,60}?)\s*[—–]\s*это\s", "Что такое {X}?"),
    (r"([А-ЯЁ][^.!?\n]{2,60}?)\s+-\s+это\s", "Что такое {X}?"),
    (r"([А-ЯЁ][^.!?\n]{2,60}?)\s+это\s+[а-яё]", "Что такое {X}?"),
    (r"[Пп]од\s+([а-яёА-ЯЁ][^.!?\n]{2,60}?)\s+понимается\s", "Что такое {X}?"),
    (r"([А-ЯЁ][^.!?\n]{2,60}?)\s+называется\s", "Что такое {X}?"),
    (r"([А-ЯЁ][^.!?\n]{2,60}?)\s+определяется\s+как\s", "Что такое {X}?"),
    (r"([А-ЯЁ][^.!?\n]{2,60}?)\s+означает\s", "Что такое {X}?"),
    (r"([А-ЯЁ][^.!?\n]{2,60}?)\s+представляет\s+собой\s", "Что такое {X}?"),
    (r"[Тт]ермин\s+[«\"']?([А-ЯЁ][^.!?\n«»\"']{2,60}?)[»\"']?\s+(?:означает|—|это)", "Что такое {X}?"),
    (r"([А-ЯЁ][^.!?\n]{2,60}?)\s*[—–]\s*это\s+не\s", "Что такое {X}?"),
]


def md5_text(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def extract_question(chunk_text: str):
    """
    Ищет определение в первых MAX_SENTENCES_TO_CHECK предложениях.
    Возвращает сгенерированный вопрос или None.
    """
    sentences = re.split(r"(?<=[.!?])\s+", chunk_text.strip())

    for sent in sentences[:MAX_SENTENCES_TO_CHECK]:
        sent = sent.strip()
        if len(sent) < 20:
            continue
        if len(sent) > 400:
            sent = sent[:400]

        for pattern, template in DEFINITION_PATTERNS:
            m = re.search(pattern, sent, re.IGNORECASE)
            if not m:
                continue

            subject = re.sub(r"\s+", " ", m.group(1)).strip(".,;: ")
            subject = subject.strip("«»\"'()[]")

            if MIN_QUESTION_LEN <= len(subject) <= MAX_QUESTION_LEN:
                first_word = subject.split()[0].lower()
                if first_word in ("это", "под", "термин", "если", "когда", "там", "так"):
                    continue
                return template.format(X=subject)

    return None


def load_knowledge():
    if not KNOWLEDGE_FILE.exists():
        raise SystemExit("❌ knowledge.json не найден")
    with open(KNOWLEDGE_FILE, encoding="utf-8") as f:
        return json.load(f).get("chunks", [])


def build_pairs(chunks):
    """Создаёт пары (question, positive, negative)."""
    print(f"📚 Чанков: {len(chunks)}")

    by_book = {}
    for c in chunks:
        book = c.get("book", "unknown")
        by_book.setdefault(book, []).append(c)

    books = list(by_book.keys())
    print(f"📖 Книг: {len(books)}")

    pairs, seen = [], set()
    no_match = 0
    for c in chunks:
        text = c.get("text", "")
        if len(text) < MIN_CHUNK_LEN:
            continue

        question = extract_question(text)
        if not question:
            no_match += 1
            continue

        qh = md5_text(question)
        if qh in seen:
            continue
        seen.add(qh)

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

    print(f"🔎 Чанков без определения: {no_match}")
    print(f"✨ Собрано уникальных пар: {len(pairs)}")
    return pairs


def build_dataset(pairs):
    """
    Формат для CosineSimilarityLoss:
    sentence1, sentence2, label (1.0 = похожи, 0.0 = не похожи).
    Каждая пара даёт ДВА примера: (q, pos, 1.0) и (q, neg, 0.0).
    """
    s1, s2, labels = [], [], []
    for p in pairs:
        # positive
        s1.append(p["question"])
        s2.append(p["positive"])
        labels.append(1.0)
        # negative
        s1.append(p["question"])
        s2.append(p["negative"])
        labels.append(0.0)
    return Dataset.from_dict({
        "sentence1": s1,
        "sentence2": s2,
        "label": labels,
    })


def train(pairs):
    print(f"\n🤖 Загрузка базовой модели: {BASE_MODEL}")
    model = SentenceTransformer(BASE_MODEL)

    dataset = build_dataset(pairs)
    print(f"🧮 Обучающих примеров: {len(dataset)} "
          f"({len(pairs)} пар × 2)")

    train_loss = losses.CosineSimilarityLoss(model)

    total_steps = (len(dataset) // BATCH_SIZE + 1) * EPOCHS
    warmup_steps = max(1, int(total_steps * WARMUP_RATIO))
    print(f"📊 Шагов: ~{total_steps}, warmup: {warmup_steps}")
    print(f"📉 Learning rate: {LEARNING_RATE}, эпох: {EPOCHS}")

    args = SentenceTransformerTrainingArguments(
        output_dir=str(OUTPUT_DIR),
        num_train_epochs=EPOCHS,
        per_device_train_batch_size=BATCH_SIZE,
        learning_rate=LEARNING_RATE,
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
        "learning_rate": LEARNING_RATE,
        "loss": "CosineSimilarityLoss",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    with open(MODEL_INFO_V2_FILE, "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, indent=2)
    print(f"💾 Сохранено: {MODEL_INFO_V2_FILE}")


def main():
    print("=" * 60)
    print("🧠 ARGUS — FINETUNE v1.2 [EXPERIMENTAL]")
    print("=" * 60)

    chunks = load_knowledge()
    pairs = build_pairs(chunks)

    if len(pairs) < 20:
        raise SystemExit(
            f"❌ Слишком мало пар: {len(pairs)}. Нужно ≥ 20.\n"
            f"   Попробуй ещё ослабить паттерны или проверить "
            f"качество knowledge.json."
        )

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


if __name__ == "__main__":
    main()