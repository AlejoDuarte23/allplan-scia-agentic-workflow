import json
import logging

import viktor as vkt
from dotenv import load_dotenv

from app.agent.runner import workflow_agent_sync_stream
from app.viktor_api_tool.code_editor import (
    get_code_editor_visibility,
    render_code_editor_html,
)
from app.viktor_tools.table_tool import TableTool
from app.workflow_graph.state import delete_canvas_state, load_canvas_state
from app.workflow_graph.viewer import WorkflowViewer

load_dotenv()

logger = logging.getLogger(__name__)


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
