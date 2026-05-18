import json
from typing import Any, Literal

import viktor as vkt
from pydantic import BaseModel, Field

from app.viktor_tools.table_tool import generate_table, show_hide_table_tool
from app.viktor_api_tool import get_viktor_api_tools
from app.viktor_api_tool.local_shell import create_viktor_local_shell_tool
from app.workflow_graph.models import (
    Connection,
    Node,
    PlanTodo,
    ProgressStep,
    Workflow,
    WorkflowPlan,
    WorkflowProgress,
)
from app.workflow_graph.state import build_canvas_state, load_canvas_state, save_canvas_state
from app.workflow_graph.viewer import WorkflowViewer

TOOL_DISPLAY_NAMES: dict[str, str] = {
    "create_dummy_workflow_node": "Create Workflow Node",
    "compose_workflow_graph": "Compose Workflow Graph",
    "get_workflow_plan": "Get Workflow Plan",
    "set_workflow_plan": "Set Workflow Plan",
    "update_workflow_plan": "Update Workflow Plan",
    "set_workflow_progress": "Set Workflow Progress",
    "inspect_viktor_app": "Inspect VIKTOR App",
    "run_viktor_app_method": "Run VIKTOR App Method",
    "create_viktor_sibling_entity": "Create VIKTOR Sibling Entity",
    "generate_viktor_bridge_code": "Generate VIKTOR Bridge Code",
    "save_workflow_code": "Save Workflow Code",
    "show_hide_code_editor": "Show/Hide Code Editor",
    "viktor_local_shell": "VIKTOR Local Shell",
    "shell": "VIKTOR Local Shell",
    "generate_table": "Generate Table",
    "show_hide_table": "Show/Hide Table",
}


class DummyWorkflowNode(BaseModel):
    node_id: str = Field(..., description="Unique id for this workflow node")
    node_type: Literal[
        "allplan_model",
        "scia_model",
        "viktor_api_tool",
        "table_output",
        "workflow_step",
        "custom_step",
    ] = Field(..., description="Type of workflow node to add to the graph")
    label: str = Field(..., description="Human-readable label for the node")
    url: str | None = Field(
        default=None,
        description="Optional URL for nodes that should open an external page or VIKTOR app.",
    )
    inputs: dict[str, Any] = Field(
        default_factory=dict,
        description="Optional input parameters for this workflow node.",
    )
    depends_on: list[str] = Field(
        default_factory=list,
        description="List of upstream node_ids this node depends on.",
    )


async def create_dummy_workflow_node_func(_ctx: Any, args: str) -> str:
    payload = DummyWorkflowNode.model_validate_json(args)
    return (
        f"Node '{payload.node_id}' ({payload.node_type}) is valid and ready "
        "for compose_workflow_graph."
    )


def create_dummy_workflow_node_tool() -> Any:
    from agents import FunctionTool

    return FunctionTool(
        name="create_dummy_workflow_node",
        description="Validate a workflow node payload before graph composition.",
        params_json_schema=DummyWorkflowNode.model_json_schema(),
        on_invoke_tool=create_dummy_workflow_node_func,
        strict_json_schema=False,
    )


class ComposeWorkflowGraphArgs(BaseModel):
    workflow_name: str = Field(..., description="Name for the composed workflow")
    nodes: list[DummyWorkflowNode] = Field(
        ...,
        description="Workflow nodes with dependencies to compose into a DAG.",
    )


def _toposort_edges(nodes: list[str], edges: list[tuple[str, str]]) -> bool:
    indegree: dict[str, int] = {node_id: 0 for node_id in nodes}
    outgoing: dict[str, list[str]] = {node_id: [] for node_id in nodes}

    for src, dst in edges:
        outgoing.setdefault(src, []).append(dst)
        indegree[dst] = indegree.get(dst, 0) + 1

    queue = [node_id for node_id, degree in indegree.items() if degree == 0]
    visited = 0
    while queue:
        node_id = queue.pop(0)
        visited += 1
        for next_node_id in outgoing.get(node_id, []):
            indegree[next_node_id] -= 1
            if indegree[next_node_id] == 0:
                queue.append(next_node_id)

    return visited == len(nodes)


