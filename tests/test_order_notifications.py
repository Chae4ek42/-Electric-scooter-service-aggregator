from __future__ import annotations

import asyncio

import pytest
from aiogram.enums import ParseMode


@pytest.mark.asyncio
async def test_notify_client_admins_uses_html_parse_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import aiogram
    import client_bot.handlers.order as order

    captured: dict[str, object] = {}

    class _FakeSession:
        async def close(self) -> None:
            return None

    class _FakeBot:
        def __init__(self, token: str, default=None) -> None:
            captured["token"] = token
            captured["default"] = default
            self.session = _FakeSession()

    async def _fake_notify_admins(
        bot,
        *,
        scope: str,
        event_key: str,
        text: str,
        dedupe_prefix: str,
    ) -> None:
        captured["scope"] = scope
        captured["event_key"] = event_key
        captured["text"] = text
        captured["dedupe_prefix"] = dedupe_prefix
        captured["bot"] = bot

    def _fake_create_guarded_task(coro, **kwargs):
        captured["coro"] = coro
        return None

    monkeypatch.setattr(aiogram, "Bot", _FakeBot)
    monkeypatch.setattr(order, "notify_admins", _fake_notify_admins)
    monkeypatch.setattr(order, "create_guarded_task", _fake_create_guarded_task)

    order._notify_client_admins(
        event_key="no_center",
        text="<b>test</b>",
        dedupe_suffix="123",
        order_id=1,
        user_id=42,
    )

    coro = captured.get("coro")
    assert coro is not None
    await coro

    default = captured.get("default")
    assert default is not None
    assert default.parse_mode == ParseMode.HTML
    assert captured.get("event_key") == "no_center"
