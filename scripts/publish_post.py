# ============================================================
# ARGUS — ПУБЛИКАЦИЯ ПОСТА (TG + VK)
# ============================================================

import os
import json
import sys
import requests
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPT_DIR)
DATA_DIR = os.path.join(REPO_ROOT, "data")
POST_FILE = os.path.join(DATA_DIR, "pending_post.json")

VK_TOKEN = os.getenv("VK_TOKEN")
VK_GROUP_ID = os.getenv("VK_GROUP_ID")
TG_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN")
TG_CHANNEL_ID = os.getenv("TG_CHANNEL_ID")
TG_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


def load_json(path, default=None):
    if not os.path.exists(path):
        return default if default is not None else {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def strip_html(text):
    """Грубо убирает HTML-теги для VK."""
    import re
    text = re.sub(r"<br\s*/?>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    return text


def publish_tg(text):
    if not TG_BOT_TOKEN or not TG_CHANNEL_ID:
        return False, "TG not configured"
    try:
        r = requests.post(
            "https://api.telegram.org/bot" + TG_BOT_TOKEN + "/sendMessage",
            json={
                "chat_id": TG_CHANNEL_ID,
                "text": text[:4000],
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=15,
        )
        if r.status_code == 200:
            return True, "OK"
        return False, "TG " + str(r.status_code) + ": " + r.text[:200]
    except Exception as e:
        return False, "TG error: " + str(e)


def publish_vk(text):
    if not VK_TOKEN or not VK_GROUP_ID:
        return False, "VK not configured"
    try:
        clean = strip_html(text)
        r = requests.post(
            "https://api.vk.com/method/wall.post",
            data={
                "owner_id": "-" + str(VK_GROUP_ID),
                "from_group": 1,
                "message": clean[:4000],
                "access_token": VK_TOKEN,
                "v": "5.199",
            },
            timeout=20,
        )
        data = r.json()
        if "response" in data:
            return True, "post_id=" + str(data["response"].get("post_id"))
        return False, "VK error: " + str(data.get("error", {}))[:200]
    except Exception as e:
        return False, "VK error: " + str(e)


def notify_owner(text):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        return
    try:
        requests.post(
            "https://api.telegram.org/bot" + TG_BOT_TOKEN + "/sendMessage",
            json={
                "chat_id": TG_CHAT_ID,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=15,
        )
    except Exception:
        pass


def main():
    action = sys.argv[1] if len(sys.argv) > 1 else "go"

    print("📤 PUBLISH POST — action=" + action)

    draft = load_json(POST_FILE, {})
    if not draft or not draft.get("text"):
        print("❌ Черновик не найден")
        notify_owner("⚠️ Post: черновик не найден.")
        return

    if action == "skip":
        draft["status"] = "skipped"
        draft["handled_at"] = datetime.utcnow().isoformat()
        save_json(POST_FILE, draft)
        print("❌ Пост удалён")
        notify_owner("❌ Пост удалён.")
        return

    text = draft["text"]

    ok_tg, msg_tg = publish_tg(text)
    ok_vk, msg_vk = publish_vk(text)

    print("TG: " + str(ok_tg) + " — " + msg_tg)
    print("VK: " + str(ok_vk) + " — " + msg_vk)

    if ok_tg or ok_vk:
        draft["status"] = "published"
        draft["published_at"] = datetime.utcnow().isoformat()
        save_json(POST_FILE, draft)

    result = "📤 <b>Пост опубликован</b>\n\n"
    result += ("✅ TG: " if ok_tg else "❌ TG: ") + msg_tg[:80] + "\n"
    result += ("✅ VK: " if ok_vk else "❌ VK: ") + msg_vk[:80]
    notify_owner(result)


if __name__ == "__main__":
    main()