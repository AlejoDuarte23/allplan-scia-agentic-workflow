from __future__ import annotations

import asyncio
import queue
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

from agents import Agent, ItemHelpers, Runner, set_tracing_disabled
from openai.types.responses import ResponseTextDeltaEvent

from app.agent.system_prompt import SYSTEM_PROMPT
from app.agent.tools import get_agent_tools, get_tool_display_names

DEFAULT_AGENT_MODEL = "gpt-5.4"

event_loop: asyncio.AbstractEventLoop | None = None
event_loop_thread: threading.Thread | None = None

set_tracing_disabled(True)


@dataclass
class AgentContext:
    """Context object reserved for future workflow state."""


def create_workflow_agent(*, model: str = DEFAULT_AGENT_MODEL) -> Agent[AgentContext]:
    return Agent[AgentContext](
        name="Allplan SCIA Workflow Assistant",
        instructions=SYSTEM_PROMPT,
        model=model,
        tools=get_agent_tools(),
    )


def ensure_loop() -> asyncio.AbstractEventLoop:
    global event_loop, event_loop_thread
    if event_loop and event_loop.is_running():
        return event_loop

    event_loop = asyncio.new_event_loop()
    event_loop_thread = threading.Thread(
        target=event_loop.run_forever,
        name="workflow-agent-loop",
        daemon=True,
    )
    event_loop_thread.start()
    return event_loop


def _extract_call_id(raw: Any) -> str | None:
    if isinstance(raw, dict):
        value = raw.get("call_id") or raw.get("id") or raw.get("tool_call_id")
        return str(value) if value else None

    for attr in ("call_id", "id", "tool_call_id"):
        value = getattr(raw, attr, None)
        if value:
            return str(value)
    return None


def _extract_tool_name(raw: Any) -> str:
    if isinstance(raw, dict):
        if raw.get("name"):
            return str(raw["name"])
        fn = raw.get("function")
        if isinstance(fn, dict) and fn.get("name"):
            return str(fn["name"])
        if raw.get("tool_name"):
            return str(raw["tool_name"])

    for attr in ("name", "tool_name", "function_name"):
        value = getattr(raw, attr, None)
        if value:
            return str(value)

    fn = getattr(raw, "function", None)
    if fn is not None and getattr(fn, "name", None):
        return str(fn.name)
    return "tool"


def _normalize_stream_text(text: str) -> str:
    return " ".join(text.split()).strip()


def workflow_agent_sync_stream(
    chat_history: list[dict[str, str]],
    *,
    on_done: Callable[[], None] | None = None,
    show_tool_progress: bool = True,
    model: str = DEFAULT_AGENT_MODEL,
) -> Iterator[str]:
    q: queue.Queue[object] = queue.Queue()
    sentinel = object()
    loop = ensure_loop()

    async def _produce() -> None:
        call_id_to_name: dict[str, str] = {}
        pending_assistant_message: str | None = None
        streamed_text = ""

        try:
            agent = create_workflow_agent(model=model)
            result = Runner.run_streamed(
                agent,
                input=chat_history,
                context=AgentContext(),
                max_turns=100,
            )

            async for event in result.stream_events():
                if event.type == "raw_response_event" and isinstance(
                    event.data,
                    ResponseTextDeltaEvent,
                ):
                    if event.data.delta:
                        streamed_text += event.data.delta
                        q.put(event.data.delta)
                    continue

                if not show_tool_progress:
                    continue

                if event.type != "run_item_stream_event":
                    continue

                item = event.item
                raw = getattr(item, "raw_item", None)

                if (
                    event.name == "message_output_created"
                    and getattr(item, "type", None) == "message_output_item"
                ):
                    text = ItemHelpers.text_message_output(item).strip()
                    if text:
                        if _normalize_stream_text(text) == _normalize_stream_text(
                            streamed_text
                        ):
                            pending_assistant_message = None
                        else:
                            pending_assistant_message = text
                    streamed_text = ""
                    continue

                if event.name == "tool_called":
                    if pending_assistant_message:
                        q.put(f"\n\n{pending_assistant_message}\n\n")
                        pending_assistant_message = None
                    call_id = _extract_call_id(raw)
                    tool_name = _extract_tool_name(raw)
                    if call_id:
                        call_id_to_name[call_id] = tool_name
                    display_name = get_tool_display_names().get(tool_name, tool_name)
                    q.put(f"\n\n> Running **{display_name}**\n")
                    continue

                if event.name == "tool_output":
                    call_id = _extract_call_id(raw)
                    tool_name = call_id_to_name.get(call_id or "", "tool")
                    display_name = get_tool_display_names().get(tool_name, tool_name)
                    q.put(f"\n> Done **{display_name}**\n\n")
                    continue

        except Exception as exc:
            q.put(f"\n\n{type(exc).__name__}: {exc}\n")
        finally:
            if pending_assistant_message:
                q.put(f"\n\n{pending_assistant_message}\n\n")
            q.put(sentinel)

    asyncio.run_coroutine_threadsafe(_produce(), loop)

    def _gen() -> Iterator[str]:
        while True:
            item = q.get()
            if item is sentinel:
                break
            yield item  # type: ignore[misc]
        if on_done:
            on_done()

    return _gen()
