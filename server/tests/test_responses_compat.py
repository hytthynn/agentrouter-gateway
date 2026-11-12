"""Тесты трансляции Chat Completions ↔ Responses API (gpt-5.x на AgentRouter)."""

import json

import pytest

from gateway_app.gateway.responses_compat import (
    ResponsesSSETranslator,
    chat_to_responses_payload,
    is_responses_required,
    responses_to_chat_response,
)


def _make_400_error():
    """Реальная ошибка апстрима AgentRouter (§2.5, скрин пользователя)."""
    from gateway_app.providers.agentrouter.errors import classify_upstream_error

    return classify_upstream_error(
        400,
        {
            "error": {
                "message": (
                    "模型 gpt-5.6-sol 不支持 /v1/chat/completions 接口，请改用 "
                    "/v1/responses (OpenAI Responses API) 。Model gpt-5.6-sol does not "
                    "support /v1/chat/completions instead, please use /v1/responses"
                )
            }
        },
    )


def test_marker_detection():
    exc = _make_400_error()
    assert is_responses_required(exc)
    assert not is_responses_required(Exception("обычная ошибка"))


def test_chat_to_responses_payload():
    chat = {
        "model": "gpt-5.6-sol",
        "stream": True,
        "max_tokens": 1024,
        "temperature": 0.7,
        "messages": [
            {"role": "system", "content": "Ты полезный ассистент"},
            {"role": "user", "content": "привет"},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "call_1", "type": "function",
                 "function": {"name": "read_file", "arguments": "{\"path\":\"a.txt\"}"}},
            ]},
            {"role": "tool", "tool_call_id": "call_1", "content": "содержимое файла"},
        ],
        "tools": [{"type": "function", "function": {
            "name": "read_file", "description": "читает файл", "parameters": {"type": "object"}}}],
    }
    out = chat_to_responses_payload(chat)
    assert out["model"] == "gpt-5.6-sol" and out["stream"] is True
    assert out["instructions"] == "Ты полезный ассистент"
    assert out["max_output_tokens"] == 1024
    assert out["temperature"] == 0.7
    types = [i["type"] for i in out["input"]]
    assert types == ["message", "function_call", "function_call_output"]
    assert out["input"][0] == {"type": "message", "role": "user",
                               "content": [{"type": "input_text", "text": "привет"}]}
    assert out["input"][1]["call_id"] == "call_1"
    assert out["input"][1]["arguments"] == '{"path":"a.txt"}'
    assert out["input"][2]["output"] == "содержимое файла"
    assert out["tools"] == [{"type": "function", "name": "read_file",
                             "description": "читает файл", "parameters": {"type": "object"}}]


def test_responses_to_chat_response():
    resp = {
        "id": "resp_1", "object": "response", "status": "completed", "model": "gpt-5.6-sol",
        "output": [
            {"type": "reasoning", "summary": []},
            {"type": "message", "role": "assistant",
             "content": [{"type": "output_text", "text": "Привет!", "annotations": []}]},
        ],
        "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
    }
    chat = responses_to_chat_response(resp)
    assert chat["object"] == "chat.completion"
    assert chat["choices"][0]["message"]["content"] == "Привет!"
    assert chat["choices"][0]["finish_reason"] == "stop"
    assert chat["usage"] == {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}


def test_responses_to_chat_with_tool_calls():
    resp = {
        "id": "resp_2", "status": "completed", "model": "m",
        "output": [{"type": "function_call", "call_id": "c1", "name": "read_file",
                    "arguments": "{\"path\":\"a\"}"}],
        "usage": {"input_tokens": 3, "output_tokens": 4},
    }
    chat = responses_to_chat_response(resp)
    msg = chat["choices"][0]["message"]
    assert chat["choices"][0]["finish_reason"] == "tool_calls"
    assert msg["tool_calls"][0]["id"] == "c1"
    assert msg["tool_calls"][0]["function"]["name"] == "read_file"


def _sse(events: list[dict]) -> bytes:
    return b"".join(b"data: " + json.dumps(e).encode() + b"\n\n" for e in events)


