"""Трансляция OpenAI Chat Completions ↔ Responses API (§2.1, runtime-discovery §2.6).

Некоторые модели апстрима (новые GPT-модели AgentRouter) поддерживают только
`/v1/responses`. Шлюз детектит это по ошибке апстрима («does not support
/v1/chat/completions … use /v1/responses»), переводит payload и запрос/ответ/SSE
транслируются в chat-формат для IDE-клиентов.
"""

import json
import time
from typing import Any

RESPONSES_PATH = "/v1/responses"
CHAT_PATH = "/v1/chat/completions"


def is_responses_required(exc: Exception) -> bool:
    """Апстрим ответил, что модель не поддерживает chat/completions (нужен /v1/responses)."""
    msg = str(getattr(exc, "message", "") or exc)
    low = msg.lower()
    return "/v1/responses" in low and ("does not support" in low or "不支持" in msg)


# ------------------------------------------------------------------ request


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                parts.append(part["text"])
        return "\n".join(parts)
    return "" if content is None else str(content)


def _content_for_responses(role: str, content: Any) -> list[dict[str, Any]]:
    """Контент → items responses API: input_text/output_text + input_image."""
    text_type = "output_text" if role == "assistant" else "input_text"
    if isinstance(content, str):
        return [{"type": text_type, "text": content}]
    if isinstance(content, list):
        out: list[dict[str, Any]] = []
        for part in content:
            if not isinstance(part, dict):
                continue
            ptype = part.get("type")
            if ptype == "text" and isinstance(part.get("text"), str):
                out.append({"type": text_type, "text": part["text"]})
            elif ptype == "image_url":
                url = (part.get("image_url") or {})
                src = url.get("url") if isinstance(url, dict) else url
                if src:
                    out.append({"type": "input_image", "image_url": src})
        return out or [{"type": text_type, "text": ""}]
    return [{"type": text_type, "text": ""}]


def chat_to_responses_payload(chat: dict[str, Any]) -> dict[str, Any]:
    """Chat Completions payload → Responses API payload."""
    instructions: list[str] = []
    input_items: list[dict[str, Any]] = []
    tools_out: list[dict[str, Any]] = []

    for msg in chat.get("messages") or []:
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role") or "user")
        content = msg.get("content")

        if role in ("system", "developer"):
            text = _content_to_text(content)
            if text:
                instructions.append(text)
            continue
        if role == "tool":
            input_items.append(
                {
                    "type": "function_call_output",
                    "call_id": msg.get("tool_call_id"),
                    "output": _content_to_text(content),
                }
            )
            continue

        tool_calls = msg.get("tool_calls") or []
        if role == "assistant" and tool_calls:
            text = _content_to_text(content)
            if text:
                input_items.append(
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": text}],
                    }
                )
            for tc in tool_calls:
                if not isinstance(tc, dict):
                    continue
                fn = tc.get("function") or {}
                input_items.append(
                    {
                        "type": "function_call",
                        "name": fn.get("name"),
                        "arguments": fn.get("arguments") or "{}",
                        "call_id": tc.get("id"),
                    }
                )
            continue

        input_items.append(
            {"type": "message", "role": role, "content": _content_for_responses(role, content)}
        )

    for tool in chat.get("tools") or []:
        if isinstance(tool, dict) and tool.get("type") == "function":
            fn = tool.get("function") or {}
            tools_out.append(
                {
                    "type": "function",
                    "name": fn.get("name"),
                    "description": fn.get("description"),
                    "parameters": fn.get("parameters"),
                }
            )

    out: dict[str, Any] = {
        "model": chat.get("model"),
        "input": input_items,
        "stream": bool(chat.get("stream")),
    }
    if instructions:
        out["instructions"] = "\n\n".join(instructions)
    if tools_out:
        out["tools"] = tools_out
    max_tokens = chat.get("max_tokens") or chat.get("max_completion_tokens")
    if max_tokens:
        out["max_output_tokens"] = max_tokens
    if chat.get("temperature") is not None:
        out["temperature"] = chat["temperature"]
    if chat.get("top_p") is not None:
        out["top_p"] = chat["top_p"]
    response_format = chat.get("response_format")
    if isinstance(response_format, dict):
        fmt: dict[str, Any] = {"type": response_format.get("type")}
        schema = response_format.get("json_schema")
        if isinstance(schema, dict):
            fmt.update(schema)
        out["text"] = {"format": fmt}
    return out


# ----------------------------------------------------------------- response