async def compose_workflow_graph_func(_ctx: Any, args: str) -> str:
    payload = ComposeWorkflowGraphArgs.model_validate_json(args)

    ids = [node.node_id for node in payload.nodes]
    duplicates = sorted({node_id for node_id in ids if ids.count(node_id) > 1})
    if duplicates:
        raise ValueError(f"Duplicate node_id(s): {', '.join(duplicates)}")

    id_set = set(ids)
    missing_deps: dict[str, list[str]] = {}
    edges: list[tuple[str, str]] = []
    for node in payload.nodes:
        unknown = [dep for dep in node.depends_on if dep not in id_set]
        if unknown:
            missing_deps[node.node_id] = unknown
        for dep in node.depends_on:
            edges.append((dep, node.node_id))

    if missing_deps:
        message = "; ".join(
            f"{node_id} -> missing {', '.join(deps)}"
            for node_id, deps in missing_deps.items()
        )
        raise ValueError(f"Unknown dependency node_id(s): {message}")

    if not _toposort_edges(ids, edges):
        raise ValueError("Cycle detected in depends_on; workflow_graph expects a DAG.")

    workflow = Workflow(
        nodes=[
            Node(
                id=node.node_id,
                title=node.label,
                type=node.node_type,
                url=node.url,
                depends_on=[Connection(node_id=dep) for dep in node.depends_on],
            )
            for node in payload.nodes
        ]
    )

    canvas_state = build_canvas_state(payload.workflow_name, workflow)
    viewer = WorkflowViewer(lambda: canvas_state)
    html_content = viewer.write()

    save_canvas_state(canvas_state)
    vkt.Storage().set(
        "workflow_html",
        data=vkt.File.from_data(
            json.dumps(
                {
                    "html": html_content,
                    "workflow_name": payload.workflow_name,
                }
            )
        ),
        scope="entity",
    )

    return (
        f"Workflow '{payload.workflow_name}' created with {len(payload.nodes)} "
        f"nodes and {len(edges)} connections."
    )


def compose_workflow_graph_tool() -> Any:
    from agents import FunctionTool

    return FunctionTool(
        name="compose_workflow_graph",
        description="Compose nodes into a workflow_graph DAG and render it in the app.",
        params_json_schema=ComposeWorkflowGraphArgs.model_json_schema(),
        on_invoke_tool=compose_workflow_graph_func,
        strict_json_schema=False,
    )


class WorkflowPlanTodoInput(BaseModel):
    id: str = Field(..., description="Stable todo id for this plan item")
    label: str = Field(..., description="Short todo label shown in the plan")
    status: Literal["pending", "in_progress", "completed", "cancelled"] = Field(
        default="pending",
        description="Current status for this todo item.",
    )
    description: str | None = Field(
        default=None,
        description="Optional detail shown when the todo row is expanded.",
    )


class SetWorkflowPlanArgs(BaseModel):
    title: str = Field(..., description="Plan title shown in the overlay card")
    description: str | None = Field(default=None, description="Optional plan description")
    todos: list[WorkflowPlanTodoInput] = Field(
        ...,
        description="Ordered todo items for the workflow plan.",
    )
    max_visible_todos: int = Field(
        default=4,
        ge=1,
        description="Maximum number of todos shown before the overlay collapses.",
    )


class UpdateWorkflowPlanTodoInput(BaseModel):
    id: str = Field(..., description="Existing todo id to update")
    label: str | None = Field(default=None, description="Optional replacement label")
    status: Literal["pending", "in_progress", "completed", "cancelled"] | None = Field(
        default=None,
        description="Optional replacement status.",
    )
    description: str | None = Field(
        default=None,
        description="Optional replacement description.",
    )


class GetWorkflowPlanArgs(BaseModel):
    pass