def test_sse_translator():
    tr = ResponsesSSETranslator("gpt-5.6-sol")
    out = tr.feed(_sse([
        {"type": "response.output_text.delta", "delta": "При"},
        {"type": "response.output_text.delta", "delta": "вет!"},
    ]))
    lines = [ln[5:].strip() for ln in out.decode().splitlines() if ln.startswith("data:")]
    chunks = [json.loads(ln) for ln in lines]
    assert chunks[0]["choices"][0]["delta"]["content"] == "При"
    assert chunks[1]["choices"][0]["delta"]["content"] == "вет!"
    assert tr.completion_chars == len("Привет!")

    out2 = tr.feed(_sse([
        {"type": "response.completed",
         "response": {"usage": {"input_tokens": 3, "output_tokens": 6, "total_tokens": 9}}},
    ]))
    lines2 = [ln[5:].strip() for ln in out2.decode().splitlines() if ln.startswith("data:")]
    final = json.loads(lines2[0])
    assert final["choices"][0]["finish_reason"] == "stop"
    assert final["usage"]["total_tokens"] == 9
    assert lines2[1].strip() == "[DONE]"
    assert tr.last_usage["total_tokens"] == 9


def test_sse_translator_split_packets():
    """Событие, разрезанное между сетевыми пакетами (баг «stream ended without finish_reason»)."""
    tr = ResponsesSSETranslator("gpt-5.6-sol")
    tr.feed(_sse([{"type": "response.output_text.delta", "delta": "Привет"}]))
    completed = _sse([
        {"type": "response.completed",
         "response": {"usage": {"input_tokens": 2, "output_tokens": 6, "total_tokens": 8}}},
    ])
    # разрезаем completed-событие пополам — строго посередине JSON
    cut = len(completed) // 2
    out1 = tr.feed(completed[:cut])
    assert out1 == b""  # разрезанное событие не выдаётся
    out2 = tr.feed(completed[cut:])
    assert b'"finish_reason": "stop"' in out2 or b'"finish_reason":"stop"' in out2
    assert b"[DONE]" in out2
    assert tr.last_usage is not None and tr.last_usage["total_tokens"] == 8

    # строка без завершающего \n в конце стрима — достаётся через flush()
    tr2 = ResponsesSSETranslator("m")
    raw = b'data: {"type": "response.completed", "response": {"usage": {"input_tokens": 1, "output_tokens": 1}}}'
    tr2.feed(raw[:20])   # кусок события
    tr2.feed(raw[20:])   # остаток — но строка не закрыта \n
    tail = tr2.flush()
    assert b'"finish_reason": "stop"' in tail or b'"finish_reason":"stop"' in tail


def test_sse_translator_tool_calls():
    tr = ResponsesSSETranslator("m")
    tr.feed(_sse([
        {"type": "response.output_item.added", "output_index": 1,
         "item": {"type": "function_call", "call_id": "c9", "name": "read_file"}},
        {"type": "response.function_call_arguments.delta", "output_index": 1, "delta": "{\"p\""},
    ]))
    tr.feed(_sse([
        {"type": "response.function_call_arguments.delta", "output_index": 1, "delta": ":1}"},
        {"type": "response.completed", "response": {"usage": {"input_tokens": 1, "output_tokens": 2}}},
    ]))
    assert tr.last_usage is not None
    assert tr.completion_chars >= 0


# ------------------------------------------------ интеграция через шлюз

pytestmark = pytest.mark.usefixtures("seeded_db")


