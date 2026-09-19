# ============================================================
# ARGUS — ГЕНЕРАТОР ПОСТОВ ДЛЯ КАНАЛА
# Делает черновик, шлёт владельцу на утверждение
# ============================================================

import os
import json
import random
import requests
from datetime import datetime

# ---------- Пути ----------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPT_DIR)
DATA_DIR = os.path.join(REPO_ROOT, "data")

NEWS_FILE = os.path.join(DATA_DIR, "news_sentiment.json")
PRICE_FILE = os.path.join(DATA_DIR, "price_history.json")
POST_FILE = os.path.join(DATA_DIR, "pending_post.json")

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


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
# ДАННЫЕ
# ============================================================
def get_prices():
    """Пробует вытащить последние цены BTC/ETH из price_history."""
    data = load_json(PRICE_FILE, [])
    result = {}

    if isinstance(data, dict):
        # формат {"BTCUSDT": [...], "ETHUSDT": [...]}
        for sym in ("BTCUSDT", "BTC", "btc"):
            if sym in data:
                arr = data[sym]
                if arr and isinstance(arr, list):
                    last = arr[-1]
                    if isinstance(last, dict):
                        result["btc"] = last.get("close") or last.get("c")
                    elif isinstance(last, (int, float)):
                        result["btc"] = last
                break
        for sym in ("ETHUSDT", "ETH", "eth"):
            if sym in data:
                arr = data[sym]
                if arr and isinstance(arr, list):
                    last = arr[-1]
                    if isinstance(last, dict):
                        result["eth"] = last.get("close") or last.get("c")
                    elif isinstance(last, (int, float)):
                        result["eth"] = last
                break
    elif isinstance(data, list) and data:
        # формат [{"symbol":"BTC","close":...}, ...]
        for item in data:
            if not isinstance(item, dict):
                continue
            s = (item.get("symbol") or item.get("s") or "").upper()
            price = item.get("close") or item.get("c") or item.get("price")
            if s.startswith("BTC") and "btc" not in result:
                result["btc"] = price
            if s.startswith("ETH") and "eth" not in result:
                result["eth"] = price

    return result


def get_sentiment():
    data = load_json(NEWS_FILE, {})
    return {
        "mood": data.get("mood", "🟡 НЕЙТРАЛЬНОЕ"),
        "score": data.get("avg_sentiment", 0),
        "top_bull": (data.get("top_bullish") or [{}])[:1],
        "top_bear": (data.get("top_bearish") or [{}])[:1],
        "total": data.get("total_news", 0),
    }


# ============================================================
# ШАБЛОНЫ ФИЛОСОФСКИХ ЗАМЕТОК
# ============================================================
PHILOSOPHY_THEMES = [
    {
        "author": "Фрэнсис Бэкон",
        "quote": "Знание — сила, но только тогда, когда оно превращается в действие.",
        "bridge": "Все знают про RSI. Все знают про риск-менеджмент. Но 90% теряют депозит — потому что не действуют по правилам. Правило без действия — просто текст.",
    },
    {
        "author": "Фрэнсис Бэкон",
        "quote": "Мысли господствуют над человеком, но не всегда он ими.",
        "bridge": "Рынок не двигается по вашим желаниям. Он двигается по чужим решениям. Управлять можно только собой — своей реакцией.",
    },
    {
        "author": "Фрэнсис Бэкон",
        "quote": "Кто не ищет нового, тот не находит ничего.",
        "bridge": "Алготрейдинг — это не про одну стратегию. Это про цикл: гипотеза → тест → улучшение. Каждый день — новая итерация.",
    },
    {
        "author": "Фрэнсис Бэкон",
        "quote": "Чтение делает человека знающим, беседа — находчивым, а привычка записывать — точным.",
        "bridge": "Журнал сделок — самая недооценённая вещь в трейдинге. Без него вы не трейдер, а игрок.",
    },
    {
        "author": "Фрэнсис Бэкон",
        "quote": "Мы не должны быть судьями истины, но её искателями.",
        "bridge": "Любая модель ошибается. Хороший трейдер не защищает свою идею — он ищет, где она сломается.",
    },
    {
        "author": "Фрэнсис Бэкон",
        "quote": "Величайшее зло — это когда мудрость не соединена с действием.",
        "bridge": "Стратегия без бэктеста — фантазия. Бэктест без дисциплины — самообман.",
    },
]


# ============================================================
# ГЕНЕРАТОРЫ
# ============================================================
def gen_morning():
    prices = get_prices()
    sent = get_sentiment()

    btc = prices.get("btc")
    eth = prices.get("eth")

    lines = ["☀️ <b>Утренняя сводка ARGUS</b>\n"]

    if btc:
        lines.append("📈 BTC: <b>$" + f"{btc:,.0f}" + "</b>")
    if eth:
        lines.append("📈 ETH: <b>$" + f"{eth:,.0f}" + "</b>")
    if not btc and not eth:
        lines.append("📈 Цены: обновляются")

    lines.append("")
    lines.append("📰 Настроение рынка: " + str(sent["mood"]))
    lines.append("📊 Сентимент: " + f"{sent['score']:+.3f}")
    lines.append("🗞 Новостей проанализировано: " + str(sent["total"]))

    if sent["top_bull"] and sent["top_bull"][0].get("title"):
        lines.append("")
        lines.append("🟢 Главное позитивное:")
        lines.append("• " + str(sent["top_bull"][0]["title"])[:120])

    if sent["top_bear"] and sent["top_bear"][0].get("title"):
        lines.append("")
        lines.append("🔴 Главное негативное:")
        lines.append("• " + str(sent["top_bear"][0]["title"])[:120])

    lines.append("")
    lines.append("<i>Данные — ARGUS.</i>")

    return "\n".join(lines), "morning"


