import asyncio
import json
import logging
import queue
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from textwrap import dedent
from typing import Any

import viktor as vkt
from agents import Agent, ItemHelpers, Runner, set_tracing_disabled
from dotenv import load_dotenv
from openai.types.responses import ResponseTextDeltaEvent

from app.tools import TOOL_DISPLAY_NAMES, get_tools
from app.viktor_api_tool.code_editor import (
    get_code_editor_visibility,
    render_code_editor_html,
)
from app.viktor_tools.table_tool import TableTool
from app.workflow_graph.state import delete_canvas_state, load_canvas_state
from app.workflow_graph.viewer import WorkflowViewer

load_dotenv()

logger = logging.getLogger(__name__)

event_loop: asyncio.AbstractEventLoop | None = None
event_loop_thread: threading.Thread | None = None

set_tracing_disabled(True)


@dataclass
class AgentContext:
    """Context object reserved for future workflow state."""


def ensure_loop() -> asyncio.AbstractEventLoop:
    """Ensure a background event loop is running for the async agent runner."""
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
) -> Iterator[str]:
    q: queue.Queue[object] = queue.Queue()
    sentinel = object()
    loop = ensure_loop()

    async def _produce() -> None:
        call_id_to_name: dict[str, str] = {}
        pending_assistant_message: str | None = None
        streamed_text = ""

        try:
            agent = Agent[AgentContext](
                name="Allplan SCIA Workflow Assistant",
                instructions=dedent(
                    """
                    You are an engineering workflow assistant inside a VIKTOR app template.
                    Help users compose and track Allplan, SCIA, and VIKTOR API workflows.

                    Available tools:
                    - compose_workflow_graph: create a directed workflow graph.
                    - get_workflow_plan, set_workflow_plan, update_workflow_plan:
                      read and manage the plan card on the workflow graph.
                    - set_workflow_progress: show execution progress below the plan.
                    - inspect_viktor_app: get entity properties, resolved parametrization,
                      default payload candidates, available methods, and DataView/TableView methods.
                    - run_viktor_app_method: call a VIKTOR app method through the VIKTOR REST API.
                    - create_viktor_sibling_entity: create a new entity next to a source entity.
                    - generate_viktor_bridge_code: inspect two apps and write Python bridge code.
                    - save_workflow_code and show_hide_code_editor: control the Monaco code WebView.
                    - run_openai_shell_viktor_task: delegate flexible API exploration to the
                      OpenAI hosted shell tool with a domain-scoped TOKEN_VK_APP secret.
                    - generate_table and show_hide_table: create and display tabular outputs.

                    Workflow rules:
                    - When the user asks to connect VIKTOR apps, inspect both apps first.
                    - Prefer DataView or TableView source methods as outputs that can feed another app.
                    - Use default_plus_saved_payload as the first payload candidate unless the user provides params.
                    - If direct tools are too rigid, use run_openai_shell_viktor_task for trial-and-error API calls.
                    - When the mapping is understood, generate and show the Python bridge code.
                    - When the user asks to create or start a workflow graph, create the graph first.
                    - After creating a graph, make sure a useful plan exists.
                    - Before updating plan items, call get_workflow_plan and use the existing todo ids.
                    - Mark a todo in_progress when starting work and completed when it finishes.
                    - Use cancelled only when a planned step is intentionally skipped.
                    - Use set_workflow_progress for lower-level execution progress.
                    - Use concise engineering language. Avoid inventing external tool results.

                    Suggested node types:
                    - allplan_model: source model or model extraction step.
                    - scia_model: SCIA model generation or analysis step.
                    - viktor_api_tool: a remote VIKTOR app method call.
                    - table_output: tabular result output.
                    - workflow_step: a generic process step.
                    """
                ),
                model="gpt-5-mini",
                tools=get_tools(),
            )

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
                    display_name = TOOL_DISPLAY_NAMES.get(tool_name, tool_name)
                    q.put(f"\n\n> Running **{display_name}**\n")
                    continue

                if event.name == "tool_output":
                    call_id = _extract_call_id(raw)
                    tool_name = call_id_to_name.get(call_id or "", "tool")
                    display_name = TOOL_DISPLAY_NAMES.get(tool_name, tool_name)
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


