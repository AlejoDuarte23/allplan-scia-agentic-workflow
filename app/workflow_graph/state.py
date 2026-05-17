import re

import viktor as vkt

from app.workflow_graph.models import (
    Node,
    PlanTodo,
    Workflow,
    WorkflowCanvasState,
    WorkflowPlan,
)

WORKFLOW_GRAPH_STATE_STORAGE_KEY = "workflow_graph_state"


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "workflow"


def _topological_node_order(workflow: Workflow) -> list[Node]:
    by_id = {node.id: node for node in workflow.nodes}
    indegree = {node.id: 0 for node in workflow.nodes}
    outgoing: dict[str, list[str]] = {node.id: [] for node in workflow.nodes}

    for node in workflow.nodes:
        for dep in node.depends_on:
            outgoing.setdefault(dep.node_id, []).append(node.id)
            indegree[node.id] = indegree.get(node.id, 0) + 1

    queue = [node.id for node in workflow.nodes if indegree[node.id] == 0]
    order: list[str] = []
    while queue:
        node_id = queue.pop(0)
        order.append(node_id)
        for downstream in outgoing.get(node_id, []):
            indegree[downstream] -= 1
            if indegree[downstream] == 0:
                queue.append(downstream)

    if len(order) != len(workflow.nodes):
        return workflow.nodes
    return [by_id[node_id] for node_id in order]


def _describe_node(node: Node) -> str | None:
    if not node.depends_on:
        return "Starting point for this workflow."

    upstream_titles = ", ".join(dep.node_id for dep in node.depends_on)
    return f"Depends on: {upstream_titles}."


def build_default_plan(workflow_name: str, workflow: Workflow) -> WorkflowPlan:
    ordered_nodes = _topological_node_order(workflow)
    plan_id = f"{_slugify(workflow_name)}-plan"
    todos = [
        PlanTodo(
            id=node.id,
            label=node.title,
            description=_describe_node(node),
        )
        for node in ordered_nodes
    ]
    return WorkflowPlan(
        id=plan_id,
        title=f"{workflow_name} Plan",
        description="Agent-managed checklist for the current workflow graph.",
        todos=todos,
        max_visible_todos=min(max(len(todos), 1), 4),
    )


def build_canvas_state(workflow_name: str, workflow: Workflow) -> WorkflowCanvasState:
    return WorkflowCanvasState(
        workflow_name=workflow_name,
        workflow=workflow,
        plan=build_default_plan(workflow_name, workflow),
        progress=None,
    )


def save_canvas_state(state: WorkflowCanvasState) -> None:
    payload = state.model_dump_json()
    vkt.Storage().set(
        WORKFLOW_GRAPH_STATE_STORAGE_KEY,
        data=vkt.File.from_data(payload),
        scope="entity",
    )


def load_canvas_state() -> WorkflowCanvasState | None:
    try:
        stored_file = vkt.Storage().get(WORKFLOW_GRAPH_STATE_STORAGE_KEY, scope="entity")
        raw = stored_file.getvalue_binary().decode("utf-8")
        return WorkflowCanvasState.model_validate_json(raw)
    except Exception:
        return None


def delete_canvas_state() -> None:
    try:
        vkt.Storage().delete(WORKFLOW_GRAPH_STATE_STORAGE_KEY, scope="entity")
    except Exception:
        pass