def gen_philosophy():
    theme = random.choice(PHILOSOPHY_THEMES)

    text = "📖 <b>Заметка дня</b>\n\n"
    text += "<i>" + theme["author"] + ":</i>\n\n"
    text += "«" + theme["quote"] + "»\n\n"
    text += "💭 " + theme["bridge"] + "\n\n"
    text += "<i>— ARGUS</i>"

    return text, "philosophy"


def gen_insight():
    sent = get_sentiment()
    prices = get_prices()

    news_title = ""
    if sent["top_bear"] and sent["top_bear"][0].get("title"):
        news_title = sent["top_bear"][0]["title"]
    elif sent["top_bull"] and sent["top_bull"][0].get("title"):
        news_title = sent["top_bull"][0]["title"]

    if not news_title:
        return gen_philosophy()

    text = "🔥 <b>Инсайт дня</b>\n\n"
    text += "<b>" + str(news_title)[:140] + "</b>\n\n"

    btc = prices.get("btc")
    if btc:
        text += "📊 BTC сейчас: <b>$" + f"{btc:,.0f}" + "</b>\n\n"

    if sent["score"] < -0.1:
        text += "Что это значит: рынок под давлением. Волатильность растёт — следи за уровнями поддержки."
    elif sent["score"] > 0.1:
        text += "Что это значит: рынок в позитиве. Следи за объёмами — они подтверждают рост или ловушку."
    else:
        text += "Что это значит: рынок в равновесии. Хорошее время для анализа, не для агрессии."

    text += "\n\n<i>ARGUS следит за ситуацией.</i>"

    return text, "insight"


# ============================================================
# ВЫБОР ТИПА
# ============================================================
def generate_post():
    # Смотрим, что уже было — чтобы не повторяться
    history_file = os.path.join(DATA_DIR, "post_history.json")
    history = load_json(history_file, {"types": []})
    last_types = history.get("types", [])[-3:]

    # Определяем кандидатов по времени
    now_hour = datetime.utcnow().hour
    if 4 <= now_hour < 10:
        candidates = ["morning", "philosophy", "insight"]
    elif 10 <= now_hour < 17:
        candidates = ["philosophy", "insight", "morning"]
    else:
        candidates = ["insight", "philosophy", "morning"]

    # Убираем те, что были в последних 3 постах
    fresh = [c for c in candidates if c not in last_types]
    if not fresh:
        fresh = candidates

    post_type = fresh[0]

    if post_type == "morning":
        text, _ = gen_morning()
    elif post_type == "philosophy":
        text, _ = gen_philosophy()
    else:
        text, _ = gen_insight()

    # Запоминаем
    history["types"].append(post_type)
    if len(history["types"]) > 50:
        history["types"] = history["types"][-50:]
    save_json(history_file, history)

    return text, post_type


# ============================================================
# ОТПРАВКА
# ============================================================
def send_draft(text, post_type):
    if not BOT_TOKEN or not CHAT_ID:
        print("❌ Нет BOT_TOKEN / CHAT_ID")
        return False

    # Сохраняем черновик
    save_json(POST_FILE, {
        "text": text,
        "type": post_type,
        "created_at": datetime.utcnow().isoformat(),
        "status": "pending",
    })

    kb = {
        "inline_keyboard": [[
            {"text": "✅ Опубликовать", "callback_data": "post_pub:go"},
            {"text": "🔄 Перегенерировать", "callback_data": "post_pub:regen"},
            {"text": "❌ Удалить", "callback_data": "post_skip:go"},
        ]]
    }

    header = "📝 <b>Черновик поста</b> (" + post_type + ")\n\n"
    full = header + text

    try:
        r = requests.post(
            "https://api.telegram.org/bot" + BOT_TOKEN + "/sendMessage",
            json={
                "chat_id": CHAT_ID,
                "text": full[:4000],
                "parse_mode": "HTML",
                "reply_markup": kb,
                "disable_web_page_preview": True,
            },
            timeout=15,
        )
        return r.status_code == 200
    except Exception as e:
        print("❌ " + str(e))
        return False


# ============================================================
# MAIN
# ============================================================
def main():
    print("📝 ARGUS CONTENT GENERATOR")
    print("=" * 50)

    text, post_type = generate_post()

    print("Тип поста: " + post_type)
    print("Длина: " + str(len(text)) + " символов")

    ok = send_draft(text, post_type)

    if ok:
        print("✅ Черновик отправлен в Telegram")
    else:
        print("❌ Не удалось отправить")


if __name__ == "__main__":
    main()