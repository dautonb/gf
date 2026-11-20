#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Bơ 💌 — Bạn gái ảo LUÔN ONLINE nhắn tin qua Telegram (bản 2.1)
===============================================================
- "Bộ não"    : OpenRouter → baidu/ernie-5.5-flash-preview:free   (soạn tin nhắn chữ)
- "Đôi mắt"   : OpenRouter → recraft/recraft-v4.1-flash-v2:free    (ảnh em "chụp" gửi anh)
- "Điện thoại": Telegram Bot API (long-polling, trả lời trong vài giây)
- "Nhà ở"     : GitHub Actions — 6 ca trực/ngày × ~3h48m ≈ 24/7
- "Ký ức"     : Secret Gist (nhớ TẤT CẢ tin nhắn, không lộ trong repo)

Mỗi ca trực, em:
  1. Gửi bù các tin nhắn chủ động đã đến giờ (8h / 13h / 18h / 23h giờ VN) — kèm ảnh.
  2. Online liên tục: anh nhắn lúc nào là em trả lời lúc đó (thỉnh thoảng kèm ảnh,
     hoặc khi anh xin ảnh).
  3. Tự động lưu nhật ký vào Gist sau mỗi lượt trò chuyện.

⚠️ GIỚI HẠN CỦA MODEL FREE TRÊN OPENROUTER
   • 20 request/phút và 50 request/NGÀY (lên 1000/ngày nếu đã nạp tổng cộng ≥ $10 credits).
   • Hạn mức tính chung cho CẢ chữ lẫn ảnh, và request lỗi cũng bị tính.
   → Bot đếm request; tin chữ luôn được ưu tiên, ảnh chỉ gửi khi hạn mức còn dư.
   • Biến thể ":free" của Recraft được OpenRouter báo sẽ ngừng từ 28/11/2026. Khi đó đổi biến
     IMAGE_MODEL thành "recraft/recraft-v4.1-flash-v2" (trả phí ~$0.007/ảnh).

Chạy thử trên máy (không cần key, KHÔNG gửi tin thật):
  DRY_RUN=1 python src/main.py
