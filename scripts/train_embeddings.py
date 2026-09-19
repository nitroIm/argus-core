# ============================================================
# ARGUS - OBuchenIE EMBEDDINGOV (v2.2)
# v2.2: ASCII-only strings, fix syntax error
# ============================================================

import json
import random
from pathlib import Path
from collections import defaultdict

from sentence_transformers import SentenceTransformer, InputExample, losses
from sentence_transformers.evaluation import EmbeddingSimilarityEvaluator
from torch.utils.data import DataLoader

# --- Paths ---
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent

DATA_DIR = REPO_ROOT / "data"
MODELS_DIR = REPO_ROOT / "models"
KNOWLEDGE_FILE = DATA_DIR / "knowledge.json"
OUTPUT_DIR = MODELS_DIR / "argus-embeddings"

MODELS_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# --- Settings ---
SEED = 42
MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
EPOCHS = 3
BATCH_SIZE = 32
VAL_RATIO = 0.15
MIN_CHUNK_LEN = 150

random.seed(SEED)

# ============================================================
# 1. LOAD CHUNKS
# ============================================================
print("Loading:", KNOWLEDGE_FILE)
with open(KNOWLEDGE_FILE, "r", encoding="utf-8") as f:
    knowledge = json.load(f)

chunks = knowledge.get("chunks", [])
print("Total chunks:", len(chunks))

if len(chunks) < 10:
    raise RuntimeError("Too few chunks (" + str(len(chunks)) + "). Run ingest first.")

# Normalize + dedup
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
        "id": c.get("id", "chunk_" + str(len(unique_chunks))),
        "source": c.get("source", c.get("book", "unknown")),
        "text": text,
    })

print("After cleanup:", len(unique_chunks), "chunks")

# ============================================================
# 2. GROUP BY SOURCE (book)
# ============================================================
groups = defaultdict(list)
for c in unique_chunks:
    groups[c["source"]].append(c)

sources = list(groups.keys())
print("Books/sources:", len(sources))

# ============================================================
# 3. TRAIN/VAL SPLIT BY BOOKS (no leaks)
# ============================================================
random.shuffle(sources)
n_val = max(1, int(len(sources) * VAL_RATIO))
n_val = min(n_val, len(sources) - 1) if len(sources) > 1 else 0

val_sources = set(sources[:n_val])
train_sources = set(sources[n_val:])

print("Train books:", len(train_sources), "Val books:", len(val_sources))

# Build pairs: adjacent chunks within one book
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

print("Train pairs:", len(train_pairs), "Val pairs:", len(val_pairs))

# Fallback if few val pairs
if len(val_pairs) < 5 and train_pairs:
    print("Warning: few val pairs, adding random from train")
    extra = random.sample(train_pairs, min(20, len(train_pairs)))
    val_pairs.extend(extra)

# ============================================================
# 4. PREPARE DATA
# ============================================================
train_examples = [InputExample(texts=[a, b]) for a, b in train_pairs]

# For validation: positives + negatives
val_sentences1, val_sentences2, val_scores = [], [], []
all_val_texts = [c["text"] for c in val_chunks] or [c["text"] for c in unique_chunks]

for a, b in val_pairs:
    val_sentences1.append(a)
    val_sentences2.append(b)
    val_scores.append(1.0)
    
    # Negative: random chunk not equal to a and b
    for _ in range(20):
        neg = random.choice(all_val_texts)
        if neg != a and neg != b:
            val_sentences1.append(a)
            val_sentences2.append(neg)
            val_scores.append(0.0)
            break

evaluator = EmbeddingSimilarityEvaluator(
    sentences1=val_sentences1,
    sentences2=val_sentences2,
    scores=val_scores,
    name="argus-val",
)

# ============================================================
# 5. MODEL AND TRAINING
# ============================================================
print("Loading model:", MODEL_NAME)
model = SentenceTransformer(MODEL_NAME)

train_dataloader = DataLoader(train_examples, shuffle=True, batch_size=BATCH_SIZE, drop_last=False)
train_loss = losses.MultipleNegativesRankingLoss(model)

total_steps = len(train_dataloader) * EPOCHS
warmup = max(0, int(total_steps * 0.1))

print("Epochs:", EPOCHS, "Batch:", BATCH_SIZE, "Steps:", total_steps, "Warmup:", warmup)

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
# 6. SAVE
# ============================================================
model.save(str(OUTPUT_DIR))

info = {
    "model": MODEL_NAME,
    "chunks": len(unique_chunks),
    "train_pairs": len(train_pairs),
    "val_pairs": len(val_pairs),
    "epochs": EPOCHS,
    "batch_size": BATCH_SIZE,
}
with open(OUTPUT_DIR / "training_info.json", "w", encoding="utf-8") as f:
    json.dump(info, f, indent=2)

print("Model saved:", OUTPUT_DIR)
print("training_info.json: created")