class UpdateWorkflowPlanArgs(BaseModel):
    title: str | None = Field(default=None, description="Optional replacement title")
    description: str | None = Field(default=None, description="Optional description")
    max_visible_todos: int | None = Field(
        default=None,
        ge=1,
        description="Optional replacement max visible todo count.",
    )
    todos: list[UpdateWorkflowPlanTodoInput] = Field(
        default_factory=list,
        description="Todo updates matched by id.",
    )
    append_missing: bool = Field(
        default=False,
        description="Append unknown todo ids instead of failing.",
    )


class WorkflowProgressStepInput(BaseModel):
    id: str = Field(..., description="Stable progress step id")
    label: str = Field(..., description="Short progress step label")
    description: str | None = Field(default=None, description="Optional detail text")
    status: Literal["pending", "in_progress", "completed", "failed"] = Field(
        default="pending",
        description="Current execution status for this step.",
    )


class SetWorkflowProgressArgs(BaseModel):
    title: str = Field(
        default="Execution Progress",
        description="Progress section title shown below the plan.",
    )
    steps: list[WorkflowProgressStepInput] = Field(
        default_factory=list,
        description="Ordered execution steps for the progress tracker.",
    )
    elapsed_time_ms: int | None = Field(
        default=None,
        ge=0,
        description="Optional elapsed time in milliseconds.",
    )
    clear: bool = Field(
        default=False,
        description="Clear the progress tracker instead of replacing it.",
    )


def _require_canvas_state():
    state = load_canvas_state()
    if state is None:
        raise ValueError("No workflow graph is available yet. Run compose_workflow_graph first.")
    return state


def _missing_workflow_plan_response(*, reason: str) -> str:
    response = {
        "status": "missing_prerequisite",
        "reason": reason,
        "next_steps": ["compose_workflow_graph", "set_workflow_plan"],
    }
    return json.dumps(response, indent=2)


async def get_workflow_plan_func(_ctx: Any, args: str) -> str:
    GetWorkflowPlanArgs.model_validate_json(args or "{}")
    state = load_canvas_state()
    if state is None:
        return _missing_workflow_plan_response(
            reason="No workflow graph is available yet."
        )
    if state.plan is None:
        return _missing_workflow_plan_response(
            reason=f"Workflow graph '{state.workflow_name}' has no plan yet."
        )

    plan_data = {
        "title": state.plan.title,
        "description": state.plan.description,
        "workflow_name": state.workflow_name,
        "todos": [
            {
                "id": todo.id,
                "label": todo.label,
                "status": todo.status,
                "description": todo.description,
            }
            for todo in state.plan.todos
        ],
    }
    return (
        f"Current workflow plan for '{state.workflow_name}':\n"
        f"{json.dumps(plan_data, indent=2)}"
    )


async def set_workflow_plan_func(_ctx: Any, args: str) -> str:
    payload = SetWorkflowPlanArgs.model_validate_json(args)
    try:
        state = _require_canvas_state()
    except ValueError as exc:
        return _missing_workflow_plan_response(reason=str(exc))
    state.plan = WorkflowPlan(
        id=state.plan.id if state.plan else "workflow-plan",
        title=payload.title,
        description=payload.description,
        todos=[
            PlanTodo(
                id=todo.id,
                label=todo.label,
                status=todo.status,
                description=todo.description,
            )
            for todo in payload.todos
        ],
        max_visible_todos=payload.max_visible_todos,
    )
    save_canvas_state(state)
    return f"Workflow plan updated with {len(payload.todos)} todo items."


