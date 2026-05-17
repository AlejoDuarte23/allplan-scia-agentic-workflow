from typing import Literal

from pydantic import BaseModel, Field


class Connection(BaseModel):
    node_id: str
    kind: str = "default"


class Node(BaseModel):
    id: str
    title: str
    type: str = "default"
    url: str | None = None
    depends_on: list[Connection] = Field(default_factory=list)


class Workflow(BaseModel):
    nodes: list[Node]


class PlanTodo(BaseModel):
    id: str
    label: str
    status: Literal["pending", "in_progress", "completed", "cancelled"] = "pending"
    description: str | None = None


class WorkflowPlan(BaseModel):
    id: str
    title: str
    description: str | None = None
    todos: list[PlanTodo]
    max_visible_todos: int = Field(default=4, ge=1)


class ProgressStep(BaseModel):
    id: str
    label: str
    description: str | None = None
    status: Literal["pending", "in_progress", "completed", "failed"] = "pending"


class WorkflowProgress(BaseModel):
    id: str
    title: str = "Execution Progress"
    steps: list[ProgressStep]
    elapsed_time_ms: int | None = Field(default=None, ge=0)


class WorkflowCanvasState(BaseModel):
    workflow_name: str
    workflow: Workflow
    plan: WorkflowPlan | None = None
    progress: WorkflowProgress | None = None
