# ============================================================
# ARGUS — ОБУЧЕНИЕ СВОЕЙ МОДЕЛИ ЭМБЕДДИНГОВ
# ============================================================

import json
from sentence_transformers import SentenceTransformer, InputExample, losses
from torch.utils.data import DataLoader

# 1. Загружаем чанки
with open("data/knowledge.json", "r", encoding="utf-8") as f:
    knowledge = json.load(f)

chunks = [c["text"] for c in knowledge["chunks"]]
print(f"Чанков: {len(chunks)}")

# 2. Обучающие пары — соседние чанки (они из одного контекста)
train_examples = []
for i in range(len(chunks) - 1):
    train_examples.append(InputExample(texts=[chunks[i], chunks[i+1]]))

print(f"Обучающих пар: {len(train_examples)}")

# 3. Базовая модель (маленькая, работает на CPU)
model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")

# 4. DataLoader
train_dataloader = DataLoader(train_examples, shuffle=True, batch_size=16)

# 5. Функция потерь
train_loss = losses.MultipleNegativesRankingLoss(model)

# 6. Обучаем
model.fit(
    train_objectives=[(train_dataloader, train_loss)],
    epochs=3,
    warmup_steps=100,
    show_progress_bar=True
)

# 7. Сохраняем
model.save("models/argus-embeddings")
print("✅ Модель сохранена")