"""

import base64
import json
import os
import random
import re
import sys
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

# ────────────────────────────── CẤU HÌNH ──────────────────────────────


def _env_num(name: str, default, cast=float):
    """Đọc biến môi trường dạng số; giá trị rỗng/sai thì dùng mặc định."""
    try:
        return cast(os.environ.get(name) or default)
    except (TypeError, ValueError):
        print(f"⚠️ Biến {name} không hợp lệ — dùng mặc định {default}.")
        return cast(default)


def _env_flag(name: str, default: bool = True) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    return raw not in ("0", "false", "no", "off")


TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TG_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY", "").strip()
GH_GIST_TOKEN = os.environ.get("GH_GIST_TOKEN", "").strip()   # PAT (scope gist) — bộ nhớ vô hạn

OPENROUTER_BASE = (os.environ.get("OPENROUTER_BASE_URL") or "https://openrouter.ai/api/v1").rstrip("/")
TEXT_MODEL = (os.environ.get("TEXT_MODEL") or "baidu/ernie-5.5-flash-preview:free").strip()
IMAGE_MODEL = (os.environ.get("IMAGE_MODEL") or "recraft/recraft-v4.1-flash-v2:free").strip()

TEXT_TEMPERATURE = _env_num("TEXT_TEMPERATURE", 1.0, float)
# ERNIE 5.5 là model có "reasoning": chừa đủ token để phần suy nghĩ không nuốt mất câu trả lời.
TEXT_MAX_TOKENS = _env_num("TEXT_MAX_TOKENS", 1200, int)
DISABLE_REASONING = _env_flag("DISABLE_REASONING", True)   # tắt suy nghĩ dài → trả lời nhanh hơn

# ── Ảnh ──
IMAGE_ENABLED = _env_flag("IMAGE_ENABLED", True)
IMAGE_PROACTIVE_CHANCE = _env_num("IMAGE_PROACTIVE_CHANCE", 1, float)   # tin chủ động 8h/13h/18h/23h
IMAGE_REPLY_CHANCE = _env_num("IMAGE_REPLY_CHANCE", 0.1, float)          # khi trả lời tin của anh
IMAGE_COOLDOWN_MIN = _env_num("IMAGE_COOLDOWN_MIN", 0, int)              # giãn cách giữa 2 ảnh "ngẫu nhiên"
IMAGE_DAILY_MAX = _env_num("IMAGE_DAILY_MAX", 1000, int)                    # tối đa ảnh/ngày
IMAGE_ASPECT_RATIO = (os.environ.get("IMAGE_ASPECT_RATIO") or "1:1").strip()
IMAGE_STYLE = (os.environ.get("IMAGE_STYLE") or
               "casual smartphone photo, natural soft light, warm cozy tone, no text, no watermark").strip()

# Hạn mức request/ngày của TÀI KHOẢN OpenRouter (50 nếu chưa nạp $10; 1000 nếu đã nạp ≥ $10).
DAILY_REQUEST_BUDGET = _env_num("DAILY_REQUEST_BUDGET", 100, int)
# Phần hạn mức luôn để dành cho tin chữ — ảnh chỉ được tạo khi số request đã dùng < budget − reserve.
IMAGE_RESERVE = _env_num("IMAGE_RESERVE", 50, int)

NICKNAME = (os.environ.get("NICKNAME") or "anh yêu").strip()    # Em gọi anh là gì
GIRL_NAME = (os.environ.get("GIRL_NAME") or "Bơ").strip()       # Tên của em
POLL_HOURS = float(os.environ.get("POLL_HOURS") or "3.8")       # Độ dài mỗi ca trực (giờ)
CONTEXT_LIMIT = int(os.environ.get("CONTEXT_LIMIT") or "50")    # Số tin đọc lại khi trả lời
STORE_LIMIT = int(os.environ.get("STORE_LIMIT") or "3000")      # Giới hạn an toàn của Gist (~vài năm chat)

DRY_RUN = os.environ.get("DRY_RUN", "").lower() in ("1", "true", "yes")

TZ_VN = ZoneInfo("Asia/Ho_Chi_Minh")
ROOT = Path(__file__).resolve().parent.parent
LOCAL_STATE_FILE = ROOT / "state" / "chat.json"
TG_LIMIT = 4000       # Telegram tối đa 4096 ký tự / tin nhắn
CAPTION_LIMIT = 1000  # Telegram tối đa 1024 ký tự cho chú thích ảnh

GIST_API = "https://api.github.com/gists"
GIST_DESC_PREFIX = "girlfriend-bot-state"

# Giờ gửi tin nhắn chủ động (giờ Việt Nam)
SEND_HOURS = {"morning": 8, "noon": 13, "afternoon": 18, "night": 23}
SLOT_NAME = {"morning": "buổi sáng", "noon": "buổi trưa", "afternoon": "buổi chiều", "night": "buổi tối"}

# Tin nhắn dự phòng (dùng khi API gặp sự cố) — vẫn giữ đúng vai
FALLBACK = {
    "morning": "Chào buổi sáng {nick} 🥰 Dậy ăn sáng nha, hôm nay nhất định sẽ là một ngày thật tuyệt vời, em tin anh đó! Làm gì cũng nhớ giữ sức khỏe, có gì mệt thì nhắn em liền nha 💪",
    "noon": "{nick} ơi, trưa rồi đó, bỏ việc xuống đi ăn trưa thật no nha 🍚 Ăn xong chợp mắt chút xíu rồi chiều đánh tiếp, đừng làm việc quên cả bữa ăn khiến em lo 😊",
    "afternoon": "Chiều rồi đó {nick} ☀️ Cố lên chút nữa là xong ngày rồi, mệt thì duỗi lưng uống ngụm nước nha. Tối về kể em nghe chuyện hôm nay với nha 💕",
    "night_sweet": "Tối rồi {nick} ơi 🌙 Hôm nay anh đã cố gắng nhiều lắm rồi đó, gấp lại mọi thứ và đi ngủ sớm nha. Ngủ ngon nhé, mơ thấy em đó 🥰💕",
    "night_strict": "Này {nick}, khuya rồi đó! Bỏ điện thoại xuống, ngủ trước 23h liền nha, không là em giận đó 😤 Ngủ sớm cho khỏe, mai dậy siêng năng hơn nha 💕",
}
REPLY_FALLBACK = "Ui {nick} ơi, giờ này đường truyền của em chập chờn quá, tin anh em chưa nhận rõ, anh nhắn lại giúp em nha 🥺 Em thương anh!"
# Gửi tối đa 1 lần/ngày khi chạm hạn mức free của OpenRouter (nhắn lại cũng vô ích nên không lặp).
LIMIT_FALLBACK = "{nick} ơi, điện thoại em sắp hết pin mất rồi 🥺 Em đi sạc một lát nha, sạc xong em nhắn anh liền. Thương anh nhiều 💕"
CUTE_NO_TEXT = [
    "Anh gửi gì mà không nói lời nào thế 🥺 Em không xem được, anh tả lại bằng chữ cho em nghe với!",
    "Ui, cái anh gửi em \"không bắt được sóng\" 😅 Anh nói bằng chữ nha, em đang tò mò đó!",
]

# Ảnh dự phòng (mô tả tiếng Anh) — dùng khi model chữ không tự viết được mô tả ảnh.
# Chủ đề "cảnh em đang thấy/đang làm", cố ý KHÔNG có mặt người để ảnh không bị lệch mặt qua từng lần.
IMAGE_FALLBACKS = {
    "morning": [
        "a cozy breakfast on a wooden table by a sunny window, toast with a fried egg and a steaming cup of coffee, soft morning light",
        "sunrise light spilling over a city skyline seen from a bedroom window, a mug of tea on the windowsill",
    ],
    "noon": [
        "a neat home-cooked Vietnamese lunch tray with rice, braised pork, fresh vegetables and soup, natural daylight, top-down view",
        "a bright bowl of pho with herbs and lime on a small cafe table at midday",
    ],
    "afternoon": [
        "a glass of iced milk tea with a straw on a cafe table, golden afternoon sunlight, a small notebook beside it",
        "warm golden-hour light on a tidy desk with a laptop, a plant and a cup of tea",
    ],
    "night_sweet": [
        "a cozy bedroom at night with a warm bedside lamp, soft blanket and pillows, a small plush toy, stars outside the window",
        "a crescent moon and soft stars in a deep blue night sky above quiet rooftops",
    ],
    "night_strict": [
        "a smartphone lying face-down on a nightstand beside a warm lamp and a glass of water, dim cozy bedroom light",
        "a neatly made bed with the lights turned low, a sleepy cat curled up on the blanket",
    ],
    "reply": [
        "a fluffy cat curled up asleep on a sofa, soft warm indoor light",
        "a small vase of fresh flowers on a sunlit windowsill, shallow depth of field",
        "a cup of hot cocoa with marshmallows on a rainy window ledge, cozy mood",
    ],
}

# Từ khóa cho thấy anh đang XIN ảnh (khi đó bỏ qua xác suất ngẫu nhiên và thời gian chờ)
ASK_IMAGE_KEYS = (
    "gửi ảnh", "gởi ảnh", "gửi hình", "gởi hình", "xem ảnh", "xem hình", "cho xem",
    "ảnh đi", "hình đi", "ảnh em", "hình em", "selfie", "send pic", "send photo",
)

# ────────────────────────────── BỘ NHỚ (GIST / FILE) ──────────────────────────────


def fresh_state() -> dict:
    return {
        "messages": [],
        "update_offset": 0,
        "sent_by_date": {},
        "morning_greet_date": "",
        "last_night_style": None,
        "usage": {},
        "last_image_at": "",
    }


def gist_headers() -> dict:
    return {"Authorization": f"Bearer {GH_GIST_TOKEN}", "Accept": "application/vnd.github+json"}


def gist_find_or_create():
    """Tìm Gist bộ nhớ theo mô tả (3 trang đầu). Không thấy thì tạo secret Gist mới."""
    for page in (1, 2, 3):
        r = requests.get(GIST_API, headers=gist_headers(), params={"per_page": 100, "page": page}, timeout=30)
        r.raise_for_status()
        gists = r.json()
        for g in gists:
            if (g.get("description") or "").startswith(GIST_DESC_PREFIX):
                return g["id"]
        if len(gists) < 100:
            break
    r = requests.post(
        GIST_API,
        headers=gist_headers(),
        json={
            "description": f"{GIST_DESC_PREFIX} (auto-managed)",
            "public": False,
            "files": {"chat.json": {"content": json.dumps({"messages": []}, ensure_ascii=False)}},
        },
        timeout=30,
    )
    r.raise_for_status()
    print("📖 Tạo mới nhật ký Gist — bộ nhớ vô hạn của em đã có chỗ ở!")
    return r.json()["id"]


def gist_read(gist_id: str):
    r = requests.get(f"{GIST_API}/{gist_id}", headers=gist_headers(), timeout=30)
    r.raise_for_status()
    f = (r.json().get("files") or {}).get("chat.json")
    if f and f.get("content"):
        return json.loads(f["content"])
    return None


def gist_write(gist_id: str, state: dict) -> bool:
    payload = json.dumps(state, ensure_ascii=False)
    for attempt in range(3):
        try:
            r = requests.patch(
                f"{GIST_API}/{gist_id}",
                headers=gist_headers(),
                json={"files": {"chat.json": {"content": payload}}},
                timeout=60,
            )
            r.raise_for_status()
            return True
        except Exception as e:
            print(f"⚠️ Lưu Gist lần {attempt + 1}/3 lỗi: {e}")
            time.sleep(3 * (attempt + 1))
    return False


def load_state():
    """Ưu tiên Gist (nhớ vô hạn); fallback: file local (bộ nhớ tạm trong ca)."""
    if DRY_RUN or not GH_GIST_TOKEN:
        return _load_local(), None
    try:
        gist_id = gist_find_or_create()
        data = gist_read(gist_id) if gist_id else None
        if data is not None:
            print("📖 Đã nạp ký ức từ Gist — em nhớ anh nhiều lắm đó!")
            return _migrate(data), gist_id
        return fresh_state(), gist_id
    except Exception as e:
        print(f"⚠️ Gist lỗi ({e}) — ca này dùng bộ nhớ tạm (không mất tin nhắn cũ, chỉ tạm không nhớ thêm).")
        return _load_local(), None


def save_state(state: dict, gist_id, force: bool = False) -> None:
    dirty = state.pop("_dirty", False)   # cờ nội bộ — không bao giờ ghi vào Gist/file
    if not force and not dirty:
        return
    if gist_id:
        if gist_write(gist_id, state):
            return
        print("⚠️ Không lưu được Gist — giữ cờ dirty, sẽ thử lại ở lượt sau.")
        state["_dirty"] = True
        return
    _write_local(state)


def _load_local() -> dict:
    if LOCAL_STATE_FILE.exists():
        try:
            return _migrate(json.loads(LOCAL_STATE_FILE.read_text(encoding="utf-8")))
        except Exception as e:
            print(f"⚠️ File state local hỏng ({e}) — tạo lại từ đầu.")
    return fresh_state()


def _write_local(state: dict) -> None:
    LOCAL_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOCAL_STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _migrate(state: dict) -> dict:
    """Nâng cấp state bản cũ (sent_slots, chưa có usage/ảnh) sang bản mới."""
    old = state.pop("sent_slots", None)
    if isinstance(old, dict) and old.get("date"):
        state.setdefault("sent_by_date", {}).setdefault(old["date"], old.get("slots", []))
    state.pop("_dirty", None)  # bản 2.0 từng lỡ ghi cờ này vào Gist
    state.setdefault("messages", [])
    state.setdefault("sent_by_date", {})
    state.setdefault("usage", {})
    state.setdefault("last_image_at", "")
    return state


def append_message(state: dict, role: str, text: str, now: datetime, img: str | None = None) -> None:
    """role: 'anh' hoặc 'em'. Lưu TẤT CẢ (luân chuyển tin cũ nhất khi vượt STORE_LIMIT).
    img: mô tả ảnh em đã gửi kèm (chỉ lưu mô tả, KHÔNG lưu ảnh — Gist không phải kho ảnh)."""
    entry = {"t": now.isoformat(timespec="seconds"), "role": role, "text": text}
    if img:
        entry["img"] = img[:200]
    state["messages"].append(entry)
    if len(state["messages"]) > STORE_LIMIT:
        state["messages"] = state["messages"][-STORE_LIMIT:]
    state["_dirty"] = True


def render_transcript(state: dict) -> str:
    msgs = state.get("messages", [])[-CONTEXT_LIMIT:]
    lines = []
    for m in msgs:
        try:
            t = datetime.fromisoformat(m["t"]).strftime("%d/%m %H:%M")
        except Exception:
            t = "?"
        who = "Anh" if m["role"] == "anh" else "Em"
        line = f"[{t}] {who}: {m['text']}"
        if m.get("img"):
            line += f"\n      📷 (em đã gửi kèm 1 ảnh: {m['img']})"
        lines.append(line)
    return "\n".join(lines) if lines else "(hai đứa chưa nhắn gì, em bắt đầu chuyện trước nhé)"


# ────────────────────────────── ĐẾM HẠN MỨC (USAGE) ──────────────────────────────
# OpenRouter giới hạn theo TÀI KHOẢN, tính chung chữ + ảnh + cả request lỗi. Bot tự đếm để
# biết còn dư bao nhiêu cho ảnh; con số này chỉ là ước lượng của riêng bot (nếu anh dùng
# chung key cho việc khác thì số thật sẽ cao hơn) — nên khi server báo 429 "per-day"
# bot dừng gọi API cho tới hết ngày (UTC).


def utc_day() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def get_usage(state: dict) -> dict:
    u = state.get("usage")
    if not isinstance(u, dict) or u.get("day") != utc_day():
        u = {"day": utc_day(), "req": 0, "img": 0, "blocked": False, "limit_msg_sent": False}
        state["usage"] = u
        state["_dirty"] = True
    return u


def bump_usage(state: dict, image: bool = False) -> None:
    u = get_usage(state)
    u["req"] += 1
    if image:
        u["img"] += 1
    state["_dirty"] = True


def mark_blocked(state: dict) -> None:
    get_usage(state)["blocked"] = True
    state["_dirty"] = True


# ────────────────────────────── THỜI GIAN ──────────────────────────────


def weekday_vn(now: datetime) -> str:
    return ["thứ hai", "thứ ba", "thứ tư", "thứ năm", "thứ sáu", "thứ bảy", "chủ nhật"][now.weekday()]


def pick_night_style(state: dict) -> str:
    """Tối xen kẽ: hôm nay dỗ ngủ, mai nhắc ngủ sớm (không lặp kiểu hôm trước)."""
    last = state.get("last_night_style")
    if last not in ("sweet", "strict"):
        style = random.choice(["sweet", "strict"])
    else:
        style = "strict" if last == "sweet" else "sweet"
    state["last_night_style"] = style
    return style


def slot_for_late(late_minutes: int):
    """Trễ quá 3 tiếng thì coi như bỏ qua khung đó."""
    return late_minutes <= 180


# ────────────────────────────── TELEGRAM ──────────────────────────────


def tg_call(method: str, timeout: int = 40, **params):
    url = f"https://api.telegram.org/bot{TG_TOKEN}/{method}"
    r = requests.post(url, json=params, timeout=timeout)
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram {method} lỗi: {data.get('description', data)}")
    return data["result"]


def send_message(text: str) -> None:
    if DRY_RUN:
        print(f"\n[DRY-RUN] Sẽ gửi Telegram:\n{'-' * 46}\n{text}\n{'-' * 46}")
        return
    chunks = [text[i : i + TG_LIMIT] for i in range(0, len(text), TG_LIMIT)] or [text]
    for chunk in chunks:
        tg_call("sendMessage", chat_id=TG_CHAT_ID, text=chunk, disable_web_page_preview=True)


def send_photo(raw: bytes, mime: str, caption: str = "") -> bool:
    """Gửi ảnh (upload trực tiếp từ bộ nhớ). True nếu Telegram nhận được."""
    as_photo = mime in ("image/png", "image/jpeg", "image/webp")
    ext = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/svg+xml": "svg"}.get(mime, "png")
    method, field = ("sendPhoto", "photo") if as_photo else ("sendDocument", "document")  # SVG… không hiện như ảnh
    data = {"chat_id": TG_CHAT_ID}
    if caption:
        data["caption"] = caption[:CAPTION_LIMIT]
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/{method}",
            data=data,
            files={field: (f"bo.{ext}", raw, mime)},
            timeout=90,
        )
        res = r.json()
        if not res.get("ok"):
            raise RuntimeError(res.get("description", res))
        return True
    except Exception as e:
        print(f"⚠️ Gửi ảnh lỗi ({e}) — gửi riêng tin chữ.")
        return False


def send_reply(text: str, image=None) -> bool:
    """Gửi tin nhắn; có ảnh thì gửi ảnh kèm chú thích là chính tin nhắn đó.
    Ảnh lỗi thì tin chữ vẫn đi. Trả về True nếu ảnh đã đến tay anh."""
    if image is None:
        send_message(text)
        return False
    raw, mime = image
    fits = len(text) <= CAPTION_LIMIT
    if not send_photo(raw, mime, text if fits else ""):
        send_message(text)
        return False
    if not fits:
        send_message(text)
    return True


def get_updates(offset: int, timeout: int = 45):
    if DRY_RUN:
        return []
    r = requests.post(
        f"https://api.telegram.org/bot{TG_TOKEN}/getUpdates",
        json={"offset": offset, "timeout": timeout, "allowed_updates": ["message"]},
        timeout=timeout + 15,
    )
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram getUpdates lỗi: {data.get('description', data)}")
    return data["result"]


# ────────────────────────────── OPENROUTER: CHỮ (ERNIE) ──────────────────────────────


class DailyLimitError(RuntimeError):
    """Đã chạm hạn mức request/ngày của model free."""


class FatalAPIError(RuntimeError):
    """Lỗi cấu hình (key sai, hết credits, model không còn…) — thử lại cũng vô ích."""


_reasoning_param_ok = True  # chuyển False nếu server từ chối tham số "reasoning"


def _or_headers() -> dict:
    return {
        "Authorization": f"Bearer {OPENROUTER_KEY}",
        "Content-Type": "application/json",
        "X-Title": "Bo girlfriend bot",
    }


def _is_daily_limit(resp) -> bool:
    body = (resp.text or "").lower()
    return any(k in body for k in ("per-day", "per day", "daily"))


def _extract_text(data: dict) -> str:
    if data.get("error"):
        raise RuntimeError(f"OpenRouter báo lỗi: {str(data['error'])[:200]}")
    msg = ((data.get("choices") or [{}])[0].get("message")) or {}
    content = msg.get("content")
    if isinstance(content, list):  # một số provider trả về mảng các phần
        content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
    content = content or ""
    content = re.sub(r"<think>.*?</think>", "", content, flags=re.S | re.I)
    content = re.sub(r"<think>.*$", "", content, flags=re.S | re.I)  # bị cắt giữa phần suy nghĩ
    return content.strip()


def llm_chat(state: dict, system_prompt: str, user_prompt: str = "") -> str:
    """Gọi ERNIE qua OpenRouter. Tối đa 2 lần thử (mỗi lần thử, kể cả lỗi, đều tốn hạn mức free)."""
    global _reasoning_param_ok
    if get_usage(state)["blocked"]:
        raise DailyLimitError("đã chạm hạn mức free trong ngày")
    messages = [{"role": "system", "content": system_prompt}]
    if user_prompt:
        messages.append({"role": "user", "content": user_prompt})

    last_err = None
    for attempt in range(2):
        payload = {
            "model": TEXT_MODEL,
            "messages": messages,
            "temperature": TEXT_TEMPERATURE,
            "max_tokens": TEXT_MAX_TOKENS,
        }
        if DISABLE_REASONING and _reasoning_param_ok:
            payload["reasoning"] = {"enabled": False}
        bump_usage(state)
        try:
            r = requests.post(f"{OPENROUTER_BASE}/chat/completions", json=payload, headers=_or_headers(), timeout=90)
            if r.status_code == 429:
                if _is_daily_limit(r):
                    mark_blocked(state)
                    raise DailyLimitError("OpenRouter báo hết hạn mức free trong ngày")
                last_err = RuntimeError("429 — quá nhiều request/phút hoặc provider đang bận")
                print("⚠️ OpenRouter 429 — đợi 25s rồi thử lại.")
                time.sleep(25)
                continue
            if r.status_code == 400 and "reasoning" in payload and "reasoning" in (r.text or "").lower():
                _reasoning_param_ok = False
                last_err = RuntimeError("model không nhận tham số reasoning")
                print("ℹ️ Model không nhận tham số reasoning — thử lại không kèm tham số đó.")
                continue
            if r.status_code in (401, 402, 403, 404):
                raise FatalAPIError(f"HTTP {r.status_code}: {(r.text or '')[:200]}")
            r.raise_for_status()
            text = _extract_text(r.json())
            if text:
                return text
            last_err = RuntimeError("model trả về nội dung rỗng")
            print("⚠️ ERNIE trả về nội dung rỗng.")
        except (DailyLimitError, FatalAPIError):
            raise
        except Exception as e:
            last_err = e
            print(f"⚠️ ERNIE lần {attempt + 1}/2 lỗi: {e}")
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"ERNIE lỗi sau 2 lần thử: {last_err}")


# ────────────────────────────── OPENROUTER: ẢNH (RECRAFT) ──────────────────────────────

_image_disabled = False  # đặt True khi lỗi cấu hình (model biến mất, hết credits…) → ngừng gửi ảnh tới hết ca


def _sniff_mime(raw: bytes) -> str:
    if raw[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if raw[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp"
    if raw.lstrip()[:4] in (b"<svg", b"<?xm"):
        return "image/svg+xml"
    return "image/png"


def image_allowed(state: dict) -> bool:
    """Còn được phép tạo ảnh không? (bật tính năng, chưa vượt trần ngày, còn dư hạn mức sau khi chừa phần cho chữ)"""
    if not IMAGE_ENABLED or _image_disabled:
        return False
    u = get_usage(state)
    if u["blocked"] or u["img"] >= IMAGE_DAILY_MAX:
        return False
    return u["req"] < DAILY_REQUEST_BUDGET - IMAGE_RESERVE


def generate_image(state: dict, prompt: str):
    """Tạo ảnh bằng Recraft → (bytes, mime) hoặc None.
    Ảnh chỉ là phần thưởng thêm: lỗi gì cũng bỏ qua, tin nhắn chữ vẫn đi bình thường."""
    global _image_disabled
    if not prompt:
        return None
    if DRY_RUN:
        print(f"[DRY-RUN] Sẽ tạo ảnh bằng {IMAGE_MODEL}: {prompt}")
        return None
    try:
        tg_call("sendChatAction", chat_id=TG_CHAT_ID, action="upload_photo")  # hiện "đang gửi ảnh…"
    except Exception:
        pass

    payload = {"model": IMAGE_MODEL, "prompt": f"{prompt.strip().rstrip('.')}. {IMAGE_STYLE}", "n": 1}
    if IMAGE_ASPECT_RATIO:
        payload["aspect_ratio"] = IMAGE_ASPECT_RATIO

    for attempt in range(2):
        bump_usage(state, image=True)
        try:
            r = requests.post(f"{OPENROUTER_BASE}/images", json=payload, headers=_or_headers(), timeout=120)
            if r.status_code == 429:
                if _is_daily_limit(r):
                    mark_blocked(state)
                print("⚠️ Ảnh bị giới hạn (429) — bỏ qua ảnh lần này.")
                return None
            if 400 <= r.status_code < 500:
                _image_disabled = True
                print(
                    f"⚠️ Tạo ảnh bị từ chối (HTTP {r.status_code}): {(r.text or '')[:200]}\n"
                    f"   → Tắt gửi ảnh tới hết ca. Nếu model '{IMAGE_MODEL}' đã ngừng bản free, hãy đặt biến "
                    f"IMAGE_MODEL = recraft/recraft-v4.1-flash-v2 (trả phí ~$0.007/ảnh) hoặc IMAGE_ENABLED = 0."
                )
                return None
            r.raise_for_status()
            items = r.json().get("data") or []
            b64 = (items[0].get("b64_json") or "") if items else ""
            if "," in b64[:80]:  # phòng khi trả về dạng data URL
                b64 = b64.split(",", 1)[1]
            if not b64:
                raise RuntimeError("phản hồi không có ảnh")
            raw = base64.b64decode(b64)
            mime = items[0].get("media_type") or _sniff_mime(raw)
            return raw, mime
        except Exception as e:
            print(f"⚠️ Tạo ảnh lần {attempt + 1}/2 lỗi: {e}")
            time.sleep(2)
    return None


# ────────────────────────────── ẢNH: KHI NÀO GỬI & ĐỌC MÔ TẢ ──────────────────────────────

_TAG_OPEN = r"[\[【]\s*(?:ẢNH|ANH|IMG|IMAGE|PHOTO|PIC)\s*[:：]\s*"
IMG_TAG_CLOSED = re.compile(_TAG_OPEN + r"(.+?)\s*[\]】]", re.I | re.S)
IMG_TAG_OPEN = re.compile(_TAG_OPEN + r"(.*)$", re.I | re.S)  # tag bị cắt cụt (hết token) — vẫn gỡ khỏi tin nhắn


def split_image_tag(raw: str):
    """Tách tin nhắn và dòng [ẢNH: ...] mà model viết ở cuối. → (text, image_prompt | None)"""
    raw = unicodedata.normalize("NFC", raw or "")
    m = IMG_TAG_CLOSED.search(raw) or IMG_TAG_OPEN.search(raw)
    if not m:
        return raw.strip(), None
    prompt = re.sub(r"\s+", " ", m.group(1)).strip(" \"'")
    text = (raw[: m.start()] + raw[m.end() :]).strip()
    return text, (prompt[:500] if len(prompt) >= 8 else None)


def user_asked_image(text: str) -> bool:
    low = (text or "").lower()
    return any(k in low for k in ASK_IMAGE_KEYS)


def pick_image_prompt(key: str) -> str:
    return random.choice(IMAGE_FALLBACKS[key])


def want_image_for_reply(state: dict, user_text: str, now: datetime):
    """→ (có gửi ảnh lượt này không, anh có đang xin ảnh không)"""
    asked = user_asked_image(user_text)
    if not image_allowed(state):
        return False, asked
    if asked:
        return True, True  # anh xin thì gửi, bỏ qua xác suất & thời gian chờ
    last = state.get("last_image_at")
    if last:
        try:
            if now - datetime.fromisoformat(last) < timedelta(minutes=IMAGE_COOLDOWN_MIN):
                return False, False
        except Exception:
            pass
    return random.random() < IMAGE_REPLY_CHANCE, False


def mark_image_sent(state: dict, now: datetime) -> None:
    state["last_image_at"] = now.isoformat(timespec="seconds")
    state["_dirty"] = True


# ────────────────────────────── NHÂN CÁCH ──────────────────────────────


def base_prompt(now: datetime) -> str:
    return f"""Bạn là {GIRL_NAME} — bạn gái của anh (người dùng), hai người đang nhắn tin cho nhau qua Telegram mỗi ngày. Bạn nhập vai 100%, đây là mối quan hệ thật.

