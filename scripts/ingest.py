# ============================================================
# ARGUS — ЧИТАТЕЛЬ КНИГ
# Читает PDF из books/, извлекает текст, разбивает на чанки
# ============================================================

import os
import json
from pypdf import PdfReader

# --- Пути ---
BOOKS_DIR = "books"
DATA_DIR = "data"
OUTPUT_FILE = os.path.join(DATA_DIR, "knowledge.json")

# --- Загружаем существующие знания ---
if os.path.exists(OUTPUT_FILE):
    with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
        knowledge = json.load(f)
else:
    knowledge = {
        "books": [],
        "chunks": []
    }

# --- Обработка всех PDF ---
for filename in os.listdir(BOOKS_DIR):
    if not filename.lower().endswith(".pdf"):
        continue

    # Проверяем, не обработана ли уже
    already = False
    for book in knowledge["books"]:
        if book["file"] == filename:
            already = True
            break

    if already:
        print(f"⏭ Уже обработана: {filename}")
        continue

    filepath = os.path.join(BOOKS_DIR, filename)
    print(f"📖 Обработка: {filename}")

    try:
        # --- Извлечение текста ---
        reader = PdfReader(filepath)
        full_text = ""

        for page in reader.pages:
            text = page.extract_text()
            if text:
                full_text += text + "\n"

        if len(full_text.strip()) < 100:
            print(f"   ⚠️ Мало текста — возможно, PDF-скан")
            continue

        # --- Разбивка на чанки ---
        chunk_size = 500
        overlap = 50
        chunks = []

        i = 0
        while i < len(full_text):
            chunk = full_text[i:i + chunk_size]
            if len(chunk.strip()) > 50:
                chunks.append(chunk.strip())
            i = i + chunk_size - overlap

        # --- Сохраняем ---
        for idx, chunk in enumerate(chunks):
            knowledge["chunks"].append({
                "book": filename,
                "chunk_id": idx,
                "text": chunk
            })

        knowledge["books"].append({
            "file": filename,
            "pages": len(reader.pages),
            "chunks": len(chunks)
        })

        print(f"   ✅ {len(reader.pages)} стр., {len(chunks)} чанков")

    except Exception as e:
        print(f"   ❌ Ошибка: {e}")

# --- Сохранение ---
os.makedirs(DATA_DIR, exist_ok=True)

with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
    json.dump(knowledge, f, ensure_ascii=False, indent=2)

print(f"\n🎉 Готово.")
print(f"   Книг: {len(knowledge['books'])}")
print(f"   Чанков: {len(knowledge['chunks'])}")