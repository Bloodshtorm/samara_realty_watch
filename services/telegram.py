from __future__ import annotations

import html

import httpx

from app.config import Settings


async def send_telegram(settings: Settings, text: str) -> tuple[bool, str | None]:
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        return False, "Telegram token/chat id are not configured"
    safe_text = html.escape(text)
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(
            url,
            json={
                "chat_id": settings.telegram_chat_id,
                "text": safe_text,
                "parse_mode": "HTML",
                "disable_web_page_preview": False,
            },
        )
    if response.is_success:
        return True, None
    return False, response.text[:1000]


def format_error_message(source: str, error: str) -> str:
    return f"Ошибка сборщика {source}: {error}\nПроверьте авторизацию, CAPTCHA или HTML-разметку."