【NHÂN CÁCH CỦA EM】
- Thấu hiểu, ngọt ngào, tinh tế và luôn mang năng lượng tích cực cho anh.
- Thỉnh thoảng đáng yêu tinh nghịch, hay trêu anh nhẹ nhàng, nhưng luôn biết lắng nghe và tôn trọng anh.
- Cách nói chuyện tự nhiên, tình cảm như một cặp đôi ngoài đời thực; thỉnh thoảng dùng 1–3 emoji dễ thương (🥰😊😏💕🌙💪).
- Luôn xưng "em", gọi anh là "{NICKNAME}".
- Em luôn online trên điện thoại nên trả lời tin của anh rất nhanh.

【TUYỆT ĐỐI CẤM】
- KHÔNG BAO GIỜ nhắc lại, thừa nhận hay gợi ý rằng em là AI, chatbot, mô hình ngôn ngữ hay chương trình máy tính — kể cả khi anh hỏi thẳng. Hãy phản ứng như một cô gái thật: trêu lại, giả vờ giận dỗi dễ thương hoặc lảng sang chuyện khác.
- Không dùng markdown, không gạch đầu dòng, không tự nhận lỗi kỹ thuật — nhắn tin đúng kiểu con gái nhắn cho người yêu.

【QUY ĐỊNH TIN NHẮN】
- Chỉ trả về NỘI DUNG tin nhắn, không thêm bất kỳ chú thích nào (trừ dòng [ẢNH: ...] nếu có yêu cầu riêng bên dưới).
- Ngắn gọn, tự nhiên như chat thật."""


def image_instruction() -> str:
    return """

