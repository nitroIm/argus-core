# ============================================================
# ARGUS — ОБУЧЕНИЕ ЭМБЕДДИНГОВ (v2.1)
# v2.1: фикс параметра evaluator (scores вместо similarities)
# ============================================================

import json
import random
from pathlib import Path
from collections import defaultdict

from sentence_transformers import SentenceTransformer, InputExample, losses
from sentence_transformers.evaluation import EmbeddingSimilarityEvaluator
from torch.utils.data import DataLoader

# --- Пути от корня репо ---
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent

DATA_DIR = REPO_ROOT / "data"
MODELS_DIR = REPO_ROOT / "models"
KNOWLEDGE_FILE = DATA_DIR / "knowledge.json"
OUTPUT_DIR = MODELS_DIR / "argus-embeddings"

MODELS_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# --- Настройки ---
SEED = 42
MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
EPOCHS = 3
BATCH_SIZE = 32
VAL_RATIO = 0.15
MIN_CHUNK_LEN = 150

random.seed(SEED)

# ============================================================
# 1. ЗАГРУЗКА ЧАНКОВ
# ============================================================
print(f"Загрузка: {KNOWLEDGE_FILE}")
with open(KNOWLEDGE_FILE, "r", encoding="utf-8") as f:
    knowledge = json.load(f)

chunks = knowledge.get("chunks", [])
print(f"Всего чанков: {len(chunks)}")

if len(chunks) < 10:
    raise RuntimeError(f"Слишком мало чанков ({len(chunks)}). Сначала запусти ingest.")

# Нормализация + дедуп
seen_hashes = set()
unique_chunks = []
for c in chunks:
    text = " ".join(c.get("text", "").split())
    if len(text) < MIN_CHUNK_LEN:
        continue
    h = hash(text)
    if h in seen_hashes:
        continue
    seen_hashes.add(h)
    unique_chunks.append({
        "id": c.get("id", f"chunk_{len(unique_chunks)}"),
        "source": c.get("source", c.get("book", "unknown")),
        "text": text,
    })

print(f"После очистки: {len(unique_chunks)} чанков")

# ============================================================
# 2. ГРУППИРОВКА ПО ИСТОЧНИКУ (книге)
# ============================================================
groups = defaultdict(list)
for c in unique_chunks:
    groups[c["source"]].append(c)

sources = list(groups.keys())
print(f"Книг/источников: {len(sources)}")

# ============================================================
# 3. TRAIN/VAL SPLIT ПО КНИГАМ (без утечек)
# ============================================================
random.shuffle(sources)
n_val = max(1, int(len(sources) * VAL_RATIO))
n_val = min(n_val, len(sources) - 1) if len(sources) > 1 else 0

val_sources = set(sources[:n_val])
train_sources = set(sources[n_val:])

print(f"Train книг: {len(train_sources)}, Val книг: {len(val_sources)}")

# Строим пары: соседние чанки внутри одной книги
def build_pairs(chunk_list):
    pairs = []
    for i in range(len(chunk_list) - 1):
        a = chunk_list[i]["text"]
        b = chunk_list[i + 1]["text"]
        if a != b:
            pairs.append((a, b))
    return pairs

train_chunks = [c for c in unique_chunks if c["source"] in train_sources]
val_chunks = [c for c in unique_chunks if c["source"] in val_sources]

train_pairs = build_pairs(train_chunks)
val_pairs = build_pairs(val_chunks)

print(f"Train пар: {len(train_pairs)}, Val пар: {len(val_pairs)}")

# Если val пар мало — берём случайные из train (fallback)
if len(val_pairs) < 5 and train_pairs:
    print("⚠️ Мало val пар, добавляю случайные из train")
    extra = random.sample(train_pairs, min(20, len(train_pairs)))
    val_pairs.extend(extra)

# ============================================================
# 4. ПОДГОТОВКА ДАННЫХ
# ============================================================
train_examples = [InputExample(texts=[a, b]) for a, b in train_pairs]

# Для валидации: позитивы + негативы
val_sentences1, val_sentences2, val_scores = [], [], []
all_val_texts = [c["text"] for c in val_chunks] or [c["text"] for c in unique_chunks]

for a, b in val_pairs:
    val_sentences1.append(a)
    val_sentences2.append(b)
    val_scores.append(1.0)
    
    # Негатив: случайный чанк, не равный a и b
    for _ in range(20):
        neg = random.choice(all_val_texts)
        if neg != a and neg != b:
            val_sentences1.append(a)
            val_sentences2.append(neg)
            val_scores.append(0.0)
            break

# ВАЖНО: параметр называется 'scores', а не 'similarities'
evaluator = EmbeddingSimilarityEvaluator(
    sentences1=val_sentences1,
    sentences2=val_sentences2,
    scores=val_scores,
    name="argus-val",
)===========
model.save(str(OUTPUT_DIR))

info =

# ============================================================
# 5. МОДЕЛЬ И ОБУЧЕНИЕ
# ============================================================
print(f"Загрузка модели: {MODEL_NAME}")
model = SentenceTransformer(MODEL_NAME)

train_dataloader = DataLoader(train_examples, shuffle=True, batch_size=BATCH_SIZE, drop_last=False)
train_loss = losses.MultipleNegativesRankingLoss(model)

total_steps = len(train_dataloader) * EPOCHS
warmup = max(0, int(total_steps * 0.1))

print(f"Epochs: {EPOCHS}, Batch: {BATCH_SIZE}, Steps: {total_steps}, Warmup: {warmup}")

model.fit(
    train_objectives=[(train_dataloader, train_loss)],
    epochs=EPOCHS,
    warmup_steps=warmup,
    optimizer_params={"lr": 2e-5},
    show_progress_bar=True,
    evaluator=evaluator,
    evaluation_steps=max(1, len(train_dataloader)),
    output_path=str(OUTPUT_DIR),
    save_best_model=True,
)

# ============================================================
# 6. СОХРАНЕНИЕ
# ============================================================
model.save(str(OUTPUT_DIR))

info = {
    "model": MODEL_NAME,
    " {
    "model": MODEL_NAME,
    "chunks": len(unique_chunks),
    "train_pairs":chunks": len(unique_chunks),
    "train_pairs": len(train_pairs),
    "val_pairs": len(val len(train_pairs),
    "val_pairs": len(val_pairs),
    "epochs": EPOCHS,
_pairs),
    "epochs": EPOCHS,
    "batch_size": BATCH_SIZE,
}
with    "batch_size": BATCH_SIZE,
}
with open(OUTPUT_DIR / "training_info.json", "w open(OUTPUT_DIR / "training_info.json", "w", encoding="utf-8") as f:
   ", encoding="utf-8") as f:
    json.dump(info, f, indent=2)

print json.dump(info, f, indent=2)

print(f"✅ Модель сохранена: {OUTPUT_DIR}")
(f"✅ Модель сохранена: {OUTPUT_DIR}")
print(f"   training_info.json: создан")
```print(f"   training_info.json: создан")