class _ResponsesOnlyAdapter:
    """Апстрим: chat/completions → 400 «нужен /v1/responses»; responses → работает."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def get_user_self(self, creds):
        from gateway_app.providers.base import UserSelf

        return UserSelf(id=1, username="u", quota=1_000_000, used_quota=0, request_count=0, group=None)

    async def list_models(self, creds):
        return ["gpt-5.6-sol"]

    async def list_tokens(self, creds):
        return []

    async def create_token(self, creds, spec):
        from gateway_app.providers.base import ProviderTokenInfo

        return ProviderTokenInfo(provider_token_id=1, name=spec.name, key_full="sk-fake")

    async def update_token(self, creds, token_id, fields):
        from gateway_app.providers.base import ProviderTokenInfo

        return ProviderTokenInfo(provider_token_id=token_id)

    async def delete_token(self, creds, token_id):
        return None

    async def checkin(self, creds, cached_path=None):
        from gateway_app.providers.base import CheckinOutcome

        return CheckinOutcome(True, path="/api/user/checkin")

    async def platform_status(self, base_url):
        from gateway_app.providers.base import PlatformStatus

        return PlatformStatus(version="t")

    async def chat_non_stream(self, creds, api_key, payload, *, path="/v1/chat/completions", **kw):
        self.calls.append(path)
        if path == "/v1/chat/completions":
            raise _make_400_error()
        return await self._responses_result(payload)

    def chat_stream(self, creds, api_key, payload, *, path="/v1/chat/completions", **kw):
        self.calls.append(path + ":stream")
        if path == "/v1/chat/completions":
            raise _make_400_error()
        return _FakeCtx(self, payload)

    async def _responses_result(self, payload):
        from gateway_app.providers.base import UpstreamResponse

        text = "".join(
            p.get("text", "")
            for item in payload["input"] if item.get("type") == "message"
            for p in item.get("content", [])
        )
        return UpstreamResponse(status=200, body={
            "id": "resp_x", "status": "completed", "model": payload["model"],
            "output": [{"type": "message", "role": "assistant",
                        "content": [{"type": "output_text", "text": "Эхо: " + text}]}],
            "usage": {"input_tokens": 4, "output_tokens": 2, "total_tokens": 6},
        })


class _FakeCtx:
    def __init__(self, adapter, payload):
        self.adapter = adapter
        self.payload = payload

    async def __aenter__(self):
        from gateway_app.providers.base import UpstreamStream

        class _Live(UpstreamStream):
            def __init__(s, adapter, payload):
                super().__init__(status=200, headers={}, _close=None)
                s.adapter, s.payload = adapter, payload
                s.last_usage = {"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6}
                s.completion_chars = 2

            async def aiter_bytes(s):
                for ev in (
                    {"type": "response.output_text.delta", "delta": "стрим-"},
                    {"type": "response.output_text.delta", "delta": "ответ"},
                    {"type": "response.completed", "response": {
                        "usage": {"input_tokens": 4, "output_tokens": 2, "total_tokens": 6}}},
                ):
                    yield b"data: " + json.dumps(ev).encode() + b"\n\n"

        return _Live(self.adapter, self.payload)

    async def __aexit__(self, *exc):
        return None


@pytest.fixture
def responses_adapter(fake_adapter):
    """Подменяет FakeAdapter на Responses-ONLY адаптер."""
    import gateway_app.api.deps as deps

    adapter = _ResponsesOnlyAdapter()
    deps._adapter = adapter
    yield adapter
    deps._adapter = fake_adapter


async def _make_key():
    from gateway_app.core.security import get_secret_box
    from gateway_app.db import database
    from gateway_app.domain.keys.gateway_keys import create_gateway_key

    async with database.make_session() as session:
        _, full = await create_gateway_key(
            session, get_secret_box(), name="t", token_limit=None, expires_at=None, allowed_models=None
        )
    return full


async def test_gateway_auto_translates_responses_api(client, responses_adapter, account_with_key):
    key = await _make_key()
    resp = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": "glm-4.6", "messages": [{"role": "user", "content": "привет"}]},
    )
    assert resp.status_code == 200, resp.text[:400]
    body = resp.json()
    assert body["choices"][0]["message"]["content"] == "Эхо: привет", resp.text[:400]
    assert body["usage"]["total_tokens"] == 6
    # первая попытка — chat/completions, затем авто-переход на responses
    assert responses_adapter.calls == ["/v1/chat/completions", "/v1/responses"]

    # вторая просьба идёт сразу на responses (модель в кэше)
    resp2 = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": "glm-4.6", "messages": [{"role": "user", "content": "ещё"}]},
    )
    assert resp2.status_code == 200
    assert responses_adapter.calls[-1] == "/v1/responses"


async def test_gateway_auto_translates_stream(client, responses_adapter, account_with_key):
    key = await _make_key()
    resp = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": "glm-4.6", "messages": [], "stream": True},
    )
    assert resp.status_code == 200
    assert b"chat.completion.chunk" in resp.content
    assert "стрим-".encode() in resp.content and "ответ".encode() in resp.content
    assert b"[DONE]" in resp.content
    assert "/v1/responses:stream" in responses_adapter.calls[-1]