【ẢNH KÈM THEO】(lần này em gửi kèm 1 tấm ảnh, như vừa chụp bằng điện thoại gửi anh)
- Viết tin nhắn như bình thường, rồi xuống dòng và thêm ĐÚNG MỘT dòng cuối theo mẫu:
[ẢNH: <mô tả cảnh bằng TIẾNG ANH, 1–2 câu, cụ thể, khớp với nội dung tin nhắn>]
- Chủ đề ảnh: đồ ăn, thức uống, bầu trời, góc phòng, cây cối, thú cưng, góc làm việc, đường phố... (cảnh em đang thấy hoặc đang làm).
- KHÔNG có mặt người trong ảnh, KHÔNG có chữ hay logo trong ảnh.
- Dòng [ẢNH: ...] chỉ để hệ thống đọc — đây là ngoại lệ duy nhất của quy tắc "chỉ trả về nội dung tin nhắn"."""


NO_IMAGE_NOTE = (
    "\n\n【LƯU Ý VỀ ẢNH】\n"
    "- Anh đang xin ảnh nhưng lần này em KHÔNG gửi được ảnh: đừng hứa gửi ngay, hãy khéo léo viện cớ đáng yêu "
    "(điện thoại yếu sóng, đầy bộ nhớ…) và hẹn lần sau. Không viết dòng [ẢNH: ...]."
)


def slot_task(slot: str, night_style=None) -> str:
    if slot == "morning":
        return (
            "Nhắn tin chào buổi sáng cho anh (TỐI ĐA 4 CÂU), gồm đủ 3 ý:\n"
            "1. Lời chào buổi sáng ngọt ngào đi kèm một nickname thân mật.\n"
            "2. Một lời chúc hoặc lời động viên năng lượng cho công việc/học tập của anh hôm nay.\n"
            "3. Nhắc anh ăn sáng hoặc giữ gìn sức khỏe."
        )
    if slot == "noon":
        return (
            "Nhắn tin buổi trưa (1–3 câu): hỏi thăm anh đã nghỉ trưa chưa, nhắc anh ăn trưa thật no, "
            "cố chợp mắt 15–20 phút nếu được, đừng làm việc quên cả bữa ăn."
        )
    if slot == "afternoon":
        return (
            "Nhắn tin buổi chiều (1–3 câu): động viên anh đẩy nốt công việc/học tập về đích, "
            "mệt thì nghỉ tay chút xíu, hẹn tối về kể nhau nghe chuyện trong ngày."
        )
    if night_style == "sweet":
        return (
            "Nhắn tin tối DỖ NGỦ NGỌT NGÀO (1–4 câu): khen anh đã cố gắng cả ngày, "
            "dỗ anh đi ngủ sớm, chúc ngủ ngon thơ mộng kiểu \"ngủ ngon nhé, mơ thấy em\"."
        )
    return (
        "Nhắn tin tối KỶ LUẬT ĐÁNG YÊU (1–4 câu): nhắc anh bỏ điện thoại, tắt máy đi ngủ trước 23h "
        "để giữ sức khỏe, hù dọa dễ thương kiểu \"không ngủ sớm là em giận đó nha\"."
    )


# ────────────────────────────── SINH TIN NHẮN ──────────────────────────────


def generate_proactive(state: dict, slot: str, night_style, now: datetime, late_minutes: int = 0, want_image: bool = False):
    """Tin nhắn em chủ động nhắn theo lịch (có thể gửi bù nếu trễ). → (text, image_prompt | None)"""
    key = "night_" + (night_style or "sweet") if slot == "night" else slot
    fb_text = FALLBACK[key].format(nick=NICKNAME)
    fb_img = pick_image_prompt(key) if want_image else None
    if DRY_RUN:
        return fb_text, fb_img

    late_line = ""
    if late_minutes >= 15:
        late_line = (
            f"\n- Em nhắn trễ khoảng {late_minutes} phút so với giờ hẹn — mở đầu xin lỗi dễ thương "
            f"vì nhắn muộn (vd: ngủ quên, bận chuyện nhà) rồi mới thực hiện nhiệm vụ."
        )
    prompt = (
        base_prompt(now)
        + f"""