def responses_to_chat_response(resp: dict[str, Any]) -> dict[str, Any]:
    """Не-стрим ответ /v1/responses → chat.completion."""
    text_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    for item in resp.get("output") or []:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if item_type == "message":
            for part in item.get("content") or []:
                if isinstance(part, dict) and part.get("type") in ("output_text", "text"):
                    text_parts.append(part.get("text") or "")
        elif item_type == "function_call":
            tool_calls.append(
                {
                    "id": item.get("call_id") or item.get("id") or f"call_{len(tool_calls)}",
                    "type": "function",
                    "function": {
                        "name": item.get("name"),
                        "arguments": item.get("arguments") or "{}",
                    },
                }
            )

    usage_in = resp.get("usage") or {}
    prompt = int(usage_in.get("input_tokens") or 0)
    completion = int(usage_in.get("output_tokens") or 0)
    message: dict[str, Any] = {"role": "assistant", "content": "".join(text_parts) or None}
    finish_reason = "stop"
    if tool_calls:
        message["tool_calls"] = tool_calls
        finish_reason = "tool_calls"
    if resp.get("status") == "incomplete":
        finish_reason = "length"

    return {
        "id": resp.get("id") or f"chatcmpl-resp-{int(time.time())}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": resp.get("model"),
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
        "usage": {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": int(usage_in.get("total_tokens") or prompt + completion),
        },
    }


# --------------------------------------------------------------- SSE stream


class ResponsesSSETranslator:
    """SSE /v1/responses → SSE chat.completion.chunk (по событиям)."""

    def __init__(self, model: str) -> None:
        self.model = model
        self.last_usage: dict[str, Any] | None = None
        self.completion_chars = 0
        self._tools: dict[int, dict[str, Any]] = {}
        self._id = f"chatcmpl-resp-{int(time.time())}"

    def _chunk(self, choices_delta: dict[str, Any], usage: dict[str, Any] | None = None) -> bytes:
        obj: dict[str, Any] = {
            "id": self._id,
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": self.model,
            "choices": [{"index": 0, **choices_delta}],
        }
        if usage is not None:
            obj["usage"] = usage
        return b"data: " + json.dumps(obj, ensure_ascii=False).encode() + b"\n\n"

    def feed(self, chunk: bytes) -> bytes:
        out: list[bytes] = []
        try:
            text = chunk.decode("utf-8", errors="ignore")
        except Exception:
            return b""
        for line in text.splitlines():
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if not data or data == "[DONE]":
                continue
            try:
                ev = json.loads(data)
            except json.JSONDecodeError:
                continue
            if not isinstance(ev, dict):
                continue
            etype = ev.get("type")

            if etype == "response.output_text.delta":
                delta = ev.get("delta") or ""
                self.completion_chars += len(delta)
                out.append(self._chunk({"delta": {"content": delta}, "finish_reason": None}))
            elif etype == "response.output_item.added":
                item = ev.get("item") or {}
                if item.get("type") == "function_call":
                    idx = int(ev.get("output_index") or len(self._tools))
                    self._tools[idx] = {
                        "id": item.get("call_id") or item.get("id") or f"call_{idx}",
                        "name": item.get("name"),
                    }
            elif etype == "response.function_call_arguments.delta":
                idx = int(ev.get("output_index") or 0)
                info = self._tools.setdefault(idx, {"id": f"call_{idx}", "name": None})
                tc: dict[str, Any] = {
                    "index": idx,
                    "id": info["id"],
                    "type": "function",
                    "function": {"arguments": ev.get("delta") or ""},
                }
                if info.get("name"):
                    tc["function"]["name"] = info["name"]
                out.append(self._chunk({"delta": {"tool_calls": [tc]}, "finish_reason": None}))
            elif etype in ("response.completed", "response.incomplete"):
                resp = ev.get("response") or {}
                u = resp.get("usage") or {}
                prompt = int(u.get("input_tokens") or 0)
                completion = int(u.get("output_tokens") or 0)
                self.last_usage = {
                    "prompt_tokens": prompt,
                    "completion_tokens": completion,
                    "total_tokens": int(u.get("total_tokens") or prompt + completion),
                }
                finish = "tool_calls" if self._tools else ("length" if etype == "response.incomplete" else "stop")
                out.append(self._chunk({"delta": {}, "finish_reason": finish}, usage=self.last_usage))
                out.append(b"data: [DONE]\n\n")
        return b"".join(out)


class ResponsesLiveStream:
    """Обёртка стрима /v1/responses: отдаёт chat-формат SSE + usage (duck-typing UpstreamStream)."""

    def __init__(self, upstream: Any, model: str) -> None:
        self._upstream = upstream
        self._translator = ResponsesSSETranslator(model)

    @property
    def status(self) -> int:
        return int(self._upstream.status)

    @property
    def headers(self) -> dict[str, str]:
        return self._upstream.headers

    @property
    def last_usage(self) -> dict[str, Any] | None:
        return self._translator.last_usage

    @property
    def completion_chars(self) -> int:
        return self._translator.completion_chars

    async def aiter_bytes(self):  # type: ignore[no-untyped-def]
        async for chunk in self._upstream.aiter_bytes():
            translated = self._translator.feed(chunk)
            if translated:
                yield translated

    async def close(self) -> None:
        aclose = getattr(self._upstream, "aclose", None)
        if aclose is not None:
            await aclose()
