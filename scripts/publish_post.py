# ============================================================
# ARGUS — ПУБЛИКАЦИЯ ПОСТА (TG + VK) (v2)
# v2: pathlib, безопасная обрезка HTML, детальная отчетность, timezone
# ============================================================

import os
import sys
import json
import re
import html
import requests
from datetime import datetime, timezone
from pathlib import Path

# --- Пути от корня репо ---
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
DATA_DIR = REPO_ROOT / "data"
POST_FILE = DATA_DIR / "pending_post.json"

# --- Переменные окружения ---
VK_TOKEN = os.getenv("VK_TOKEN")
VK_GROUP_ID = os.getenv("VK_GROUP_ID")
TG_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN")
TG_CHANNEL_ID = os.getenv("TG_CHANNEL_ID")
TG_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


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


def strip_html_for_vk(text: str) -> str:
    """Безопасно убирает HTML-теги и декодирует сущности для VK."""
    # Заменяем переносы строк на реальные \n
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    # Удаляем все остальные теги
    text = re.sub(r"<[^>]+>", "", text)
    # Декодируем &nbsp;, &quot; и т.д.
    text = html.unescape(text)
    return text.strip()


def safe_truncate_html(text: str, max_len: int = 4000) -> str:
    """Обрезает текст, не разрывая HTML-теги в конце."""
    if len(text) <= max_len:
        return text
    
    # Обрезаем и ищем последний незакрытый тег, чтобы не сломать HTML
    truncated = text[:max_len]
    # Находим последнюю '<' и обрезаем до неё, если она близко к концу
    last_open = truncated.rfind('<')
    if last_open > max_len - 50:  # Если тег начался в последних 50 символах
        return truncated[:last_open] + "... (продолжение следует)"
    
    return truncated + "..."


def publish_tg(text: str):
    if not TG_BOT_TOKEN or not TG_CHANNEL_ID:
        return False, "TG not configured"
    
    safe_text = safe_truncate_html(text, 4000)
    
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage",
            json={
                "chat_id": TG_CHANNEL_ID,
                "text": safe_text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=15,
        )
        if r.status_code == 200:
            return True, "OK"
        return False, f"TG {r.status_code}: {r.text[:200]}"
    except Exception as e:
        return False, f"TG error: {e}"


def publish_vk(text: str):
    if not VK_TOKEN or not VK_GROUP_ID:
        return False, "VK not configured"
    
    clean_text = strip_html_for_vk(text)[:4000]  # Лимит VK API ~4096 символов
    
    try:
        r = requests.post(
            "https://api.vk.com/method/wall.post",
            data={
                "owner_id": f"-{VK_GROUP_ID}",
                "from_group": 1,
                "message": clean_text,
                "access_token": VK_TOKEN,
                "v": "5.199",
            },
            timeout=20,
        )
        data = r.json()
        if "response" in data:
            post_id = data["response"].get("post_id", "?")
            return True, f"post_id={post_id}"
        
        error_msg = data.get("error", {}).get("error_msg", "Unknown VK error")
        return False, f"VK error: {error_msg[:200]}"
    except Exception as e:
        return False, f"VK error: {e}"


def notify_owner(text: str):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage",
            json={
                "chat_id": TG_CHAT_ID,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=15,
        )
    except Exception:
        pass  # Тихо игнорируем, чтобы не ломать основной поток


def main():
    action = sys.argv[1] if len(sys.argv) > 1 else "go"
    print(f"📤 PUBLISH POST — action={action}")

    draft = load_json(POST_FILE, {})
    if not draft or not draft.get("text"):
        print("❌ Черновик не найден или пуст")
        notify_owner("⚠️ <b>Post:</b> черновик не найден.")
        sys.exit(1)

    if action == "skip":
        draft["status"] = "skipped"
        draft["handled_at"] = datetime.now(timezone.utc).isoformat()
        save_json(POST_FILE, draft)
        print("❌ Пост удалён (skipped)")
        notify_owner("❌ <b>Post:</b> черновик отклонён и удалён.")
        sys.exit(0)

    text = draft["text"]
    print("Публикация...")

    ok_tg, msg_tg = publish_tg(text)
    ok_vk, msg_vk = publish_vk(text)

    print(f"TG: {'✅' if ok_tg else '❌'} {msg_tg}")
    print(f"VK: {'✅' if ok_vk else '❌'} {msg_vk}")

    # Обновляем статус с детализацией
    draft["status"] = "published" if (ok_tg or ok_vk) else "failed"
    draft["tg_status"] = "success" if ok_tg else "failed"
    draft["vk_status"] = "success" if ok_vk else "failed"
    draft["handled_at"] = datetime.now(timezone.utc).isoformat()
    save_json(POST_FILE, draft)

    # Формируем отчет для владельца
    result_lines = ["📤 <b>Результат публикации</b>\n"]
    result_lines.append(("✅ <b>TG:</b> " if ok_tg else "❌ <b>TG:</b> ") + msg_tg[:100])
    result_lines.append(("✅ <b>VK:</b> " if ok_vk else "❌ <b>VK:</b> ") + msg_vk[:100])
    
    notify_owner("\n".join(result_lines))


if __name__ == "__main__":
    main()
