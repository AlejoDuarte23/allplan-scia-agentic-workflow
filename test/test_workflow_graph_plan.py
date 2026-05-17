from app.workflow_graph.models import Connection, Node, Workflow, WorkflowCanvasState
from app.workflow_graph.state import build_default_plan
from app.workflow_graph.viewer import WorkflowViewer


def test_build_default_plan_follows_dependency_order():
    workflow = Workflow(
        nodes=[
            Node(id="analyze", title="Analyze", depends_on=[Connection(node_id="extract")]),
            Node(id="extract", title="Extract"),
            Node(id="report", title="Report", depends_on=[Connection(node_id="analyze")]),
        ]
    )

    plan = build_default_plan("SCIA Flow", workflow)

    assert [todo.id for todo in plan.todos] == ["extract", "analyze", "report"]
    assert all(todo.status == "pending" for todo in plan.todos)
    assert plan.title == "SCIA Flow Plan"


def test_workflow_viewer_embeds_plan_overlay_state():
    workflow = Workflow(nodes=[Node(id="start", title="Start")])
    state = WorkflowCanvasState(
        workflow_name="Feature Workflow",
        workflow=workflow,
        plan=build_default_plan("Feature Workflow", workflow),
        progress=None,
    )

    html = WorkflowViewer(lambda: state).render_html()

    assert "workflow-overlay" in html
    assert "Feature Workflow Plan" in html
    assert "workflow-progress-bar" in html