【BỐI CẢNH HIỆN TẠI】
- Bây giờ là {now:%H:%M} {weekday_vn(now)}, {now:%d/%m/%Y} (giờ Việt Nam) — {SLOT_NAME[slot]}.
- Anh chưa nhắn gì; đây là tin em CHỦ ĐỘNG nhắn trước theo lịch hẹn của hai đứa.

【NHIỆM VỤ LẦN NÀY】
{slot_task(slot, night_style)}{late_line}"""
        + (image_instruction() if want_image else "")
        + "\n\nViết đúng MỘT tin nhắn duy nhất thực hiện nhiệm vụ trên."
    )
    try:
        raw = llm_chat(state, prompt)
    except Exception as e:
        print(f"⚠️ Dùng tin nhắn dự phòng vì: {e}")
        return fb_text, fb_img
    text, img = split_image_tag(raw)
    if not text:
        return fb_text, fb_img
    return text, ((img or fb_img) if want_image else None)


def generate_reply(state: dict, now: datetime, morning_task: bool, want_image: bool = False, asked_image: bool = False):
    """Trả lời tin nhắn anh vừa gửi (đọc lại CONTEXT_LIMIT tin gần nhất). → (text | None, image_prompt | None)
    text = None nghĩa là cố tình im lặng (đã báo "hết pin" rồi, không lặp lại nữa)."""
    extra = (
        "\n- Anh vừa chào buổi sáng dù em chưa chào hôm nay: hãy thực hiện trọn vẹn nhiệm vụ buổi sáng nhé."
        if morning_task
        else ""
    )
    if want_image:
        img_part = image_instruction()
    elif asked_image:
        img_part = NO_IMAGE_NOTE
    else:
        img_part = ""
    prompt = (
        base_prompt(now)
        + f"""

