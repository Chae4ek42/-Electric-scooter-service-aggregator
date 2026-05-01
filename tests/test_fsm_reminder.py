from __future__ import annotations

import os
import sys
import time
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from client_bot.services.fsm_reminder import FSMActivityMiddleware, _activity, _reminded


class FakeState:
    def __init__(self, initial_state: str | None) -> None:
        self._state = initial_state

    async def get_state(self) -> str | None:
        return self._state

    async def clear(self) -> None:
        self._state = None

    async def set_state(self, value: str | None) -> None:
        self._state = value


class FakeMessage:
    def __init__(self, user_id: int) -> None:
        self.from_user = types.SimpleNamespace(id=user_id)


@pytest.mark.asyncio
async def test_fsm_activity_cleanup_after_state_clear() -> None:
    _activity.clear()
    _reminded.clear()

    user_id = 1001
    state = FakeState("OrderFSM:city_search")
    event = FakeMessage(user_id)
    middleware = FSMActivityMiddleware("client", form_state_prefixes=["OrderFSM:"])

    async def _handler(_event, _data):
        await state.clear()
        return "done"

    result = await middleware(_handler, event, {"state": state})

    assert result == "done"
    assert ("client", user_id) not in _activity
    assert ("client", user_id) not in _reminded


@pytest.mark.asyncio
async def test_fsm_activity_kept_for_active_form_state() -> None:
    _activity.clear()
    _reminded.clear()

    user_id = 1002
    state = FakeState("OrderFSM:city_search")
    event = FakeMessage(user_id)
    middleware = FSMActivityMiddleware("client", form_state_prefixes=["OrderFSM:"])

    async def _handler(_event, _data):
        await state.set_state("OrderFSM:service_type")
        return None

    await middleware(_handler, event, {"state": state})

    key = ("client", user_id)
    assert key in _activity
    assert _activity[key] <= time.monotonic()
    assert key not in _reminded