async def update_workflow_plan_func(_ctx: Any, args: str) -> str:
    payload = UpdateWorkflowPlanArgs.model_validate_json(args)
    try:
        state = _require_canvas_state()
    except ValueError as exc:
        return _missing_workflow_plan_response(reason=str(exc))
    if state.plan is None:
        return _missing_workflow_plan_response(
            reason="No workflow plan exists yet. Run set_workflow_plan first."
        )

    todos_by_id = {todo.id: todo for todo in state.plan.todos}
    missing_ids: list[str] = []
    appended = 0
    updated = 0

    for todo_update in payload.todos:
        todo = todos_by_id.get(todo_update.id)
        if todo is None:
            if not payload.append_missing:
                missing_ids.append(todo_update.id)
                continue
            if not todo_update.label:
                raise ValueError(
                    f"Todo '{todo_update.id}' needs a label when appended."
                )
            todo = PlanTodo(
                id=todo_update.id,
                label=todo_update.label,
                status=todo_update.status or "pending",
                description=todo_update.description,
            )
            state.plan.todos.append(todo)
            todos_by_id[todo.id] = todo
            appended += 1
            continue

        if todo_update.label is not None:
            todo.label = todo_update.label
        if todo_update.status is not None:
            todo.status = todo_update.status
        if todo_update.description is not None:
            todo.description = todo_update.description
        updated += 1

    if missing_ids:
        raise ValueError(
            "Unknown todo id(s): "
            + ", ".join(missing_ids)
            + ". Pass append_missing=true to add them."
        )

    if payload.title is not None:
        state.plan.title = payload.title
    if payload.description is not None:
        state.plan.description = payload.description
    if payload.max_visible_todos is not None:
        state.plan.max_visible_todos = payload.max_visible_todos

    save_canvas_state(state)
    return (
        f"Workflow plan updated for '{state.workflow_name}' "
        f"({updated} modified, {appended} appended)."
    )


async def set_workflow_progress_func(_ctx: Any, args: str) -> str:
    payload = SetWorkflowProgressArgs.model_validate_json(args)
    try:
        state = _require_canvas_state()
    except ValueError as exc:
        return _missing_workflow_plan_response(reason=str(exc))

    if payload.clear:
        state.progress = None
        save_canvas_state(state)
        return f"Workflow progress cleared for '{state.workflow_name}'."

    state.progress = WorkflowProgress(
        id=state.progress.id if state.progress else "workflow-progress",
        title=payload.title,
        steps=[
            ProgressStep(
                id=step.id,
                label=step.label,
                description=step.description,
                status=step.status,
            )
            for step in payload.steps
        ],
        elapsed_time_ms=payload.elapsed_time_ms,
    )
    save_canvas_state(state)
    return f"Workflow progress updated with {len(payload.steps)} steps."


def get_workflow_plan_tool() -> Any:
    from agents import FunctionTool

    return FunctionTool(
        name="get_workflow_plan",
        description=(
            "Get the current workflow plan with todo ids and statuses. "
            "Call this before update_workflow_plan."
        ),
        params_json_schema=GetWorkflowPlanArgs.model_json_schema(),
        on_invoke_tool=get_workflow_plan_func,
        strict_json_schema=False,
    )


def set_workflow_plan_tool() -> Any:
    from agents import FunctionTool

    return FunctionTool(
        name="set_workflow_plan",
        description="Create or replace the workflow plan card shown on the graph.",
        params_json_schema=SetWorkflowPlanArgs.model_json_schema(),
        on_invoke_tool=set_workflow_plan_func,
        strict_json_schema=False,
    )


def update_workflow_plan_tool() -> Any:
    from agents import FunctionTool

    return FunctionTool(
        name="update_workflow_plan",
        description="Update existing todo labels, descriptions, and statuses.",
        params_json_schema=UpdateWorkflowPlanArgs.model_json_schema(),
        on_invoke_tool=update_workflow_plan_func,
        strict_json_schema=False,
    )


def set_workflow_progress_tool() -> Any:
    from agents import FunctionTool

    return FunctionTool(
        name="set_workflow_progress",
        description="Show, replace, or clear detailed execution progress.",
        params_json_schema=SetWorkflowProgressArgs.model_json_schema(),
        on_invoke_tool=set_workflow_progress_func,
        strict_json_schema=False,
    )


def get_tools() -> list[Any]:
    return [
        create_dummy_workflow_node_tool(),
        compose_workflow_graph_tool(),
        get_workflow_plan_tool(),
        set_workflow_plan_tool(),
        update_workflow_plan_tool(),
        set_workflow_progress_tool(),
        *get_viktor_api_tools(),
        create_viktor_local_shell_tool(),
        generate_table(),
        show_hide_table_tool(),
    ]