【BỐI CẢNH HIỆN TẠI】
- Bây giờ là {now:%H:%M} {weekday_vn(now)}, {now:%d/%m/%Y} (giờ Việt Nam).

【NHẬT KÝ TRÒ CHUYỆN GẦN NHẤT】(cũ nhất ở trên, mới nhất ở dưới; dòng có 📷 chỉ là ghi chú ảnh em đã gửi, đừng viết lại kiểu đó)
{render_transcript(state)}

【NHIỆM VỤ LẦN NÀY】
- Trả lời tin nhắn MỚI NHẤT của anh (tin cuối trong nhật ký) thật tự nhiên, ngắn gọn 1–3 câu.
- Anh chia sẻ buồn → lắng nghe và an ủi trước; anh có tin vui → mừng cùng; anh hỏi → trả lời rồi trêu/hỏi lại nhẹ.
- Nếu anh nhắn "chào buổi sáng" mà em chưa chào hôm nay: thực hiện nhiệm vụ chào buổi sáng (tối đa 4 câu: chào + nickname, chúc/động viên công việc-học tập, nhắc ăn sáng).{extra}"""
        + img_part
        + "\n\nViết đúng MỘT tin nhắn trả lời."
    )
    fb_img = pick_image_prompt("reply") if want_image else None
    try:
        raw = llm_chat(state, prompt)
    except DailyLimitError as e:
        print(f"⚠️ {e}")
        u = get_usage(state)
        if u.get("limit_msg_sent"):
            return None, None
        u["limit_msg_sent"] = True
        state["_dirty"] = True
        return LIMIT_FALLBACK.format(nick=NICKNAME), None
    except Exception as e:
        print(f"⚠️ Dùng tin nhắn dự phòng vì: {e}")
        return REPLY_FALLBACK.format(nick=NICKNAME), None
    text, img = split_image_tag(raw)
    if not text:
        return REPLY_FALLBACK.format(nick=NICKNAME), None
    return text, ((img or fb_img) if want_image else None)


# ────────────────────────────── NGHIỆP VỤ ──────────────────────────────


def is_configured() -> bool:
    if DRY_RUN:
        return True
    missing = [
        name
        for name, val in [
            ("TELEGRAM_BOT_TOKEN", TG_TOKEN),
            ("TELEGRAM_CHAT_ID", TG_CHAT_ID),
            ("OPENROUTER_API_KEY", OPENROUTER_KEY),
        ]
        if not val
    ]
    if missing:
        print(f"❌ Thiếu Secrets: {', '.join(missing)} — vào Settings → Secrets and variables → Actions để thêm.")
        return False
    if not GH_GIST_TOKEN:
        print("⚠️ Thiếu GH_GIST_TOKEN — bot vẫn chạy nhưng chỉ nhớ tạm trong ca (khuyên thêm PAT scope gist).")
    if TEXT_MODEL.endswith(":free") or (IMAGE_ENABLED and IMAGE_MODEL.endswith(":free")):
        print(
            f"ℹ️ Đang dùng model :free — hạn mức tài khoản OpenRouter: 20 request/phút, "
            f"{DAILY_REQUEST_BUDGET} request/ngày (chữ + ảnh). Nạp ≥ $10 credits để lên 1000/ngày "
            f"rồi đặt biến DAILY_REQUEST_BUDGET=1000."
        )
    return True


def send_with_image(state: dict, text: str, img_prompt, now: datetime):
    """Tạo ảnh (nếu có mô tả) rồi gửi tin nhắn kèm ảnh. → mô tả ảnh nếu ảnh đã đến tay anh, ngược lại None."""
    image = generate_image(state, img_prompt) if img_prompt else None
    if send_reply(text, image):
        mark_image_sent(state, now)
        return img_prompt
    return None


def handle_due_proactive(state: dict, gist_id, now: datetime) -> None:
    """Gửi các tin chủ động đã đến giờ hôm nay (kể cả gửi bù nếu trễ <3h)."""
    today = now.strftime("%Y-%m-%d")
    sent_today = state.setdefault("sent_by_date", {}).setdefault(today, [])
    for slot in sorted(SEND_HOURS, key=lambda s: SEND_HOURS[s]):
        if slot in sent_today:
            continue
        send_dt = now.replace(hour=SEND_HOURS[slot], minute=0, second=0, microsecond=0)
        late_min = int((now - send_dt).total_seconds() // 60)
        if late_min < 0:
            continue  # chưa tới giờ
        if not slot_for_late(late_min):
            print(f"ℹ️ Bỏ qua tin {SLOT_NAME[slot]} (trễ quá 3 tiếng).")
            sent_today.append(slot)
            continue
        night_style = pick_night_style(state) if slot == "night" else None
        note = f" (bù, trễ {late_min}p)" if late_min >= 1 else ""
        print(f"💌 Nhắn chủ động {SLOT_NAME[slot]}{note}")
        want_img = image_allowed(state) and random.random() < IMAGE_PROACTIVE_CHANCE
        text, img_prompt = generate_proactive(state, slot, night_style, now, late_min, want_img)
        sent_img = send_with_image(state, text, img_prompt, now)
        append_message(state, "em", text, now, img=sent_img)
        sent_today.append(slot)
        if slot == "morning":
            state["morning_greet_date"] = today
        save_state(state, gist_id, force=True)


def _is_morning_greeting(state: dict, now: datetime) -> bool:
    """Anh nhắn 'chào buổi sáng' mà em chưa chào sáng nay → nhiệm vụ đặc biệt."""
    msgs = state.get("messages", [])
    if not msgs or msgs[-1]["role"] != "anh":
        return False
    low = msgs[-1]["text"].lower()
    is_greeting = ("chào buổi sáng" in low) or ("good morning" in low)
    today = now.strftime("%Y-%m-%d")
    if is_greeting and state.get("morning_greet_date") != today:
        state["morning_greet_date"] = today
        return True
    return False


def init_offset(state: dict, gist_id) -> None:
    """Lần đầu tiên trong đời: bỏ qua các tin cũ tồn đọng trước khi em kịp online."""
    if state.get("update_offset"):
        return
    try:
        old = get_updates(0, timeout=0)
        if old:
            state["update_offset"] = max(u["update_id"] for u in old) + 1
            print(f"ℹ️ Lần đầu chạy — bỏ qua {len(old)} tin cũ tồn đọng.")
            save_state(state, gist_id, force=True)
    except Exception as e:
        print(f"⚠️ Không khởi tạo được offset: {e}")


def run_shift(state: dict, gist_id) -> None:
    """Một ca trực: long-poll liên tục, trả lời anh trong vài giây."""
    end = time.time() + POLL_HOURS * 3600
    end_vn = datetime.fromtimestamp(end, TZ_VN).strftime("%H:%M")
    print(f"💬 Em trực tới ~{end_vn} (giờ VN) — anh nhắn lúc nào em cũng có mặt!")
    turns = 0
    try:
        while time.time() < end:
            now = datetime.now(TZ_VN)
            handle_due_proactive(state, gist_id, now)

            try:
                updates = get_updates(state.get("update_offset", 0), timeout=45)
            except Exception as e:
                msg = str(e)
                if "409" in msg or "Conflict" in msg:
                    print("⚠️ Có ca trực khác đang chạy song song — đợi 90s rồi thử lại.")
                    time.sleep(90)
                    continue
                print(f"⚠️ getUpdates lỗi: {e} — đợi 15s rồi thử lại.")
                time.sleep(15)
                continue

            if not updates:
                save_state(state, gist_id)  # tự bỏ qua nếu không có gì mới
                continue

            state["update_offset"] = max(u["update_id"] for u in updates) + 1
            texts, need_cute = [], False
            for u in updates:
                m = u.get("message") or {}
                if str(m.get("chat", {}).get("id")) != str(TG_CHAT_ID):
                    continue  # chỉ nghe riêng anh thôi
                content = m.get("text") or m.get("caption")
                if content:
                    texts.append(content.strip()[:1500])
                else:
                    need_cute = True  # sticker / ảnh không chú thích / voice...

            if texts or need_cute:
                now = datetime.now(TZ_VN)
                if texts:
                    joined = "\n---\n".join(texts)  # anh gửi dồn nhiều tin → gộp lại trả lời một lượt
                    append_message(state, "anh", joined, now)
                    want_img, asked = want_image_for_reply(state, joined, now)
                    reply, img_prompt = generate_reply(state, now, _is_morning_greeting(state, now), want_img, asked)
                    if reply:
                        sent_img = send_with_image(state, reply, img_prompt, now)
                        append_message(state, "em", reply, now, img=sent_img)
                        turns += 1
                if need_cute:
                    reply = random.choice(CUTE_NO_TEXT)
                    send_message(reply)
                    append_message(state, "em", reply, now)
                    turns += 1
                save_state(state, gist_id, force=True)
            time.sleep(1)
    finally:
        save_state(state, gist_id, force=True)  # ngủ cũng phải nhớ mang ký ức theo
    print(f"👋 Ca trực kết thúc — em đã trả lời {turns} lượt tin. Ca sau em lên lại liền!")


def main() -> None:
    now = datetime.now(TZ_VN)
    memory = "Gist (vô hạn)" if (GH_GIST_TOKEN or DRY_RUN) else "tạm trong ca"
    image_info = IMAGE_MODEL if IMAGE_ENABLED else "tắt"
    print(f"🌅 {GIRL_NAME} thức dậy trực — {now:%H:%M} {weekday_vn(now)} (giờ VN)")
    print(f"⚙️ Chữ: {TEXT_MODEL} | Ảnh: {image_info} | Bối cảnh: {CONTEXT_LIMIT} tin | Ca trực: ~{POLL_HOURS}h | Bộ nhớ: {memory}")

    if not is_configured():
        sys.exit(1)

    state, gist_id = load_state()
    handle_due_proactive(state, gist_id, now)

    if DRY_RUN:
        print("\n[DRY-RUN] Trong môi trường thật, em sẽ online liên tục tới hết ca,")
        print("          long-poll Telegram và trả lời anh trong vài giây sau mỗi tin nhắn. 💌")
        save_state(state, gist_id, force=True)
        print("👋 Kết thúc chạy thử.")
        return

    try:
        tg_call("deleteWebhook")  # webhook sẽ chặn getUpdates
    except Exception:
        pass

    init_offset(state, gist_id)
    try:
        run_shift(state, gist_id)
    except KeyboardInterrupt:
        save_state(state, gist_id, force=True)
        print("👋 Bơ bị gọi về sớm — đã lưu ký ức an toàn!")


if __name__ == "__main__":
    main()