def get_table_visibility(params, **kwargs) -> bool:
    if not params.chat:
        for key in ("show_table", "TableTool"):
            try:
                vkt.Storage().delete(key, scope="entity")
            except Exception:
                pass

    try:
        action = vkt.Storage().get("show_table", scope="entity").getvalue()
        return action == "show"
    except Exception:
        return False


class Parametrization(vkt.Parametrization):
    title = vkt.Text(
        """# Allplan SCIA Agentic Workflow

Use the chat to build workflow graphs, track execution plans, call VIKTOR API tools, and display engineering tables.
"""
    )
    chat = vkt.Chat("", method="call_llm")


class Controller(vkt.Controller):
    parametrization = Parametrization

    def call_llm(self, params, **kwargs) -> vkt.ChatResult | None:
        if not params.chat:
            return None

        messages = params.chat.get_messages()
        chat_history = [{"role": m["role"], "content": m["content"]} for m in messages]

        text_stream = workflow_agent_sync_stream(
            chat_history,
            on_done=self._refresh_workflow_storage,
            show_tool_progress=True,
        )

        return vkt.ChatResult(params.chat, text_stream)

    def _refresh_workflow_storage(self) -> None:
        canvas_state = load_canvas_state()
        if canvas_state is None:
            return

        viewer = WorkflowViewer(lambda: canvas_state)
        data_json = json.dumps(
            {
                "html": viewer.write(),
                "workflow_name": canvas_state.workflow_name,
            }
        )
        try:
            vkt.Storage().set(
                "workflow_html",
                data=vkt.File.from_data(data_json),
                scope="entity",
            )
        except Exception:
            logger.exception("Could not refresh workflow HTML storage")

    @vkt.WebView("Workflow Graph", width=100)
    def workflow_view(self, params, **kwargs) -> vkt.WebResult:
        if not params.chat:
            delete_canvas_state()
            try:
                vkt.Storage().delete("workflow_html", scope="entity")
            except Exception:
                pass

        canvas_state = load_canvas_state()
        if canvas_state is not None:
            viewer = WorkflowViewer(lambda: canvas_state)
            return vkt.WebResult(html=viewer.write())

        try:
            stored_file = vkt.Storage().get("workflow_html", scope="entity")
            data_json = stored_file.getvalue_binary().decode("utf-8")
            html_content = json.loads(data_json).get("html", "")
            if html_content:
                return vkt.WebResult(html=html_content)
        except Exception:
            pass

        placeholder_html = (
            "<!doctype html><html><head><style>"
            "body{margin:0;background:#fff;}"
            "</style></head><body></body></html>"
        )
        return vkt.WebResult(html=placeholder_html)

    @vkt.TableView("Table Tool", width=100, visible=get_table_visibility)
    def table_view(self, params, **kwargs) -> vkt.TableResult:
        if not params.chat:
            try:
                vkt.Storage().delete("TableTool", scope="entity")
            except Exception:
                pass

        try:
            raw = (
                vkt.Storage()
                .get("TableTool", scope="entity")
                .getvalue_binary()
                .decode("utf-8")
            )
            tool_input = TableTool.model_validate_json(raw)
            return vkt.TableResult(
                data=tool_input.data,
                column_headers=tool_input.column_headers,
            )
        except Exception:
            logger.exception("Error rendering table view")
            return vkt.TableResult([["No table data"]])

    @vkt.WebView("Workflow Code", width=100, visible=get_code_editor_visibility)
    def workflow_code_view(self, params, **kwargs) -> vkt.WebResult:
        return vkt.WebResult(html=render_code_editor_html())
